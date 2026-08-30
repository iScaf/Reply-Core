# -*- coding: utf-8 -*-
"""
Web 端审核操作。

不实例化 ReviewService（其构造依赖 bot 且启动后台循环），此处用
AsyncSessionLocal 直接复制 approve/reject 的核心 ORM 逻辑，
Discord 消息编辑副作用天然跳过，向量化副作用保留。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from src.database.database import AsyncSessionLocal
from src.database.models import (
    CommunitySettingDocument,
    CommunitySettingPendingEntry,
)

log = logging.getLogger(__name__)

# 持有后台向量化任务的引用，防止被垃圾回收
_background_tasks: set = set()


def _parse_data(entry: CommunitySettingPendingEntry) -> Dict[str, Any]:
    data = entry.data_json
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    return data if isinstance(data, dict) else {}


class WebReviewService:
    async def list_pending(
        self, page: int = 1, page_size: int = 20
    ) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(CommunitySettingPendingEntry)
                .where(CommunitySettingPendingEntry.status == "pending")
                .order_by(CommunitySettingPendingEntry.created_at.desc())
            )
            total = len((await session.execute(select(stmt.subquery()))).all())
            rows = (
                (
                    await session.execute(
                        stmt.offset((page - 1) * page_size).limit(page_size)
                    )
                )
                .scalars()
                .all()
            )

        items: List[Dict[str, Any]] = []
        for entry in rows:
            data = _parse_data(entry)
            items.append(
                {
                    "id": entry.id,
                    "entry_type": entry.entry_type,
                    "title": data.get("title") or "（无标题）",
                    "content_text": data.get("content_text") or "",
                    "category_name": data.get("category_name") or "通用知识",
                    "proposer_id": entry.proposer_id,
                    "created_at": entry.created_at.isoformat()
                    if entry.created_at
                    else None,
                    "expires_at": entry.expires_at.isoformat()
                    if entry.expires_at
                    else None,
                }
            )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def approve(self, pending_id: int) -> Optional[Dict[str, Any]]:
        """
        批准条目：写入社区设定主表并触发增量向量化。

        Returns:
            {"document_id": ...}；条目不存在返回 None；已处理过抛 ConflictError。
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                entry = (
                    await session.execute(
                        select(CommunitySettingPendingEntry)
                        .where(CommunitySettingPendingEntry.id == pending_id)
                        .with_for_update()
                    )
                ).scalars().first()
                if not entry:
                    return None
                if entry.status != "pending":
                    raise ConflictError(f"条目 #{pending_id} 已处理（{entry.status}）")

                data = _parse_data(entry)
                title = data.get("title", "无标题")
                content_text = data.get("content_text", "")
                category_name = data.get("category_name", "通用知识")
                document = CommunitySettingDocument(
                    external_id=f"pending_{pending_id}",
                    title=title,
                    full_text=f"标题: {title}\n类别: {category_name}\n内容: {content_text}",
                    source_metadata={
                        "category": category_name,
                        "source": "web_console",
                        "contributor_id": str(entry.proposer_id),
                        "original_submission": data,
                    },
                )
                session.add(document)
                await session.flush()
                new_doc_id = document.id

                result = await session.execute(
                    update(CommunitySettingPendingEntry)
                    .where(
                        CommunitySettingPendingEntry.id == pending_id,
                        CommunitySettingPendingEntry.status == "pending",
                    )
                    .values(status="approved")
                )
                if result.rowcount == 0:
                    raise ConflictError(f"条目 #{pending_id} 已处理")

        # 触发增量向量化（与 Discord 端行为一致：传 documents.id）
        try:
            from src.chat.features.community_settings.services.incremental_rag_service import (
                incremental_rag_service,
            )

            task = asyncio.create_task(
                incremental_rag_service.process_setting_entry(new_doc_id)
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        except Exception as e:
            log.warning(f"条目 #{pending_id} 已收录（文档 {new_doc_id}），向量化任务启动失败: {e}")

        log.info(f"[Web 审核] 已批准 #{pending_id} → 社区设定文档 {new_doc_id}")
        return {"document_id": new_doc_id}

    async def reject(self, pending_id: int, reason: str) -> Optional[Dict[str, Any]]:
        """驳回条目：status='rejected'，理由写入 data_json.reject_reason"""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                entry = (
                    await session.execute(
                        select(CommunitySettingPendingEntry)
                        .where(CommunitySettingPendingEntry.id == pending_id)
                        .with_for_update()
                    )
                ).scalars().first()
                if not entry:
                    return None
                if entry.status != "pending":
                    raise ConflictError(f"条目 #{pending_id} 已处理（{entry.status}）")

                data = _parse_data(entry)
                data["reject_reason"] = reason or "Web 管理员驳回"
                result = await session.execute(
                    update(CommunitySettingPendingEntry)
                    .where(
                        CommunitySettingPendingEntry.id == pending_id,
                        CommunitySettingPendingEntry.status == "pending",
                    )
                    .values(status="rejected", data_json=data)
                )
                if result.rowcount == 0:
                    raise ConflictError(f"条目 #{pending_id} 已处理")

        log.info(f"[Web 审核] 已驳回 #{pending_id}，理由: {reason}")
        return {"ok": True}


class ConflictError(Exception):
    """条目已被处理（并发冲突）"""


# 模块级单例
web_review_service = WebReviewService()
