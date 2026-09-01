# -*- coding: utf-8 -*-
"""
Web 后台测试。

TestClient（同步，自带事件循环）与 pytest-asyncio 的 session loop 不能共用
全局 asyncpg 连接池（跨 loop 复用会报 attached to a different loop），
因此本目录的 DB 准备/清理一律走 asyncio.run + NullPool 临时引擎，
不使用根 conftest 的 async fixtures。
"""
import asyncio
import os

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

load_dotenv()

from tests.conftest import _TEST_DATABASE_URL, _ALL_TABLES

TEST_TOKEN = "test-web-admin-token"


def run_db(coro_factory):
    """在独立事件循环 + NullPool 引擎中执行一段异步 DB 操作，用后即毁"""

    async def runner():
        engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def truncate_tables(tables) -> None:
    def _do(factory):
        async def inner(session):
            table_refs = ", ".join(
                f'"{schema}"."{name}"' for t in tables for schema, name in [t.split(".", 1)]
            )
            await session.execute(text(f"TRUNCATE TABLE {table_refs} CASCADE"))

        async def flow():
            async with factory() as session:
                async with session.begin():
                    await inner(session)

        return flow()

    run_db(_do)


def _set_token_env() -> None:
    os.environ["WEB_ADMIN_TOKEN"] = TEST_TOKEN


def _use_pool_free_engine() -> None:
    """
    把全局引擎替换为 NullPool：TestClient 的 anyio portal 每个实例有独立
    事件循环，池化 asyncpg 连接跨 loop 复用会报 attached to a different loop。
    必须在 src.web.* services 首次导入（绑定 AsyncSessionLocal 名字）之前执行。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.database import database as db_mod

    if getattr(db_mod, "_web_test_engine", False):
        return
    db_mod.engine = create_async_engine(db_mod.DATABASE_URL, poolclass=NullPool)
    db_mod.AsyncSessionLocal = async_sessionmaker(
        autocommit=False, autoflush=False, bind=db_mod.engine
    )
    db_mod._web_test_engine = True


@pytest.fixture(scope="session")
def web_client():
    """整个测试会话共用一个 TestClient（登录一次、单事件循环）"""
    _set_token_env()
    _use_pool_free_engine()
    from src.web.app import create_app

    client = TestClient(create_app())
    with client:
        resp = client.post("/api/login", json={"token": TEST_TOKEN})
        assert resp.status_code == 200
        yield client


@pytest.fixture(scope="session")
def anon_client():
    """未登录客户端"""
    _set_token_env()
    _use_pool_free_engine()
    from src.web.app import create_app

    client = TestClient(create_app())
    with client:
        yield client


@pytest.fixture
def clean_db():
    """每个用例前后清空全部可清空表（等价于根 conftest 的 clean_tables）"""
    truncate_tables(_ALL_TABLES)
    yield
    truncate_tables(_ALL_TABLES)


@pytest.fixture
def clean_community_knowledge():
    """快照精确清理：只删除测试期间新创建的社区设定文档及其 chunks。

    community_settings.documents 是知识库主数据，测试严禁整表清空——
    fixture 开始前记录已有文档 id，结束后仅删除新增部分（CASCADE 清 chunks）。
    """
    def _snapshot(factory):
        async def inner(session):
            rows = await session.execute(
                text("SELECT id FROM community_settings.documents")
            )
            return {r[0] for r in rows.fetchall()}

        async def flow():
            async with factory() as session:
                return await inner(session)

        return flow()

    before = run_db(_snapshot)
    yield
    after = run_db(_snapshot)
    created = after - before
    if not created:
        return
    id_list = ", ".join(str(int(i)) for i in created)

    def _do(factory):
        async def inner(session):
            await session.execute(
                text(
                    f"DELETE FROM community_settings.chunks "
                    f"WHERE document_id IN ({id_list})"
                )
            )
            await session.execute(
                text(
                    f"DELETE FROM community_settings.documents "
                    f"WHERE id IN ({id_list})"
                )
            )

        async def flow():
            async with factory() as session:
                async with session.begin():
                    await inner(session)

        return flow()

    run_db(_do)
