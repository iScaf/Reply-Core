# -*- coding: utf-8 -*-
import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import database as db_mod
from src.database.models import TutorialDocument, KnowledgeChunk
from src.chat.services.embedding_factory import (
    get_embedding_service,
    get_embedding_column,
    is_vector_enabled,
)
from src.chat.features.tutorial_search.services.document_chunking import (
    chunk_markdown,
)
from sqlalchemy.future import select

log = logging.getLogger(__name__)


class TutorialRAGService:
    """
    负责处理作者提交教程后的RAG流程。

    文本来源有两类：
    - Discord 模态框提交的短教程：整篇为一块，无父子结构（与旧行为一致）
    - Web 后台上传的文件：统一 Markdown 后按标题切分（面包屑）+
      递归细切，长节拆"节级父块（无向量）+ 子块（有向量）"，
      检索命中子块后回取父块（small-to-big）。
    """

    def __init__(self):
        """初始化服务"""
        log.info("TutorialRAGService 已初始化（支持上传文档分块 small-to-big）")

    async def process_tutorial_document(self, document_id: int):
        """
        为教程文档生成块并写入 knowledge_chunks。

        Returns:
            dict: {"chunk_count": 可检索块数, "parent_count": 父块数}；失败返回 None。
        """
        log.info(f"开始为文档 ID {document_id} 处理 RAG 流程...")
        try:
            # 延迟属性访问（而非模块级绑定）：Web 测试会替换 database 模块上的
            # AsyncSessionLocal 以隔离事件循环，此处须在调用时取最新值
            async with db_mod.AsyncSessionLocal() as session:
                # 1. 查询父文档
                doc_result = await session.execute(
                    select(TutorialDocument).where(TutorialDocument.id == document_id)
                )
                document = doc_result.scalar_one_or_none()

                if not document:
                    log.error(f"无法找到 ID 为 {document_id} 的教程文档。")
                    return None

                content = document.original_content
                if not content.strip():
                    log.warning(f"文档 {document_id} 的内容为空。")
                    return None

                # 2. 检查向量模式是否启用（禁用时仍写文本块，保留 BM25 检索能力）
                vector_enabled = is_vector_enabled()
                embedding_service = (
                    await get_embedding_service() if vector_enabled else None
                )
                embedding_col = await get_embedding_column() if vector_enabled else None

                # 3. 切块：original_content 为 Markdown/纯文本（文件解析在
                #    上传时已完成）。短文档产出一个单块（无父子，与旧行为
                #    一致），长文档产出父子结构。
                result = chunk_markdown(
                    content,
                    source_name=document.title or f"document_{document_id}",
                )
                # 文档原始内容可能本就是 Markdown（Discord 提交/上传的 md），
                # 以 .md 路径直接进入标题切分管线
                total_children = 0
                total_parents = 0
                order = 0

                for block in result.blocks:
                    parent_id = None
                    if block.parent_text:
                        # 父块：存节全文，不建向量（检索后回取用）
                        parent = KnowledgeChunk(
                            document_id=document.id,
                            chunk_text=block.parent_text,
                            chunk_order=order,
                            section_path=block.path or None,
                        )
                        session.add(parent)
                        await session.flush()
                        parent_id = parent.id
                        order += 1
                        total_parents += 1

                    for child_text in block.children:
                        embedding = None
                        if embedding_service:
                            # 子块向量携带面包屑前缀，注入章节语境
                            embed_text = (
                                f"{block.path}\n{child_text}"
                                if block.path
                                else child_text
                            )
                            embedding = await embedding_service.generate_embedding(
                                text=embed_text, task_type="retrieval_document"
                            )
                            if not embedding:
                                log.warning(
                                    f"文档 {document_id} 的一个子块生成嵌入失败，"
                                    f"仅保留文本（BM25 仍可检索）。"
                                )
                        session.add(
                            KnowledgeChunk(
                                document_id=document.id,
                                chunk_text=child_text,
                                chunk_order=order,
                                parent_id=parent_id,
                                section_path=block.path or None,
                                **(
                                    {embedding_col: embedding}
                                    if embedding_col and embedding
                                    else {}
                                ),
                            )
                        )
                        order += 1
                        total_children += 1

                await session.commit()
                log.info(
                    f"文档 ID {document_id} 处理完成："
                    f"{total_parents} 个父块 + {total_children} 个可检索块。"
                )
                return {
                    "chunk_count": total_children,
                    "parent_count": total_parents,
                }

        except Exception as e:
            log.error(
                f"处理教程文档 {document_id} 的 RAG 流程时发生严重错误: {e}",
                exc_info=True,
            )
            return None

    # search 功能将由现有的 tutorial_search_service 统一处理，
    # 以保持查询入口的一致性。该服务仅负责数据的索引。

    async def delete_vectors_by_document_id(
        self, document_id: int, session: AsyncSession
    ) -> bool:
        """
        从 knowledge_chunks 表中删除与特定教程文档关联的所有向量记录。
        这个操作应该在一个更大的事务中被调用。
        """
        log.info(f"准备从数据库中删除文档 ID {document_id} 关联的向量...")
        try:
            # 构建删除语句
            stmt = delete(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document_id
            )
            # 直接在传入的 session 上执行删除操作，以保持事务的原子性
            await session.execute(stmt)
            log.info(f"已成功为文档 ID {document_id} 提交向量删除请求。")
            return True
        except Exception as e:
            log.error(
                f"在删除文档 ID {document_id} 的向量时发生错误: {e}",
                exc_info=True,
            )
            return False


# 创建服务的单例
tutorial_rag_service = TutorialRAGService()
