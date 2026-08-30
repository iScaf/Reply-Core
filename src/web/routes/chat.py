# -*- coding: utf-8 -*-
"""问答演示路由"""
from fastapi import APIRouter, Depends

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
