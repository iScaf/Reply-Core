import os
import sys

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_TEST_DATABASE_URL = os.getenv("DATABASE_URL")

if not _TEST_DATABASE_URL:
    _db_user = os.getenv("POSTGRES_USER", "user")
    _db_password = os.getenv("POSTGRES_PASSWORD", "password")
    _db_name = os.getenv("POSTGRES_DB", "bot_db")
    _db_port = os.getenv("DB_PORT", "5432")
    _db_host = os.getenv("DB_HOST", "db") if os.getenv("RUNNING_IN_DOCKER") else os.getenv("DB_HOST", "localhost")
    _TEST_DATABASE_URL = (
        f"postgresql+asyncpg://{_db_user}:{_db_password}"
        f"@{_db_host}:{_db_port}/{_db_name}"
    )

_test_engine = create_async_engine(_TEST_DATABASE_URL, echo=False, poolclass=NullPool)
_TestSessionFactory = async_sessionmaker(_test_engine, expire_on_commit=False)

# bot schema：原遗留 SQLite chat.db 迁移过来的 Bot 运行时表
BOT_TABLES = [
    "bot.global_settings",
    "bot.blacklisted_users",
    "bot.globally_blacklisted_users",
    "bot.global_chat_config",
    "bot.channel_chat_config",
    "bot.user_channel_cooldown",
    "bot.user_channel_timestamps",
    "bot.muted_channels",
    "bot.ai_prompts",
    "bot.channel_memory_anchors",
    "bot.ai_model_usage",
    "bot.daily_model_usage",
    "bot.daily_stats",
]

# user schema：警告记录与用户人设偏好
USER_TABLES = [
    "user.user_warnings",
    "user.user_persona_preference",
]

# community_settings schema：待审核条目队列。
# 注意：documents / chunks 是知识库主数据，community.member_profiles 是个人记忆
# 载体，均不在全局清空之列；相关测试需在用例内做定向清理（见
# test_community_settings_service_pg.py）。
COMMUNITY_SETTINGS_TABLES = [
    "community_settings.pending_entries",
]

# forum schema：论坛索引处理状态
FORUM_TABLES = [
    "forum.processed_threads",
    "forum.backfill_status",
]

_ALL_TABLES = BOT_TABLES + USER_TABLES + COMMUNITY_SETTINGS_TABLES + FORUM_TABLES


async def _truncate_all():
    table_refs = ", ".join(
        f'"{schema}"."{name}"'
        for t in _ALL_TABLES
        for schema, name in [t.split(".", 1)]
    )
    async with _TestSessionFactory() as session:
        await session.execute(text(f"TRUNCATE TABLE {table_refs} CASCADE"))
        await session.commit()


@pytest_asyncio.fixture
async def db_session():
    await _truncate_all()
    async with _TestSessionFactory() as session:
        async with session.begin():
            yield session
    await _truncate_all()


@pytest_asyncio.fixture
async def clean_tables():
    await _truncate_all()
    yield
    await _truncate_all()
