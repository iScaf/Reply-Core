# -*- coding: utf-8 -*-
"""Web 后台 API 请求/响应模型"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    token: str


class RejectRequest(BaseModel):
    reason: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    scope: Literal["tutorials", "community_settings", "all"] = "all"
    top_k: int = Field(default=10, ge=1, le=50)


class ChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: List[ChatHistoryItem] = Field(default_factory=list, max_length=20)
    scope: Literal["tutorials", "community_settings", "all"] = "all"


class SearchResultItem(BaseModel):
    source: str
    chunk_id: int
    document_id: int
    title: str
    chunk_text: str
    semantic_rank: Optional[int] = None
    keyword_rank: Optional[int] = None
    rrf_score: float = 0.0
    vec_distance: Optional[float] = None
    bm25_score: Optional[float] = None


class ToolTraceItem(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    elapsed_ms: int = 0


class ChatResponse(BaseModel):
    reply: Optional[str]
    model: Optional[str] = None
    degraded: bool = False
    degrade_reason: Optional[str] = None
    citations: List[SearchResultItem] = Field(default_factory=list)
    tool_trace: List[ToolTraceItem] = Field(default_factory=list)
    elapsed_ms: int = 0


class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    channels: Dict[str, bool]
    vector_mode: str
    embedding_column: str
    elapsed_ms: int
