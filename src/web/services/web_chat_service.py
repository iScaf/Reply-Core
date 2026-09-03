# -*- coding: utf-8 -*-
"""
Web 问答演示服务。

不复用 chat_service（强耦合 discord.Message），直接驱动 ai_service：
检索上下文预注入 + 完整 search 工具 + 工具调用循环；AI 不可用时降级为仅返回检索结果。

generate_reply_stream 为流式版本：以事件生成器形式逐段产出
思维链 / 工具调用 / 正文增量，供 SSE 端点推送给前端做 DeepSeek 风格展示。
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.config import BOT_NAME, BOT_SELF_INTRODUCTION, COMMUNITY_NAME
from src.chat.services.ai.providers.base import GenerationConfig
from src.chat.services.ai.utils.tool_converter import ToolConverter
from src.chat.features.tools.tool_metadata import TOOL_METADATA

log = logging.getLogger(__name__)

TOOL_ITERATIONS = 10

# 持久化到 bot.global_settings 的键：Web 问答可选模型列表（首次拉取后缓存）
WEB_MODELS_SETTING_KEY = "web_chat_available_models"

# Web 端无 Discord 频道上下文，社区设定检索（community_settings scope）
# 需要 guild_id 才能执行；取 .env 中配置的开发服务器 ID
_GUILD_ID: Optional[int] = None
if os.getenv("GUILD_ID", "").isdigit():
    _GUILD_ID = int(os.getenv("GUILD_ID"))

SYSTEM_PROMPT = """你是 {bot_name}，{community_name} 的 AI 知识库助手。{self_intro}

当前运行在 Web 管理控制台的问答演示区，调用方是管理员。
回答规则：
- 优先依据下方【资料】回答；引用资料时在句末标注编号，如 [资料1]。
- 需要补充信息时可调用 search 工具检索知识库；scope 只能使用 tutorial / community_settings / forum。
- **实时信息**（天气、新闻、汇率、赛事比分等时效性内容）知识库里没有——必须调用 web_search 工具联网搜索，不要凭训练记忆回答，也不要说"我无法获取实时信息"。
- **塔罗占卜**：用户想占卜、抽牌、看运势时，调用 tarot_reading 工具，牌面会直接展示给用户，你负责结合牌意给出解读。
- **数据库查询**：管理员询问数据库内容（有哪些表、某张表的数据、统计数量等）时，调用 sql_query 工具。它是只读的（仅 SELECT/WITH/SHOW/EXPLAIN），可查 information_schema.columns / pg_catalog.pg_tables 了解表结构。涉及数据统计与分析时优先使用它，不要凭空编造数据。**不确定列名时不要猜**：先查该表的结构（`SELECT table_schema, column_name FROM information_schema.columns WHERE table_name = '表名'`），再写正式查询。例如用户记忆相关的表 `user.user_memory_notes` 中，用户标识列是 `user_id`（存 Discord ID 字符串）。
- channel（服务器消息历史）与 memory（个人记忆）检索在 Web 环境不可用，不要调用这两个 scope；gather_context / issue_user_warning 依赖 Discord 上下文，也不要调用。
- 资料中没有答案且联网搜索也无结果时，如实说明，不要编造。"""

KNOWLEDGE_BLOCK = """
【资料】
{materials}
"""


def _detect_provider_type(provider: Optional[Any] = None) -> str:
    """探测 Provider 类型，决定工具下发格式（gemini / openai 兼容）。

    不传 provider 时按默认模型路由；Web 问答指定模型时应传入对应 provider。
    """
    try:
        from src.chat.services.ai.service import ai_service

        if provider is None:
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
        model: Optional[str] = None,
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

        # 启用的 prompt 注入型技能（如 sql-query 速查表）拼入 system 尾部
        try:
            from src.web.services.skill_service import skill_service

            skill_block = skill_service.build_prompt_block()
            if skill_block:
                system += "\n\n" + skill_block
        except Exception as e:
            log.warning(f"[Web 问答] 技能注入失败（跳过）: {e}")

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
                guild_id=_GUILD_ID,
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
                model=model,
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

    # ------------------------------------------------------------------
    # 模型选择器：拉取 Provider 端点开放的模型并持久化，避免每次实时查询
    # ------------------------------------------------------------------

    async def get_model_options(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Web 问答可选模型列表。

        优先读取 bot.global_settings 中的持久化列表；缺失或 force_refresh 时
        实时拉取 Provider 端点（OpenAI 兼容 GET /models），成功后回写持久化。
        拉取失败时回退 ai_config 中注册的模型。
        """
        from sqlalchemy import select

        from src.database.database import AsyncSessionLocal
        from src.database.models import GlobalSetting

        from src.chat.services.ai.service import ai_service

        default_model = ai_service._get_default_model()

        persisted: Optional[List[str]] = None
        if not force_refresh:
            try:
                async with AsyncSessionLocal() as session:
                    raw = (
                        await session.execute(
                            select(GlobalSetting.value).where(
                                GlobalSetting.key == WEB_MODELS_SETTING_KEY
                            )
                        )
                    ).scalar_one_or_none()
                parsed = json.loads(raw) if raw else None
                if isinstance(parsed, list) and parsed:
                    persisted = [str(m) for m in parsed]
            except Exception as e:
                log.warning(f"[Web 问答] 读取持久化模型列表失败: {e}")

        models = persisted
        if not models:
            models = await self._fetch_models_from_provider()
            if models:
                try:
                    async with AsyncSessionLocal() as session:
                        await session.merge(
                            GlobalSetting(
                                key=WEB_MODELS_SETTING_KEY,
                                value=json.dumps(models, ensure_ascii=False),
                            )
                        )
                        await session.commit()
                except Exception as e:
                    log.warning(f"[Web 问答] 持久化模型列表失败: {e}")

        if not models:
            models = ai_service.get_available_models() or [default_model]

        # 默认模型置顶并去重
        options = list(dict.fromkeys([default_model] + models))
        return {"models": options, "default": default_model}

    @staticmethod
    async def _fetch_models_from_provider() -> List[str]:
        """实时拉取默认 Provider 端点的模型列表（OpenAI 兼容 GET /models）。"""
        try:
            from src.chat.services.ai.service import ai_service

            model_name, explicit = ai_service.parse_model_id(
                ai_service._get_default_model()
            )
            provider = ai_service.get_provider_for_model(model_name, explicit)
            if provider is None or not hasattr(provider, "list_models"):
                return []
            return await provider.list_models()
        except Exception as e:
            log.warning(f"[Web 问答] 实时拉取模型列表失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 流式版本：yield (event, data) 事件元组，SSE 端点负责序列化
    # ------------------------------------------------------------------

    async def generate_reply_stream(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        scope: str = "all",
        model: Optional[str] = None,
        persona: Optional[str] = None,
    ) -> AsyncGenerator[tuple, None]:
        """流式问答：先推预检索引用，再逐段推思维链 / 工具 / 正文增量。

        model: 管理员在 Web 选择器中指定的模型；None/未知时回退默认模型。
        persona: 管理员选择的人设（bot_persona.name，如 default/gentle/frank）；
                 提供时以其正文替换默认人设开场，工具规则段保留。

        事件协议（与前端约定）：
          citations    预检索资料（正文渲染引用徽章用）
          degraded     AI 不可用降级（终止）
          round_start  工具循环新一轮开始（前端折叠上一轮思维链）
          reasoning    思维链增量 {delta, round}
          tool_start   工具开始 {name, arguments}
          tool_end     工具结束 {name, summary, elapsed_ms}
          content      正文增量 {delta}
          final        收尾 {reply, model, citations, tool_trace, elapsed_ms}
          error        出错 {message}

        持久化约定：用户消息在流开始时落库；assistant 回复在成功、异常截断、
        客户端中断（CancelledError）、降级四条退出路径都会落库，保证历史完整。
        """
        started = time.perf_counter()

        # 0. 用户消息立即落库：客户端中途断开/生成失败时，问题也不丢失
        await self._save_user_message(message)

        # 1. 预检索：注入上下文 + citations 先行推送
        from src.web.services.web_search_service import web_search_service

        search_data = await web_search_service.search(
            query=message, scope=scope, top_k=5
        )
        citations = search_data["results"]
        materials = "\n\n".join(
            f"[资料{i}]《{c['title']}》：{c['chunk_text'][:500]}"
            for i, c in enumerate(citations, start=1)
        ) or "（未检索到相关资料）"
        yield ("citations", {"citations": citations})

        system = SYSTEM_PROMPT.format(
            bot_name=BOT_NAME,
            community_name=COMMUNITY_NAME,
            self_intro=BOT_SELF_INTRODUCTION,
        ) + KNOWLEDGE_BLOCK.format(materials=materials)

        # 启用的 prompt 注入型技能（如 sql-query 速查表）拼入 system 尾部
        try:
            from src.web.services.skill_service import skill_service

            skill_block = skill_service.build_prompt_block()
            if skill_block:
                system += "\n\n" + skill_block
        except Exception as e:
            log.warning(f"[Web 问答] 技能注入失败（跳过）: {e}")

        # 人设选择：选中的人设正文替换默认人设开场，回答规则段（工具引导）保留
        if persona:
            try:
                from src.chat.services.persona_service import persona_service

                items = await persona_service.get_all()
                match = next(
                    (
                        p
                        for p in items
                        if p["name"] == persona and p.get("enabled", True)
                    ),
                    None,
                )
                if match and "回答规则：" in system:
                    system = (
                        match["system_prompt"]
                        + "\n\n"
                        + system[system.index("回答规则："):]
                    )
            except Exception as e:
                log.warning(f"[Web 问答] 人设注入失败（使用默认人设）: {e}")

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for item in history or []:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})

        # 2. AI 可用性预检 + 按所选模型路由 Provider
        try:
            from src.chat.services.ai.service import ai_service

            available = bool(ai_service.get_available_models())
            default_id = ai_service._get_default_model()
            model_name, explicit_provider = ai_service.parse_model_id(
                model or default_id
            )
            provider = ai_service.get_provider_for_model(
                model_name, explicit_provider
            )
            if provider is None:
                # 所选模型未注册到 ai_config（如从端点实时拉取的开放模型）：
                # 交给默认 Provider 直调——下拉列表本就来自该端点
                provider = ai_service.get_provider(ai_service._default_provider)
        except Exception:
            available = False
            provider = None
            model_name = None
        if not available or provider is None:
            payload = self._degraded(
                citations, "AI Provider 未配置或不可用", started
            )
            await self._save_assistant_message(
                "（AI Provider 未配置或不可用，已降级为仅检索结果）", payload
            )
            yield ("degraded", payload)
            return
        if not hasattr(provider, "stream_chat"):
            payload = self._degraded(
                citations, "当前 Provider 不支持流式输出", started
            )
            await self._save_assistant_message(
                "（当前 Provider 不支持流式输出，已降级为仅检索结果）", payload
            )
            yield ("degraded", payload)
            return

        # 3. 工具装配与流式循环（工具下发格式跟随所选 Provider）
        from src.chat.services.ai.providers.base import GenerationConfig

        tool_trace: List[Dict[str, Any]] = []
        provider_type = _detect_provider_type(provider)

        try:
            tools = await ai_service.tool_service.get_dynamic_tools_for_context(
                None, provider_type=provider_type
            )
        except Exception as e:
            log.error(f"[Web 问答] 工具装配失败: {e}", exc_info=True)
            tools = None

        config = GenerationConfig(temperature=0.7, max_output_tokens=2000)
        conversation = list(messages)
        content_buf = ""
        reasoning_buf = ""
        model_used = None
        prompt_tokens = 0
        completion_tokens = 0

        try:
            for round_no in range(1, TOOL_ITERATIONS + 1):
                yield ("round_start", {"round": round_no})
                round_tool_calls = None
                round_reasoning = ""

                async for ev in provider.stream_chat(
                    conversation, config=config, tools=tools, model=model_name
                ):
                    ev_type = ev["type"]
                    if ev_type == "reasoning":
                        round_reasoning += ev["delta"]
                        yield ("reasoning", {**ev, "round": round_no})
                    elif ev_type == "content":
                        content_buf += ev["delta"]
                        yield ("content", ev)
                    elif ev_type == "usage":
                        # 逐轮累积（工具循环每轮请求都有 usage）
                        prompt_tokens += ev.get("prompt_tokens") or 0
                        completion_tokens += ev.get("completion_tokens") or 0
                        yield ("usage", ev)
                    elif ev_type == "tool_calls":
                        round_tool_calls = ev["tool_calls"]
                        break
                    elif ev_type == "finish":
                        break

                # 思维链持久化：多轮以空行分隔拼接（历史展示与现场一致）
                if round_reasoning.strip():
                    reasoning_buf += (
                        ("\n\n" if reasoning_buf.strip() else "")
                        + f"[第 {round_no} 轮]\n"
                        + round_reasoning.strip()
                    )

                if not round_tool_calls:
                    break  # 正文已流式输出完毕

                # 记录 assistant 工具调用消息并逐个执行
                conversation.append(
                    {
                        "role": "assistant",
                        "content": content_buf,
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(
                                        call["arguments"], ensure_ascii=False
                                    ),
                                },
                            }
                            for call in round_tool_calls
                        ],
                    }
                )
                for call in round_tool_calls:
                    name = call.get("name", "")
                    args = call.get("arguments", {}) or {}
                    # 工具显示名与描述（来自 tool_metadata 注册表）
                    meta = TOOL_METADATA.get(name, {})
                    display = meta.get("name", name)
                    description = meta.get("description", "")
                    yield (
                        "tool_start",
                        {
                            "name": name,
                            "display": display,
                            "description": description,
                            "arguments": args,
                        },
                    )
                    t0 = time.perf_counter()
                    image_b64 = None
                    try:
                        part = await ai_service.tool_service.execute_tool_call(
                            call,
                            channel=None,
                            user_id=0,
                            user_name="Web管理员",
                            fallback_query=message,
                            channel_context=None,
                            guild_id=_GUILD_ID,
                        )
                        func_resp = getattr(part, "function_response", None)
                        captured = getattr(func_resp, "response", None) or {}
                        is_error = isinstance(captured, dict) and "error" in captured
                    except Exception as e:
                        log.error(f"[Web 问答] 工具 {name} 执行失败: {e}")
                        captured = {"error": str(e)}
                        is_error = True

                    elapsed = int((time.perf_counter() - t0) * 1000)
                    # 塔罗等工具返回的 base64 图片：剥离出独立事件给前端渲染，
                    # 不进入对话历史（避免超长 base64 撑爆第二轮请求）。
                    # execute_tool_call 会把工具返回值包装成 {"result": {...}}，需兼容两层
                    holders = [
                        d
                        for d in (captured, captured.get("result"))
                        if isinstance(d, dict)
                    ]
                    for holder in holders:
                        if holder.get("image_base64"):
                            image_b64 = holder.pop("image_base64")
                            holder["note"] = (
                                "牌阵图片已直接展示给用户，请结合牌面与牌意给出解读。"
                            )
                            break
                    tool_message = ToolConverter.tool_result_to_openai_message(
                        tool_call_id=call["id"],
                        tool_name=call["name"],
                        result=captured,
                        is_error=is_error,
                    )
                    conversation.append(tool_message)
                    summary = str(captured)[:200]
                    if image_b64:
                        # cards 与 image_base64 同层（可能被包装在 result 内）
                        cards_holder = next(
                            (
                                d
                                for d in (captured, captured.get("result"))
                                if isinstance(d, dict) and "cards" in d
                            ),
                            {},
                        )
                        summary = (
                            f"已生成牌阵图片（{len(cards_holder.get('cards', []))} 张牌）"
                        )
                    tool_trace.append(
                        {
                            "name": name,
                            "display": display,
                            "description": description,
                            "arguments": args,
                            "summary": summary,
                            "elapsed_ms": elapsed,
                        }
                    )
                    if image_b64:
                        yield (
                            "image",
                            {
                                "data_url": f"data:image/png;base64,{image_b64}",
                                "name": name,
                            },
                        )
                    yield (
                        "tool_end",
                        {
                            "name": name,
                            "display": display,
                            "description": description,
                            "summary": summary,
                            "elapsed_ms": elapsed,
                        },
                    )
                # 多轮工具循环间正文缓冲重置：正文只在最后一轮流式产出
                content_buf = ""

            reply = content_buf.strip()
            if reply:
                # 表情占位符（含 :吃瓜: 等变体）统一转颜文字，与 Discord 端一致
                from src.chat.utils.prompt_utils import replace_emojis

                reply = replace_emojis(reply)
            if not reply:
                payload = self._degraded(citations, "AI 返回了空回复", started)
                await self._save_assistant_message(
                    "（AI 返回了空回复，已降级为仅检索结果）", payload
                )
                yield ("degraded", payload)
                return
            model_used = model_name or getattr(provider, "default_model", None)
            final_payload = {
                "reply": reply,
                "reasoning": reasoning_buf.strip() or None,
                "model": model_used,
                "citations": citations,
                "tool_trace": tool_trace,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "prompt_tokens": prompt_tokens or None,
                "completion_tokens": completion_tokens or None,
            }
            await self._save_assistant_message(reply, final_payload)
            yield ("final", final_payload)
        except asyncio.CancelledError:
            # 客户端中途断开（刷新/关闭页面/网络中断）会取消此生成器，
            # CancelledError 不是 Exception 子类，此前不会走到任何落库逻辑，
            # 导致整轮对话"消失"。这里把已生成的部分正文补录进历史。
            partial = content_buf.strip()
            if partial:
                payload = {
                    "reply": partial,
                    "reasoning": reasoning_buf.strip() or None,
                    "model": getattr(provider, "default_model", None),
                    "citations": citations,
                    "tool_trace": tool_trace,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "truncated": True,
                    "prompt_tokens": prompt_tokens or None,
                    "completion_tokens": completion_tokens or None,
                }
                try:
                    # shield：任务取消过程中保证落库完整执行
                    await asyncio.shield(self._save_assistant_message(partial, payload))
                except Exception as save_err:
                    log.warning(f"[Web 问答] 中断轮次落库失败: {save_err}")
            raise
        except Exception as e:
            log.error(f"[Web 问答] 流式生成失败: {e}", exc_info=True)
            if content_buf.strip():
                # 已有部分正文：收尾并注明截断
                final_payload = {
                    "reply": content_buf.strip(),
                    "reasoning": reasoning_buf.strip() or None,
                    "model": getattr(provider, "default_model", None),
                    "citations": citations,
                    "tool_trace": tool_trace,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "truncated": True,
                    "prompt_tokens": prompt_tokens or None,
                    "completion_tokens": completion_tokens or None,
                }
                await self._save_assistant_message(content_buf.strip(), final_payload)
                yield ("final", final_payload)
            else:
                yield ("error", {"message": f"AI 调用失败: {e}"})

    async def _save_user_message(self, message: str) -> None:
        """落库用户消息（流式开始时调用）。失败仅记日志，不阻塞问答。"""
        try:
            from src.database.database import AsyncSessionLocal
            from src.database.models import WebChatMessage

            async with AsyncSessionLocal() as session:
                session.add(WebChatMessage(role="user", content=message))
                await session.commit()
        except Exception as e:
            log.warning(f"[Web 问答] 用户消息保存失败: {e}")

    async def _save_assistant_message(
        self, reply: str, payload: Dict[str, Any]
    ) -> None:
        """落库 assistant 回复（含工具轨迹与 token 用量）。失败仅记日志。"""
        try:
            from src.database.database import AsyncSessionLocal
            from src.database.models import WebChatMessage

            tool_trace = payload.get("tool_trace") or None
            if tool_trace is not None:
                # 工具执行会把嵌套参数模型（如 WebSearchParams）实例化回填进
                # arguments，直接交给 JSON 列会序列化失败；与 SSE 端点一致，
                # 用 default=str 兜底转成纯 JSON 结构
                tool_trace = json.loads(
                    json.dumps(tool_trace, ensure_ascii=False, default=str)
                )

            async with AsyncSessionLocal() as session:
                session.add(
                    WebChatMessage(
                        role="assistant",
                        content=reply,
                        reasoning=payload.get("reasoning"),
                        tool_trace=tool_trace,
                        model=payload.get("model"),
                        elapsed_ms=payload.get("elapsed_ms"),
                        prompt_tokens=payload.get("prompt_tokens"),
                        completion_tokens=payload.get("completion_tokens"),
                    )
                )
                await session.commit()
        except Exception as e:
            log.warning(f"[Web 问答] 回复保存失败: {e}")

    # ------------------------------------------------------------------
    # 历史查询（供 GET /api/chat/history 分页向上加载）
    # ------------------------------------------------------------------
    async def get_history(self, page: int = 1, rounds: int = 5) -> Dict[str, Any]:
        """分页返回历史对话（按轮 = 一问一答），时间正序。

        page 从 1 开始；返回 {messages, page, has_more}。
        """
        from sqlalchemy import func, select

        from src.database.database import AsyncSessionLocal
        from src.database.models import WebChatMessage

        page = max(1, page)
        page_size = max(1, rounds) * 2
        async with AsyncSessionLocal() as session:
            total = (
                await session.execute(
                    select(func.count()).select_from(WebChatMessage)
                )
            ).scalar() or 0
            rows = (
                await session.execute(
                    select(WebChatMessage)
                    .order_by(WebChatMessage.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()

        items = [
            {
                "role": r.role,
                "content": r.content,
                "reasoning": r.reasoning,
                "tool_trace": r.tool_trace or [],
                "model": r.model,
                "elapsed_ms": r.elapsed_ms,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reversed(rows)  # 翻转为时间正序
        ]
        has_more = page * page_size < total
        return {"messages": items, "page": page, "has_more": has_more}


# 模块级单例
web_chat_service = WebChatService()
