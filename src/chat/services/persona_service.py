# -*- coding: utf-8 -*-
"""Bot 人设服务：ai_config.bot_persona 的读取 / seed / 缓存。

- 人设正文存数据库（后台「技能与人设」视图可编辑，保存即刷新缓存生效）
- 应用启动或表为空时，从 prompts.py 的静态定义 seed（default + 变体）
- prompts.py 静态定义保留为兜底：DB 无记录/查询异常时回退，Discord 端永不失效
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from src.chat.config.prompts import PERSONA_VARIANTS

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30


class PersonaService:
    def __init__(self) -> None:
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_loaded_at: float = 0.0

    # ---------- 读取 ----------

    def _cache_valid(self) -> bool:
        import time

        return self._cache is not None and (time.monotonic() - self._cache_loaded_at) < _CACHE_TTL_SECONDS

    def get_all_sync(self) -> Optional[List[Dict[str, Any]]]:
        """同步读取缓存（供 prompt_service 同步链路使用）；缓存过期返回 None。"""
        return self._cache if self._cache_valid() else None

    async def get_all(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """读取全部人设（缓存 30s；force_refresh 强制拉库）。"""
        import time

        if self._cache is not None and not force_refresh and self._cache_valid():
            return self._cache
        from sqlalchemy import select

        from src.database.database import AsyncSessionLocal
        from src.database.models import BotPersona

        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        select(BotPersona)
                        .where(BotPersona.enabled == 1)
                        .order_by(BotPersona.id.asc())
                    )
                ).scalars().all()
            self._cache = [
                {
                    "name": r.name,
                    "display_name": r.display_name,
                    "system_prompt": r.system_prompt,
                    "is_default": bool(r.is_default),
                    "enabled": bool(r.enabled),
                }
                for r in rows
            ]
        except Exception as e:
            log.warning(f"[Persona] 读取人设库失败（回退静态定义）: {e}")
            self._cache = self._static_fallback()
        self._cache_loaded_at = time.monotonic()
        return self._cache

    def get_prompt_for_style(self, persona_style: str) -> Optional[str]:
        """按 persona_style 取人设正文（同步，走缓存）。

        查找顺序：缓存中 name 匹配（enabled）→ 缓存中 is_default → None（调用方回退静态）。
        """
        cache = self.get_all_sync()
        if cache is None:
            return None
        for p in cache:
            if p["name"] == persona_style:
                return p["system_prompt"]
        for p in cache:
            if p["is_default"]:
                return p["system_prompt"]
        return None

    # ---------- seed 与回退 ----------

    @staticmethod
    def _static_fallback() -> List[Dict[str, Any]]:
        """prompts.py 静态定义（DB 异常时的兜底视图）。

        default 取 prompts.SYSTEM_PROMPT（已经 _apply_identity 身份替换的版本）。
        """
        from src.chat.config.prompts import SYSTEM_PROMPT

        items = [
            {
                "name": "default",
                "display_name": "默认人设",
                "system_prompt": SYSTEM_PROMPT,
                "is_default": True,
            }
        ]
        for style, variants in PERSONA_VARIANTS.items():
            prompt = variants.get("default", {}).get("SYSTEM_PROMPT")
            if prompt:
                items.append(
                    {"name": style, "display_name": style, "system_prompt": prompt, "is_default": False}
                )
        return items

    async def ensure_seeded(self) -> None:
        """表为空时从 prompts.py 静态定义写入（幂等）。"""
        from src.database.database import AsyncSessionLocal
        from src.database.models import BotPersona

        async with AsyncSessionLocal() as session:
            count = (
                await session.execute(select(func.count()).select_from(BotPersona))
            ).scalar() or 0
            if count > 0:
                return
            for item in self._static_fallback():
                session.add(
                    BotPersona(
                        name=item["name"],
                        display_name=item["display_name"],
                        system_prompt=item["system_prompt"],
                        is_default=int(item["is_default"]),
                        enabled=1,
                    )
                )
            await session.commit()
        log.info("[Persona] 人设库为空，已从静态定义 seed")
        await self.get_all(force_refresh=True)

    # ---------- 后台写入 ----------

    async def save_persona(
        self,
        name: str,
        system_prompt: str,
        display_name: Optional[str] = None,
        is_default: Optional[bool] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """创建/更新人设并刷新缓存。设为默认时清除其他人的 is_default。"""
        from sqlalchemy import update

        from src.database.database import AsyncSessionLocal
        from src.database.models import BotPersona

        if not system_prompt.strip():
            raise ValueError("人设正文不能为空")
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(BotPersona).where(BotPersona.name == name)
                )
            ).scalars().first()
            if row is None:
                row = BotPersona(
                    name=name,
                    display_name=display_name or name,
                    system_prompt=system_prompt,
                    is_default=int(is_default) if is_default is not None else 0,
                    enabled=int(enabled) if enabled is not None else 1,
                )
                session.add(row)
            else:
                if display_name:
                    row.display_name = display_name
                row.system_prompt = system_prompt
                if enabled is not None:
                    row.enabled = int(enabled)
                if is_default:
                    row.is_default = 1
            if is_default:
                await session.execute(
                    update(BotPersona)
                    .where(BotPersona.name != name)
                    .values(is_default=0)
                )
            # session 关闭前提取返回值（避免 DetachedInstanceError）
            result = {
                "name": name,
                "display_name": row.display_name,
                "is_default": bool(row.is_default),
                "enabled": bool(row.enabled),
            }
            await session.commit()

        await self.get_all(force_refresh=True)
        log.info(f"[Persona] 人设已保存: {name}")
        return result


# 模块级单例
persona_service = PersonaService()
