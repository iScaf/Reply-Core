# -*- coding: utf-8 -*-
"""Discord 用户信息查询服务（member_profiles / 记忆笔记 / 对话块）"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select

from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile, UserMemoryNote

log = logging.getLogger(__name__)


class UsersService:
    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        """分页列出 Discord 用户档案，支持按 ID/昵称/档案/摘要模糊搜索。"""
        offset = (page - 1) * page_size
        async with AsyncSessionLocal() as session:
            base = select(CommunityMemberProfile)
            if q:
                like = f"%{q}%"
                base = base.where(
                    or_(
                        CommunityMemberProfile.discord_id.ilike(like),
                        CommunityMemberProfile.title.ilike(like),
                        CommunityMemberProfile.full_text.ilike(like),
                        CommunityMemberProfile.personal_summary.ilike(like),
                    )
                )
            total = (
                await session.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await session.execute(
                    base.order_by(
                        CommunityMemberProfile.personal_message_count.desc(),
                        CommunityMemberProfile.id.asc(),
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars().all()

        items = [
            {
                "id": p.id,
                "discord_id": p.discord_id,
                "title": p.title,
                "personal_summary": p.personal_summary,
                "personal_message_count": p.personal_message_count,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in rows
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_user(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """用户详情：档案 + 记忆笔记 + 最近对话块。"""
        async with AsyncSessionLocal() as session:
            profile = (
                await session.execute(
                    select(CommunityMemberProfile).where(
                        CommunityMemberProfile.id == profile_id
                    )
                )
            ).scalars().first()
            if not profile:
                return None

            notes = (
                await session.execute(
                    select(UserMemoryNote)
                    .where(UserMemoryNote.user_id == profile.discord_id)
                    .order_by(UserMemoryNote.updated_at.desc())
                    .limit(20)
                )
            ).scalars().all()

            # 最近对话块（原生 SQL：按 start_time 倒序取 5 块）
            from sqlalchemy import text

            blocks = (
                await session.execute(
                    text(
                        "SELECT id, conversation_text, start_time, end_time, "
                        "message_count FROM conversation.conversation_blocks "
                        "WHERE discord_id = :did ORDER BY start_time DESC LIMIT 5"
                    ),
                    {"did": profile.discord_id or ""},
                )
            ).mappings().all()

        return {
            "id": profile.id,
            "discord_id": profile.discord_id,
            "title": profile.title,
            "full_text": profile.full_text,
            "personal_summary": profile.personal_summary,
            "personal_message_count": profile.personal_message_count,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "memory_notes": [
                {
                    "category": n.category,
                    "content": n.content,
                    "updated_at": n.updated_at.isoformat() if n.updated_at else None,
                }
                for n in notes
            ],
            "recent_blocks": [
                {
                    "id": b["id"],
                    "conversation_text": b["conversation_text"],
                    "start_time": (
                        b["start_time"].isoformat() if b["start_time"] else None
                    ),
                    "message_count": b["message_count"],
                }
                for b in blocks
            ],
        }

    async def search_user_chats(
        self, profile_id: int, q: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """在指定用户的对话记忆块中按关键词搜索。"""
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            profile = (
                await session.execute(
                    select(CommunityMemberProfile.discord_id).where(
                        CommunityMemberProfile.id == profile_id
                    )
                )
            ).scalar_one_or_none()
            if not profile:
                return []
            rows = (
                await session.execute(
                    text(
                        "SELECT id, conversation_text, start_time, message_count "
                        "FROM conversation.conversation_blocks "
                        "WHERE discord_id = :did AND conversation_text ILIKE :kw "
                        "ORDER BY start_time DESC LIMIT :lim"
                    ),
                    {"did": profile, "kw": f"%{q}%", "lim": limit},
                )
            ).mappings().all()

        return [
            {
                "id": r["id"],
                "conversation_text": r["conversation_text"],
                "start_time": (
                    r["start_time"].isoformat() if r["start_time"] else None
                ),
                "message_count": r["message_count"],
            }
            for r in rows
        ]


# 模块级单例
users_service = UsersService()
