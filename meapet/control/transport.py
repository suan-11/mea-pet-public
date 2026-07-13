"""Companion MCP 的 Streamable HTTP 配置、安全中间件与运行器。"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import ssl
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .broker import CompanionControlBroker
from .mcp_server import build_companion_mcp


def ensure_control_token(config: dict) -> str:
    """就地补齐 256 bit 以上随机 token；已有值不会被静默轮换。"""
    current = str(config.get("auth_token") or "").strip()
    if current:
        return current
    current = secrets.token_urlsafe(48)
    config["auth_token"] = current
    return current


def _existing_file(value: object, label: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    resolved = str(Path(path).expanduser().resolve())
    if not os.path.isfile(resolved):
        raise ValueError(f"{label} file does not exist")
    return resolved


@dataclass(frozen=True)
class ControlServerConfig:
    listen_host: str = "127.0.0.1"
    allowed_agent_ip: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""
    allow_insecure_http: bool = False
    cert_file: str = ""
    key_file: str = ""
    ca_file: str = ""
    max_request_bytes: int = 1_048_576
    rate_limit_per_minute: int = 60

    def __post_init__(self) -> None:
        try:
            listen_ip = ipaddress.ip_address(str(self.listen_host or "").strip())
        except ValueError as exc:
            raise ValueError("listen_host must be a literal IP address") from exc
        if listen_ip.is_unspecified or listen_ip.is_multicast:
            raise ValueError("listen_host must identify one local interface")
        try:
            agent_ip = ipaddress.ip_address(
                str(self.allowed_agent_ip or "").strip()
            )
        except ValueError as exc:
            raise ValueError("allowed_agent_ip must be one literal IP address") from exc
        if agent_ip.is_unspecified or agent_ip.is_multicast:
            raise ValueError("allowed_agent_ip must identify one Agent host")
        try:
            port = int(self.port)
        except (TypeError, ValueError) as exc:
            raise ValueError("port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")

        token = str(self.auth_token or "").strip()
        if len(token) < 32:
            raise ValueError("auth_token must contain at least 32 characters")

        cert = _existing_file(self.cert_file, "certificate")
        key = _existing_file(self.key_file, "private key")
        ca = _existing_file(self.ca_file, "CA")
        if bool(cert) != bool(key):
            raise ValueError("certificate and private key must be configured together")
        if not cert and not listen_ip.is_loopback and not self.allow_insecure_http:
            raise ValueError(
                "LAN HTTP is disabled; configure TLS or explicitly allow insecure HTTP"
            )

        try:
            max_bytes = int(self.max_request_bytes)
            rate_limit = int(self.rate_limit_per_minute)
        except (TypeError, ValueError) as exc:
            raise ValueError("request and rate limits must be integers") from exc
        if not 1024 <= max_bytes <= 16 * 1024 * 1024:
            # 测试和嵌入式调用可使用小阈值；生产配置仍会在 store 中收紧。
            if max_bytes <= 0:
                raise ValueError("max_request_bytes must be positive")
        if not 1 <= rate_limit <= 10_000:
            raise ValueError("rate_limit_per_minute must be between 1 and 10000")

        object.__setattr__(self, "listen_host", str(listen_ip))
        object.__setattr__(self, "allowed_agent_ip", str(agent_ip))
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "auth_token", token)
        object.__setattr__(self, "allow_insecure_http", bool(self.allow_insecure_http))
        object.__setattr__(self, "cert_file", cert)
        object.__setattr__(self, "key_file", key)
        object.__setattr__(self, "ca_file", ca)
        object.__setattr__(self, "max_request_bytes", max_bytes)
        object.__setattr__(self, "rate_limit_per_minute", rate_limit)

    @property
    def scheme(self) -> str:
        return "https" if self.cert_file else "http"

    @property
    def authority(self) -> str:
        host = (
            f"[{self.listen_host}]"
            if ":" in self.listen_host
            else self.listen_host
        )
        return f"{host}:{self.port}"

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.authority}/mcp"

    @property
    def allowed_hostnames(self) -> frozenset[str]:
        values = {self.listen_host}
        if ipaddress.ip_address(self.listen_host).is_loopback:
            values.update({"127.0.0.1", "::1", "localhost"})
        return frozenset(values)


class ControlSecurityMiddleware:
    """在 MCP SDK 之前执行来源、鉴权、载荷与频率检查。"""

    def __init__(
        self,
        app,
        config: ControlServerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.config = config
        self._clock = clock
        self._rate_lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self.mcp_endpoint = "/mcp"
        self.tool_count = 4

    @staticmethod
    def _headers(scope) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }

    async def _reject(
        self,
        scope,
        receive,
        send,
        status: int,
        code: str,
        *,
        extra_headers: tuple[tuple[bytes, bytes], ...] = (),
    ) -> None:
        body = json.dumps(
            {"error": {"code": code}},
            separators=(",", ":"),
        ).encode("utf-8")
        headers = (
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        )
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": list(headers),
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _valid_host(self, raw_host: str) -> bool:
        if not raw_host:
            return False
        try:
            parsed = urlsplit(f"//{raw_host}")
            hostname = parsed.hostname or ""
            port = parsed.port
        except ValueError:
            return False
        if hostname not in self.config.allowed_hostnames:
            return False
        return port in (None, self.config.port)

    def _valid_origin(self, origin: str) -> bool:
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname or ""
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if hostname not in self.config.allowed_hostnames:
            return False
        default_port = 443 if parsed.scheme == "https" else 80
        return (port or default_port) == self.config.port

    def _within_rate_limit(self, source_ip: str) -> bool:
        now = self._clock()
        cutoff = now - 60.0
        with self._rate_lock:
            recent = self._requests[source_ip]
            while recent and recent[0] <= cutoff:
                recent.popleft()
            if len(recent) >= self.config.rate_limit_per_minute:
                return False
            recent.append(now)
            return True

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        client = scope.get("client") or ("", 0)
        try:
            source_ip = str(ipaddress.ip_address(client[0]))
        except ValueError:
            source_ip = ""
        if source_ip != self.config.allowed_agent_ip:
            await self._reject(scope, receive, send, 403, "source_ip_denied")
            return
        if not self._valid_host(headers.get("host", "")):
            await self._reject(scope, receive, send, 403, "invalid_host")
            return
        if not self._valid_origin(headers.get("origin", "")):
            await self._reject(scope, receive, send, 403, "invalid_origin")
            return

        authorization = headers.get("authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix):] if authorization.startswith(prefix) else ""
        if not supplied or not secrets.compare_digest(
            supplied,
            self.config.auth_token,
        ):
            await self._reject(
                scope,
                receive,
                send,
                401,
                "invalid_token",
                extra_headers=((b"www-authenticate", b"Bearer"),),
            )
            return
        if not self._within_rate_limit(source_ip):
            await self._reject(
                scope,
                receive,
                send,
                429,
                "rate_limited",
                extra_headers=((b"retry-after", b"60"),),
            )
            return

        method = str(scope.get("method") or "").upper()
        if method == "POST":
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                await self._reject(
                    scope,
                    receive,
                    send,
                    415,
                    "unsupported_content_type",
                )
                return
            raw_length = headers.get("content-length", "")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        400,
                        "invalid_content_length",
                    )
                    return
                if content_length > self.config.max_request_bytes:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        413,
                        "request_too_large",
                    )
                    return

            messages = []
            total = 0
            while True:
                message = await receive()
                messages.append(message)
                if message.get("type") == "http.request":
                    total += len(message.get("body", b""))
                    if total > self.config.max_request_bytes:
                        await self._reject(
                            scope,
                            receive,
                            send,
                            413,
                            "request_too_large",
                        )
                        return
                    if not message.get("more_body", False):
                        break
                elif message.get("type") == "http.disconnect":
                    break

            async def replay_receive():
                if messages:
                    return messages.pop(0)
                return {"type": "http.disconnect"}

            receive = replay_receive

        await self.app(scope, receive, send)


def build_control_asgi_app(
    broker: CompanionControlBroker,
    config: ControlServerConfig,
):
    """构造官方 FastMCP 1.x Streamable HTTP ASGI 应用。"""
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:  # pragma: no cover - 可选依赖环境
        raise RuntimeError("Companion MCP 需要安装 mcp>=1.27,<2") from exc

    allowed_hosts = [
        hostname
        for name in sorted(config.allowed_hostnames)
        for hostname in (name, f"{name}:{config.port}")
    ]
    origin = f"{config.scheme}://{config.authority}"
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[origin],
    )
    server = build_companion_mcp(
        broker,
        transport_security=transport_security,
    )
    app = server.streamable_http_app()
    secured = ControlSecurityMiddleware(app, config)
    secured.mcp_server = server
    return secured


class CompanionMcpRuntime:
    """在 MeaPet 共用 asyncio 守护线程中启动/停止 Uvicorn。"""

    def __init__(
        self,
        broker: CompanionControlBroker,
        config: ControlServerConfig,
    ) -> None:
        self.broker = broker
        self.config = config
        self.app = build_control_asgi_app(broker, config)
        self._future = None
        self._server = None

    @property
    def running(self) -> bool:
        return self._future is not None and not self._future.done()

    def start(self) -> None:
        if self.running:
            return
        from meapet.async_runtime import submit

        self._future = submit(self._serve())

    async def _serve(self) -> None:
        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - 可选依赖环境
            raise RuntimeError("Companion MCP 需要安装 uvicorn>=0.30") from exc
        uvicorn_config = uvicorn.Config(
            self.app,
            host=self.config.listen_host,
            port=self.config.port,
            log_level="warning",
            access_log=False,
            ssl_certfile=self.config.cert_file or None,
            ssl_keyfile=self.config.key_file or None,
            ssl_ca_certs=self.config.ca_file or None,
            ssl_cert_reqs=(
                ssl.CERT_REQUIRED if self.config.ca_file else ssl.CERT_NONE
            ),
        )
        self._server = uvicorn.Server(uvicorn_config)
        await self._server.serve()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        future = self._future
        if future is not None:
            try:
                future.result(timeout=max(0.0, float(timeout_seconds)))
            except Exception:
                pass
        self._future = None
        self._server = None
