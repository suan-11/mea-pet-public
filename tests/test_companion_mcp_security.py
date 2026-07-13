"""Companion MCP Streamable HTTP 的监听与安全门闩。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
