"""Companion MCP Streamable HTTP 的监听与安全门闩。"""

from __future__ import annotations

import json
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def _ok_app(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    payload = json.dumps({"ok": True, "size": len(body)}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class TestControlServerConfig(unittest.TestCase):
    def test_generates_strong_token_and_requires_explicit_lan_http(self):
        from meapet.control.transport import (
            ControlServerConfig,
            ensure_control_token,
        )

        raw = {"auth_token": ""}
        token = ensure_control_token(raw)
        self.assertGreaterEqual(len(token), 43)
        self.assertEqual(raw["auth_token"], token)
        self.assertEqual(ensure_control_token(raw), token)

        with self.assertRaisesRegex(ValueError, "HTTP"):
            ControlServerConfig(
                listen_host="192.168.1.10",
                allowed_agent_ip="192.168.1.20",
                port=8765,
                auth_token=token,
                allow_insecure_http=False,
            )

        config = ControlServerConfig(
            listen_host="192.168.1.10",
            allowed_agent_ip="192.168.1.20",
            port=8765,
            auth_token=token,
            allow_insecure_http=True,
        )
        self.assertEqual(config.scheme, "http")
        self.assertEqual(config.endpoint, "http://192.168.1.10:8765/mcp")

    def test_tls_requires_existing_certificate_and_private_key(self):
        from meapet.control.transport import ControlServerConfig

        with tempfile.TemporaryDirectory() as td:
            cert = Path(td) / "cert.pem"
            key = Path(td) / "key.pem"
            cert.write_text("test cert", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "key"):
                ControlServerConfig(
                    listen_host="192.168.1.10",
                    allowed_agent_ip="192.168.1.20",
                    port=8765,
                    auth_token="x" * 48,
                    cert_file=str(cert),
                    key_file=str(key),
                )

            ca = Path(td) / "client-ca.pem"
            ca.write_text("test ca", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CA"):
                ControlServerConfig(
                    auth_token="x" * 48,
                    ca_file=str(ca),
                )

            key.write_text("test key", encoding="utf-8")
            config = ControlServerConfig(
                listen_host="192.168.1.10",
                allowed_agent_ip="192.168.1.20",
                port=8765,
                auth_token="x" * 48,
                cert_file=str(cert),
                key_file=str(key),
            )
            self.assertEqual(config.scheme, "https")

    def test_rejects_wildcard_hosts_short_tokens_and_invalid_limits(self):
        from meapet.control.transport import ControlServerConfig

        cases = (
            ({"listen_host": "0.0.0.0", "auth_token": "x" * 48}, "interface"),
            ({"listen_host": "localhost", "auth_token": "x" * 48}, "literal IP"),
            ({"listen_host": "127.0.0.1", "auth_token": "short"}, "auth_token"),
            (
                {
                    "listen_host": "127.0.0.1",
                    "auth_token": "x" * 48,
                    "rate_limit_per_minute": 0,
                },
                "rate_limit",
            ),
        )
        for values, message in cases:
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, message):
                ControlServerConfig(
                    allowed_agent_ip="127.0.0.1",
                    port=8765,
                    **values,
                )


class TestControlSecurityMiddleware(unittest.IsolatedAsyncioTestCase):
    def _config(self, **overrides):
        from meapet.control.transport import ControlServerConfig

        values = {
            "listen_host": "127.0.0.1",
            "allowed_agent_ip": "127.0.0.1",
            "port": 8765,
            "auth_token": "t" * 48,
            "max_request_bytes": 128,
            "rate_limit_per_minute": 2,
        }
        values.update(overrides)
        return ControlServerConfig(**values)

    async def _request(
        self,
        config,
        *,
        headers=None,
        content=b"{}",
        client=("127.0.0.1", 55123),
        app=None,
    ):
        from meapet.control.transport import ControlSecurityMiddleware

        app = app or ControlSecurityMiddleware(_ok_app, config)
        transport = httpx.ASGITransport(app=app, client=client)
        merged = {
            "Authorization": f"Bearer {config.auth_token}",
            "Content-Type": "application/json",
            "Host": "127.0.0.1:8765",
        }
        if headers:
            merged.update(headers)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8765",
        ) as http:
            return await http.post("/mcp", headers=merged, content=content)

    async def test_valid_request_passes_and_wrong_token_or_ip_is_rejected(self):
        config = self._config()
        accepted = await self._request(config)
        wrong_token = await self._request(
            config,
            headers={"Authorization": "Bearer wrong"},
        )
        wrong_ip = await self._request(
            self._config(rate_limit_per_minute=10),
            client=("127.0.0.2", 55123),
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(wrong_token.status_code, 401)
        self.assertEqual(wrong_ip.status_code, 403)

    async def test_host_origin_content_type_and_body_size_are_enforced(self):
        self.assertEqual(
            (await self._request(self._config(), headers={"Host": "evil.test"})).status_code,
            403,
        )
        self.assertEqual(
            (
                await self._request(
                    self._config(),
                    headers={"Origin": "https://evil.test"},
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            (
                await self._request(
                    self._config(),
                    headers={"Content-Type": "text/plain"},
                )
            ).status_code,
            415,
        )
        self.assertEqual(
            (
                await self._request(
                    self._config(max_request_bytes=8),
                    content=b"{" + b"x" * 20 + b"}",
                )
            ).status_code,
            413,
        )
        accepted_origin = await self._request(
            self._config(rate_limit_per_minute=10),
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(accepted_origin.status_code, 200)

    async def test_rate_limit_is_per_source_and_returns_retry_after(self):
        from meapet.control.transport import ControlSecurityMiddleware

        config = self._config(rate_limit_per_minute=2)
        app = ControlSecurityMiddleware(_ok_app, config)
        first = await self._request(config, app=app)
        second = await self._request(config, app=app)
        third = await self._request(config, app=app)

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(third.status_code, 429)
        self.assertIn("retry-after", third.headers)


class TestControlAsgiSurface(unittest.TestCase):
    def test_streamable_http_app_uses_single_mcp_endpoint(self):
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.transport import (
            ControlServerConfig,
            build_control_asgi_app,
        )

        app = build_control_asgi_app(
            CompanionControlBroker(state={}),
            ControlServerConfig(
                listen_host="127.0.0.1",
                allowed_agent_ip="127.0.0.1",
                port=8765,
                auth_token="t" * 48,
            ),
        )

        self.assertEqual(app.mcp_endpoint, "/mcp")
        self.assertEqual(app.tool_count, 4)

    def test_runtime_start_and_stop_are_idempotent(self):
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.transport import (
            CompanionMcpRuntime,
            ControlServerConfig,
        )

        runtime = CompanionMcpRuntime(
            CompanionControlBroker(state={}),
            ControlServerConfig(
                listen_host="127.0.0.1",
                allowed_agent_ip="127.0.0.1",
                port=8765,
                auth_token="t" * 48,
            ),
        )

        class Future:
            def __init__(self):
                self.result_calls = 0

            @staticmethod
            def done():
                return False

            def result(self, timeout=None):
                self.result_calls += 1

        future = Future()
        submitted = []

        def submit(coroutine):
            submitted.append(coroutine)
            coroutine.close()
            return future

        with mock.patch("meapet.async_runtime.submit", side_effect=submit):
            runtime.start()
            runtime.start()
        self.assertEqual(len(submitted), 1)

        server = SimpleNamespace(should_exit=False)
        runtime._server = server
        runtime.stop(timeout_seconds=0.1)
        self.assertTrue(server.should_exit)
        self.assertEqual(future.result_calls, 1)
        self.assertFalse(runtime.running)

        # Default stop is non-blocking (no Future.result) so GUI callers
        # never stall the Qt event loop.
        future2 = Future()
        runtime._future = future2
        server2 = SimpleNamespace(should_exit=False)
        runtime._server = server2
        runtime.stop()
        self.assertTrue(server2.should_exit)
        self.assertEqual(future2.result_calls, 0)
        self.assertFalse(runtime.running)

    def test_configured_client_ca_requires_a_client_certificate(self):
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.transport import (
            CompanionMcpRuntime,
            ControlServerConfig,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cert = root / "server.pem"
            key = root / "server-key.pem"
            ca = root / "client-ca.pem"
            for path in (cert, key, ca):
                path.write_text("test material", encoding="utf-8")
            runtime = CompanionMcpRuntime(
                CompanionControlBroker(state={}),
                ControlServerConfig(
                    auth_token="t" * 48,
                    cert_file=str(cert),
                    key_file=str(key),
                    ca_file=str(ca),
                ),
            )

            server = SimpleNamespace(serve=mock.AsyncMock())
            with mock.patch("uvicorn.Config") as make_config, mock.patch(
                "uvicorn.Server",
                return_value=server,
            ):
                import asyncio

                asyncio.run(runtime._serve())

        self.assertEqual(
            make_config.call_args.kwargs["ssl_cert_reqs"],
            ssl.CERT_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
