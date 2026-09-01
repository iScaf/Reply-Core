# -*- coding: utf-8 -*-
import logging
import logging.handlers
from typing import Any, List, Dict
from sqlalchemy import text

from src.database.database import AsyncSessionLocal

import os

# 导入新的帖子设置服务
from src.chat.features.tutorial_search.services.thread_settings_service import (
    thread_settings_service,
)
from src.chat.services.embedding_factory import (
    get_embedding_service,
    get_embedding_column,
)


# --- RAG 追踪日志系统 ---
LOG_DIR = "logs"
LOG_FILE_PATH = os.path.join(LOG_DIR, "tutorial_rag_trace.log")
os.makedirs(LOG_DIR, exist_ok=True)

rag_trace_logger = logging.getLogger("rag_trace")
rag_trace_logger.setLevel(logging.INFO)
rag_trace_logger.propagate = False

handler = logging.handlers.RotatingFileHandler(
    LOG_FILE_PATH,
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
formatter = logging.Formatter("%(asctime)s - %(message)s")
handler.setFormatter(formatter)

if not rag_trace_logger.handlers:
    rag_trace_logger.addHandler(handler)

log = logging.getLogger(__name__)

# 从全局配置导入 RAG 设置
from src.chat.config.chat_config import TUTORIAL_RAG_CONFIG


class TutorialSearchService:
    def __init__(self):
        log.info("TutorialSearchService 已初始化")
        # 将配置加载到实例属性中，方便访问
        self.config = TUTORIAL_RAG_CONFIG
        log.info(f"教程 RAG 配置已加载: {self.config}")

    async def _hybrid_search_with_rrf(
        self,
        session,
        query_text: str,
        query_vector: List[float],
        thread_id: int | None,
        search_mode: str = "ISOLATED",
    ) -> List:
        """使用原生 SQL 在数据库中执行高效的混合搜索和 RRF 融合，返回最佳 chunk 及其分数。"""
        # 根据配置选择使用哪个 embedding 列
        embedding_col = await get_embedding_column()
        # We need to join with tutorial_documents to get the thread_id
        # parent_id IS NULL：只检索子块/单块；父块（节级全文）不参与召回，
        # 避免与子块重复命中同一内容稀释 RRF 排名
        sql_query = text(
            f"""
            WITH semantic_search AS (
                SELECT
                    kc.id,
                    RANK() OVER (ORDER BY kc.{embedding_col} <=> :query_vector) as rank,
                    td.thread_id
                FROM tutorials.knowledge_chunks kc
                JOIN tutorials.tutorial_documents td ON kc.document_id = td.id
                WHERE kc.parent_id IS NULL AND kc.{embedding_col} IS NOT NULL
                ORDER BY kc.{embedding_col} <=> :query_vector
                LIMIT :top_k_vector
            ),
            keyword_search AS (
                SELECT
                    kc.id,
                    ROW_NUMBER() OVER (ORDER BY pdb.score(kc.id) DESC) as rank,
                    td.thread_id
                FROM tutorials.knowledge_chunks kc
                JOIN tutorials.tutorial_documents td ON kc.document_id = td.id
                WHERE kc.chunk_text @@@ :query_text AND kc.parent_id IS NULL
                LIMIT :top_k_fts
            )
            SELECT
                COALESCE(s.id, k.id) as id,
                (CASE WHEN COALESCE(s.thread_id, k.thread_id) = :thread_id THEN 1 ELSE 0 END) as is_thread_match,
                (COALESCE(1.0 / (:rrf_k + s.rank), 0.0) + COALESCE(1.0 / (:rrf_k + k.rank), 0.0)) as rrf_score
            FROM semantic_search s
            FULL OUTER JOIN keyword_search k ON s.id = k.id
            """
        )

        # 根据搜索模式添加过滤条件
        if search_mode == "ISOLATED":
            # 隔离模式：只搜索当前帖子（thread_id匹配）和基础库（thread_id为NULL）
            sql_query = text(
                sql_query.text
                + """
                WHERE COALESCE(s.thread_id, k.thread_id) = :thread_id
                   OR COALESCE(s.thread_id, k.thread_id) IS NULL
                ORDER BY
                    is_thread_match DESC,
                    rrf_score DESC
                LIMIT :final_k;
                """
            )
        else:
            # 优先模式：搜索所有教程，但当前帖子优先
            sql_query = text(
                sql_query.text
                + """
                ORDER BY
                    is_thread_match DESC,
                    rrf_score DESC
                LIMIT :final_k;
                """
            )
        result = await session.execute(
            sql_query,
            {
                "query_text": query_text,
                "query_vector": str(query_vector),
                "thread_id": str(thread_id) if thread_id is not None else None,
                "top_k_vector": self.config["TOP_K_VECTOR"],
                "top_k_fts": self.config["TOP_K_FTS"],
                "rrf_k": self.config["RRF_K"],
                "final_k": self.config["HYBRID_SEARCH_FINAL_K"],
            },
        )
        return result.fetchall()

    async def _get_parent_docs_by_chunk_ids(
        self, session, ids: List[int]
    ) -> List[Dict[str, str]]:
        """
        根据命中的 chunk ID 列表，回取节级上下文（small-to-big）：
        - 命中子块（parent_id 非空）→ 返回父块的节级全文
        - 命中单块（parent_id 为空）→ 返回其自身内容

        同一父块的多个子块命中时合并为一个上下文条目；
        去重与截断均保持原始传入 `ids` 的优先级顺序。
        """
        if not ids:
            return []

        log.info(f"父块回取：收到排序后的 chunk IDs: {ids}")

        # 一条 SQL 完成：子块 → 父块 LEFT JOIN，标题来自所属文档；
        # COALESCE 保证单块（无父块）回退到自身内容
        stmt = text(
            """
            SELECT
                kc.id AS chunk_id,
                COALESCE(kc.parent_id, kc.id) AS context_id,
                COALESCE(parent.chunk_text, kc.chunk_text) AS content,
                COALESCE(parent.section_path, kc.section_path) AS section_path,
                td.title AS title,
                td.thread_id AS thread_id
            FROM tutorials.knowledge_chunks kc
            LEFT JOIN tutorials.knowledge_chunks parent
                ON kc.parent_id = parent.id
            JOIN tutorials.tutorial_documents td ON kc.document_id = td.id
            """
        )
        # ids 来自数据库自增主键（int），f-string 拼接无注入风险；
        # WHERE 限定命中块，CASE 保持命中优先级顺序
        ids_str = ", ".join(str(int(i)) for i in ids)
        stmt = text(
            stmt.text
            + f"WHERE kc.id IN ({ids_str}) "
            + "ORDER BY CASE "
            + " ".join(
                [f"WHEN kc.id = {id_} THEN {i}" for i, id_ in enumerate(ids)]
            )
            + " END"
        )

        rows = (await session.execute(stmt)).fetchall()
        if not rows:
            return []

        # Python 有序去重：同一上下文（父块/单块）只保留一条
        seen_context_ids = set()
        contexts: List[Dict[str, str]] = []
        for row in rows:
            context_id = row.context_id
            if context_id in seen_context_ids:
                continue
            seen_context_ids.add(context_id)
            content = row.content
            if row.section_path:
                # 喂给 LLM 时拼接面包屑，注入章节位置语境
                content = f"【{row.section_path}】\n{content}"
            contexts.append({"title": row.title, "content": content})

        # 截断，只保留最高优先级的上下文条目
        max_contexts = self.config["MAX_PARENT_DOCS"]
        if len(contexts) > max_contexts:
            log.info(
                f"去重后的上下文条目数 ({len(contexts)}) 超过上限 {max_contexts}，"
                f"将截断为前 {max_contexts} 条。"
            )
            contexts = contexts[:max_contexts]

        log.info(
            f"父块回取：{len(ids)} 个命中块 → {len(contexts)} 条上下文"
            f"（{[c['title'] for c in contexts]}）"
        )
        return contexts

    async def search(
        self, query: str, user_id: str = "N/A", thread_id: int | None = None
    ) -> List[Dict[str, Any]]:
        """
        执行 RAG 流程：混合搜索找到最佳子文档，然后返回其完整的父文档内容列表。
        每个文档是一个包含 title, content, thread_id 的字典。
        """
        trace_log = ["--- RAG TRACE START ---", f"UserID: {user_id}", f"Query: {query}"]
        log.info(f"收到来自用户 '{user_id}' 的教程知识库搜索请求: '{query}'")

        try:
            # 根据配置选择对应的 embedding 服务
            embedding_service = await get_embedding_service()
            query_embedding = await embedding_service.generate_embedding(
                text=query, task_type="retrieval_query"
            )
            if not query_embedding:
                log.info("RAG功能未启用：Ollama服务不可用，跳过教程检索。")
                return []
        except Exception as e:
            log.error(f"为查询 '{query}' 生成 embedding 时出错: {e}", exc_info=True)
            return []

        final_parent_docs: List[Dict[str, str]] = []
        try:
            # 获取当前帖子的搜索模式。
            # 无帖子上下文（频道聊天 / Web 控制台）时使用 PRIORITY：
            # 全库检索且退化为纯 RRF 排序；若沿用默认 ISOLATED，
            # SQL 会排除所有帖内教程（仅剩 thread_id 为 NULL 的基础库）。
            if thread_id is not None:
                current_search_mode = await thread_settings_service.get_search_mode(
                    str(thread_id)
                )
            else:
                current_search_mode = "PRIORITY"
            log.info(f"帖子 {thread_id} 的搜索模式: {current_search_mode}")

            async with AsyncSessionLocal() as session:
                # 1. 混合搜索，找到最相关的 chunk ID
                search_results = await self._hybrid_search_with_rrf(
                    session, query, query_embedding, thread_id, current_search_mode
                )
                log.info(
                    f"混合搜索 RRF 结果 (id, is_thread_match, rrf_score): {search_results}"
                )
                best_chunk_ids = [res.id for res in search_results]

                trace_log.append(
                    f"Found best chunk IDs from DB (Top {len(best_chunk_ids)}): {best_chunk_ids}"
                )

                if not best_chunk_ids:
                    log.info(f"数据库内混合搜索未找到 '{query}' 的相关文档。")
                    return []

                # 2. 根据 chunk IDs 获取其所属的完整父文档内容
                final_parent_docs = await self._get_parent_docs_by_chunk_ids(
                    session, best_chunk_ids
                )
                retrieved_titles = [doc["title"] for doc in final_parent_docs]
                trace_log.append(
                    f"Retrieved {len(final_parent_docs)} parent document(s): {retrieved_titles}"
                )

        except Exception as e:
            log.error(f"在数据库中执行搜索或获取父文档时出错: {e}", exc_info=True)
            return []

        if not final_parent_docs:
            log.warning(f"找到了 chunk ID，但未能获取任何父文档。查询: '{query}'")
            return []

        # 3. 直接返回包含完整信息的文档列表
        trace_log.append(
            f"Returning {len(final_parent_docs)} full parent documents to be formatted by PromptService."
        )
        trace_log.append("--- RAG TRACE END ---")
        rag_trace_logger.info("\n".join(trace_log))

        # 新增：在主日志中也记录命中的文档标题，方便快速诊断
        retrieved_titles_str = ", ".join([doc["title"] for doc in final_parent_docs])
        log.info(
            f"为查询 '{query}' 检索到 {len(final_parent_docs)} 份父文档。标题: [{retrieved_titles_str}]"
        )
        return final_parent_docs


# 创建服务的单例
tutorial_search_service = TutorialSearchService()
