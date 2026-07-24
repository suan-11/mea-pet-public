"""OpenClaw 官方 Gateway WebSocket v4 适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import locale
import platform as runtime_platform
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed

from meapet import __version__
from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.openclaw_identity import (
    OpenClawDeviceIdentity,
    build_device_auth_payload_v3,
)
from meapet.agent.prompts import (
    MAX_REPAIR_INPUT_CHARS,
    REPAIR_INSTRUCTION,
    gateway_user_message,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
)
from meapet.paths import get_data_dir


_PROTOCOL_VERSION = 4
_PRECONNECT_MAX_BYTES = 64 * 1024
_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}
_SCOPES = ("operator.read", "operator.write")
_CONTROL_CHARS = {"\r", "\n", "\x00"}
_MAX_SERVER_PAYLOAD = 100 * 1024 * 1024


def _is_loopback(hostname: str) -> bool:
    value = str(hostname or "").strip().lower().rstrip(".")
    if value in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _safe_identifier(name: str, value: object, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{name} is required")
    if len(result) > 512 or any(char in result for char in _CONTROL_CHARS):
        raise ValueError(f"{name} is not a safe identifier")
    return result


@dataclass(frozen=True)
class OpenClawConfig:
    base_url: str = "ws://127.0.0.1:18789"
    auth_token: str = ""
    session_key: str = ""
    session_id: str = ""
    timeout_seconds: float = 120.0
    verify_tls: bool = True
    ca_file: str = ""
    allow_insecure_ws: bool = False
    identity_path: str = ""

    def __post_init__(self) -> None:
        raw_url = str(self.base_url or "").strip().rstrip("/")
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("base_url must be a ws(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "base_url must not contain credentials, query, or fragment"
            )
        if (
            parsed.scheme.lower() == "ws"
            and not _is_loopback(parsed.hostname or "")
            and not bool(self.allow_insecure_ws)
        ):
            raise ValueError("remote plaintext ws requires allow_insecure_ws")
        normalized = urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path.rstrip("/"),
                "",
                "",
            )
        )
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(self, "auth_token", str(self.auth_token or "").strip())
        object.__setattr__(
            self,
            "session_key",
            _safe_identifier("session_key", self.session_key, required=True),
        )
        object.__setattr__(
            self,
            "session_id",
            _safe_identifier("session_id", self.session_id),
        )
        try:
            timeout = float(self.timeout_seconds)
        except (TypeError, ValueError):
            timeout = 120.0
        object.__setattr__(self, "timeout_seconds", timeout if timeout > 0 else 120.0)
        object.__setattr__(self, "verify_tls", bool(self.verify_tls))
        object.__setattr__(self, "ca_file", str(self.ca_file or "").strip())
        object.__setattr__(
            self,
            "allow_insecure_ws",
            bool(self.allow_insecure_ws),
        )
        object.__setattr__(
            self,
            "identity_path",
            str(self.identity_path or "").strip(),
        )


@dataclass(frozen=True)
class OpenClawCapabilities:
    platform: str
    protocol: int
    server_version: str
    chat_send: bool
    chat_abort: bool
    methods: tuple[str, ...] = ()
    events: tuple[str, ...] = ()


@dataclass
class _ActiveConnection:
    websocket: Any
    run_id: str = ""
    session_key: str = ""
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _GatewayFailure(Exception):
    def __init__(
        self,
        category: str,
        safe_message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable

    def event(self, turn_id: str) -> TurnFailed:
        return TurnFailed(
            turn_id,
            self.category,
            self.safe_message,
            self.retryable,
        )


def _gateway_error(error: object) -> _GatewayFailure:
    payload = error if isinstance(error, Mapping) else {}
    code = str(payload.get("code") or "").strip().upper()
    retryable = bool(payload.get("retryable", False))
    if "PAIR" in code or code in {"DEVICE_REQUIRED", "DEVICE_NOT_PAIRED"}:
        return _GatewayFailure(
            "permission",
            "OpenClaw 需要先批准此设备配对。",
        )
    if code in {"UNAUTHORIZED", "AUTHENTICATION_FAILED", "INVALID_TOKEN"}:
        return _GatewayFailure(
            "authentication",
            "OpenClaw 认证失败，请检查访问令牌。",
        )
    if code in {"FORBIDDEN", "PERMISSION_DENIED", "INSUFFICIENT_SCOPE"}:
        return _GatewayFailure("permission", "OpenClaw 拒绝了当前请求。")
    if code in {"RATE_LIMITED", "RATE_LIMIT", "TOO_MANY_REQUESTS"}:
        return _GatewayFailure(
            "rate_limit",
            "OpenClaw 请求过于频繁，请稍后再试。",
            True,
        )
    if code in {"UNAVAILABLE", "SERVICE_UNAVAILABLE", "STARTING"}:
        return _GatewayFailure(
            "backend_unavailable",
            "OpenClaw Gateway 暂时不可用。",
            True,
        )
    return _GatewayFailure(
        "protocol",
        "OpenClaw Gateway 返回了无法处理的响应。",
        retryable,
    )


def _chat_error(payload: Mapping[str, object]) -> _GatewayFailure:
    kind = str(payload.get("errorKind") or "unknown").strip().lower()
    if kind == "rate_limit":
        return _GatewayFailure(
            "rate_limit",
            "OpenClaw 请求过于频繁，请稍后再试。",
            True,
        )
    if kind == "timeout":
        return _GatewayFailure("timeout", "OpenClaw 响应超时，请稍后再试。", True)
    if kind == "context_length":
        return _GatewayFailure("context_length", "OpenClaw 当前会话内容过长。")
    if kind == "refusal":
        return _GatewayFailure("permission", "OpenClaw 拒绝了当前请求。")
    return _GatewayFailure("backend", "OpenClaw 未能完成本轮回复。")


def _status_event(event_name: str, payload: Mapping[str, object]) -> ToolStatus:
    state_value = str(
        payload.get("state") or payload.get("status") or "running"
    ).strip().lower()
    if state_value in {"completed", "complete", "succeeded", "success", "done"}:
        state = "succeeded"
    elif state_value in {"failed", "error", "cancelled", "aborted"}:
        state = "failed"
    else:
        state = "started"
    if event_name == "session.tool":
        text = {
            "started": "Agent 正在执行工具",
            "succeeded": "Agent 工具执行完成",
            "failed": "Agent 工具执行失败",
        }[state]
    else:
        text = {
            "started": "Agent 正在处理",
            "succeeded": "Agent 处理完成",
            "failed": "Agent 处理失败",
        }[state]
    return ToolStatus(state, text)


def _message_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    return "".join(chunks)


class OpenClawAdapter:
    def __init__(
        self,
        config: OpenClawConfig,
        *,
        connector: Optional[Callable[..., object]] = None,
        identity: Optional[OpenClawDeviceIdentity] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.config = config
        if connector is None:
            from websockets.asyncio.client import connect

            connector = connect
        self._connector = connector
        self._identity = identity
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._active: dict[str, _ActiveConnection] = {}
        self._cancelled_turns: set[str] = set()

    def _get_identity(self) -> OpenClawDeviceIdentity:
        if self._identity is None:
            if self.config.identity_path:
                path = self.config.identity_path
            else:
                # 便携打包与源码模式均写入 get_data_dir()（frozen = _internal）。
                path = str(Path(get_data_dir()) / "openclaw_device_identity.json")
            self._identity = OpenClawDeviceIdentity.load_or_create(path)
        return self._identity

    def _connection_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "open_timeout": min(self.config.timeout_seconds, 15.0),
            "close_timeout": 5.0,
            "max_size": _MAX_SERVER_PAYLOAD,
        }
        if self.config.base_url.startswith("wss://"):
            if self.config.ca_file:
                ca_path = Path(self.config.ca_file).expanduser()
                if not ca_path.is_file():
                    raise _GatewayFailure(
                        "configuration",
                        "OpenClaw CA 证书文件不存在。",
                    )
                context = ssl.create_default_context(cafile=str(ca_path))
            else:
                context = ssl.create_default_context()
            if not self.config.verify_tls:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = context
        return kwargs

    async def _recv_frame(self, websocket: Any, *, max_bytes: int) -> dict:
        raw = await asyncio.wait_for(
            websocket.recv(),
            timeout=self.config.timeout_seconds,
        )
        if not isinstance(raw, str):
            raise _GatewayFailure("protocol", "OpenClaw 返回了非文本数据。")
        if len(raw.encode("utf-8")) > max_bytes:
            raise _GatewayFailure("protocol", "OpenClaw 返回的数据超过安全上限。")
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _GatewayFailure(
                "protocol",
                "OpenClaw 返回了无法解析的数据。",
            ) from exc
        if not isinstance(frame, dict):
            raise _GatewayFailure("protocol", "OpenClaw 返回了无效的数据帧。")
        return frame

    @staticmethod
    async def _send_frame(websocket: Any, frame: Mapping[str, object]) -> None:
        await websocket.send(
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        )

    async def _handshake(self, websocket: Any) -> OpenClawCapabilities:
        if not self.config.auth_token:
            raise _GatewayFailure(
                "configuration",
                "OpenClaw 配置不完整，请填写访问令牌。",
            )
        challenge = await self._recv_frame(
            websocket,
            max_bytes=_PRECONNECT_MAX_BYTES,
        )
        challenge_payload = challenge.get("payload")
        if (
            challenge.get("type") != "event"
            or challenge.get("event") != "connect.challenge"
            or not isinstance(challenge_payload, Mapping)
        ):
            raise _GatewayFailure("protocol", "OpenClaw 未发送连接挑战。")
        nonce = _safe_identifier(
            "challenge nonce",
            challenge_payload.get("nonce"),
            required=True,
        )
        identity = self._get_identity()
        signed_at = int(self._clock_ms())
        system_name = runtime_platform.system().strip().lower() or "unknown"
        device_family = "desktop"
        signature_payload = build_device_auth_payload_v3(
            device_id=identity.device_id,
            client_id="webchat",
            client_mode="webchat",
            role="operator",
            scopes=_SCOPES,
            signed_at_ms=signed_at,
            token=self.config.auth_token,
            nonce=nonce,
            platform=system_name,
            device_family=device_family,
        )
        language = locale.getlocale()[0] or "zh-CN"
        connect_frame = {
            "type": "req",
            "id": "connect",
            "method": "connect",
            "params": {
                "minProtocol": _PROTOCOL_VERSION,
                "maxProtocol": _PROTOCOL_VERSION,
                "client": {
                    "id": "webchat",
                    "displayName": "MeaPet",
                    "version": __version__,
                    "platform": system_name,
                    "deviceFamily": device_family,
                    "mode": "webchat",
                },
                "caps": [],
                "commands": [],
                "permissions": {},
                "role": "operator",
                "scopes": list(_SCOPES),
                "auth": {"token": self.config.auth_token},
                "locale": language,
                "userAgent": f"MeaPet/{__version__}",
                "device": {
                    "id": identity.device_id,
                    "publicKey": identity.public_key,
                    "signature": identity.sign(signature_payload),
                    "signedAt": signed_at,
                    "nonce": nonce,
                },
            },
        }
        await self._send_frame(websocket, connect_frame)
        for _ in range(32):
            response = await self._recv_frame(
                websocket,
                max_bytes=_PRECONNECT_MAX_BYTES,
            )
            if response.get("type") != "res" or response.get("id") != "connect":
                continue
            if not response.get("ok"):
                raise _gateway_error(response.get("error"))
            hello = response.get("payload")
            if not isinstance(hello, Mapping) or hello.get("type") != "hello-ok":
                raise _GatewayFailure("protocol", "OpenClaw 握手响应无效。")
            protocol = int(hello.get("protocol") or 0)
            if protocol != _PROTOCOL_VERSION:
                raise _GatewayFailure("protocol", "OpenClaw 协议版本不兼容。")
            features = hello.get("features")
            features = features if isinstance(features, Mapping) else {}
            methods = tuple(
                str(value)
                for value in features.get("methods", ())
                if isinstance(value, str)
            )
            events = tuple(
                str(value)
                for value in features.get("events", ())
                if isinstance(value, str)
            )
            server = hello.get("server")
            server = server if isinstance(server, Mapping) else {}
            return OpenClawCapabilities(
                platform="openclaw",
                protocol=protocol,
                server_version=str(server.get("version") or ""),
                chat_send="chat.send" in methods,
                chat_abort="chat.abort" in methods,
                methods=methods,
                events=events,
            )
        raise _GatewayFailure("protocol", "OpenClaw 未完成连接握手。")

    async def probe(self) -> OpenClawCapabilities:
        try:
            connection = self._connector(
                self.config.base_url,
                **self._connection_kwargs(),
            )
            async with connection as websocket:
                capabilities = await self._handshake(websocket)
            if not capabilities.chat_send:
                raise ValueError("OpenClaw Gateway does not advertise chat.send")
            return capabilities
        except _GatewayFailure as exc:
            raise ValueError(exc.safe_message) from None
        except (asyncio.TimeoutError, TimeoutError, ConnectionClosed, OSError) as exc:
            raise ValueError("无法连接 OpenClaw Gateway") from exc

    async def cancel(self, turn_id: str) -> None:
        safe_turn_id = _safe_identifier("turn_id", turn_id, required=True)
        self._cancelled_turns.add(safe_turn_id)
        active = self._active.get(safe_turn_id)
        if active is None:
            return
        params: dict[str, object] = {
            "sessionKey": active.session_key or self.config.session_key
        }
        if active.run_id:
            params["runId"] = active.run_id
        frame = {
            "type": "req",
            "id": f"abort:{safe_turn_id}",
            "method": "chat.abort",
            "params": params,
        }
        try:
            async with active.send_lock:
                await self._send_frame(active.websocket, frame)
        except (ConnectionClosed, OSError):
            return

    async def close(self) -> None:
        active = tuple(self._active.values())
        self._active.clear()
        for item in active:
            try:
                await item.websocket.close()
            except (ConnectionClosed, OSError):
                pass

    def _repair_session_key(self, turn_id: str) -> str:
        scope = hashlib.sha256(
            f"{self.config.session_key}\x00{turn_id}".encode("utf-8")
        ).hexdigest()[:24]
        return f"agent:main:meapet:format-repair:{scope}"

    async def _repair_result(
        self,
        *,
        websocket: Any,
        active: _ActiveConnection,
        request: AgentTurnRequest,
        malformed_output: str,
        max_payload: int,
    ):
        """在独立 OpenClaw 会话中仅做一次格式转换。"""
        repair_id = f"repair:{request.turn_id}"
        repair_session = self._repair_session_key(request.turn_id)
        active.run_id = ""
        active.session_key = repair_session
        await self._send_frame(
            websocket,
            {
                "type": "req",
                "id": repair_id,
                "method": "chat.send",
                "params": {
                    "sessionKey": repair_session,
                    "message": (
                        f"{REPAIR_INSTRUCTION}\n\n待转换原文：\n"
                        f"{malformed_output[:MAX_REPAIR_INPUT_CHARS]}"
                    ),
                    "deliver": False,
                    "timeoutMs": max(
                        0,
                        int(self.config.timeout_seconds * 1000),
                    ),
                    "idempotencyKey": f"{request.turn_id}-format-repair",
                },
            },
        )
        parser = MeaPetOutputStreamParser()
        raw_output = ""
        for _ in range(10000):
            frame = await self._recv_frame(websocket, max_bytes=max_payload)
            if request.turn_id in self._cancelled_turns:
                return None
            if frame.get("type") == "res" and frame.get("id") == repair_id:
                if not frame.get("ok"):
                    return None
                payload = frame.get("payload")
                if isinstance(payload, Mapping):
                    active.run_id = _safe_identifier(
                        "run_id",
                        payload.get("runId"),
                    )
                continue
            if frame.get("type") != "event" or frame.get("event") != "chat":
                continue
            payload = frame.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("sessionKey") or "") != repair_session:
                continue
            run_id = _safe_identifier("run_id", payload.get("runId"))
            if run_id:
                if active.run_id and active.run_id != run_id:
                    continue
                active.run_id = run_id
            state = str(payload.get("state") or "").strip().lower()
            if state == "delta":
                delta = payload.get("deltaText")
                if not isinstance(delta, str) or not delta:
                    continue
                if bool(payload.get("replace")):
                    parser = MeaPetOutputStreamParser()
                    raw_output = delta
                else:
                    raw_output += delta
                parser.feed(delta)
                continue
            if state in {"aborted", "error"}:
                return None
            if state == "final":
                if not raw_output:
                    final_text = _message_text(payload.get("message"))
                    if final_text:
                        parser.feed(final_text)
                result = parser.close(tts_enabled=request.tts_enabled)
                if result.requires_repair(tts_enabled=request.tts_enabled):
                    return None
                return result
        return None

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
        if request.turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(request.turn_id)
            yield TurnCancelled(request.turn_id)
            return

        parser = MeaPetOutputStreamParser()
        raw_output = ""
        completed_indices: set[int] = set()
        protocol_completed_emitted = False
        suppress_stream = False
        max_payload = _MAX_SERVER_PAYLOAD
        active: Optional[_ActiveConnection] = None
        try:
            connection = self._connector(
                self.config.base_url,
                **self._connection_kwargs(),
            )
            async with connection as websocket:
                capabilities = await self._handshake(websocket)
                if not capabilities.chat_send:
                    raise _GatewayFailure(
                        "protocol",
                        "OpenClaw Gateway 未提供 chat.send。",
                    )
                active = _ActiveConnection(
                    websocket,
                    session_key=self.config.session_key,
                )
                self._active[request.turn_id] = active
                params: dict[str, object] = {
                    "sessionKey": self.config.session_key,
                    "message": gateway_user_message(request),
                    "deliver": False,
                    "timeoutMs": max(0, int(self.config.timeout_seconds * 1000)),
                    "idempotencyKey": request.turn_id,
                }
                if self.config.session_id:
                    params["sessionId"] = self.config.session_id
                if request.attachments:
                    params["attachments"] = [
                        {
                            "type": "image",
                            "mimeType": attachment.media_type,
                            "fileName": attachment.file_name,
                            "content": attachment.data,
                        }
                        for attachment in request.attachments
                    ]
                await self._send_frame(
                    websocket,
                    {
                        "type": "req",
                        "id": f"send:{request.turn_id}",
                        "method": "chat.send",
                        "params": params,
                    },
                )

                while True:
                    frame = await self._recv_frame(websocket, max_bytes=max_payload)
                    if request.turn_id in self._cancelled_turns:
                        self._cancelled_turns.discard(request.turn_id)
                        yield TurnCancelled(request.turn_id)
                        return
                    if (
                        frame.get("type") == "res"
                        and frame.get("id") == f"send:{request.turn_id}"
                    ):
                        if not frame.get("ok"):
                            raise _gateway_error(frame.get("error"))
                        response_payload = frame.get("payload")
                        if isinstance(response_payload, Mapping):
                            active.run_id = _safe_identifier(
                                "run_id",
                                response_payload.get("runId"),
                            )
                        continue
                    if frame.get("type") != "event":
                        continue
                    event_name = str(frame.get("event") or "")
                    event_payload = frame.get("payload")
                    if not isinstance(event_payload, Mapping):
                        continue
                    event_session = str(event_payload.get("sessionKey") or "")
                    if event_session and event_session != self.config.session_key:
                        continue
                    if event_name in {"session.operation", "session.tool"}:
                        yield _status_event(event_name, event_payload)
                        continue
                    if event_name != "chat":
                        continue
                    run_id = _safe_identifier("run_id", event_payload.get("runId"))
                    if run_id:
                        if active.run_id and active.run_id != run_id:
                            continue
                        active.run_id = run_id
                    state = str(event_payload.get("state") or "").strip().lower()
                    if state == "delta":
                        delta = event_payload.get("deltaText")
                        if not isinstance(delta, str) or not delta:
                            continue
                        if bool(event_payload.get("replace")):
                            raw_output = delta
                            parser = MeaPetOutputStreamParser()
                            completed_indices.clear()
                            protocol_completed_emitted = False
                            suppress_stream = True
                        else:
                            raw_output += delta
                        for event in parser.feed(delta):
                            if isinstance(event, SegmentCompleted):
                                if event.segment.missing_required_fields:
                                    continue
                                completed_indices.add(event.segment.index)
                            elif isinstance(event, ProtocolCompleted):
                                protocol_completed_emitted = True
                            if not suppress_stream:
                                yield event
                        continue
                    if state == "final":
                        if not raw_output:
                            final_text = _message_text(event_payload.get("message"))
                            if final_text:
                                raw_output = final_text
                                parser.feed(final_text)
                        break
                    if state == "aborted":
                        yield TurnCancelled(request.turn_id)
                        return
                    if state == "error":
                        raise _chat_error(event_payload)

                result = parser.close(tts_enabled=request.tts_enabled)
                if result.requires_repair(tts_enabled=request.tts_enabled):
                    yield FormatRepairRequired(result)
                    repaired = await self._repair_result(
                        websocket=websocket,
                        active=active,
                        request=request,
                        malformed_output=raw_output,
                        max_payload=max_payload,
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
            yield TurnCompleted(request.turn_id, result)
        except asyncio.CancelledError:
            raise
        except _GatewayFailure as exc:
            yield exc.event(request.turn_id)
        except (asyncio.TimeoutError, TimeoutError):
            yield TurnFailed(
                request.turn_id,
                "timeout",
                "OpenClaw 响应超时，请稍后再试。",
                True,
            )
        except (ConnectionClosed, OSError):
            yield TurnFailed(
                request.turn_id,
                "connection",
                "无法连接 OpenClaw Gateway，请检查地址和网络。",
                True,
            )
        except ValueError:
            yield TurnFailed(
                request.turn_id,
                "configuration",
                "OpenClaw 配置不完整，请检查地址、令牌和证书。",
            )
        finally:
            if self._active.get(request.turn_id) is active:
                self._active.pop(request.turn_id, None)
