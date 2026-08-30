# -*- coding: utf-8 -*-
"""
Web 问答演示服务。

不复用 chat_service（强耦合 discord.Message），直接驱动 ai_service：
检索上下文预注入 + 完整 search 工具 + 工具调用循环；AI 不可用时降级为仅返回检索结果。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from src.config import BOT_NAME, BOT_SELF_INTRODUCTION, COMMUNITY_NAME
from src.chat.services.ai.providers.base import GenerationConfig

log = logging.getLogger(__name__)

TOOL_ITERATIONS = 3

SYSTEM_PROMPT = """你是 {bot_name}，{community_name} 的 AI 知识库助手。{self_intro}

当前运行在 Web 管理控制台的问答演示区，调用方是管理员。
回答规则：
- 优先依据下方【资料】回答；引用资料时在句末标注编号，如 [资料1]。
- 需要补充信息时可调用 search 工具检索知识库；scope 只能使用 tutorial / community_settings / forum。
- channel（服务器消息历史）与 memory（个人记忆）检索在 Web 环境不可用，不要调用这两个 scope。
- 资料中没有答案时如实说明，不要编造。"""

KNOWLEDGE_BLOCK = """
【资料】
{materials}
"""


def _detect_provider_type() -> str:
    """探测默认模型的 Provider 类型，决定工具下发格式（gemini / openai 兼容）"""
    try:
        from src.chat.services.ai.service import ai_service

        model_id = ai_service._get_default_model()
        model_name, explicit = ai_service.parse_model_id(model_id)
        provider = ai_service.get_provider_for_model(model_name, explicit)
        return (
            "gemini_official"
            if provider is not None and provider.__class__.__name__ == "GeminiProvider"
            else ""
        )
    except Exception:
        return ""


class WebChatService:
    async def generate_reply(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        scope: str = "all",
    ) -> Dict[str, Any]:
        started = time.perf_counter()

        # 1. 预检索：注入上下文 + 作为降级时的 citations
        from src.web.services.web_search_service import web_search_service

        search_data = await web_search_service.search(
            query=message, scope=scope, top_k=5
        )
        citations = search_data["results"]
        materials = "\n\n".join(
            f"[资料{i}]《{c['title']}》：{c['chunk_text'][:500]}"
            for i, c in enumerate(citations, start=1)
        ) or "（未检索到相关资料）"

        system = SYSTEM_PROMPT.format(
            bot_name=BOT_NAME,
            community_name=COMMUNITY_NAME,
            self_intro=BOT_SELF_INTRODUCTION,
        ) + KNOWLEDGE_BLOCK.format(materials=materials)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for item in history or []:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})

        # 2. AI 可用性预检
        try:
            from src.chat.services.ai.service import ai_service

            available = bool(ai_service.get_available_models())
        except Exception:
            available = False
        if not available:
            return self._degraded(
                citations, "AI Provider 未配置或不可用", started
            )

        # 3. 工具装配与调用循环
        tool_trace: List[Dict[str, Any]] = []
        provider_type = _detect_provider_type()

        async def tool_executor(call, **kwargs):
            if isinstance(call, dict):
                name = call.get("name", "")
                args = call.get("arguments", {}) or {}
            else:
                name = getattr(call, "name", "")
                args = dict(call.args) if getattr(call, "args", None) else {}
            t0 = time.perf_counter()
            part = await ai_service.tool_service.execute_tool_call(
                call,
                channel=None,
                user_id=0,
                user_name="Web管理员",
                fallback_query=message,
                channel_context=None,
            )
            func_resp = getattr(part, "function_response", None)
            captured = getattr(func_resp, "response", None) or {}
            summary = str(captured)[:200]
            tool_trace.append(
                {
                    "name": name,
                    "arguments": args,
                    "summary": summary,
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                }
            )
            return part

        try:
            tools = await ai_service.tool_service.get_dynamic_tools_for_context(
                None, provider_type=provider_type
            )
            result = await ai_service.generate_with_tools(
                messages=messages,
                config=GenerationConfig(
                    temperature=0.7, max_output_tokens=2000
                ),
                model=None,
                tools=tools,
                tool_executor=tool_executor,
                max_iterations=TOOL_ITERATIONS,
                fallback=True,
            )
            reply = (result.content or "").strip()
            if not reply:
                return self._degraded(citations, "AI 返回了空回复", started)
            return {
                "reply": reply,
                "model": result.model_used,
                "degraded": False,
                "degrade_reason": None,
                "citations": citations,
                "tool_trace": tool_trace,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as e:
            log.error(f"[Web 问答] 生成失败，降级为仅检索结果: {e}", exc_info=True)
            return self._degraded(citations, f"AI 调用失败: {e}", started)

    def _degraded(
        self, citations: List[Dict[str, Any]], reason: str, started: float
    ) -> Dict[str, Any]:
        return {
            "reply": None,
            "model": None,
            "degraded": True,
            "degrade_reason": reason,
            "citations": citations,
            "tool_trace": [],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


# 模块级单例
web_chat_service = WebChatService()
