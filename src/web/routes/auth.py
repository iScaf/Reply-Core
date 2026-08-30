# -*- coding: utf-8 -*-
"""登录/登出路由"""
import logging
import secrets

from fastapi import APIRouter, HTTPException, Response

from src.web.deps import SESSION_COOKIE, load_token
from src.web.schemas import LoginRequest

log = logging.getLogger(__name__)

router = APIRouter()

# token 校验使用常量时间比较，登录接口不设速率限制（内网演示场景）


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    load_token()  # 每次登录时刷新读取，支持 .env 修改后免重启
    from src.web.deps import WEB_ADMIN_TOKEN

    if not WEB_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Web 管理未配置 WEB_ADMIN_TOKEN")
    if not secrets.compare_digest(body.token, WEB_ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Token 不正确")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=WEB_ADMIN_TOKEN,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE)
    return {"ok": True}
