# -*- coding: utf-8 -*-
"""知识库健康统计聚合"""
import logging
from typing import Any, Dict

from sqlalchemy import text

from src.chat.services.embedding_factory import get_embedding_column, get_vector_mode
from src.database.database import AsyncSessionLocal

log = logging.getLogger(__name__)

_COUNTS = {
    "tutorial_documents": "SELECT count(*) FROM tutorials.tutorial_documents",
    "tutorial_chunks": "SELECT count(*) FROM tutorials.knowledge_chunks",
    "community_documents": "SELECT count(*) FROM community_settings.documents",
    "community_chunks": "SELECT count(*) FROM community_settings.chunks",
    "community_pending": (
        "SELECT count(*) FROM community_settings.pending_entries "
        "WHERE status = 'pending'"
    ),
    "forum_threads": "SELECT count(*) FROM forum.forum_threads",
    "conversation_blocks": "SELECT count(*) FROM conversation.conversation_blocks",
    "member_profiles": "SELECT count(*) FROM community.member_profiles",
}


class StatsService:
    async def collect(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        async with AsyncSessionLocal() as session:
            for key, sql in _COUNTS.items():
                try:
                    counts[key] = (await session.execute(text(sql))).scalar() or 0
                except Exception as e:
                    log.warning(f"统计 {key} 失败: {e}")
                    counts[key] = -1

        try:
            embedding_column = await get_embedding_column()
        except Exception:
            embedding_column = ""

        ai_ready = False
        try:
            from src.chat.services.ai.service import ai_service

            ai_ready = bool(ai_service.get_available_models())
        except Exception:
            pass

        return {
            "tutorials": {
                "documents": counts["tutorial_documents"],
                "chunks": counts["tutorial_chunks"],
            },
            "community": {
                "documents": counts["community_documents"],
                "chunks": counts["community_chunks"],
                "pending": counts["community_pending"],
            },
            "forum_threads": counts["forum_threads"],
            "conversation_blocks": counts["conversation_blocks"],
            "member_profiles": counts["member_profiles"],
            "vector_mode": get_vector_mode(),
            "embedding_column": embedding_column,
            "ai_ready": ai_ready,
        }


# 模块级单例
stats_service = StatsService()
