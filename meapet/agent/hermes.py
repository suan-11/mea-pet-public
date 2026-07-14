"""Hermes Agent 官方 API Server（OpenAI Chat Completions + SSE）适配器。"""

from __future__ import annotations

import asyncio
import time
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
)
from meapet.agent.prompts import (
    MAX_REPAIR_INPUT_CHARS,
    build_output_instruction,
    build_repair_instruction,
    frontend_context_json,
)
from meapet.log import get_color_logger

_CONTROL_RE = re.compile(r"[\r\n\x00]")
_SAFE_STATUS_RE = re.compile(r"[\x00-\x1f\x7f<>]")

log = get_color_logger("hermes")


@dataclass(frozen=True)
class HermesConfig:
    base_url: str = "http://127.0.0.1:8642"
    auth_token: str = ""
    model: str = "hermes-agent"
    session_id: str = ""
    session_key: str = ""
    history_turns: int = 5
    timeout_seconds: float = 120.0
    verify_tls: bool = True
    ca_file: str = ""

    def __post_init__(self) -> None:
        raw_url = str(self.base_url or "").strip().rstrip("/")
        parsed = urlsplit(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query, or fragment")
        normalized_url = urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "auth_token", str(self.auth_token or "").strip())
        object.__setattr__(self, "model", str(self.model or "hermes-agent").strip())
        object.__setattr__(
            self,
            "session_id",
            self._safe_session_value("session_id", self.session_id),
        )
        object.__setattr__(
            self,
            "session_key",
            self._safe_session_value("session_key", self.session_key),
        )
        try:
            history_turns = int(self.history_turns)
        except (TypeError, ValueError):
            history_turns = 5
        object.__setattr__(self, "history_turns", max(0, min(history_turns, 50)))
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        object.__setattr__(self, "verify_tls", bool(self.verify_tls))
        object.__setattr__(self, "ca_file", str(self.ca_file or "").strip())

    @staticmethod
    def _safe_session_value(name: str, value: object) -> str:
        result = str(value or "").strip()
        if len(result) > 256 or _CONTROL_RE.search(result):
            raise ValueError(f"{name} must be at most 256 characters without controls")
        return result

    def endpoint(self, path: str) -> str:
        target = "/" + str(path or "").lstrip("/")
        if self.base_url.lower().endswith("/v1") and target.lower().startswith("/v1/"):
            target = target[3:]
        return self.base_url + target


@dataclass(frozen=True)
class HermesCapabilities:
    platform: str
    model: str
    chat_completions: bool
    features: Mapping[str, bool] = field(default_factory=dict)
    session_key_header: str = ""


@dataclass(frozen=True)
class _SseEvent:
    event: str
    data: str


async def _iter_sse(response: httpx.Response) -> AsyncIterator[_SseEvent]:
    event_name = "message"
    data_lines = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield _SseEvent(event_name, "\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field_name, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field_name == "event":
            event_name = value or "message"
        elif field_name == "data":
            data_lines.append(value)
    if data_lines:
        yield _SseEvent(event_name, "\n".join(data_lines))


def _safe_status_text(payload: Mapping[str, object], state: str) -> str:
    raw = payload.get("status_text") or payload.get("label") or ""
    safe = _SAFE_STATUS_RE.sub(" ", str(raw or ""))
    safe = " ".join(safe.split())[:80].strip()
    if safe:
        return safe
    return {
        "started": "正在处理",
        "succeeded": "处理完成",
        "failed": "处理失败",
    }[state]


def _tool_status(payload: Mapping[str, object]) -> ToolStatus:
    raw_state = str(payload.get("status") or "running").strip().lower()
    state = {
        "running": "started",
        "started": "started",
        "completed": "succeeded",
        "complete": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "error": "failed",
    }.get(raw_state, "started")
    return ToolStatus(state=state, safe_text=_safe_status_text(payload, state))


def _failure_for_status(turn_id: str, status_code: int) -> TurnFailed:
    if status_code == 401:
        return TurnFailed(turn_id, "authentication", "Agent 认证失败，请检查访问令牌。")
    if status_code == 403:
        return TurnFailed(turn_id, "permission", "Agent 拒绝了当前请求。")
    if status_code == 429:
        return TurnFailed(turn_id, "rate_limit", "Agent 请求过于频繁，请稍后再试。", True)
    if status_code >= 500:
        return TurnFailed(turn_id, "backend_unavailable", "Agent 服务暂时不可用。", True)
    return TurnFailed(turn_id, "protocol", "Agent 返回了无法处理的响应。")


class HermesAdapter:
    def __init__(
        self,
        config: HermesConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owned_client: Optional[httpx.AsyncClient] = None
        self._cancelled_turns: set[str] = set()
        self.last_session_id = config.session_id

    def _validate_auth(self) -> None:
        if not self.config.auth_token:
            raise ValueError("auth_token is required by the Hermes API Server")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self.config.ca_file or not self.config.verify_tls:
            if self._owned_client is None or self._owned_client.is_closed:
                verify: object = self.config.verify_tls
                if self.config.ca_file:
                    ca_path = Path(self.config.ca_file).expanduser()
                    if not ca_path.is_file():
                        raise ValueError("ca_file does not exist")
                    verify = str(ca_path)
                self._owned_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.config.timeout_seconds, connect=10.0),
                    follow_redirects=True,
                    verify=verify,
                    headers={"User-Agent": "MeaPet/1.0"},
                )
            return self._owned_client
        from meapet.http_async import get_client

        return await get_client()

    def _headers(
        self,
        *,
        turn_id: str = "",
        include_session: bool = True,
    ) -> dict[str, str]:
        self._validate_auth()
        headers = {
            "Authorization": f"Bearer {self.config.auth_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if include_session and self.config.session_id:
            headers["X-Hermes-Session-Id"] = self.config.session_id
        if include_session and self.config.session_key:
            headers["X-Hermes-Session-Key"] = self.config.session_key
        if turn_id:
            headers["Idempotency-Key"] = turn_id
        return headers

    async def probe(self) -> HermesCapabilities:
        client = await self._get_client()
        response = await client.get(
            self.config.endpoint("/v1/capabilities"),
            headers=self._headers(),
            timeout=min(self.config.timeout_seconds, 15.0),
        )
        if response.status_code >= 400:
            raise ValueError(
                f"Hermes capability probe failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Hermes capabilities response is not JSON") from exc
        if not isinstance(payload, dict) or payload.get("platform") != "hermes-agent":
            raise ValueError("endpoint is not a Hermes API Server")
        features = payload.get("features")
        if not isinstance(features, dict):
            features = {}
        normalized_features = {
            str(key): bool(value) for key, value in features.items()
        }
        return HermesCapabilities(
            platform="hermes-agent",
            model=str(payload.get("model") or self.config.model),
            chat_completions=bool(normalized_features.get("chat_completions")),
            features=normalized_features,
            session_key_header=str(payload.get("session_key_header") or ""),
        )

    def _messages(self, request: AgentTurnRequest) -> list[dict[str, object]]:
        messages = [
            {
                "role": "system",
                "content": (
                    f"{build_output_instruction(request)}\n"
                    f"前端只读摘要：{frontend_context_json(request)}"
                ),
            }
        ]
        history = []
        for item in request.history:
            role = str(item.get("role") or "").strip().lower()
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            content = content.strip()
            if content:
                history.append({"role": role, "content": content})
        if self.config.history_turns:
            history = history[-self.config.history_turns * 2:]
        else:
            history = []
        messages.extend(history)
        if request.attachments:
            content: list[dict[str, object]] = [
                {"type": "text", "text": request.user_text}
            ]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{attachment.media_type};base64,"
                            f"{attachment.data}"
                        )
                    },
                }
                for attachment in request.attachments
            )
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": request.user_text})
        return messages

    async def cancel(self, turn_id: str) -> None:
        self._cancelled_turns.add(str(turn_id or "").strip())

    async def close(self) -> None:
        if self._owned_client is not None and not self._owned_client.is_closed:
            await self._owned_client.aclose()
        self._owned_client = None

    async def _repair_result(
        self,
        *,
        request: AgentTurnRequest,
        malformed_output: str,
        client: httpx.AsyncClient,
    ):
        """在隔离会话中做一次纯格式转换；任何异常都回退原解析结果。"""
        repair_turn_id = f"{request.turn_id[:220]}-format-repair"
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": build_repair_instruction(request)},
                {
                    "role": "user",
                    "content": malformed_output[:MAX_REPAIR_INPUT_CHARS],
                },
            ],
            "stream": True,
            "tool_choice": "none",
        }
        parser = MeaPetOutputStreamParser()
        try:
            async with client.stream(
                "POST",
                self.config.endpoint("/v1/chat/completions"),
                headers=self._headers(
                    turn_id=repair_turn_id,
                    include_session=False,
                ),
                json=body,
                timeout=self.config.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    return None
                if "text/event-stream" not in response.headers.get(
                    "Content-Type", ""
                ).lower():
                    return None
                async for sse in _iter_sse(response):
                    if request.turn_id in self._cancelled_turns:
                        return None
                    if sse.data.strip() == "[DONE]":
                        continue
                    try:
                        payload = json.loads(sse.data)
                    except json.JSONDecodeError:
                        return None
                    if not isinstance(payload, dict):
                        return None
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        parser.feed(content)
            result = parser.close(tts_enabled=request.tts_enabled)
        except (httpx.HTTPError, ValueError):
            return None
        if result.requires_repair(tts_enabled=request.tts_enabled):
            return None
        return result

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
        turn = str(request.turn_id or "")[:24]
        started = time.perf_counter()
        log.info(
            f"[agent] 回合开始 turn={turn} model={self.config.model} "
            f"user_chars={len(request.user_text or '')} "
            f"base={self.config.base_url}"
        )
        if request.turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(request.turn_id)
            log.info(f"[agent] 回合已取消(开始前) turn={turn}")
            yield TurnCancelled(request.turn_id)
            return

        try:
            headers = self._headers(turn_id=request.turn_id)
            client = await self._get_client()
        except ValueError:
            log.error(f"[agent] 配置不完整 turn={turn}")
            yield TurnFailed(
                request.turn_id,
                "configuration",
                "Agent 配置不完整，请检查地址、令牌和证书。",
            )
            return

        body = {
            "model": self.config.model,
            "messages": self._messages(request),
            "stream": True,
        }
        parser = MeaPetOutputStreamParser()
        raw_chunks: list[str] = []
        completed_indices: set[int] = set()
        protocol_completed_emitted = False
        endpoint = self.config.endpoint("/v1/chat/completions")
        log.info(
            f"[agent] HTTP 发起 method=POST url={endpoint} "
            f"messages={len(body['messages'])} timeout={self.config.timeout_seconds:.0f}s"
        )

        try:
            async with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json=body,
                timeout=self.config.timeout_seconds,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                log.info(
                    f"[agent] HTTP 响应 status={response.status_code} "
                    f"content_type={content_type or '-'}"
                )
                if response.status_code >= 400:
                    log.error(
                        f"[agent] HTTP 错误 status={response.status_code} turn={turn}"
                    )
                    yield _failure_for_status(request.turn_id, response.status_code)
                    return
                content_type_l = content_type.lower()
                if "text/event-stream" not in content_type_l:
                    log.error(f"[agent] 非 SSE 响应 turn={turn}")
                    yield TurnFailed(
                        request.turn_id,
                        "protocol",
                        "Agent 未返回预期的流式响应。",
                    )
                    return
                echoed_session = response.headers.get("X-Hermes-Session-Id", "").strip()
                if echoed_session and not _CONTROL_RE.search(echoed_session):
                    self.last_session_id = echoed_session[:256]

                async for sse in _iter_sse(response):
                    if request.turn_id in self._cancelled_turns:
                        self._cancelled_turns.discard(request.turn_id)
                        yield TurnCancelled(request.turn_id)
                        return
                    if sse.data.strip() == "[DONE]":
                        continue
                    try:
                        payload = json.loads(sse.data)
                    except json.JSONDecodeError:
                        yield TurnFailed(
                            request.turn_id,
                            "protocol",
                            "Agent 返回了无法解析的流式数据。",
                        )
                        return
                    if not isinstance(payload, dict):
                        yield TurnFailed(
                            request.turn_id,
                            "protocol",
                            "Agent 返回了无法解析的流式数据。",
                        )
                        return
                    if sse.event == "hermes.tool.progress":
                        yield _tool_status(payload)
                        continue

                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if not isinstance(content, str) or not content:
                        continue
                    raw_chunks.append(content)
                    for event in parser.feed(content):
                        if isinstance(event, SegmentCompleted):
                            if event.segment.missing_required_fields:
                                continue
                            completed_indices.add(event.segment.index)
                        elif isinstance(event, ProtocolCompleted):
                            protocol_completed_emitted = True
                        yield event

            raw_text = "".join(raw_chunks)
            if raw_text.strip():
                log.info(
                    f"[agent] 模型返回文本 turn={turn} chars={len(raw_text)}\n{raw_text}"
                )
            else:
                log.info(f"[agent] 模型返回文本为空 turn={turn}")

            result = parser.close(tts_enabled=request.tts_enabled)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                log.warning(
                    f"[agent] 输出协议需修复 turn={turn} raw_chars={len(raw_text)}"
                )
                yield FormatRepairRequired(result)
                repaired = await self._repair_result(
                    request=request,
                    malformed_output=raw_text,
                    client=client,
                )
                if request.turn_id in self._cancelled_turns:
                    self._cancelled_turns.discard(request.turn_id)
                    yield TurnCancelled(request.turn_id)
                    return
                if repaired is not None:
                    result = repaired

            for segment in result.segments:
                if segment.index not in completed_indices:
                    yield SegmentCompleted(segment)
                    completed_indices.add(segment.index)
            if result.done and not protocol_completed_emitted:
                yield ProtocolCompleted()
            elapsed = time.perf_counter() - started
            log.info(
                f"[agent] 回合完成 turn={turn} segments={len(result.segments)} "
                f"elapsed={elapsed:.2f}s"
            )
            yield TurnCompleted(request.turn_id, result)
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - started
            log.error(f"[agent] 超时 turn={turn} elapsed={elapsed:.2f}s")
            yield TurnFailed(
                request.turn_id,
                "timeout",
                "Agent 响应超时，请稍后再试。",
                True,
            )
        except httpx.RequestError as exc:
            elapsed = time.perf_counter() - started
            log.error(
                f"[agent] 连接失败 turn={turn} type={type(exc).__name__} "
                f"elapsed={elapsed:.2f}s"
            )
            yield TurnFailed(
                request.turn_id,
                "connection",
                "无法连接 Agent，请检查地址和网络。",
                True,
            )
