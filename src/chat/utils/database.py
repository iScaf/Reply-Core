# -*- coding: utf-8 -*-
"""Bot 运行时数据管理器（PostgreSQL 版）。

原遗留 SQLite chat.db 已全部迁移到 PostgreSQL 的 `bot` schema，
本模块保持原有的 ChatDatabaseManager 接口不变，内部改用 SQLAlchemy 异步实现。
表结构见 src/database/models.py（GlobalSetting / BlacklistedUser / ... 等，
由 Alembic 迁移负责建表）。
"""
import logging
import os
from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta

from sqlalchemy import delete, select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.database import AsyncSessionLocal
from src.database.models import (
    GlobalSetting,
    BlacklistedUser,
    GloballyBlacklistedUser,
    GlobalChatConfig,
    ChannelChatConfig,
    UserChannelCooldown,
    UserChannelTimestamp,
    MutedChannel,
    AiPrompt,
    ChannelMemoryAnchor,
    ModelUsage,
    DailyModelUsage,
    DailyStat,
)

log = logging.getLogger(__name__)


def get_beijing_today_str() -> str:
    """获取北京时间今天的日期字符串（YYYY-MM-DD）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")


def get_database_url(sync: bool = False) -> str:
    """根据环境变量组装 PostgreSQL 连接 URL。

    Args:
        sync: True 返回 psycopg2 同步驱动 URL，False 返回 asyncpg 异步 URL。
    """
    if os.getenv("RUNNING_IN_DOCKER"):
        default_host = "db"
    else:
        default_host = "localhost"

    db_user = os.getenv("POSTGRES_USER", "user")
    db_password = os.getenv("POSTGRES_PASSWORD", "password")
    db_host = os.getenv("DB_HOST", default_host)
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "bot_db")

    driver = "postgresql+psycopg2" if sync else "postgresql+asyncpg"
    return f"{driver}://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def _aware(dt: datetime) -> datetime:
    """确保 datetime 带时区（naive 视为 UTC）。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ChatDatabaseManager:
    """Bot 运行时数据的异步 PG 管理器（黑名单/聊天配置/冷却/统计等）"""

    def __init__(self):
        pass

    async def init_async(self):
        """兼容入口：表结构由 Alembic 迁移管理，此处仅做日志。"""
        log.info("ChatDatabaseManager（PostgreSQL 版）初始化完成。")

    async def disconnect(self):
        """兼容入口：连接池由 database 模块统一管理。"""
        pass

    # --- 频道记忆锚点 ---

    async def get_channel_memory_anchor(
        self, guild_id: int, channel_id: int
    ) -> Optional[int]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChannelMemoryAnchor.anchor_message_id).where(
                    ChannelMemoryAnchor.guild_id == guild_id,
                    ChannelMemoryAnchor.channel_id == channel_id,
                )
            )
            return result.scalar_one_or_none()

    async def set_channel_memory_anchor(
        self, guild_id: int, channel_id: int, anchor_message_id: int
    ) -> None:
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(ChannelMemoryAnchor).values(
                guild_id=guild_id,
                channel_id=channel_id,
                anchor_message_id=anchor_message_id,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "channel_id"],
                set_={"anchor_message_id": stmt.excluded.anchor_message_id},
            )
            await session.execute(stmt)
            await session.commit()
        log.info(
            f"已为服务器 {guild_id} 的频道 {channel_id} 设置记忆锚点: {anchor_message_id}"
        )

    async def delete_channel_memory_anchor(self, guild_id: int, channel_id: int) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ChannelMemoryAnchor).where(
                    ChannelMemoryAnchor.guild_id == guild_id,
                    ChannelMemoryAnchor.channel_id == channel_id,
                )
            )
            await session.commit()
            deleted = result.rowcount or 0
        if deleted > 0:
            log.info(f"已删除服务器 {guild_id} 频道 {channel_id} 的记忆锚点。")
        return deleted

    # --- AI提示词管理 ---

    async def get_ai_prompt(self, guild_id: int, prompt_name: str) -> Optional[str]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AiPrompt.prompt_content).where(
                    AiPrompt.guild_id == guild_id,
                    AiPrompt.prompt_name == prompt_name,
                    AiPrompt.is_active == 1,
                )
            )
            return result.scalar_one_or_none()

    async def set_ai_prompt(
        self, guild_id: int, prompt_name: str, prompt_content: str
    ) -> None:
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(AiPrompt).values(
                guild_id=guild_id,
                prompt_name=prompt_name,
                prompt_content=prompt_content,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "prompt_name"],
                set_={
                    "prompt_content": stmt.excluded.prompt_content,
                    "is_active": 1,
                },
            )
            await session.execute(stmt)
            await session.commit()
        log.info(f"已为服务器 {guild_id} 设置AI提示词: {prompt_name}")

    async def get_all_ai_prompts(self, guild_id: int) -> Dict[str, str]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AiPrompt.prompt_name, AiPrompt.prompt_content).where(
                    AiPrompt.guild_id == guild_id, AiPrompt.is_active == 1
                )
            )
            return {name: content for name, content in result.all()}

    # --- 服务器黑名单 ---

    async def add_to_blacklist(
        self, user_id: int, guild_id: int, expires_at: datetime
    ) -> None:
        expires_at = _aware(expires_at)
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(BlacklistedUser).values(
                user_id=user_id, guild_id=guild_id, expires_at=expires_at
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "guild_id"],
                set_={"expires_at": stmt.excluded.expires_at},
            )
            await session.execute(stmt)
            await session.commit()
        log.info(
            f"已将用户 {user_id} 添加到服务器 {guild_id} 的黑名单，到期时间: {expires_at}"
        )

    async def remove_from_blacklist(self, user_id: int, guild_id: int) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(BlacklistedUser).where(
                    BlacklistedUser.user_id == user_id,
                    BlacklistedUser.guild_id == guild_id,
                )
            )
            await session.commit()
        log.info(f"已将用户 {user_id} 从服务器 {guild_id} 的黑名单中移除")

    async def is_user_blacklisted(self, user_id: int, guild_id: int) -> bool:
        async with AsyncSessionLocal() as session:
            # 顺带清理过期记录
            await session.execute(
                delete(BlacklistedUser).where(
                    BlacklistedUser.expires_at < func.now()
                )
            )
            result = await session.execute(
                select(BlacklistedUser.expires_at).where(
                    BlacklistedUser.user_id == user_id,
                    BlacklistedUser.guild_id == guild_id,
                )
            )
            expires_at = result.scalar_one_or_none()
            await session.commit()

        if expires_at is not None:
            return _aware(expires_at) > datetime.now(timezone.utc)
        return False

    # --- 全局黑名单管理 ---

    async def add_to_global_blacklist(self, user_id: int, expires_at: datetime) -> None:
        """将用户添加到全局黑名单。"""
        expires_at = _aware(expires_at)
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(GloballyBlacklistedUser).values(
                user_id=user_id, expires_at=expires_at
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={"expires_at": stmt.excluded.expires_at},
            )
            await session.execute(stmt)
            await session.commit()
        log.info(f"已将用户 {user_id} 添加到全局黑名单，到期时间: {expires_at}")

    async def remove_from_global_blacklist(self, user_id: int) -> None:
        """将用户从全局黑名单中移除。"""
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(GloballyBlacklistedUser).where(
                    GloballyBlacklistedUser.user_id == user_id
                )
            )
            await session.commit()
        log.info(f"已将用户 {user_id} 从全局黑名单中移除")

    async def is_user_globally_blacklisted(self, user_id: int) -> bool:
        """检查用户是否在全局黑名单中。"""
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(GloballyBlacklistedUser).where(
                    GloballyBlacklistedUser.expires_at < func.now()
                )
            )
            result = await session.execute(
                select(GloballyBlacklistedUser.expires_at).where(
                    GloballyBlacklistedUser.user_id == user_id
                )
            )
            expires_at = result.scalar_one_or_none()
            await session.commit()

        if expires_at is not None:
            return _aware(expires_at) > datetime.now(timezone.utc)
        return False

    # --- 全局键值设置 ---

    async def get_global_setting(self, key: str) -> Optional[str]:
        """获取一个全局设置的值。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GlobalSetting.value).where(GlobalSetting.key == key)
            )
            return result.scalar_one_or_none()

    async def set_global_setting(self, key: str, value: str) -> None:
        """设置一个全局设置的值。"""
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(GlobalSetting).values(key=key, value=value)
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"], set_={"value": stmt.excluded.value}
            )
            await session.execute(stmt)
            await session.commit()
        log.info(f"已更新全局设置: {key} = {value}")

    async def delete_global_setting(self, key: str) -> None:
        """删除一个全局设置。"""
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(GlobalSetting).where(GlobalSetting.key == key)
            )
            await session.commit()

    # --- 聊天设置管理 ---

    async def get_global_chat_config(self, guild_id: int) -> Optional[Dict]:
        """获取服务器的全局聊天配置。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GlobalChatConfig).where(GlobalChatConfig.guild_id == guild_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "guild_id": row.guild_id,
                "chat_enabled": bool(row.chat_enabled),
                "api_fallback_enabled": bool(row.api_fallback_enabled),
            }

    async def update_global_chat_config(
        self,
        guild_id: int,
        chat_enabled: Optional[bool] = None,
        api_fallback_enabled: Optional[bool] = None,
    ) -> None:
        """更新或创建服务器的全局聊天配置。"""
        updates = {}
        if chat_enabled is not None:
            updates["chat_enabled"] = int(chat_enabled)
        if api_fallback_enabled is not None:
            updates["api_fallback_enabled"] = int(api_fallback_enabled)

        if not updates:
            return

        async with AsyncSessionLocal() as session:
            stmt = pg_insert(GlobalChatConfig).values(
                guild_id=guild_id, **updates
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id"], set_=updates
            )
            await session.execute(stmt)
            await session.commit()
        log.info(f"已更新服务器 {guild_id} 的全局聊天配置: {updates}")

    async def get_channel_config(self, guild_id: int, entity_id: int) -> Optional[Dict]:
        """获取特定频道或分类的聊天配置。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChannelChatConfig).where(
                    ChannelChatConfig.guild_id == guild_id,
                    ChannelChatConfig.entity_id == entity_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "guild_id": row.guild_id,
                "entity_id": row.entity_id,
                "entity_type": row.entity_type,
                "is_chat_enabled": (
                    None if row.is_chat_enabled is None else bool(row.is_chat_enabled)
                ),
                "cooldown_seconds": row.cooldown_seconds,
                "cooldown_duration": row.cooldown_duration,
                "cooldown_limit": row.cooldown_limit,
            }

    async def get_all_channel_configs_for_guild(self, guild_id: int) -> List[Dict]:
        """获取服务器内所有特定频道/分类的配置。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChannelChatConfig).where(
                    ChannelChatConfig.guild_id == guild_id
                )
            )
            rows = result.scalars().all()
            return [
                {
                    "guild_id": r.guild_id,
                    "entity_id": r.entity_id,
                    "entity_type": r.entity_type,
                    "is_chat_enabled": (
                        None if r.is_chat_enabled is None else bool(r.is_chat_enabled)
                    ),
                    "cooldown_seconds": r.cooldown_seconds,
                    "cooldown_duration": r.cooldown_duration,
                    "cooldown_limit": r.cooldown_limit,
                }
                for r in rows
            ]

    async def update_channel_config(
        self,
        guild_id: int,
        entity_id: int,
        entity_type: str,
        is_chat_enabled: Optional[bool],
        cooldown_seconds: Optional[int],
        cooldown_duration: Optional[int],
        cooldown_limit: Optional[int],
    ) -> None:
        """更新或创建频道/分类的聊天配置，支持两种CD模式。"""
        values = {
            "entity_type": entity_type,
            "is_chat_enabled": (
                None if is_chat_enabled is None else int(is_chat_enabled)
            ),
            "cooldown_seconds": cooldown_seconds,
            "cooldown_duration": cooldown_duration,
            "cooldown_limit": cooldown_limit,
        }
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(ChannelChatConfig).values(
                guild_id=guild_id, entity_id=entity_id, **values
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "entity_id"], set_=values
            )
            await session.execute(stmt)
            await session.commit()
        log.info(
            f"已更新服务器 {guild_id} 的实体 {entity_id} ({entity_type}) 的聊天配置。"
        )

    # --- 冷却（固定时长模式） ---

    async def get_user_cooldown(self, user_id: int, channel_id: int) -> Optional[Dict]:
        """获取用户的最后消息时间戳（兼容旧接口，返回 ISO 字符串）。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserChannelCooldown.last_message_timestamp).where(
                    UserChannelCooldown.user_id == user_id,
                    UserChannelCooldown.channel_id == channel_id,
                )
            )
            ts = result.scalar_one_or_none()
        if ts is None:
            return None
        return {"last_message_timestamp": _aware(ts).isoformat()}

    async def update_user_cooldown(self, user_id: int, channel_id: int) -> None:
        """更新用户的最后消息时间戳。"""
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(UserChannelCooldown).values(
                user_id=user_id,
                channel_id=channel_id,
                last_message_timestamp=func.now(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "channel_id"],
                set_={"last_message_timestamp": func.now()},
            )
            await session.execute(stmt)
            await session.commit()

    # --- 冷却（频率限制模式） ---

    async def add_user_timestamp(self, user_id: int, channel_id: int) -> None:
        """为频率限制系统记录一条新的消息时间戳。"""
        async with AsyncSessionLocal() as session:
            session.add(
                UserChannelTimestamp(user_id=user_id, channel_id=channel_id)
            )
            await session.commit()

    async def get_user_timestamps_in_window(
        self, user_id: int, channel_id: int, window_seconds: int
    ) -> List[Dict]:
        """获取用户在指定时间窗口内的所有消息时间戳。"""
        window_start = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserChannelTimestamp.timestamp).where(
                    UserChannelTimestamp.user_id == user_id,
                    UserChannelTimestamp.channel_id == channel_id,
                    UserChannelTimestamp.timestamp >= window_start,
                )
            )
            return [{"timestamp": ts} for (ts,) in result.all()]

    async def cleanup_old_timestamps(self, max_age_hours: int = 24) -> int:
        """清理过期的频率限制时间戳记录，防止表无限增长。"""
        threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(UserChannelTimestamp).where(
                    UserChannelTimestamp.timestamp < threshold
                )
            )
            await session.commit()
            return result.rowcount or 0

    # --- 频道禁言 ---

    async def add_muted_channel(self, channel_id: int, duration_minutes: int):
        """将一个频道添加到禁言列表，并设置禁言持续时间。"""
        muted_until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(MutedChannel).values(
                channel_id=channel_id, muted_until=muted_until
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["channel_id"],
                set_={"muted_until": stmt.excluded.muted_until},
            )
            await session.execute(stmt)
            await session.commit()
        log.info(
            f"已将频道 {channel_id} 添加到禁言列表，解禁时间: {muted_until.isoformat()}"
        )

    async def remove_muted_channel(self, channel_id: int) -> None:
        """将一个频道从禁言列表中移除。"""
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(MutedChannel).where(MutedChannel.channel_id == channel_id)
            )
            await session.commit()
        log.info(f"已将频道 {channel_id} 从禁言列表中移除")

    async def is_channel_muted(self, channel_id: int) -> bool:
        """检查一个频道当前是否被禁言（过期自动解除）。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MutedChannel.muted_until).where(
                    MutedChannel.channel_id == channel_id
                )
            )
            muted_until = result.scalar_one_or_none()

            if muted_until is None:
                return False

            if datetime.now(timezone.utc) > _aware(muted_until):
                await session.execute(
                    delete(MutedChannel).where(MutedChannel.channel_id == channel_id)
                )
                await session.commit()
                log.info(f"频道 {channel_id} 的禁言已到期，已自动解除。")
                return False
            return True

    # --- AI模型使用计数 ---

    async def increment_model_usage(
        self, model_name: str, provider_name: str = "unknown"
    ) -> None:
        """为一个模型增加累计和每日使用次数。"""
        today_date_str = get_beijing_today_str()
        async with AsyncSessionLocal() as session:
            stmt_total = pg_insert(ModelUsage).values(
                model_name=model_name, usage_count=1, provider_name=provider_name
            )
            stmt_total = stmt_total.on_conflict_do_update(
                index_elements=["model_name"],
                set_={
                    "usage_count": ModelUsage.usage_count + 1,
                    "provider_name": func.coalesce(
                        stmt_total.excluded.provider_name, ModelUsage.provider_name
                    ),
                },
            )
            await session.execute(stmt_total)

            stmt_daily = pg_insert(DailyModelUsage).values(
                model_name=model_name,
                usage_date=today_date_str,
                usage_count=1,
                provider_name=provider_name,
            )
            stmt_daily = stmt_daily.on_conflict_do_update(
                index_elements=["model_name", "usage_date"],
                set_={
                    "usage_count": DailyModelUsage.usage_count + 1,
                    "provider_name": func.coalesce(
                        stmt_daily.excluded.provider_name, DailyModelUsage.provider_name
                    ),
                },
            )
            await session.execute(stmt_daily)
            await session.commit()

    async def get_model_usage_counts(self) -> List[Dict]:
        """获取所有模型累计的使用次数（包含 provider_name）。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    ModelUsage.model_name,
                    ModelUsage.usage_count,
                    ModelUsage.provider_name,
                )
            )
            return [dict(row._mapping) for row in result.all()]

    async def get_model_usage_counts_today(self) -> List[Dict]:
        """获取今天所有模型的使用次数（包含 provider_name）。"""
        today_date_str = get_beijing_today_str()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    DailyModelUsage.model_name,
                    DailyModelUsage.usage_count,
                    DailyModelUsage.provider_name,
                ).where(DailyModelUsage.usage_date == today_date_str)
            )
            return [dict(row._mapping) for row in result.all()]

    async def get_provider_usage_stats(self) -> dict:
        """
        获取按 Provider 分组的使用统计。

        Returns:
            {"gemini_official": {"total": 100, "today": 10}, ...}
        """
        today_date_str = get_beijing_today_str()
        async with AsyncSessionLocal() as session:
            total_result = await session.execute(
                select(
                    ModelUsage.provider_name,
                    func.sum(ModelUsage.usage_count).label("total_count"),
                )
                .where(ModelUsage.provider_name.isnot(None))
                .group_by(ModelUsage.provider_name)
            )
            today_result = await session.execute(
                select(
                    DailyModelUsage.provider_name,
                    func.sum(DailyModelUsage.usage_count).label("today_count"),
                )
                .where(
                    DailyModelUsage.usage_date == today_date_str,
                    DailyModelUsage.provider_name.isnot(None),
                )
                .group_by(DailyModelUsage.provider_name)
            )

            result: Dict[str, Dict[str, int]] = {}
            for row in total_result.all():
                result[row.provider_name] = {"total": row.total_count, "today": 0}
            for row in today_result.all():
                if row.provider_name in result:
                    result[row.provider_name]["today"] = row.today_count
                else:
                    result[row.provider_name] = {"total": 0, "today": row.today_count}
            return result

    # --- 每日功能统计 ---

    async def _increment_daily_stat(self, column: str) -> None:
        """通用：为 daily_stats 的某一列 +1（北京时间日期）。"""
        today = get_beijing_today_str()
        col = DailyStat.__table__.c[column]
        async with AsyncSessionLocal() as session:
            stmt = pg_insert(DailyStat).values(stat_date=today, **{column: 1})
            stmt = stmt.on_conflict_do_update(
                index_elements=["stat_date"],
                set_={column: col + 1},
            )
            await session.execute(stmt)
            await session.commit()

    async def _get_daily_stat(self, column: str) -> int:
        """通用：读取 daily_stats 某列的今日计数。"""
        today = get_beijing_today_str()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DailyStat.__table__.c[column]).where(
                    DailyStat.stat_date == today
                )
            )
            value = result.scalar_one_or_none()
            return value or 0

    async def increment_tarot_reading_count(self) -> None:
        """增加今天的塔罗牌占卜次数。"""
        await self._increment_daily_stat("tarot_reading_count")

    async def get_tarot_reading_count_today(self) -> int:
        """获取今天的塔罗牌占卜次数。"""
        return await self._get_daily_stat("tarot_reading_count")

    async def increment_forum_search_count(self) -> None:
        """增加今天的论坛搜索次数。"""
        await self._increment_daily_stat("forum_search_count")

    async def get_forum_search_count_today(self) -> int:
        """获取今天的论坛搜索次数。"""
        return await self._get_daily_stat("forum_search_count")

    async def increment_issue_user_warning_count(self) -> None:
        """增加今天的 'issue_user_warning' 工具使用次数。"""
        await self._increment_daily_stat("issue_user_warning_count")

    async def get_issue_user_warning_count_today(self) -> int:
        """获取今天的 'issue_user_warning' 工具使用次数。"""
        return await self._get_daily_stat("issue_user_warning_count")


# 全局单例
chat_db_manager = ChatDatabaseManager()
