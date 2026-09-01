# -*- coding: utf-8 -*-
"""教程上传端点端到端测试：multipart 上传 → 解析 → 切块 → 入库。"""
import io

import pytest
from sqlalchemy import text

from tests.web.conftest import run_db


def _make_docx_bytes() -> bytes:
    import docx

    document = docx.Document()
    document.add_heading("上传部署手册", level=1)
    document.add_heading("第二章 环境准备", level=2)
    body = "这是一段很长的正文，用于确保章节超过切块阈值并拆分为父子结构。" * 36
    document.add_paragraph(body)
    document.add_heading("第三章 验证", level=2)
    document.add_paragraph("短章节内容。")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _cleanup_tutorials() -> None:
    def _do(factory):
        async def inner(session):
            await session.execute(
                text(
                    "TRUNCATE TABLE tutorials.knowledge_chunks, "
                    "tutorials.tutorial_documents RESTART IDENTITY"
                )
            )

        async def flow():
            async with factory() as session:
                async with session.begin():
                    await inner(session)

        return flow()

    run_db(_do)


@pytest.fixture
def clean_tutorials_web():
    _cleanup_tutorials()
    yield
    _cleanup_tutorials()


@pytest.mark.usefixtures("clean_tutorials_web")
def test_upload_docx_end_to_end(web_client):
    resp = web_client.post(
        "/api/documents/tutorials/upload",
        files={"file": ("部署手册.docx", _make_docx_bytes(), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "部署手册"
    assert data["chunk_count"] >= 1
    assert data["doc_id"] > 0

    # 详情接口能看到切块结果
    detail = web_client.get(f"/api/documents/tutorials/{data['doc_id']}")
    assert detail.status_code == 200
    chunks = detail.json()["chunks"]
    assert len(chunks) == data["chunk_count"] + data["parent_count"]


@pytest.mark.usefixtures("clean_tutorials_web")
def test_upload_rejects_unsupported_format(web_client):
    resp = web_client.post(
        "/api/documents/tutorials/upload",
        files={"file": ("bad.exe", b"binary", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "不支持" in resp.json()["detail"]


@pytest.mark.usefixtures("clean_tutorials_web")
def test_upload_rejects_oversize(web_client):
    big = b"x" * (10 * 1024 * 1024 + 1)
    resp = web_client.post(
        "/api/documents/tutorials/upload",
        files={"file": ("big.md", big, "text/markdown")},
    )
    assert resp.status_code == 400
