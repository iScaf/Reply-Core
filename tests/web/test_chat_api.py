# -*- coding: utf-8 -*-
"""问答演示 API 测试（mock AI 生成，检索走真实 PG）"""
from types import SimpleNamespace

from src.chat.services.ai.providers.base import GenerationResult


def _patch_ai_ok(monkeypatch, reply: str = "建议从 64 起步 [资料1]"):
    from src.chat.services.ai.service import ai_service

    monkeypatch.setattr(ai_service, "get_available_models", lambda: ["test-model"])

    async def fake_generate_with_tools(**kwargs):
        return GenerationResult(content=reply, model_used="test-model")

    monkeypatch.setattr(ai_service, "generate_with_tools", fake_generate_with_tools)


def _patch_search(monkeypatch, citations: list):
    from src.web.services.web_search_service import web_search_service

    async def fake_search(query, scope="all", top_k=10):
        return {
            "results": citations,
            "channels": {"semantic": False, "keyword": True},
            "vector_mode": "none",
            "embedding_column": "",
            "elapsed_ms": 1,
        }

    monkeypatch.setattr(web_search_service, "search", fake_search)


CITATION = {
    "source": "community_settings",
    "chunk_id": 1,
    "document_id": 1,
    "title": "测试资料",
    "chunk_text": "测试内容",
    "semantic_rank": None,
    "keyword_rank": 1,
    "rrf_score": 0.016,
    "vec_distance": None,
    "bm25_score": 2.0,
}


def test_chat_normal_reply(web_client, monkeypatch):
    _patch_ai_ok(monkeypatch)
    _patch_search(monkeypatch, [CITATION])

    resp = web_client.post(
        "/api/chat", json={"message": "ef_search 设多少？", "scope": "all"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is False
    assert data["reply"] == "建议从 64 起步 [资料1]"
    assert data["model"] == "test-model"
    assert data["citations"][0]["title"] == "测试资料"


def test_chat_degrades_when_ai_fails(web_client, monkeypatch):
    from src.chat.services.ai.service import ai_service

    monkeypatch.setattr(ai_service, "get_available_models", lambda: ["test-model"])

    async def broken_generate_with_tools(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_service, "generate_with_tools", broken_generate_with_tools)
    _patch_search(monkeypatch, [CITATION])

    resp = web_client.post(
        "/api/chat", json={"message": "测试降级", "scope": "all"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["reply"] is None
    assert "provider down" in data["degrade_reason"]
    # 降级时仍返回检索结果供展示
    assert data["citations"][0]["title"] == "测试资料"


def test_chat_degrades_when_no_models(web_client, monkeypatch):
    from src.chat.services.ai.service import ai_service

    monkeypatch.setattr(ai_service, "get_available_models", lambda: [])
    _patch_search(monkeypatch, [])

    resp = web_client.post("/api/chat", json={"message": "hi", "scope": "all"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert "未配置" in data["degrade_reason"] or "不可用" in data["degrade_reason"]
