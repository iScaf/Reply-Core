# -*- coding: utf-8 -*-
"""Bot 人设管理路由（ai_config.bot_persona，保存后即时生效）"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.chat.services.persona_service import persona_service
from src.web.deps import require_auth

router = APIRouter(dependencies=[Depends(require_auth)])


class PersonaSaveRequest(BaseModel):
    system_prompt: str = Field(..., description="人设正文（<character> 结构全文）")
    display_name: str = Field(None, max_length=100)
    is_default: bool = Field(None, description="设为默认人设（清除其他默认）")
    enabled: bool = Field(None)


@router.get("/persona")
async def list_personas():
    """列出全部人设（正文截尾预览，编辑请取单条）。"""
    items = await persona_service.get_all(force_refresh=True)
    return {
        "items": [
            {
                **p,
                "system_prompt": p["system_prompt"],
                "preview": p["system_prompt"][:150],
            }
            for p in items
        ]
    }


@router.get("/persona/{name}")
async def get_persona(name: str):
    """读取单个人设全文。"""
    items = await persona_service.get_all(force_refresh=True)
    for p in items:
        if p["name"] == name:
            return p
    raise HTTPException(status_code=404, detail="人设不存在")


@router.put("/persona/{name}")
async def save_persona(name: str, body: PersonaSaveRequest):
    """创建/更新人设，保存后即时生效（缓存刷新，无需重启）。"""
    try:
        return await persona_service.save_persona(
            name,
            system_prompt=body.system_prompt,
            display_name=body.display_name,
            is_default=body.is_default,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
