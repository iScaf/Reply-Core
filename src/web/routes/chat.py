# -*- coding: utf-8 -*-
"""问答演示路由"""
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from src.web.deps import require_auth
from src.web.schemas import ChatRequest
from src.web.services.web_chat_service import web_chat_service

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/chat")
async def chat(body: ChatRequest):
    return await web_chat_service.generate_reply(
        message=body.message,
        history=[h.model_dump() for h in body.history],
        scope=body.scope,
    )


@router.get("/chat/history")
async def chat_history(
    page: int = Query(1, ge=1),
    rounds: int = Query(5, ge=1, le=20),
):
    """分页返回 Web 问答历史（按轮 = 一问一答，时间正序）。"""
    return await web_chat_service.get_history(page=page, rounds=rounds)


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    """流式问答（SSE）：思维链 / 工具调用 / 正文增量分段推送。"""

    async def event_source():
        try:
            async for event, data in web_chat_service.generate_reply_stream(
                message=body.message,
                history=[h.model_dump() for h in body.history],
                scope=body.scope,
            ):
                # default=str：工具执行会把嵌套参数模型（如 SearchParams）实例化
                # 回填进 arguments，序列化时统一转字符串表示
                payload = json.dumps(data, ensure_ascii=False, default=str)
                yield f"event: {event}\ndata: {payload}\n\n"
        except Exception as e:  # 生成器内异常兜底，避免连接静默断开
            payload = json.dumps({"message": f"服务内部错误: {e}"}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 反向代理场景禁用响应缓冲
        },
    )
