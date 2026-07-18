"""OpenAI Chat、Ollama、Responses 与 Anthropic 的异步流式客户端。"""

from __future__ import annotations

import asyncio
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from meapet.direct.types import (
    CanonicalChatRequest,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    UsageEvent,
)
from meapet.log import get_color_logger


_PROTOCOLS = {
    "openai_chat",
    "ollama_chat",
    "openai_responses",
    "anthropic_messages",
}

# 连接/超时/限流/5xx 在尚未吐出任何事件前自动重试。
_NETWORK_RETRY_ATTEMPTS = 3
_NETWORK_RETRY_BASE_DELAY_SECONDS = 0.4
log = get_color_logger("direct_client")


def _summarize_messages(messages) -> str:
    """请求消息摘要：角色 + 长度，避免把密钥/超长上下文刷爆控制台。"""
    parts: list[str] = []
    for item in messages or ():
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "?")
        content = item.get("content")
        if isinstance(content, str):
            size = len(content)
        elif isinstance(content, list):
            size = 0
            for part in content:
                if isinstance(part, Mapping):
                    if isinstance(part.get("text"), str):
                        size += len(part["text"])
                    elif part.get("type") == "image" or "image" in str(
                        part.get("type") or ""
                    ):
                        size += 8  # 图片占位
                else:
                    size += len(str(part))
        else:
            size = len(str(content or ""))
        parts.append(f"{role}:{size}")
    return ",".join(parts) if parts else "-"

@dataclass(frozen=True)
class DirectProtocolConfig:
    protocol: str
    base_url: str
    api_key: str = ""
    timeout_seconds: float = 300.0
    verify_tls: bool = True
    ca_file: str = ""

    def __post_init__(self) -> None:
        protocol = str(self.protocol or "").strip().lower()
        if protocol not in _PROTOCOLS:
            raise ValueError("protocol is unsupported")
        raw_url = str(self.base_url or "").strip().rstrip("/")
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "base_url must not contain credentials, query, or fragment"
            )
        normalized_url = urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path.rstrip("/"),
                "",
                "",
            )
        )
        try:
            timeout = float(self.timeout_seconds)
        except (TypeError, ValueError):
            timeout = 120.0
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "api_key", str(self.api_key or "").strip())
        object.__setattr__(self, "timeout_seconds", timeout if timeout > 0 else 120.0)
        object.__setattr__(self, "verify_tls", bool(self.verify_tls))
        object.__setattr__(self, "ca_file", str(self.ca_file or "").strip())

    def endpoint(self, path: str) -> str:
        target = "/" + str(path or "").lstrip("/")
        if self.base_url.lower().endswith("/v1") and target.lower().startswith("/v1/"):
            target = target[3:]
        return self.base_url + target


class DirectProtocolError(Exception):
    def __init__(
        self,
        category: str,
        safe_message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.category = str(category)
        self.safe_message = str(safe_message)
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class _RequestSpec:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, object]
    stream_kind: str


@dataclass(frozen=True)
class _SseEvent:
    event: str
    data: str


async def _iter_sse(response: httpx.Response) -> AsyncIterator[_SseEvent]:
    event_name = "message"
    data_lines: list[str] = []
    raw_line_count = 0
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield _SseEvent(event_name, "\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        # 前 10 行原始内容诊断日志，确认实际响应格式
        if raw_line_count < 20:
            log.track(
                lambda l=line, n=raw_line_count: (
                    f"[sse] raw line #{n}: "
                    f"first_500={l[:500]!r}"
                )
            )
            raw_line_count += 1
        if line.startswith("data:"):
            value = line[5:].lstrip()
            data_lines.append(value)
        elif line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("id:"):
            pass  # event id 暂不处理
        elif line.startswith("{") or line.startswith("["):
            # 非标准格式：纯 JSON 行（无 data: 前缀），直接作为 data
            data_lines.append(line)
    if data_lines:
        yield _SseEvent(event_name, "\n".join(data_lines))


async def _iter_sse_with_timeout(
    response: httpx.Response,
    *,
    event_timeout: float = 60.0,
) -> AsyncIterator[_SseEvent]:
    """包装 _iter_sse，对每个事件设独立超时。超时后静默结束流。"""
    it = _iter_sse(response).__aiter__()
    while True:
        try:
            sse = await asyncio.wait_for(
                it.__anext__(),
                timeout=event_timeout,
            )
            yield sse
        except asyncio.TimeoutError:
            log.info(
                f"[direct] SSE 事件超时 ({event_timeout:.0f}s)，视为流结束"
            )
            return
        except StopAsyncIteration:
            return


def _http_error(status_code: int) -> DirectProtocolError:
    if status_code == 401:
        return DirectProtocolError(
            "authentication",
            "模型接口认证失败，请检查 API Key。",
        )
    if status_code == 403:
        return DirectProtocolError("permission", "模型接口拒绝了当前请求。")
    if status_code == 429:
        return DirectProtocolError(
            "rate_limit",
            "模型请求过于频繁，请稍后再试。",
            True,
        )
    if status_code >= 500:
        return DirectProtocolError(
            "backend_unavailable",
            "模型服务暂时不可用。",
            True,
        )
    return DirectProtocolError("protocol", "模型接口返回了无法处理的响应。")


def _message_list(request: CanonicalChatRequest) -> list[dict[str, object]]:
    return [dict(message) for message in request.messages]


def _data_url(part: Mapping[str, object]) -> str:
    return (
        f"data:{part.get('media_type')};base64,{part.get('data')}"
    )


def _openai_messages(
    request: CanonicalChatRequest,
) -> list[dict[str, object]]:
    messages = []
    for message in request.messages:
        content = message.get("content")
        if not isinstance(content, list):
            messages.append(dict(message))
            continue
        rendered = []
        for part in content:
            if part.get("type") == "text":
                rendered.append({"type": "text", "text": part.get("text", "")})
            else:
                rendered.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(part)},
                    }
                )
        messages.append({"role": message.get("role"), "content": rendered})
    return messages


def _ollama_messages(
    request: CanonicalChatRequest,
) -> list[dict[str, object]]:
    messages = []
    for message in request.messages:
        content = message.get("content")
        if not isinstance(content, list):
            messages.append(dict(message))
            continue
        text_parts = [
            str(part.get("text") or "")
            for part in content
            if part.get("type") == "text"
        ]
        images = [
            str(part.get("data") or "")
            for part in content
            if part.get("type") == "image"
        ]
        rendered: dict[str, object] = {
            "role": message.get("role"),
            "content": "\n".join(text_parts),
        }
        if images:
            rendered["images"] = images
        messages.append(rendered)
    return messages


def _responses_input(
    request: CanonicalChatRequest,
) -> list[dict[str, object]]:
    messages = []
    for message in request.messages:
        content = message.get("content")
        if not isinstance(content, list):
            messages.append(dict(message))
            continue
        rendered = []
        for part in content:
            if part.get("type") == "text":
                rendered.append(
                    {"type": "input_text", "text": part.get("text", "")}
                )
            else:
                rendered.append(
                    {"type": "input_image", "image_url": _data_url(part)}
                )
        messages.append({"role": message.get("role"), "content": rendered})
    return messages


def _anthropic_content(parts: list[Mapping[str, object]]) -> list[dict[str, object]]:
    rendered = []
    for part in parts:
        if part.get("type") == "text":
            rendered.append({"type": "text", "text": part.get("text", "")})
        else:
            rendered.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.get("media_type"),
                        "data": part.get("data"),
                    },
                }
            )
    return rendered


def _openai_chat_spec(
    config: DirectProtocolConfig,
    request: CanonicalChatRequest,
) -> _RequestSpec:
    body: dict[str, object] = {
        "model": request.model,
        "messages": _openai_messages(request),
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": request.stream,
        "stream_options": {"include_usage": True},
        "think": False,
    }
    if request.response_format is not None:
        body["response_format"] = dict(request.response_format)
    body.update(request.extra)
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return _RequestSpec(
        config.endpoint("/v1/chat/completions"),
        headers,
        body,
        "openai_chat",
    )


def _ollama_spec(
    config: DirectProtocolConfig,
    request: CanonicalChatRequest,
) -> _RequestSpec:
    body: dict[str, object] = {
        "model": request.model,
        "messages": _ollama_messages(request),
        "stream": request.stream,
        "keep_alive": "5m",
        "think": False,
        "options": {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
            "num_ctx": 8192,
            "top_p": 0.85,
            "repeat_penalty": 1.1,
        },
    }
    if request.response_format is not None:
        body["format"] = dict(request.response_format)
    body.update(request.extra)
    headers = {
        "Accept": "application/x-ndjson",
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return _RequestSpec(
        config.endpoint("/api/chat"),
        headers,
        body,
        "ollama_chat",
    )


def _responses_spec(
    config: DirectProtocolConfig,
    request: CanonicalChatRequest,
) -> _RequestSpec:
    body: dict[str, object] = {
        "model": request.model,
        "input": _responses_input(request),
        "temperature": request.temperature,
        "max_output_tokens": request.max_tokens,
        "stream": request.stream,
        "think":False,
    }
    if request.response_format is not None:
        body["text"] = {"format": dict(request.response_format)}
    body.update(request.extra)
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return _RequestSpec(
        config.endpoint("/v1/responses"),
        headers,
        body,
        "openai_responses",
    )


def _anthropic_spec(
    config: DirectProtocolConfig,
    request: CanonicalChatRequest,
) -> _RequestSpec:
    system_parts = []
    messages = []
    for message in request.messages:
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
            continue
        rendered = dict(message)
        if isinstance(rendered.get("content"), list):
            rendered["content"] = _anthropic_content(rendered["content"])
        messages.append(rendered)
    body: dict[str, object] = {
        "model": request.model,
        "system": "\n\n".join(system_parts),
        "messages": messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": request.stream,
    }
    body.update(request.extra)
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if config.api_key:
        headers["x-api-key"] = config.api_key
    return _RequestSpec(
        config.endpoint("/v1/messages"),
        headers,
        body,
        "anthropic_messages",
    )


_SPEC_BUILDERS = {
    "openai_chat": _openai_chat_spec,
    "ollama_chat": _ollama_spec,
    "openai_responses": _responses_spec,
    "anthropic_messages": _anthropic_spec,
}


class DirectProtocolClient:
    def __init__(
        self,
        config: DirectProtocolConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owned_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self.config.ca_file or not self.config.verify_tls:
            if self._owned_client is None or self._owned_client.is_closed:
                verify: object = self.config.verify_tls
                if self.config.ca_file:
                    ca_path = Path(self.config.ca_file).expanduser()
                    if not ca_path.is_file():
                        raise DirectProtocolError(
                            "configuration",
                            "模型接口 CA 证书文件不存在。",
                        )
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

    async def close(self) -> None:
        if self._owned_client is not None and not self._owned_client.is_closed:
            await self._owned_client.aclose()
        self._owned_client = None

    async def stream(
        self,
        request: CanonicalChatRequest,
    ) -> AsyncIterator[object]:
        """流式请求模型；网络类失败在首个事件前自动重试。"""
        started = time.perf_counter()
        log.info(
            "[direct] 开始请求 "
            f"protocol={self.config.protocol} model={request.model} "
            f"messages={len(request.messages)} "
            f"msg_sizes={_summarize_messages(request.messages)} "
            f"temperature={request.temperature} max_tokens={request.max_tokens} "
            f"timeout={self.config.timeout_seconds:.0f}s "
            f"base={self.config.base_url}"
        )
        last_error: DirectProtocolError | None = None
        for attempt in range(1, _NETWORK_RETRY_ATTEMPTS + 1):
            emitted_any = False
            try:
                async for event in self._stream_once(request, attempt=attempt):
                    emitted_any = True
                    yield event
                elapsed = time.perf_counter() - started
                log.info(
                    f"[direct] 请求完成 attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS} "
                    f"elapsed={elapsed:.2f}s"
                )
                return
            except DirectProtocolError as exc:
                last_error = exc
                elapsed = time.perf_counter() - started
                can_retry = (
                    exc.retryable
                    and not emitted_any
                    and attempt < _NETWORK_RETRY_ATTEMPTS
                )
                if not can_retry:
                    log.error(
                        f"[direct] 请求失败 category={exc.category} "
                        f"retryable={exc.retryable} attempt={attempt} "
                        f"elapsed={elapsed:.2f}s msg={exc.safe_message}"
                    )
                    raise
                delay = _NETWORK_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                log.warning(
                    f"[direct] 模型请求网络失败，将重试 "
                    f"attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS} "
                    f"category={exc.category} delay={delay:.1f}s "
                    f"elapsed={elapsed:.2f}s"
                )
                await asyncio.sleep(delay)
        if last_error is not None:
            raise last_error
        raise DirectProtocolError(
            "connection",
            "无法连接模型接口，请检查地址和网络。",
            True,
        )

    async def _stream_once(
        self,
        request: CanonicalChatRequest,
        *,
        attempt: int = 1,
    ) -> AsyncIterator[object]:
        spec = _SPEC_BUILDERS[self.config.protocol](self.config, request)
        client = await self._get_client()
        log.info(
            f"[direct] HTTP 发起 attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS} "
            f"method=POST url={spec.url} stream_kind={spec.stream_kind}"
        )
        try:
            async with client.stream(
                "POST",
                spec.url,
                headers=spec.headers,
                json=spec.body,
                timeout=self.config.timeout_seconds,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                log.info(
                    f"[direct] HTTP 响应 status={response.status_code} "
                    f"content_type={content_type or '-'} "
                    f"attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS}"
                )
                if response.status_code >= 400:
                    raise _http_error(response.status_code)
                content_type_l = content_type.lower()
                event_count = 0
                text_chars = 0
                if spec.stream_kind == "ollama_chat":
                    if "ndjson" not in content_type_l and "jsonl" not in content_type_l:
                        if "json" not in content_type_l:
                            log.warning(
                                f"[direct] Ollama 返回非预期 Content-Type: "
                                f"{content_type}，将尝试按 NDJSON 解析"
                            )
                        else:
                            log.info(
                                f"[direct] Ollama 返回 Content-Type: {content_type}，"
                                f"按 JSON Lines 处理"
                            )
                    async for event in self._stream_ollama(response):
                        event_count += 1
                        if isinstance(event, TextDelta):
                            text_chars += len(event.delta or "")
                        yield event
                else:
                    if "text/event-stream" not in content_type_l:
                        raise DirectProtocolError(
                            "protocol",
                            "模型接口未返回预期的 SSE 流。",
                        )
                    parser = {
                        "openai_chat": self._stream_openai_chat,
                        "openai_responses": self._stream_responses,
                        "anthropic_messages": self._stream_anthropic,
                    }[spec.stream_kind]
                    async for event in parser(response):
                        event_count += 1
                        if isinstance(event, TextDelta):
                            text_chars += len(event.delta or "")
                        yield event
                log.info(
                    f"[direct] 流结束 events={event_count} text_chars={text_chars} "
                    f"attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS}"
                )
        except DirectProtocolError:
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            log.warning(
                f"[direct] 超时 attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS} "
                f"type={type(exc).__name__}"
            )
            raise DirectProtocolError(
                "timeout",
                "模型响应超时，请稍后再试。",
                True,
            ) from exc
        except httpx.RequestError as exc:
            log.warning(
                f"[direct] 连接失败 attempt={attempt}/{_NETWORK_RETRY_ATTEMPTS} "
                f"type={type(exc).__name__}"
            )
            raise DirectProtocolError(
                "connection",
                "无法连接模型接口，请检查地址和网络。",
                True,
            ) from exc

    @staticmethod
    def _decode_json(data: str) -> Mapping[str, object]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise DirectProtocolError(
                "protocol",
                "模型流包含无法解析的数据。",
            ) from exc
        if not isinstance(payload, Mapping):
            raise DirectProtocolError("protocol", "模型流包含无效的数据帧。")
        return payload

    async def _stream_openai_chat(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[object]:
        done = False
        async for sse in _iter_sse_with_timeout(response):
            if sse.data.strip() == "[DONE]":
                done = True
                yield StreamDone()
                break
            payload = self._decode_json(sse.data)
            if payload.get("error"):
                raise DirectProtocolError("backend", "模型未能完成回复。")
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0] if isinstance(choices[0], Mapping) else {}
                delta = choice.get("delta")
                if isinstance(delta, Mapping):
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    content = delta.get("content")
                    # OpenAI Chat 兼容流中 reasoning 字段的处理策略：
                    # 1. Ollama qwen3.5 等：content=""（空字符串），
                    #    实际文本在 reasoning 中 → 兜底为 TextDelta。
                    # 2. DeepSeek R1 等：content=None（key 缺失或 JSON null），
                    #    reasoning_content 是思考过程 → 保持为 ReasoningDelta。
                    if content is not None and not content and reasoning:
                        content = reasoning
                        reasoning = None
                    if isinstance(reasoning, str) and reasoning:
                        yield ReasoningDelta(reasoning)
                    if isinstance(content, str) and content:
                        yield TextDelta(content)
                # 部分 SSE 实现（如 Ollama）不发 [DONE]，只靠 finish_reason 标记结尾
                finish_reason = choice.get("finish_reason")
                if finish_reason is not None and isinstance(finish_reason, str):
                    log.track(
                        lambda r=finish_reason: (
                            f"[openai_chat] finish_reason={r} 提前终止 SSE 流"
                        )
                    )
                    done = True
                    yield StreamDone(finish_reason)
                    break
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                yield UsageEvent(dict(usage))
        if not done:
            # _iter_sse_with_timeout 超时返回或连接正常结束但无 [DONE] 标记
            yield StreamDone("end")
        await response.aclose()

    async def _stream_responses(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[object]:
        done = False
        async for sse in _iter_sse(response):
            payload = self._decode_json(sse.data)
            event_type = str(payload.get("type") or sse.event)
            if event_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    yield TextDelta(delta)
            elif event_type == "response.reasoning_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    yield ReasoningDelta(delta)
            elif event_type == "response.completed":
                response_payload = payload.get("response")
                if isinstance(response_payload, Mapping):
                    usage = response_payload.get("usage")
                    if isinstance(usage, Mapping):
                        yield UsageEvent(dict(usage))
                done = True
                yield StreamDone()
                break
            elif event_type in {"error", "response.failed", "response.incomplete"}:
                raise DirectProtocolError("backend", "模型未能完成回复。")
        if not done:
            raise DirectProtocolError("protocol", "Responses SSE 流意外结束。")

    async def _stream_anthropic(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[object]:
        done = False
        async for sse in _iter_sse(response):
            payload = self._decode_json(sse.data)
            event_type = str(payload.get("type") or sse.event)
            if event_type == "content_block_delta":
                delta = payload.get("delta")
                if not isinstance(delta, Mapping):
                    continue
                delta_type = str(delta.get("type") or "")
                if delta_type == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield TextDelta(text)
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        yield ReasoningDelta(thinking)
            elif event_type == "message_delta":
                usage = payload.get("usage")
                if isinstance(usage, Mapping):
                    yield UsageEvent(dict(usage))
            elif event_type == "message_stop":
                done = True
                yield StreamDone()
                break
            elif event_type == "error":
                raise DirectProtocolError("backend", "模型未能完成回复。")
        if not done:
            raise DirectProtocolError("protocol", "Anthropic SSE 流意外结束。")

    async def _stream_ollama(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[object]:
        done = False
        lines_read = 0
        error_lines = 0
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            lines_read += 1
            # 首行原始日志（带 data: 前缀等），用于诊断 SSE 与 NDJSON 格式混用
            if lines_read <= 3:
                log.track(
                    lambda l=line: f"[ollama] raw line #{lines_read}: {l[:300]}"
                )
            if line.startswith("data:"):
                line = line[5:].lstrip()
                error_lines += 1
                if error_lines <= 3:
                    log.track(
                        lambda l=line, n=error_lines: (
                            f"[ollama] stripped data: prefix #{n}, "
                            f"json_len={len(l)} first_100={l[:100]}"
                        )
                    )
            payload = self._decode_json(line)
            if payload.get("error"):
                raise DirectProtocolError("backend", "Ollama 未能完成回复。")
            message = payload.get("message")
            content_found = False
            if isinstance(message, Mapping):
                thinking = message.get("thinking")
                if isinstance(thinking, str) and thinking:
                    yield ReasoningDelta(thinking)
                content = message.get("content")
                # 部分模型可能把内容放在顶层 response 字段
                if not content:
                    content = payload.get("response")
                if isinstance(content, str) and content:
                    content_found = True
                    yield TextDelta(content)
            if bool(payload.get("done")):
                log.track(
                    lambda m=bool(message), c=content_found, l=lines_read: (
                        f"[ollama] done=True lines={l} "
                        f"has_message={m} has_content={c}"
                    )
                )
                usage = {
                    key: payload[key]
                    for key in (
                        "prompt_eval_count",
                        "eval_count",
                        "total_duration",
                    )
                    if key in payload
                }
                if usage:
                    yield UsageEvent(usage)
                done = True
                yield StreamDone(str(payload.get("done_reason") or ""))
                break
        if not done:
            # 读到了行但没有 done 标记 → 可能是非流式单行 JSON
            if lines_read > 0:
                log.info(
                    "[direct] Ollama 流未含 done 标记，但已读取 "
                    f"{lines_read} 行，视为完毕"
                )
                return
            raise DirectProtocolError("protocol", "Ollama NDJSON 流意外结束。")
        log.track(
            lambda l=lines_read, e=error_lines: (
                f"[ollama] stream finished lines={l} data_prefix_lines={e}"
            )
        )
