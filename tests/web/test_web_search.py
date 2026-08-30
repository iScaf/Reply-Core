# -*- coding: utf-8 -*-
"""检索测试台 API 测试（真实 PG，向量列为空时验证 BM25 通道与 RRF 输出）"""
import pytest

from src.database.models import CommunitySettingDocument, CommunitySettingChunk

from tests.web.conftest import run_db


def _seed_chunk() -> int:
    async def flow(factory):
        async with factory() as session:
            async with session.begin():
                doc = CommunitySettingDocument(
                    external_id="test_search_1",
                    title="ef_search 参数起步值规范",
                    full_text="标题: ef_search 参数起步值规范\n类别: 检索规范\n内容: ef_search 建议从 64 起步。",
                    source_metadata={"category": "检索规范"},
                )
                session.add(doc)
                await session.flush()
                session.add(
                    CommunitySettingChunk(
                        document_id=doc.id,
                        chunk_index=0,
                        chunk_text="ef_search 参数起步值规范。建议从 64 起步，召回不达标翻倍至 128。",
                    )
                )
                return doc.id

    return run_db(flow)


@pytest.mark.usefixtures("clean_db", "clean_community_knowledge")
def test_search_keyword_channel(web_client):
    doc_id = _seed_chunk()
    resp = web_client.post(
        "/api/search",
        json={"query": "ef_search 起步", "scope": "community_settings", "top_k": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["channels"]["keyword"] is True
    assert data["vector_mode"] in ("none", "api", "local")

    hits = [r for r in data["results"] if r["document_id"] == doc_id]
    assert hits, "关键词通道应命中种子 chunk"
    top = hits[0]
    assert top["keyword_rank"] == 1
    assert top["bm25_score"] is not None and top["bm25_score"] > 0
    assert top["rrf_score"] > 0
    assert top["title"] == "ef_search 参数起步值规范"
    # 结果按 rrf_score 降序
    scores = [r["rrf_score"] for r in data["results"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.usefixtures("clean_db")
def test_search_no_match_returns_empty(web_client):
    resp = web_client.post(
        "/api/search",
        json={"query": "完全无关的查询词组", "scope": "tutorials", "top_k": 5},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["results"], list)


def test_search_rejects_bad_scope(web_client):
    resp = web_client.post(
        "/api/search",
        json={"query": "x", "scope": "invalid_scope"},
    )
    assert resp.status_code == 422
