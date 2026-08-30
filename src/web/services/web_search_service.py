# -*- coding: utf-8 -*-
"""
Web 检索观测台专用搜索服务。

与生产检索（knowledge_search_service 的单条 CTE）不同，这里把语义通道与
关键词通道拆成两条简单 SQL，在 Python 侧完成 RRF 融合——
便于把双通道名次、原始距离/BM25 分数完整暴露给观测台界面。
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.chat.config.chat_config import COMMUNITY_SETTINGS_RAG_CONFIG
from src.chat.services.embedding_factory import (
    get_embedding_column,
    get_embedding_service,
    get_vector_mode,
)
from src.database.database import AsyncSessionLocal

log = logging.getLogger(__name__)

# 各 scope 的 schema 与表名（两侧 chunks 表结构一致：id/document_id/chunk_text）
_SOURCES: Dict[str, Dict[str, str]] = {
    "tutorials": {
        "schema": "tutorials",
        "chunk_table": "knowledge_chunks",
        "doc_table": "tutorial_documents",
    },
    "community_settings": {
        "schema": "community_settings",
        "chunk_table": "chunks",
        "doc_table": "documents",
    },
}

# 语义通道 SQL：按向量距离升序取 top_k（vec_distance 越小越相似）
_SEMANTIC_SQL = """
SELECT c.id AS chunk_id, c.document_id, d.title, c.chunk_text,
       (c.{col} <=> :qv) AS vec_distance
FROM {schema}.{chunk_table} c
JOIN {schema}.{doc_table} d ON d.id = c.document_id
ORDER BY c.{col} <=> :qv
LIMIT :k
"""

# 关键词通道 SQL：ParadeDB BM25 打分（paradedb.score 依赖 chunk_text 上的 BM25 索引）
_KEYWORD_SQL = """
SELECT c.id AS chunk_id, c.document_id, d.title, c.chunk_text,
       paradedb.score(c.id) AS bm25_score
FROM {schema}.{chunk_table} c
JOIN {schema}.{doc_table} d ON d.id = c.document_id
WHERE c.chunk_text @@@ :q
ORDER BY bm25_score DESC
LIMIT :k
"""


def _clean_fts_query(query: str) -> str:
    """清理 BM25 查询文本：仅保留字母、数字、中日韩字符和空格。"""
    return re.sub(r"[^\w\s\u4e00-\u9fff]", "", query)


class WebSearchService:
    async def search(
        self, query: str, scope: str = "all", top_k: int = 10
    ) -> Dict[str, Any]:
        """
        执行双通道检索并做 RRF 融合。

        Returns:
            {results, channels: {semantic, keyword}, vector_mode, embedding_column, elapsed_ms}
        """
        started = time.perf_counter()
        rrf_k = COMMUNITY_SETTINGS_RAG_CONFIG.get("RRF_K", 60)
        per_channel_k = max(top_k, 10)

        sources = (
            list(_SOURCES.keys()) if scope == "all" else [scope]
        )
        semantic_ok = await self._try_semantic(query)
        keyword_ok = True

        merged: Dict[int, Dict[str, Any]] = {}
        # 每个通道查询独立使用 session：单条失败不会毒化同事务中的后续查询
        for source in sources:
            cfg = _SOURCES[source]
            if semantic_ok:
                rows = await self._run_semantic(cfg, query, per_channel_k)
                self._fuse(merged, source, rows, "semantic", rrf_k)
            kw_rows = await self._run_keyword(cfg, query, per_channel_k)
            if not kw_rows and not semantic_ok:
                keyword_ok = False
            self._fuse(merged, source, kw_rows, "keyword", rrf_k)

        results = sorted(
            merged.values(), key=lambda r: r["rrf_score"], reverse=True
        )[:top_k]

        embedding_col = await self._embedding_column()
        return {
            "results": results,
            "channels": {"semantic": semantic_ok, "keyword": keyword_ok},
            "vector_mode": get_vector_mode(),
            "embedding_column": embedding_col,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # ---------- 内部实现 ----------

    async def _embedding_column(self) -> str:
        try:
            return await get_embedding_column()
        except Exception:
            return ""

    async def _try_semantic(self, query: str) -> bool:
        """语义通道可用性：local 模式且 embedding 列有效时才启用（api/none 模式向量未落库）"""
        if get_vector_mode() != "local":
            return False
        if not await self._embedding_column():
            return False
        try:
            service = await get_embedding_service()
            vector = await service.generate_embedding(
                text=query, task_type="retrieval_query"
            )
            return bool(vector)
        except Exception as e:
            log.warning(f"[Web 检索] 查询向量生成失败，语义通道降级: {e}")
            return False

    async def _run_semantic(
        self, cfg: Dict[str, str], query: str, k: int
    ) -> List[Dict[str, Any]]:
        try:
            service = await get_embedding_service()
            vector = await service.generate_embedding(
                text=query, task_type="retrieval_query"
            )
            col = await self._embedding_column()
            sql = text(
                _SEMANTIC_SQL.format(
                    col=col,
                    schema=cfg["schema"],
                    chunk_table=cfg["chunk_table"],
                    doc_table=cfg["doc_table"],
                )
            )
            async with AsyncSessionLocal() as session:
                rows = await session.execute(sql, {"qv": str(vector), "k": k})
                return [dict(r._mapping) for r in rows.fetchall()]
        except Exception as e:
            log.error(f"[Web 检索] 语义通道查询失败: {e}", exc_info=True)
            return []

    async def _run_keyword(
        self, cfg: Dict[str, str], query: str, k: int
    ) -> List[Dict[str, Any]]:
        try:
            cleaned = _clean_fts_query(query)
            if not cleaned:
                return []
            sql = text(
                _KEYWORD_SQL.format(
                    schema=cfg["schema"],
                    chunk_table=cfg["chunk_table"],
                    doc_table=cfg["doc_table"],
                )
            )
            async with AsyncSessionLocal() as session:
                rows = await session.execute(sql, {"q": cleaned, "k": k})
                return [dict(r._mapping) for r in rows.fetchall()]
        except Exception as e:
            log.error(f"[Web 检索] 关键词通道查询失败: {e}", exc_info=True)
            return []

    def _fuse(
        self,
        merged: Dict[int, Dict[str, Any]],
        source: str,
        rows: List[Dict[str, Any]],
        channel: str,
        rrf_k: int,
    ) -> None:
        """把单通道结果按名次并入融合表（rrf = Σ 1/(rrf_k + rank)）"""
        for rank, row in enumerate(rows, start=1):
            key = int(row["chunk_id"])
            entry = merged.get(key)
            if entry is None:
                entry = {
                    "source": source,
                    "chunk_id": key,
                    "document_id": int(row["document_id"]),
                    "title": row.get("title") or "（无标题）",
                    "chunk_text": row["chunk_text"],
                    "semantic_rank": None,
                    "keyword_rank": None,
                    "vec_distance": None,
                    "bm25_score": None,
                    "rrf_score": 0.0,
                }
                merged[key] = entry
            if channel == "semantic":
                entry["semantic_rank"] = rank
                entry["vec_distance"] = (
                    float(row["vec_distance"]) if row["vec_distance"] is not None else None
                )
            else:
                entry["keyword_rank"] = rank
                entry["bm25_score"] = (
                    float(row["bm25_score"]) if row["bm25_score"] is not None else None
                )
            entry["rrf_score"] += 1.0 / (rrf_k + rank)


# 模块级单例
web_search_service = WebSearchService()
