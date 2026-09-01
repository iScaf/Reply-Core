# -*- coding: utf-8 -*-
"""知识库文档列表/详情路由 + 教程文件上传"""
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile

from src.chat.features.tutorial_search.services.document_chunking import (
    SUPPORTED_EXTENSIONS,
)
from src.web.deps import require_auth
from src.web.services.documents_service import documents_service

router = APIRouter(dependencies=[Depends(require_auth)])

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


@router.get("/documents")
async def list_documents(
    source: str = Query(pattern="^(tutorials|community_settings)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(None, max_length=200),
):
    return await documents_service.list_documents(
        source=source, page=page, page_size=page_size, q=q
    )


@router.get("/documents/{source}/{doc_id}")
async def get_document(source: str, doc_id: int):
    doc = await documents_service.get_document(source, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.post("/documents/tutorials/upload")
async def upload_tutorial(file: UploadFile = File(...)):
    """上传教程文档（md/pdf/docx/xlsx）→ 解析切块 → 向量化入库。"""
    raw, ext = await _read_upload(file)
    try:
        return await documents_service.upload_tutorial_file(file.filename, raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/documents/tutorials/{doc_id}")
async def update_tutorial(doc_id: int, body: dict = Body(...)):
    """编辑教程标题与正文（Markdown），保存后重新切块向量化。"""
    try:
        return await documents_service.update_tutorial(
            doc_id, body.get("title", ""), body.get("content", "")
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/documents/tutorials/{doc_id}/reupload")
async def reupload_tutorial(doc_id: int, file: UploadFile = File(...)):
    """为已有教程重新上传文件：替换正文并重新切块向量化，标题不变。"""
    raw, ext = await _read_upload(file)
    try:
        return await documents_service.reupload_tutorial(doc_id, file.filename, raw)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/documents/tutorials/{doc_id}")
async def delete_tutorial(doc_id: int):
    """删除教程及其全部分块。"""
    try:
        return await documents_service.delete_tutorial(doc_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    """上传文件通用校验：后缀白名单、大小、非空。返回 (内容, 后缀)。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext or '(无后缀)'}，"
            f"支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 10MB 大小限制")
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    return raw, ext
