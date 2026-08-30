# -*- coding: utf-8 -*-
"""知识库健康统计路由"""
from fastapi import APIRouter, Depends

from src.web.deps import require_auth
from src.web.services.stats_service import stats_service

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/stats")
async def get_stats():
    return await stats_service.collect()
