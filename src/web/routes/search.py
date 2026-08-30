# -*- coding: utf-8 -*-
"""检索测试台路由"""
from fastapi import APIRouter, Depends

from src.web.deps import require_auth
from src.web.schemas import SearchRequest
from src.web.services.web_search_service import web_search_service

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/search")
async def search(body: SearchRequest):
    data = await web_search_service.search(
        query=body.query, scope=body.scope, top_k=body.top_k
    )
    return data
