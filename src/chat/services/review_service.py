# -*- coding: utf-8 -*-
"""社区设定审核服务：管理待审核条目的完整生命周期（提交→公开投票→批准/否决/过期）。

审核队列存储于 PostgreSQL（community_settings.pending_entries），
批准后的条目写入 community_settings.documents 并触发增量向量化。
"""
import discord
from discord.ext import tasks
import logging
from typing import Dict, Any, Optional
import weakref
import json
from datetime import datetime, timedelta, timezone
import asyncio
import re

from sqlalchemy import select, update, delete

from src.chat.config import chat_config
from src.chat.features.community_settings.services.incremental_rag_service import (
    incremental_rag_service,
)
from src.database.database import AsyncSessionLocal
from src.database.models import (
    CommunitySettingPendingEntry,
    CommunitySettingDocument,
)

log = logging.getLogger(__name__)

# --- 审核配置 ---
REVIEW_SETTINGS = chat_config.COMMUNITY_SETTINGS_CONFIG["review_settings"]
VOTE_EMOJI = REVIEW_SETTINGS["vote_emoji"]
REJECT_EMOJI = REVIEW_SETTINGS["reject_emoji"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewService:
    """管理社区设定待审条目生命周期的服务"""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.background_tasks = weakref.WeakSet()
        self.check_expired_entries.start()

    async def start_review(self, pending_id: int):
        """根据 pending_id 发起一个公开审核流程"""
        async with AsyncSessionLocal() as session:
            entry = await session.get(CommunitySettingPendingEntry, pending_id)
            if not entry:
                log.error(f"在 start_review 中找不到待审核的条目 #{pending_id}。")
                return

            if entry.entry_type != "community_setting":
                log.warning(
                    f"未知的审核条目类型: {entry.entry_type} for pending_id: {pending_id}"
                )
                return

            await self._start_setting_review(session, entry)

    async def _start_setting_review(
        self, session, entry: CommunitySettingPendingEntry
    ):
        """为社区设定条目发起审核"""
        data = entry.data_json if isinstance(entry.data_json, dict) else json.loads(entry.data_json)
        proposer = await self.bot.fetch_user(entry.proposer_id)

        embed = self._build_setting_embed(entry, data, proposer)

        review_channel_id = entry.channel_id
        channel = self.bot.get_channel(review_channel_id)
        if not channel:
            log.warning(
                f"无法找到频道 ID {review_channel_id}，审核无法发起（可能已被删除或机器人无权访问）。"
            )
            return
        if not isinstance(channel, discord.abc.Messageable):
            log.warning(
                f"频道 ID {review_channel_id} (类型: {type(channel)}) 不是一个可消息频道，审核无法发起。"
            )
            return

        review_message = await channel.send(embed=embed)

        await session.execute(
            update(CommunitySettingPendingEntry)
            .where(CommunitySettingPendingEntry.id == entry.id)
            .values(message_id=review_message.id)
        )
        await session.commit()
        log.info(f"已为待审核条目 #{entry.id} 更新 message_id 为 {review_message.id}")

    def _build_setting_embed(
        self, entry: CommunitySettingPendingEntry, data: Dict[str, Any], proposer: discord.User
    ) -> discord.Embed:
        """构建社区设定提交的审核 Embed"""
        duration = REVIEW_SETTINGS["review_duration_minutes"]
        approval_threshold = REVIEW_SETTINGS["approval_threshold"]
        instant_approval_threshold = REVIEW_SETTINGS["instant_approval_threshold"]
        rejection_threshold = REVIEW_SETTINGS["rejection_threshold"]
        title = data.get("title", data.get("name", "未知标题"))
        content = data.get("content_text", data.get("description", ""))

        embed = discord.Embed(
            title="我收到了一条新的社区设定！",
            description=(
                f"**{proposer.display_name}** 递给我一张纸条，上面写着关于 **{title}** 的设定，大家觉得内容怎么样？\n\n"
                f"*咱有 {duration} 分钟的时间来决定哦！*"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="类别", value=data.get("category_name", "通用知识"), inline=True
        )
        embed.add_field(name="标题", value=title, inline=False)

        raw_content = content or json.dumps(data, ensure_ascii=False)
        preview_content = raw_content[:500] + ("..." if len(raw_content) > 500 else "")
        embed.add_field(name="内容预览", value=preview_content, inline=False)

        rules_text = (
            f"投票小贴士: {VOTE_EMOJI} 达到{approval_threshold}个通过 | "
            f"{VOTE_EMOJI} {duration}分钟内达到{instant_approval_threshold}个立即通过 | "
            f"{REJECT_EMOJI} 达到{rejection_threshold}个否决"
        )
        footer_text = f"提交人: {proposer.display_name} | 设定ID: {entry.id} | {rules_text}"
        embed.set_footer(text=footer_text)
        embed.timestamp = entry.created_at or _utcnow()
        return embed

    async def handle_vote(self, payload: discord.RawReactionActionEvent):
        """处理来自Cog的投票事件"""
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            log.warning(f"找不到消息 {payload.message_id}，可能已被删除。")
            return

        if (
            not self.bot.user
            or message.author.id != self.bot.user.id
            or not message.embeds
        ):
            return

        embed = message.embeds[0]
        match = re.search(r"设定ID: (\d+)", embed.footer.text or "")
        if not match:
            return

        pending_id = int(match.group(1))
        log.debug(
            f"检测到对审核消息 (ID: {message.id}) 的投票，解析出 pending_id: {pending_id}"
        )
        await self.process_vote(pending_id, message)

    async def process_vote(self, pending_id: int, message: discord.Message):
        """处理投票逻辑，检查是否达到阈值"""
        log.debug(f"--- 开始处理投票 for pending_id: {pending_id} ---")
        async with AsyncSessionLocal() as session:
            entry = await session.get(CommunitySettingPendingEntry, pending_id)
            if not entry or entry.status != "pending":
                log.warning(
                    f"在 process_vote 中找不到待审核的条目 #{pending_id} 或其状态不是 'pending'。"
                )
                return

            approvals = 0
            rejections = 0
            for reaction in message.reactions:
                if str(reaction.emoji) == REVIEW_SETTINGS["vote_emoji"]:
                    approvals = reaction.count
                elif str(reaction.emoji) == REVIEW_SETTINGS["reject_emoji"]:
                    rejections = reaction.count

            instant_approval_threshold = REVIEW_SETTINGS["instant_approval_threshold"]
            log.info(
                f"审核ID #{pending_id}: 当前票数 ✅{approvals}, ❌{rejections}。快速通过阈值: {instant_approval_threshold}"
            )

            if approvals >= instant_approval_threshold:
                log.info(f"审核ID #{pending_id} 达到快速通过阈值。准备批准...")
                await self.approve_entry(pending_id, entry, message)
            elif rejections >= REVIEW_SETTINGS["rejection_threshold"]:
                log.info(f"审核ID #{pending_id} 达到否决阈值。")
                await self.reject_entry(pending_id, entry, message, "社区投票否决")
            else:
                log.info(
                    f"审核ID #{pending_id} 票数未达到任何阈值，等待更多投票或过期。"
                )

    async def approve_entry(
        self,
        pending_id: int,
        entry: CommunitySettingPendingEntry,
        message: Optional[discord.Message],
    ):
        """批准条目，将其写入社区设定主表并触发向量化"""
        try:
            data = entry.data_json if isinstance(entry.data_json, dict) else json.loads(entry.data_json)

            title = data.get("title", "无标题")
            content_text = data.get("content_text", "")
            category_name = data.get("category_name", "通用知识")
            full_text = f"标题: {title}\n类别: {category_name}\n内容: {content_text}"
            source_metadata = {
                "category": category_name,
                "source": "community_submission",
                "contributor_id": str(entry.proposer_id),
                "original_submission": data,
            }
            external_id = f"pending_{pending_id}"

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    document = CommunitySettingDocument(
                        external_id=external_id,
                        title=title,
                        full_text=full_text,
                        source_metadata=source_metadata,
                    )
                    session.add(document)
                    await session.flush()
                    new_entry_id = document.id

                    await session.execute(
                        update(CommunitySettingPendingEntry)
                        .where(CommunitySettingPendingEntry.id == pending_id)
                        .values(status="approved")
                    )

            log.info(
                f"已创建社区设定条目 {new_entry_id} (源自审核 #{pending_id})，状态已更新为 'approved'。"
            )

            # 触发增量向量化
            log.info(f"为新社区设定条目 {new_entry_id} 创建向量...")
            task = asyncio.create_task(
                incremental_rag_service.process_setting_entry(new_entry_id)
            )
            self.background_tasks.add(task)
            task.add_done_callback(self._handle_task_result)

            if message:
                original_embed = message.embeds[0]
                new_embed = original_embed.copy()
                new_embed.title = "✅ 新设定Get！"
                new_embed.description = f"大家的意见咱都收到啦！关于 **{title}** 的新设定已经被我记在小本本上啦！"
                new_embed.color = discord.Color.green()
                await message.edit(embed=new_embed)

        except Exception as e:
            log.error(f"批准条目 #{pending_id} 时出错: {e}", exc_info=True)

    def _handle_task_result(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass  # 任务被取消是正常情况
        except Exception as e:
            log.error(f"后台RAG任务执行失败: {e}", exc_info=True)

    async def reject_entry(
        self,
        pending_id: int,
        entry: CommunitySettingPendingEntry,
        message: Optional[discord.Message],
        reason: str,
    ):
        """否决条目并更新状态"""
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CommunitySettingPendingEntry)
                    .where(CommunitySettingPendingEntry.id == pending_id)
                    .values(status="rejected")
                )
                await session.commit()

            if message and message.embeds:
                original_embed = message.embeds[0]
                data_name = (
                    original_embed.fields[0].value
                    if original_embed.fields
                    else "未知设定"
                )
                new_embed = original_embed.copy()
                new_embed.title = "❌ 这份投稿好像不太行..."
                new_embed.description = f"关于 **{data_name}** 的投稿没能通过大家的考验... \n**原因:** {reason}"
                new_embed.color = discord.Color.red()
                await message.edit(embed=new_embed)

            log.info(f"审核ID #{pending_id} 已被否决，原因: {reason}")
        except Exception as e:
            log.error(f"否决条目 #{pending_id} 时出错: {e}", exc_info=True)

    @tasks.loop(minutes=1)
    async def check_expired_entries(self):
        """每分钟检查一次已到期的审核条目"""
        await self.bot.wait_until_ready()
        log.debug("开始检查过期的审核条目...")

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(CommunitySettingPendingEntry).where(
                        CommunitySettingPendingEntry.status == "pending",
                        CommunitySettingPendingEntry.expires_at <= _utcnow(),
                    )
                )
                expired_entries = result.scalars().all()

            if not expired_entries:
                log.debug("没有找到过期的审核条目。")
                return

            log.info(f"找到 {len(expired_entries)} 个过期的审核条目，正在处理...")
            for entry in expired_entries:
                try:
                    # 检查 message_id 是否有效
                    if not entry.message_id or entry.message_id <= 0:
                        log.warning(
                            f"过期条目 #{entry.id} 有一个无效的 message_id ({entry.message_id})。将直接否决。"
                        )
                        await self.reject_entry(
                            entry.id, entry, None, "呜，我好像把投票消息弄丢了..."
                        )
                        continue

                    channel = self.bot.get_channel(entry.channel_id)
                    if not channel:
                        log.warning(
                            f"无法找到频道 {entry.channel_id}，无法处理过期条目 #{entry.id}。这是一个过时的数据，将直接删除。"
                        )
                        async with AsyncSessionLocal() as session:
                            await session.execute(
                                delete(CommunitySettingPendingEntry).where(
                                    CommunitySettingPendingEntry.id == entry.id
                                )
                            )
                            await session.commit()
                        log.info(f"已删除过时的待审核条目 #{entry.id}。")
                        continue

                    if not isinstance(channel, discord.abc.Messageable):
                        log.warning(
                            f"频道 {entry.channel_id} (类型: {type(channel)}) 不是可消息频道，无法处理过期条目 #{entry.id}"
                        )
                        continue

                    message = await channel.fetch_message(entry.message_id)
                    approvals = 0
                    for reaction in message.reactions:
                        if str(reaction.emoji) == VOTE_EMOJI:
                            async for user in reaction.users():
                                if not user.bot:
                                    approvals += 1
                            break

                    log.info(
                        f"过期审核ID #{entry.id}: 最终真实用户票数 ✅{approvals}。通过阈值: {REVIEW_SETTINGS['approval_threshold']}"
                    )

                    if approvals >= REVIEW_SETTINGS["approval_threshold"]:
                        log.info(f"过期审核ID #{entry.id} 满足通过条件。")
                        await self.approve_entry(entry.id, entry, message)
                    else:
                        log.info(f"过期审核ID #{entry.id} 未满足通过条件。")
                        await self.reject_entry(
                            entry.id,
                            entry,
                            message,
                            "时间到了，但是大家好像还没决定好...",
                        )
                except discord.NotFound:
                    log.warning(
                        f"找不到审核消息 {entry.message_id}，将直接否决条目 #{entry.id}"
                    )
                    await self.reject_entry(
                        entry.id, entry, None, "哎呀，投票消息不见了！"
                    )
                except Exception as e:
                    log.error(
                        f"处理过期条目 #{entry.id} 时发生错误: {e}", exc_info=True
                    )
        except Exception as e:
            log.error(f"检查过期条目时发生数据库错误: {e}", exc_info=True)


# --- 单例模式 ---
review_service: Optional["ReviewService"] = None


def initialize_review_service(bot: discord.Client):
    """初始化并设置全局的 ReviewService 实例"""
    global review_service
    if review_service is None:
        review_service = ReviewService(bot)
        log.info("ReviewService 已成功初始化并启动定时任务。")
    return review_service
