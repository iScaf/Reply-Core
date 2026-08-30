# -*- coding: utf-8 -*-
"""FastAPI 应用工厂"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 AI 服务（Provider 配置存于 PG，无 bot 依赖）；失败不阻塞页面"""
    token = os.getenv("WEB_ADMIN_TOKEN", "")
    if not token:
        log.warning("WEB_ADMIN_TOKEN 未配置，/api 接口将返回 503（登录不可用）")
    try:
        from src.chat.features.tools.services.tool_service import ToolService
        from src.chat.features.tools.tool_loader import load_tools_from_directory
        from src.chat.services.ai.service import ai_service

        # 与 main.py 的装配对齐，但 bot 为 None（Web 端无 Discord 上下文；
        # 依赖 bot 的工具 scope 会在执行时自行返回空结果）
        ai_service.set_bot(None)
        available_tools, tool_map = load_tools_from_directory(
            "src/chat/features/tools/functions"
        )
        tool_service = ToolService(
            bot=None, tool_map=tool_map, tool_declarations=available_tools
        )
        ai_service.set_tools(available_tools, tool_map, tool_service)
        await ai_service.initialize()
        log.info("[Web] AI 服务初始化完成，可用模型: %s", ai_service.get_available_models())
    except Exception as e:
        log.warning(f"[Web] AI 服务初始化失败（检索/问答将走降级路径）: {e}")
    yield


def create_app() -> FastAPI:
    from src.web.deps import load_token

    load_token()

    app = FastAPI(title="Reply-Core Web 控制台", lifespan=lifespan)

    from src.web.routes import (
        auth,
        chat,
        documents,
        review,
        search,
        stats,
    )

    app.include_router(auth.router, prefix="/api", tags=["auth"])
    app.include_router(stats.router, prefix="/api", tags=["stats"])
    app.include_router(documents.router, prefix="/api", tags=["documents"])
    app.include_router(review.router, prefix="/api", tags=["review"])
    app.include_router(search.router, prefix="/api", tags=["search"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
