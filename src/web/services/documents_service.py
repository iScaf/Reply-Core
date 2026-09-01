# -*- coding: utf-8 -*-
"""知识库文档列表/详情查询 + 教程文件上传（tutorials / community_settings）"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, or_, select

from src.database.database import AsyncSessionLocal
from src.database.models import (
    CommunitySettingChunk,
    CommunitySettingDocument,
    KnowledgeChunk,
    TutorialDocument,
)
from src.chat.features.tutorial_search.services.document_chunking import (
    parse_to_markdown,
)

log = logging.getLogger(__name__)

_SOURCE_MODEL = {
    # (文档模型, chunk 模型, 全文列名)：教程库全文列为 original_content，
    # 社区设定为 full_text
    "tutorials": (TutorialDocument, KnowledgeChunk, "original_content"),
    "community_settings": (CommunitySettingDocument, CommunitySettingChunk, "full_text"),
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
        doc_model, chunk_model, fulltext_col = _SOURCE_MODEL[source]
        offset = (page - 1) * page_size

        async with AsyncSessionLocal() as session:
            base = select(doc_model)
            if q:
                base = base.where(
                    or_(
                        doc_model.title.ilike(f"%{q}%"),
                        getattr(doc_model, fulltext_col).ilike(f"%{q}%"),
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
        doc_model, chunk_model, fulltext_col = _SOURCE_MODEL[source]
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
            "full_text": getattr(doc, fulltext_col, None),
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            "chunks": chunk_items,
        }

    async def _reindex_tutorial(self, doc_id: int) -> Dict[str, Any]:
        """删除旧 chunks 并按最新 original_content 重新切块向量化。"""
        from src.chat.features.tutorial_search.services.tutorial_rag_service import (
            tutorial_rag_service,
        )

        async with AsyncSessionLocal() as session:
            await tutorial_rag_service.delete_vectors_by_document_id(
                doc_id, session=session
            )
            await session.commit()
        stats = await tutorial_rag_service.process_tutorial_document(doc_id)
        if stats is None:
            raise ValueError("重新向量化失败，请检查服务端日志")
        return stats

    async def update_tutorial(
        self, doc_id: int, title: str, content: str
    ) -> Dict[str, Any]:
        """编辑教程标题与正文（Markdown），保存后重新切块向量化。"""
        title = (title or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        if not (content or "").strip():
            raise ValueError("正文不能为空")

        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(TutorialDocument).where(TutorialDocument.id == doc_id)
                )
            ).scalars().first()
            if not doc:
                raise LookupError("文档不存在")
            doc.title = title
            doc.original_content = content
            await session.commit()

        log.info(f"教程 #{doc_id} 已编辑，开始重新向量化")
        stats = await self._reindex_tutorial(doc_id)
        return {"doc_id": doc_id, "title": title, **stats}

    async def reupload_tutorial(
        self, doc_id: int, filename: str, raw: bytes
    ) -> Dict[str, Any]:
        """为已有教程重新上传文件：替换正文（Markdown）并重新切块向量化，标题不变。"""
        markdown = await parse_to_markdown(filename, raw)

        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(TutorialDocument).where(TutorialDocument.id == doc_id)
                )
            ).scalars().first()
            if not doc:
                raise LookupError("文档不存在")
            doc.original_content = markdown
            title = doc.title
            tags = dict(doc.tags or {})
            tags["filename"] = filename
            doc.tags = tags
            await session.commit()

        log.info(f"教程 #{doc_id} 已重新上传（{filename}），开始重新向量化")
        stats = await self._reindex_tutorial(doc_id)
        return {"doc_id": doc_id, "title": title, **stats}

    async def delete_tutorial(self, doc_id: int) -> Dict[str, Any]:
        """删除教程及其全部分块。"""
        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(TutorialDocument).where(TutorialDocument.id == doc_id)
                )
            ).scalars().first()
            if not doc:
                raise LookupError("文档不存在")
            await session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == doc_id
                )
            )
            await session.delete(doc)
            await session.commit()
        log.info(f"教程 #{doc_id} 及其分块已删除")
        return {"doc_id": doc_id, "deleted": True}

    async def upload_tutorial_file(
        self,
        filename: str,
        raw: bytes,
        author_id: str = "web_upload",
    ) -> Dict[str, Any]:
        """上传文件 → 解析为 Markdown 落库 → 触发分块向量化。

        Returns:
            {"doc_id", "title", "chunk_count", "parent_count"}
        Raises:
            ValueError: 格式不支持 / 解析失败 / 切块为空。
        """
        from src.chat.features.tutorial_search.services.tutorial_rag_service import (
            tutorial_rag_service,
        )

        # 1. 解析为 Markdown（格式校验在此完成；扫描页 PDF 自动走 Vision 兜底）
        markdown = await parse_to_markdown(filename, raw)
        title = Path(filename).stem or filename

        # 2. 落库（original_content 保存 Markdown 全文）
        async with AsyncSessionLocal() as session:
            doc = TutorialDocument(
                title=title,
                category="上传文档",
                author="Web 上传",
                author_id=author_id,
                original_content=markdown,
                tags={"filename": filename},
            )
            session.add(doc)
            await session.flush()
            doc_id = doc.id
            await session.commit()

        log.info(f"上传文档已入库: #{doc_id} {title}（来源: {filename}）")

        # 3. 分块 + 向量化
        stats = await tutorial_rag_service.process_tutorial_document(doc_id)
        if stats is None:
            raise ValueError("向量化处理失败，请检查服务端日志")
        return {
            "doc_id": doc_id,
            "title": title,
            "chunk_count": stats["chunk_count"],
            "parent_count": stats["parent_count"],
        }


# 模块级单例
documents_service = DocumentsService()
