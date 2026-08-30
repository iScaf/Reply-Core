# -*- coding: utf-8 -*-
"""Web 后台公共依赖：管理员 token 鉴权"""
import logging
import secrets

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

# .env 中配置的管理 token；未配置时所有 /api 接口返回 503
WEB_ADMIN_TOKEN = secrets.token_urlsafe(32)  # 兜底随机值，实际以 .env 覆盖
_token_env = None

SESSION_COOKIE = "web_admin_session"


def load_token() -> None:
    """从环境变量加载 token（app 工厂创建时调用，保证测试可注入后刷新）"""
    global WEB_ADMIN_TOKEN, _token_env
    import os

    from dotenv import load_dotenv

    load_dotenv()  # create_app 阶段 database 模块可能尚未导入，确保 .env 已加载
    _token_env = os.getenv("WEB_ADMIN_TOKEN", "")
    WEB_ADMIN_TOKEN = _token_env


async def require_auth(request: Request) -> None:
    """校验会话 cookie；未配置 token 时拒绝服务"""
    if not _token_env:
        raise HTTPException(status_code=503, detail="Web 管理未配置 WEB_ADMIN_TOKEN")
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie or not secrets.compare_digest(cookie, _token_env):
        raise HTTPException(status_code=401, detail="未认证或会话已过期")
