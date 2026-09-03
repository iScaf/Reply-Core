# -*- coding: utf-8 -*-
"""Skill 技能管理路由（skills/ 目录的文件读写）"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.web.deps import require_auth
from src.web.services.skill_service import skill_service

router = APIRouter(dependencies=[Depends(require_auth)])


class SkillSaveRequest(BaseModel):
    content: str = Field(..., description="技能正文（Markdown）")
    display_name: str = Field(None, max_length=100)
    description: str = Field(None, max_length=300)
    injection_mode: str = Field(None, pattern="^(prompt|on_demand)$")
    enabled: bool = Field(None)


@router.get("/skills")
async def list_skills():
    """列出全部技能（元数据 + 正文）。"""
    return {"items": skill_service.list_skills()}


@router.get("/skills/{name}")
async def get_skill(name: str):
    skill = skill_service.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    return skill


@router.put("/skills/{name}")
async def save_skill(name: str, body: SkillSaveRequest):
    """创建/更新技能（写入 skills/<name>/SKILL.md 并刷新缓存）。"""
    try:
        return skill_service.save_skill(
            name,
            content=body.content,
            display_name=body.display_name,
            description=body.description,
            injection_mode=body.injection_mode,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
