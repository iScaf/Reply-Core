# -*- coding: utf-8 -*-
"""
community_settings_service 数据库交互测试。

直连真实 PostgreSQL（经 incremental_rag_service 的 psycopg2 同步连接），
不 mock 数据库。

注意事项：
- add_setting_entry 内部通过 asyncio.create_task 派发向量化后台任务，
  测试环境（无 Ollama / 未配置向量服务）下该任务会失败，属正常现象，
  这里只断言主表（community_settings.documents）写入成功。
- 知识库主数据（documents/chunks）与个人记忆载体（community.member_profiles）
  不在 conftest 的全局 TRUNCATE 清单内，本文件用 autouse fixture
  对测试创建的行做定向清理（测试标题前缀 / 专用 Discord ID 段）。
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from src.chat.config import chat_config
from src.chat.features.community_settings.services.community_settings_service import (
    community_settings_service,
)
from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile, CommunitySettingDocument

# 测试专用 Discord ID（超大值段，避免与真实用户数据冲突）
TEST_DISCORD_ID = 991000000001
TEST_DISCORD_ID_2 = 991000000002
TEST_DISCORD_ID_UNUSED = 991000000099

# 测试条目标题前缀：external_id 由标题清洗生成，用该前缀做定向清理
TEST_TITLE_PREFIX = "TEST_CS_"


def _test_external_id_filter():
    """生成 external_id 以 "TEST_" 开头的过滤条件（转义下划线避免 LIKE 通配歧义）。"""
    return CommunitySettingDocument.external_id.like("TEST\\_%", escape="\\")


async def _reap_background_tasks():
    """回收 add_setting_entry 派发的向量化任务并吞掉其异常。

    测试环境（无 Ollama / 未配置向量服务）下任务失败属正常，只避免
    "Task exception was never retrieved" 告警；仅针对目标协程，防止
    误伤 session 级事件循环里的其他任务。
    """
    pending = [
        t
        for t in asyncio.all_tasks() - {asyncio.current_task()}
        if not t.done() and "process_setting_entry" in repr(t)
    ]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _cleanup_test_rows():
    """定向清理本文件测试写入的行，不触碰知识库/档案库的其他数据。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(CommunitySettingDocument).where(_test_external_id_filter())
        )
        await session.execute(
            delete(CommunityMemberProfile).where(
                CommunityMemberProfile.discord_id.in_(
                    [str(TEST_DISCORD_ID), str(TEST_DISCORD_ID_2)]
                )
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def clean_test_rows():
    """每个用例前后定向清理测试数据（替代全局 TRUNCATE）。"""
    await _cleanup_test_rows()
    yield
    await _cleanup_test_rows()


@pytest.mark.asyncio
class TestGetOrCreateProfile:
    """个人记忆载体：community.member_profiles 的自动建档。"""

    async def test_first_call_creates_profile(self):
        """首次调用自动创建最小档案并落库。"""
        profile = await community_settings_service.get_or_create_profile(
            TEST_DISCORD_ID, "测试用户甲"
        )
        assert profile is not None
        assert profile["discord_id"] == str(TEST_DISCORD_ID)
        assert profile["title"] == "测试用户甲"

        # 库中确实只有这一条档案
        async with AsyncSessionLocal() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(CommunityMemberProfile)
                    .where(CommunityMemberProfile.discord_id == str(TEST_DISCORD_ID))
                )
            ).scalar_one()
        assert count == 1

    async def test_second_call_reuses_existing_profile(self):
        """二次调用复用已有档案，不新建也不覆盖。"""
        first = await community_settings_service.get_or_create_profile(
            TEST_DISCORD_ID_2, "测试用户乙"
        )
        assert first is not None

        # 传入不同昵称，应仍返回首次创建的档案
        second = await community_settings_service.get_or_create_profile(
            TEST_DISCORD_ID_2, "改过名的用户乙"
        )
        assert second is not None
        assert second["title"] == "测试用户乙"
        assert second["source_metadata"]["name"] == "测试用户乙"
        assert second["source_metadata"]["source"] == "auto_created"

        async with AsyncSessionLocal() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(CommunityMemberProfile)
                    .where(CommunityMemberProfile.discord_id == str(TEST_DISCORD_ID_2))
                )
            ).scalar_one()
        assert count == 1

    async def test_get_profile_by_discord_id_missing(self):
        """未建档用户通过查询接口应返回 None（不自动创建）。"""
        profile = await community_settings_service.get_profile_by_discord_id(
            TEST_DISCORD_ID_UNUSED
        )
        assert profile is None


@pytest.mark.asyncio
class TestAddSettingEntry:
    """社区设定条目写入 community_settings.documents 主表。"""

    async def test_add_entry_writes_documents_table(self):
        """正常写入：主表出现对应行，元数据完整。"""
        title = f"{TEST_TITLE_PREFIX}基础契约"
        ok = community_settings_service.add_setting_entry(
            title=title,
            name="基础契约",
            content_text="这是测试条目的正文内容。",
            category_name="通用知识",
            contributor_id=TEST_DISCORD_ID,
        )
        assert ok is True

        async with AsyncSessionLocal() as session:
            row = (
                (
                    await session.execute(
                        select(CommunitySettingDocument).where(_test_external_id_filter())
                    )
                )
                .scalars()
                .first()
            )
        assert row is not None
        assert row.title == title
        # full_text 存的是 content 的 JSON 序列化结果
        assert "这是测试条目的正文内容。" in row.full_text
        assert row.source_metadata["category"] == "通用知识"
        assert row.source_metadata["contributor_id"] == TEST_DISCORD_ID
        assert row.source_metadata["status"] == "approved"

        await _reap_background_tasks()

    @pytest.mark.parametrize("vector_mode", ["none", "api", "local"])
    async def test_vector_mode_does_not_block_direct_write(self, monkeypatch, vector_mode):
        """VECTOR_MODE 取值不影响直写主表（写入不经过向量链路）。"""
        monkeypatch.setattr(chat_config, "VECTOR_MODE", vector_mode)

        title = f"{TEST_TITLE_PREFIX}模式{vector_mode}"
        ok = community_settings_service.add_setting_entry(
            title=title,
            name=f"模式{vector_mode}",
            content_text=f"VECTOR_MODE={vector_mode} 时仍应直写 documents 主表。",
            category_name="通用知识",
        )
        assert ok is True

        async with AsyncSessionLocal() as session:
            row = (
                (
                    await session.execute(
                        select(CommunitySettingDocument).where(
                            CommunitySettingDocument.title == title
                        )
                    )
                )
                .scalars()
                .first()
            )
        assert row is not None
        assert f"VECTOR_MODE={vector_mode}" in row.full_text

        await _reap_background_tasks()
