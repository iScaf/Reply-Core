# -*- coding: utf-8 -*-
"""教程分块入库与节级父块回取的集成测试（直连真实 PostgreSQL）。

覆盖 small-to-big 关键约定：
- 长文档 → 父块（无向量）+ 子块（parent_id 指向父块）
- 短文档 → 单块（与不分块时代行为一致）
- 检索回取：命中子块返回父块节级全文（带面包屑前缀），命中单块返回自身
"""
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.chat.features.tutorial_search.services.tutorial_rag_service import (
    tutorial_rag_service,
)
from src.chat.features.tutorial_search.services.tutorial_search_service import (
    tutorial_search_service,
)
from src.database.database import AsyncSessionLocal
from src.database.models import KnowledgeChunk, TutorialDocument

_LONG_MD = (
    "# 部署手册\n\n## 第二章 环境准备\n\n"
    + ("这是一段很长的正文，用于确保章节超过切块阈值并拆分为父子结构。" * 36)
    + "\n\n### 2.1 安装依赖\n\n"
    + ("安装依赖章节的详细说明内容，同样需要足够长才能触发细切分逻辑。" * 36)
    + "\n"
)

_SHORT_DOC = "这是一篇很短的教程，应该整篇作为一个块。"


@pytest_asyncio.fixture
async def clean_tutorials():
    """快照精确清理：只删除本文件测试创建的文档与 chunks。

    tutorials 是知识库主数据，测试严禁整表清空——
    fixture 开始前记录已有文档 id，结束后仅删除新增部分。
    """
    async with AsyncSessionLocal() as session:
        before = set(
            (await session.execute(select(TutorialDocument.id))).scalars().all()
        )
    yield
    async with AsyncSessionLocal() as session:
        after = set(
            (await session.execute(select(TutorialDocument.id))).scalars().all()
        )
        created = after - before
        if not created:
            return
        await session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.document_id.in_(created)
            )
        )
        await session.execute(
            delete(TutorialDocument).where(TutorialDocument.id.in_(created))
        )
        await session.commit()


async def _insert_document(title: str, content: str) -> int:
    async with AsyncSessionLocal() as session:
        doc = TutorialDocument(
            title=title, author_id="test", original_content=content
        )
        session.add(doc)
        await session.flush()
        doc_id = doc.id
        await session.commit()
        return doc_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_tutorials")
async def test_long_document_parent_child_chunks():
    doc_id = await _insert_document("长文档", _LONG_MD)
    stats = await tutorial_rag_service.process_tutorial_document(doc_id)

    assert stats is not None
    assert stats["chunk_count"] >= 2, "长文档应产出多个可检索子块"
    assert stats["parent_count"] >= 1, "长文档应产出节级父块"

    async with AsyncSessionLocal() as session:
        chunks = (
            await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc_id)
                .order_by(KnowledgeChunk.chunk_order)
            )
        ).scalars().all()

    parents = [c for c in chunks if c.parent_id is None and c.chunk_text]
    children = [c for c in chunks if c.parent_id is not None]
    assert len(children) == stats["chunk_count"]
    assert parents, "存在父块或单块"
    for child in children:
        assert child.section_path, "子块应携带面包屑"
        # 父块必须真实存在且为节级全文
        parent = next(p for p in chunks if p.id == child.parent_id)
        assert child.chunk_text in parent.chunk_text


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_tutorials")
async def test_short_document_single_chunk():
    doc_id = await _insert_document("短文档", _SHORT_DOC)
    stats = await tutorial_rag_service.process_tutorial_document(doc_id)

    assert stats == {"chunk_count": 1, "parent_count": 0}

    async with AsyncSessionLocal() as session:
        chunks = (
            await session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
            )
        ).scalars().all()
    assert len(chunks) == 1
    assert chunks[0].parent_id is None
    assert chunks[0].chunk_text == _SHORT_DOC


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_tutorials")
async def test_parent_docs_fetch_returns_section_level_context():
    """命中子块 → 返回父块节级全文（带面包屑）；命中单块 → 返回自身。"""
    long_id = await _insert_document("长文档", _LONG_MD)
    short_id = await _insert_document("短文档", _SHORT_DOC)
    await tutorial_rag_service.process_tutorial_document(long_id)
    await tutorial_rag_service.process_tutorial_document(short_id)

    async with AsyncSessionLocal() as session:
        long_child = (
            await session.execute(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == long_id,
                    KnowledgeChunk.parent_id.isnot(None),
                )
            )
        ).scalars().first()
        short_chunk = (
            await session.execute(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == short_id
                )
            )
        ).scalars().first()

        # 子块命中 → 节级父块全文 + 面包屑前缀
        contexts = await tutorial_search_service._get_parent_docs_by_chunk_ids(
            session, [long_child.id]
        )
        assert len(contexts) == 1
        assert contexts[0]["title"] == "长文档"
        assert contexts[0]["content"].startswith("【"), "应拼接面包屑前缀"
        assert long_child.chunk_text in contexts[0]["content"]

        # 同一父块的多个子块命中 → 合并为一个上下文条目
        sibling_ids = [
            c.id
            for c in (
                await session.execute(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.parent_id == long_child.parent_id
                    )
                )
            ).scalars().all()
        ]
        if len(sibling_ids) >= 2:
            merged = await tutorial_search_service._get_parent_docs_by_chunk_ids(
                session, sibling_ids
            )
            assert len(merged) == 1

        # 单块命中 → 自身内容，无面包屑前缀
        single = await tutorial_search_service._get_parent_docs_by_chunk_ids(
            session, [short_chunk.id]
        )
        assert single[0]["content"] == _SHORT_DOC
