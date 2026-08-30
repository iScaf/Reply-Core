# -*- coding: utf-8 -*-
"""审核队列路由"""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.web.deps import require_auth
from src.web.schemas import RejectRequest
from src.web.services.web_review_service import (
    ConflictError,
    web_review_service,
)

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/review/pending")
async def list_pending(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
):
    return await web_review_service.list_pending(page=page, page_size=page_size)


@router.post("/review/{pending_id}/approve")
async def approve(pending_id: int):
    try:
        result = await web_review_service.approve(pending_id)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="审核条目不存在")
    return {"ok": True, **result}


@router.post("/review/{pending_id}/reject")
async def reject(pending_id: int, body: RejectRequest):
    try:
        result = await web_review_service.reject(pending_id, body.reason)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="审核条目不存在")
    return result
