# -*- coding: utf-8 -*-
"""Discord 用户信息查询路由"""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.web.deps import require_auth
from src.web.services.users_service import users_service

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(None, max_length=100),
):
    """分页列出 Discord 用户档案，支持模糊搜索。"""
    return await users_service.list_users(page=page, page_size=page_size, q=q)


@router.get("/users/{profile_id}")
async def get_user(profile_id: int):
    """用户详情：档案 + 记忆笔记 + 最近对话块。"""
    user = await users_service.get_user(profile_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/users/{profile_id}/chats")
async def search_user_chats(
    profile_id: int,
    q: str = Query(..., min_length=1, max_length=100),
):
    """在指定用户的对话记忆块中按关键词搜索。"""
    return {"items": await users_service.search_user_chats(profile_id, q)}
