# -*- coding: utf-8 -*-
"""审核队列 API 测试（真实 PG）"""
import threading

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.database.models import CommunitySettingPendingEntry

from tests.web.conftest import run_db


def _patch_rag(monkeypatch):
    """
    拦截真实向量化任务，记录调用参数。
    approve 里是 asyncio.create_task 后台执行，请求返回时可能尚未运行，
    因此提供 wait_calls() 用事件等待任务真正完成。
    """
    from src.chat.features.community_settings.services.incremental_rag_service import (
        incremental_rag_service,
    )

    calls = []
    done = threading.Event()

    async def fake_process(entry_id):
        calls.append(entry_id)
        done.set()
        return True

    monkeypatch.setattr(incremental_rag_service, "process_setting_entry", fake_process)

    def wait_calls(timeout: float = 5.0) -> list:
        done.wait(timeout)
        return calls

    return calls, wait_calls


def _insert_pending() -> int:
    async def flow(factory):
        async with factory() as session:
            async with session.begin():
                entry = CommunitySettingPendingEntry(
                    entry_type="community_setting",
                    data_json={
                        "title": "测试设定",
                        "content_text": "测试内容正文",
                        "category_name": "测试分类",
                    },
                    message_id=-1,
                    channel_id=0,
                    guild_id=0,
                    proposer_id=1,
                    status="pending",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
                session.add(entry)
                await session.flush()
                return entry.id

    return run_db(flow)


@pytest.mark.usefixtures("clean_db", "clean_community_knowledge")
def test_approve_creates_document(web_client, monkeypatch):
    entry_id = _insert_pending()
    calls, wait_calls = _patch_rag(monkeypatch)

    resp = web_client.post(f"/api/review/{entry_id}/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["document_id"] > 0
    # 向量化任务以 documents.id 被触发（等待后台任务完成）
    assert wait_calls() == [body["document_id"]]


@pytest.mark.usefixtures("clean_db", "clean_community_knowledge")
def test_approve_twice_returns_409(web_client, monkeypatch):
    entry_id = _insert_pending()
    _patch_rag(monkeypatch)
    assert web_client.post(f"/api/review/{entry_id}/approve").status_code == 200
    resp = web_client.post(f"/api/review/{entry_id}/approve")
    assert resp.status_code == 409


@pytest.mark.usefixtures("clean_db")
def test_reject_writes_reason(web_client):
    entry_id = _insert_pending()
    resp = web_client.post(
        f"/api/review/{entry_id}/reject", json={"reason": "质量不达标"}
    )
    assert resp.status_code == 200

    async def flow(factory):
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, data_json->>'reject_reason' AS reason "
                        "FROM community_settings.pending_entries WHERE id = :i"
                    ),
                    {"i": entry_id},
                )
            ).fetchone()
        return row._mapping if row else None

    row = run_db(flow)
    assert row["status"] == "rejected"
    assert row["reason"] == "质量不达标"


def test_review_missing_entry_returns_404(web_client):
    assert web_client.post("/api/review/999999/approve").status_code == 404
    assert (
        web_client.post("/api/review/999999/reject", json={"reason": "x"}).status_code
        == 404
    )
