# -*- coding: utf-8 -*-
"""教程知识库管理服务：UGC 教程的增删改查与向量索引触发。

教程 UGC 管理入口，配合 /知识库 命令使用。
"""
from typing import List, Dict, Any
import logging
import asyncio

from sqlalchemy import text
from src.database.database import AsyncSessionLocal
from src.chat.features.tutorial_search.services.tutorial_rag_service import (
    tutorial_rag_service,
)

log = logging.getLogger(__name__)


class TutorialManageService:
    """教程知识库的 UGC 管理服务"""

    async def get_tutorials_by_author(self, author_id: int) -> List[Dict[str, Any]]:
        """根据作者ID从数据库获取教程列表"""
        query = text("""
            SELECT id, title, created_at
            FROM tutorials.tutorial_documents
            WHERE author_id = :author_id
            ORDER BY created_at DESC;
        """)
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(query, {"author_id": str(author_id)})
                tutorials = result.mappings().all()
                return [dict(row) for row in tutorials]
        except Exception as e:
            log.error(f"为作者 {author_id} 获取教程时出错: {e}", exc_info=True)
            return []

    async def add_tutorial(
        self,
        title: str,
        description: str,
        author_id: int,
        author_name: str,
        thread_id: int,
    ) -> bool:
        """向数据库添加一个新的教程文档，并触发后台RAG处理。"""
        query = text(
            """
            INSERT INTO tutorials.tutorial_documents
            (title, original_content, author_id, author, thread_id)
            VALUES (:title, :description, :author_id, :author_name, :thread_id)
            RETURNING id;
        """
        )
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    query,
                    {
                        "title": title,
                        "description": description,
                        "author_id": str(author_id),
                        "author_name": author_name,
                        "thread_id": str(thread_id),
                    },
                )
                await session.commit()
                new_document_id = result.scalar_one_or_none()

                if new_document_id:
                    log.info(
                        f"教程 '{title}' 已成功添加到数据库，ID: {new_document_id}。准备进行RAG处理。"
                    )
                    asyncio.create_task(
                        tutorial_rag_service.process_tutorial_document(new_document_id)
                    )
                    return True
                else:
                    log.error(f"未能获取新添加教程 '{title}' 的ID。")
                    return False
        except Exception as e:
            log.error(f"添加教程 '{title}' 时出错: {e}", exc_info=True)
            return False

    async def delete_tutorial(self, tutorial_id: int, author_id: int) -> bool:
        """删除一个教程，包括其数据库记录和向量存储。"""
        log.info(f"用户 {author_id} 正在尝试删除教程 ID: {tutorial_id}")
        try:
            async with AsyncSessionLocal.begin() as session:
                log.info(f"正在从向量存储中删除教程 ID: {tutorial_id} 的向量...")
                delete_success = (
                    await tutorial_rag_service.delete_vectors_by_document_id(
                        tutorial_id, session=session
                    )
                )
                if not delete_success:
                    log.error(
                        f"从向量存储中删除教程 ID: {tutorial_id} 失败。正在回滚事务。"
                    )
                    return False
                log.info(f"成功从向量存储中删除教程 ID: {tutorial_id} 的向量。")

                query = text(
                    """
                    DELETE FROM tutorials.tutorial_documents
                    WHERE id = :tutorial_id AND author_id = :author_id
                    RETURNING id;
                """
                )
                result = await session.execute(
                    query,
                    {
                        "tutorial_id": tutorial_id,
                        "author_id": str(author_id),
                    },
                )
                deleted_id = result.scalar_one_or_none()

                if deleted_id is None:
                    log.warning(
                        f"删除教程失败：教程 ID: {tutorial_id} 不存在，或用户 {author_id} 不是作者。正在回滚。"
                    )
                    return False

            log.info(f"已成功从主数据库中删除教程 ID: {deleted_id}。事务已提交。")
            return True

        except Exception as e:
            log.error(
                f"删除教程 ID: {tutorial_id} 的过程中发生意外错误: {e}",
                exc_info=True,
            )
            return False

    async def get_tutorial_by_id(self, tutorial_id: int) -> Dict[str, Any] | None:
        """根据ID获取单个教程的完整内容。"""
        query = text("""
            SELECT id, title, original_content as description
            FROM tutorials.tutorial_documents
            WHERE id = :tutorial_id;
        """)
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(query, {"tutorial_id": tutorial_id})
                tutorial = result.mappings().one_or_none()
                return dict(tutorial) if tutorial else None
        except Exception as e:
            log.error(f"获取教程ID {tutorial_id} 时出错: {e}", exc_info=True)
            return None

    async def update_tutorial(
        self, tutorial_id: int, title: str, description: str, author_id: int
    ) -> bool:
        """更新一个教程，包括其数据库记录和向量存储。"""
        log.info(f"用户 {author_id} 正在尝试更新教程 ID: {tutorial_id}")
        try:
            async with AsyncSessionLocal.begin() as session:
                # 1. 删除旧的向量
                log.info(f"正在为教程更新删除旧向量 (ID: {tutorial_id})...")
                delete_success = (
                    await tutorial_rag_service.delete_vectors_by_document_id(
                        tutorial_id, session=session
                    )
                )
                if not delete_success:
                    log.error(f"为教程 {tutorial_id} 删除旧向量失败。正在回滚。")
                    return False
                log.info(f"成功删除教程 {tutorial_id} 的旧向量。")

                # 2. 更新主数据库中的教程记录
                update_query = text(
                    """
                    UPDATE tutorials.tutorial_documents
                    SET title = :title, original_content = :description, updated_at = NOW()
                    WHERE id = :tutorial_id AND author_id = :author_id
                    RETURNING id;
                """
                )
                result = await session.execute(
                    update_query,
                    {
                        "title": title,
                        "description": description,
                        "tutorial_id": tutorial_id,
                        "author_id": str(author_id),
                    },
                )
                updated_id = result.scalar_one_or_none()

                if updated_id is None:
                    log.warning(
                        f"更新教程失败：教程 ID: {tutorial_id} 不存在，或用户 {author_id} 不是作者。正在回滚。"
                    )
                    return False

            log.info(f"成功在数据库中更新教程 ID: {updated_id}。")

            # 3. 为更新后的内容重新创建向量（在事务外异步执行）
            log.info(f"正在为更新后的教程 {updated_id} 创建后台RAG处理任务...")
            asyncio.create_task(
                tutorial_rag_service.process_tutorial_document(updated_id)
            )
            return True

        except Exception as e:
            log.error(
                f"更新教程 ID: {tutorial_id} 的过程中发生意外错误: {e}",
                exc_info=True,
            )
            return False


# 全局单例
tutorial_manage_service = TutorialManageService()
