# -*- coding: utf-8 -*-
"""社区设定提交服务：处理用户提交的设定条目，创建待审核条目并分发给审核服务。"""
import logging
from typing import Dict, Any, Optional
from datetime import timedelta
import discord
import asyncio

from src.chat.config import chat_config
from src.chat.services.review_service import review_service
from src.database.database import AsyncSessionLocal
from src.database.models import CommunitySettingPendingEntry
from src.chat.services.review_service import _utcnow

log = logging.getLogger(__name__)


class SubmissionService:
    """处理社区设定内容提交的服务"""

    async def _create_pending_entry(
        self,
        interaction: discord.Interaction,
        entry_data: Dict[str, Any],
    ) -> Optional[int]:
        """
        将提交的数据作为待审核条目存入 PostgreSQL。
        """
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id
        if guild_id is None:
            log.warning(
                f"[_create_pending_entry] guild_id 为 None! "
                f"user={interaction.user.id}, "
                f"channel_id={channel_id}, channel_type={interaction.channel.type if interaction.channel else 'N/A'}, "
                f"guild={interaction.guild}"
            )

        try:
            review_settings = chat_config.COMMUNITY_SETTINGS_CONFIG.get(
                "review_settings", {}
            )
            duration_minutes = review_settings.get("review_duration_minutes", 5)
            expires_at = _utcnow() + timedelta(minutes=duration_minutes)

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    pending = CommunitySettingPendingEntry(
                        entry_type="community_setting",
                        data_json=entry_data,
                        message_id=-1,  # 临时 message_id，将在审核服务中更新
                        channel_id=interaction.channel_id or 0,
                        guild_id=interaction.guild_id or 0,
                        proposer_id=interaction.user.id,
                        status="pending",
                        expires_at=expires_at,
                    )
                    session.add(pending)
                    await session.flush()
                    pending_id = pending.id

            log.info(
                f"已创建待审核条目 #{pending_id} (类型: community_setting)，提交者: {interaction.user.id}"
            )
            return pending_id

        except Exception as e:
            log.error(f"创建待审核条目时发生数据库错误: {e}", exc_info=True)
            return None

    async def submit_community_setting(
        self,
        interaction: discord.Interaction,
        setting_data: Dict[str, Any],
        is_admin_create: bool = False,
    ) -> Optional[int]:
        """
        提交一条新的社区设定以供审核。

        Args:
            interaction: The discord interaction from the user.
            setting_data: A dict containing the setting details ('category_name', 'title', 'content_text', etc.).
            is_admin_create: 管理员直接创建时跳过公开审核。

        Returns:
            The ID of the pending entry if successful, otherwise None.
        """
        pending_id = await self._create_pending_entry(interaction, setting_data)

        if pending_id:
            if is_admin_create:
                # 管理员直接创建，跳过公开审核，直接批准
                async with AsyncSessionLocal() as session:
                    entry = await session.get(CommunitySettingPendingEntry, pending_id)
                    if entry:
                        assert review_service is not None
                        await review_service.approve_entry(
                            pending_id=pending_id,
                            entry=entry,
                            message=None,  # 管理员创建不需要消息
                        )
                        log.info(
                            f"社区设定管理员直接创建成功，待审核ID: {pending_id}。已直接批准，跳过公开审核。"
                        )
            else:
                # 调用 ReviewService 来启动审核流程
                assert review_service is not None
                asyncio.create_task(review_service.start_review(pending_id))
                log.info(f"社区设定提交成功，待审核ID: {pending_id}。已启动审核流程。")

        return pending_id


# 创建 SubmissionService 的单例
submission_service = SubmissionService()
