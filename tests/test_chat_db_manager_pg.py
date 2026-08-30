# -*- coding: utf-8 -*-
"""
chat_db_manager（PostgreSQL 版）数据库交互测试。

chat_db_manager 是模块级单例，直接导入使用；其方法内部自行通过
AsyncSessionLocal 获取会话并提交，测试用 clean_tables fixture 保证
相关 bot schema 表前后干净。直连真实 PostgreSQL，不 mock 数据库。
"""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from src.chat.utils.database import chat_db_manager
from src.database.database import AsyncSessionLocal
from src.database.models import (
    BlacklistedUser,
    ChannelChatConfig,
    GlobalChatConfig,
    GloballyBlacklistedUser,
    MutedChannel,
    UserChannelCooldown,
    UserChannelTimestamp,
)


@pytest.mark.asyncio
class TestServerBlacklist:
    """服务器级黑名单：增删查与过期清理。"""

    async def test_add_and_check(self, clean_tables):
        """添加后可查到，且不影响其他服务器。"""
        assert await chat_db_manager.is_user_blacklisted(1001, 2001) is False

        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await chat_db_manager.add_to_blacklist(1001, 2001, expires)

        assert await chat_db_manager.is_user_blacklisted(1001, 2001) is True
        assert await chat_db_manager.is_user_blacklisted(1001, 9999) is False

    async def test_remove(self, clean_tables):
        """移除后不再命中。"""
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await chat_db_manager.add_to_blacklist(1002, 2002, expires)
        await chat_db_manager.remove_from_blacklist(1002, 2002)

        assert await chat_db_manager.is_user_blacklisted(1002, 2002) is False

    async def test_expired_entry_cleaned_up(self, clean_tables):
        """过期记录在查询时被判为不在黑名单，且被顺带删除。"""
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        await chat_db_manager.add_to_blacklist(1003, 2003, expired)

        assert await chat_db_manager.is_user_blacklisted(1003, 2003) is False

        async with AsyncSessionLocal() as session:
            remaining = (await session.execute(select(BlacklistedUser))).scalars().all()
        assert remaining == []


@pytest.mark.asyncio
class TestGlobalBlacklist:
    """全局黑名单：增删查与过期清理。"""

    async def test_add_check_remove(self, clean_tables):
        assert await chat_db_manager.is_user_globally_blacklisted(1101) is False

        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await chat_db_manager.add_to_global_blacklist(1101, expires)
        assert await chat_db_manager.is_user_globally_blacklisted(1101) is True

        await chat_db_manager.remove_from_global_blacklist(1101)
        assert await chat_db_manager.is_user_globally_blacklisted(1101) is False

    async def test_expired_entry_cleaned_up(self, clean_tables):
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        await chat_db_manager.add_to_global_blacklist(1102, expired)

        assert await chat_db_manager.is_user_globally_blacklisted(1102) is False

        async with AsyncSessionLocal() as session:
            remaining = (
                (await session.execute(select(GloballyBlacklistedUser))).scalars().all()
            )
        assert remaining == []


@pytest.mark.asyncio
class TestGlobalSetting:
    """全局键值设置：读、写（upsert 覆盖）、删。"""

    async def test_set_get_delete(self, clean_tables):
        assert await chat_db_manager.get_global_setting("test_key") is None

        await chat_db_manager.set_global_setting("test_key", "v1")
        assert await chat_db_manager.get_global_setting("test_key") == "v1"

        # 同 key 再次写入走 upsert 覆盖
        await chat_db_manager.set_global_setting("test_key", "v2")
        assert await chat_db_manager.get_global_setting("test_key") == "v2"

        await chat_db_manager.delete_global_setting("test_key")
        assert await chat_db_manager.get_global_setting("test_key") is None


@pytest.mark.asyncio
class TestChatConfig:
    """服务器全局 / 频道级聊天配置的 upsert。"""

    async def test_global_chat_config_upsert(self, clean_tables):
        """首次创建 + 二次部分更新只改动指定字段。"""
        assert await chat_db_manager.get_global_chat_config(3001) is None

        await chat_db_manager.update_global_chat_config(3001, chat_enabled=False)
        cfg = await chat_db_manager.get_global_chat_config(3001)
        assert cfg == {
            "guild_id": 3001,
            "chat_enabled": False,
            "api_fallback_enabled": True,
        }

        await chat_db_manager.update_global_chat_config(3001, api_fallback_enabled=False)
        cfg = await chat_db_manager.get_global_chat_config(3001)
        assert cfg["chat_enabled"] is False
        assert cfg["api_fallback_enabled"] is False

    async def test_channel_config_upsert(self, clean_tables):
        """频道配置 upsert 不产生重复行，字段随更新变化。"""
        assert await chat_db_manager.get_channel_config(3002, 4001) is None

        await chat_db_manager.update_channel_config(
            3002, 4001, "channel", True, 5, None, None
        )
        cfg = await chat_db_manager.get_channel_config(3002, 4001)
        assert cfg is not None
        assert cfg["entity_type"] == "channel"
        assert cfg["is_chat_enabled"] is True
        assert cfg["cooldown_seconds"] == 5

        await chat_db_manager.update_channel_config(
            3002, 4001, "channel", False, 10, 60, 3
        )
        cfg = await chat_db_manager.get_channel_config(3002, 4001)
        assert cfg["is_chat_enabled"] is False
        assert cfg["cooldown_seconds"] == 10
        assert cfg["cooldown_duration"] == 60
        assert cfg["cooldown_limit"] == 3

        # upsert 后仍只有一行
        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(ChannelChatConfig).where(
                            ChannelChatConfig.guild_id == 3002
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1

    async def test_get_all_channel_configs_for_guild(self, clean_tables):
        """按服务器过滤，其他服务器的配置不混入。"""
        await chat_db_manager.update_channel_config(3003, 4002, "channel", True, None, None, None)
        await chat_db_manager.update_channel_config(3003, 4003, "category", True, None, None, None)
        await chat_db_manager.update_channel_config(9999, 4004, "channel", True, None, None, None)

        configs = await chat_db_manager.get_all_channel_configs_for_guild(3003)
        assert len(configs) == 2
        assert {c["entity_id"] for c in configs} == {4002, 4003}


@pytest.mark.asyncio
class TestUserCooldownAndTimestamps:
    """冷却时间戳：固定时长模式 + 频率限制窗口模式。"""

    async def test_fixed_cooldown_write_and_read(self, clean_tables):
        """update_user_cooldown 写入最后消息时间，重复写入不产生多行。"""
        assert await chat_db_manager.get_user_cooldown(5001, 6001) is None

        await chat_db_manager.update_user_cooldown(5001, 6001)
        snapshot = await chat_db_manager.get_user_cooldown(5001, 6001)
        assert snapshot is not None
        assert "last_message_timestamp" in snapshot

        await chat_db_manager.update_user_cooldown(5001, 6001)
        async with AsyncSessionLocal() as session:
            rows = (
                (await session.execute(select(UserChannelCooldown))).scalars().all()
            )
        assert len(rows) == 1

    async def test_window_query_filters_old_timestamps(self, clean_tables):
        """窗口查询只返回窗口内的时间戳。"""
        # 手工插入一条 2 小时前的旧时间戳
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        async with AsyncSessionLocal() as session:
            session.add(UserChannelTimestamp(user_id=5002, channel_id=6002, timestamp=old_ts))
            await session.commit()

        # 再通过接口写入两条当前时间戳
        await chat_db_manager.add_user_timestamp(5002, 6002)
        await chat_db_manager.add_user_timestamp(5002, 6002)

        in_window = await chat_db_manager.get_user_timestamps_in_window(5002, 6002, 3600)
        assert len(in_window) == 2

        wider_window = await chat_db_manager.get_user_timestamps_in_window(
            5002, 6002, 3 * 3600
        )
        assert len(wider_window) == 3

    async def test_cleanup_old_timestamps(self, clean_tables):
        """cleanup_old_timestamps 只清理过期记录。"""
        old_ts = datetime.now(timezone.utc) - timedelta(hours=30)
        async with AsyncSessionLocal() as session:
            session.add(UserChannelTimestamp(user_id=5003, channel_id=6003, timestamp=old_ts))
            await session.commit()
        await chat_db_manager.add_user_timestamp(5003, 6003)

        deleted = await chat_db_manager.cleanup_old_timestamps(max_age_hours=24)
        assert deleted == 1

        remaining = await chat_db_manager.get_user_timestamps_in_window(
            5003, 6003, 24 * 3600
        )
        assert len(remaining) == 1


@pytest.mark.asyncio
class TestMutedChannel:
    """频道禁言：add / is / remove 及到期自动解除。"""

    async def test_add_check_remove(self, clean_tables):
        assert await chat_db_manager.is_channel_muted(7001) is False

        await chat_db_manager.add_muted_channel(7001, 30)
        assert await chat_db_manager.is_channel_muted(7001) is True

        await chat_db_manager.remove_muted_channel(7001)
        assert await chat_db_manager.is_channel_muted(7001) is False

    async def test_expired_mute_auto_released(self, clean_tables):
        """duration_minutes 传负数构造已过期禁言，检查时应解除并删行。"""
        await chat_db_manager.add_muted_channel(7002, -5)
        assert await chat_db_manager.is_channel_muted(7002) is False

        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(MutedChannel).where(MutedChannel.channel_id == 7002)
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []

    async def test_re_mute_upserts(self, clean_tables):
        """重复禁言同一频道走 upsert，不产生多行。"""
        await chat_db_manager.add_muted_channel(7003, 1)
        await chat_db_manager.add_muted_channel(7003, 60)
        assert await chat_db_manager.is_channel_muted(7003) is True

        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(MutedChannel).where(MutedChannel.channel_id == 7003)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
