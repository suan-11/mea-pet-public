"""OpenAI 兼容后端的统一适配器（流式 SSE + 工具调用 + 多模态）。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Mapping, Optional

import httpx

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ImageAttachment,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.prompts import (
    build_output_instruction,
    build_repair_instruction,
    build_user_message,
)
from meapet.conversation.output_protocol import parse_reply_output, ParseResult

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# ---------------------------------------------------------------------------
# 配置与能力声明
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str = DEFAULT_OPENAI_BASE_URL
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    verify_tls: bool = True

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> OpenAIConfig:
        return cls(
            base_url=str(d.get("base_url") or d.get("host") or "").strip() or DEFAULT_OPENAI_BASE_URL,
            api_key=str(d.get("api_key") or "").strip(),
            model=str(d.get("model") or "").strip(),
            temperature=float(d.get("temperature", 0.7)),
            max_tokens=int(d.get("max_tokens", 4096)),
            timeout_seconds=float(d.get("timeout_seconds", 60.0)),
            verify_tls=bool(d.get("verify_tls", True)),
        )

@dataclass(frozen=True)
class OpenAICapabilities:
    streaming: bool = True
    tool_calling: bool = False
    vision: bool = False
    repair: bool = True

    @classmethod
    def from_config(cls, config: OpenAIConfig) -> OpenAICapabilities:
        url_lower = config.base_url.lower()
        model_lower = config.model.lower()
        vision = "vision" in model_lower or "gpt-4" in model_lower
        tool_calling = "gpt" in model_lower or "function" in model_lower
        return cls(
            streaming=True,
            tool_calling=tool_calling,
            vision=vision,
            repair=True,
        )


# ---------------------------------------------------------------------------
# 适配器主体
# ---------------------------------------------------------------------------

class OpenAIAdapter:
    """基于 OpenAI Chat Completions API 的统一适配器。"""

    def __init__(self, config: OpenAIConfig, capabilities: OpenAICapabilities | None = None) -> None:
        self._config = config
        self._capabilities = capabilities or OpenAICapabilities.from_config(config)
        # HTTP 客户端（连接池复用）
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url.rstrip("/"),
            timeout=httpx.Timeout(self._config.timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._config.api_key}" if self._config.api_key else "",
                "Content-Type": "application/json",
            },
        )

    @property
    def config(self) -> OpenAIConfig:
        return self._config

    @property
    def capabilities(self) -> OpenAICapabilities:
        return self._capabilities

    # ------------------------------------------------------------------
    # 公共接口（与 orchestrator 配合）
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        request: AgentTurnRequest,
        on_tool_status: Callable[[ToolStatus], None] | None = None,
        on_format_repair: Callable[[FormatRepairRequired], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AsyncGenerator[TurnCompleted | TurnFailed | TurnCancelled, None]:
        """发起流式聊天请求，逐段产出最终结果。"""
        messages = self._build_messages(request)
        stream_url = "/chat/completions"

        payload = {
            "model": self._config.model or "gpt-3.5-turbo",
            "messages": messages,
            "stream": True,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

        try:
            async with self._client.stream("POST", stream_url, json=payload) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    yield TurnFailed(
                        turn_id=request.turn_id,
                        category="api_error",
                        safe_message=f"API returned {response.status_code}: {error_body.decode(errors='replace')[:200]}",
                        retryable=True,
                    )
                    return

                full_content = ""
                async for line in response.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        yield TurnCancelled(turn_id=request.turn_id)
                        return

                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_content += content

                # 流结束后，解析完整内容
                if not full_content.strip():
                    yield TurnFailed(
                        turn_id=request.turn_id,
                        category="empty_response",
                        safe_message="模型返回了空白内容",
                        retryable=False,
                    )
                    return

                # 使用 parse_reply_output 一次性解析
                parse_result = parse_reply_output(full_content)
                if parse_result is None or not parse_result.segments:
                    # 触发格式修复
                    if on_format_repair:
                        repair_request = FormatRepairRequired(result=parse_result)
                        on_format_repair(repair_request)
                        yield TurnFailed(
                            turn_id=request.turn_id,
                            category="format_error",
                            safe_message="回复格式不符合 MeaPet 协议，已尝试修复但失败",
                            retryable=True,
                        )
                        return

                yield TurnCompleted(
                    turn_id=request.turn_id,
                    result=parse_result,
                )

        except httpx.RequestError as e:
            logger.error("OpenAI request failed: %s", e)
            yield TurnFailed(
                turn_id=request.turn_id,
                category="network_error",
                safe_message=f"网络请求失败: {e}",
                retryable=True,
            )
        except Exception as e:
            logger.exception("Unexpected error in chat_stream")
            yield TurnFailed(
                turn_id=request.turn_id,
                category="internal_error",
                safe_message=f"内部错误: {e}",
                retryable=False,
            )

    async def repair_format(
        self,
        malformed_content: str,
        request: AgentTurnRequest | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """调用模型修复格式错误的回复。"""
        instruction = build_repair_instruction(request) if request else "请修复以下回复的格式，使其符合 MeaPet 协议。"
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": malformed_content[:4000]},  # 截断防止过长
        ]
        payload = {
            "model": self._config.model or "gpt-3.5-turbo",
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": self._config.max_tokens,
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code != 200:
                return None
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content
        except Exception:
            logger.exception("Repair format failed")
            return None

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_messages(self, request: AgentTurnRequest) -> list[dict]:
        """构造 OpenAI 消息数组。"""
        system_msg = build_output_instruction(request)
        messages: list[dict] = []

        # System prompt
        messages.append({"role": "system", "content": system_msg})

        # 历史消息
        for hist in request.history:
            role = hist.get("role", "user")
            content = hist.get("content", "")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # 多模态历史
                messages.append({"role": role, "content": content})

        # 当前用户消息
        user_content: list[dict] = []
        user_content.append({"type": "text", "text": request.user_text})

        # 附加图片（多模态）
        for att in request.attachments:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{att.media_type};base64,{att.data}",
                },
            })

        messages.append({"role": "user", "content": user_content})

        return messages

