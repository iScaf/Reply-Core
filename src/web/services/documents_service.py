# -*- coding: utf-8 -*-
"""知识库文档列表/详情查询（tutorials / community_settings）"""
import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select

from src.database.database import AsyncSessionLocal
from src.database.models import (
    CommunitySettingChunk,
    CommunitySettingDocument,
    KnowledgeChunk,
    TutorialDocument,
)

log = logging.getLogger(__name__)

_SOURCE_MODEL = {
    "tutorials": (TutorialDocument, KnowledgeChunk),
    "community_settings": (CommunitySettingDocument, CommunitySettingChunk),
}


def _check_source(source: str) -> None:
    if source not in _SOURCE_MODEL:
        raise ValueError(f"未知的知识库来源: {source}")


class DocumentsService:
    async def list_documents(
        self,
        source: str,
        page: int = 1,
        page_size: int = 20,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        _check_source(source)
        doc_model, chunk_model = _SOURCE_MODEL[source]
        offset = (page - 1) * page_size

        async with AsyncSessionLocal() as session:
            base = select(doc_model)
            if q:
                base = base.where(
                    or_(
                        doc_model.title.ilike(f"%{q}%"),
                        doc_model.full_text.ilike(f"%{q}%"),
                    )
                )
            total = (
                await session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0

            chunk_count_sq = (
                select(
                    chunk_model.document_id.label("doc_id"),
                    func.count().label("chunk_count"),
                )
                .group_by(chunk_model.document_id)
                .subquery()
            )
            stmt = (
                select(
                    doc_model,
                    func.coalesce(chunk_count_sq.c.chunk_count, 0).label("chunk_count"),
                )
                .outerjoin(chunk_count_sq, chunk_count_sq.c.doc_id == doc_model.id)
                .order_by(doc_model.updated_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            rows = (await session.execute(stmt)).all()

        items = []
        for doc, chunk_count in rows:
            items.append(
                {
                    "id": doc.id,
                    "title": doc.title,
                    "category": getattr(doc, "category", None),
                    "author": getattr(doc, "author", None),
                    "source_url": getattr(doc, "source_url", None),
                    "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                    "chunk_count": int(chunk_count),
                }
            )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_document(self, source: str, doc_id: int) -> Optional[Dict[str, Any]]:
        _check_source(source)
        doc_model, chunk_model = _SOURCE_MODEL[source]
        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(doc_model).where(doc_model.id == doc_id)
                )
            ).scalars().first()
            if not doc:
                return None
            chunks = (
                (
                    await session.execute(
                        select(chunk_model)
                        .where(chunk_model.document_id == doc_id)
                        .order_by(
                            chunk_model.chunk_index
                            if hasattr(chunk_model, "chunk_index")
                            else chunk_model.chunk_order
                        )
                    )
                )
                .scalars()
                .all()
            )

        chunk_items = [
            {
                "id": c.id,
                "chunk_index": getattr(c, "chunk_index", None)
                or getattr(c, "chunk_order", None),
                "chunk_text": c.chunk_text,
            }
            for c in chunks
        ]
        return {
            "id": doc.id,
            "title": doc.title,
            "category": getattr(doc, "category", None),
            "author": getattr(doc, "author", None),
            "source_url": getattr(doc, "source_url", None),
            "full_text": doc.full_text,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            "chunks": chunk_items,
        }


# 模块级单例
documents_service = DocumentsService()
