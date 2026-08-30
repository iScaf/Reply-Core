# -*- coding: utf-8 -*-
"""知识库文档列表/详情路由"""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.web.deps import require_auth
from src.web.services.documents_service import documents_service

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/documents")
async def list_documents(
    source: str = Query(pattern="^(tutorials|community_settings)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(None, max_length=200),
):
    return await documents_service.list_documents(
        source=source, page=page, page_size=page_size, q=q
    )


@router.get("/documents/{source}/{doc_id}")
async def get_document(source: str, doc_id: int):
    doc = await documents_service.get_document(source, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc
