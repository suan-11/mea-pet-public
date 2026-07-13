"""OpenClaw 官方 Gateway v4 适配器契约。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _hello_ok(*, methods=None):
    return {
        "type": "hello-ok",
        "protocol": 4,
        "server": {"version": "2026.7.1", "connId": "conn-test"},
        "features": {
            "methods": methods or ["chat.send", "chat.abort"],
            "events": ["chat", "session.operation", "session.tool"],
        },
        "snapshot": {},
        "auth": {
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
        },
        "policy": {
            "maxPayload": 26_214_400,
            "maxBufferedBytes": 52_428_800,
            "tickIntervalMs": 15_000,
        },
    }


def _challenge(nonce="challenge-nonce"):
    return {
        "type": "event",
        "event": "connect.challenge",
        "payload": {"nonce": nonce, "ts": 1_735_000_000_000},
    }


def _response(request_id, *, ok=True, payload=None, error=None):
    value = {"type": "res", "id": request_id, "ok": ok}
    if payload is not None:
        value["payload"] = payload
    if error is not None:
        value["error"] = error
    return value


def _chat_event(state, *, run_id="run-1", **extra):
    payload = {
        "runId": run_id,
        "sessionKey": "agent:main:meapet:test",
        "seq": extra.pop("seq", 0),
        "state": state,
        **extra,
    }
    return {"type": "event", "event": "chat", "payload": payload}


def _valid_chunks():
    return (
        "<MEAPET_SEGMENT><DISPLAY>你好",
        "，主人</DISPLAY>"
        '<META>{"voice_text":"你好，主人","voice_language":"zh",'
        '"mood":"happy","tts_style":"轻声"}</META>'
        "</MEAPET_SEGMENT><MEAPET_DONE />",
    )


class _FakeSocket:
    def __init__(self, incoming=()):
        self.incoming = asyncio.Queue()
        for frame in incoming:
            self.push(frame)
        self.sent = []
        self.closed = False

    def push(self, frame):
        value = frame if isinstance(frame, str) else json.dumps(frame, ensure_ascii=False)
        self.incoming.put_nowait(value)

    async def recv(self):
        return await self.incoming.get()

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed = True


class _Connection:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, _exc_type, _exc, _tb):
        await self.socket.close()


class _Connector:
    def __init__(self, *sockets):
        self.sockets = list(sockets)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Connection(self.sockets.pop(0))


class TestOpenClawConfig(unittest.TestCase):
    def test_accepts_loopback_ws_and_remote_wss_without_credentials_in_url(self):
        from meapet.agent.openclaw import OpenClawConfig

        local = OpenClawConfig(
            base_url="ws://127.0.0.1:18789/",
            auth_token="secret",
            session_key="agent:main:meapet:test",
        )
        remote = OpenClawConfig(
            base_url="wss://gateway.example.test/openclaw",
            auth_token="secret",
            session_key="agent:main:meapet:test",
        )

        self.assertEqual(local.base_url, "ws://127.0.0.1:18789")
        self.assertEqual(remote.base_url, "wss://gateway.example.test/openclaw")

    def test_rejects_unsafe_url_and_remote_plaintext_without_explicit_opt_in(self):
        from meapet.agent.openclaw import OpenClawConfig

        invalid = (
            "http://127.0.0.1:18789",
            "ws://user:password@127.0.0.1:18789",
            "ws://127.0.0.1:18789?token=secret",
            "ws://192.168.1.8:18789",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenClawConfig(
                    base_url=url,
                    auth_token="secret",
                    session_key="agent:main:meapet:test",
                )

        opted_in = OpenClawConfig(
            base_url="ws://192.168.1.8:18789",
            auth_token="secret",
            session_key="agent:main:meapet:test",
            allow_insecure_ws=True,
        )
        self.assertTrue(opted_in.allow_insecure_ws)


class TestOpenClawDeviceIdentity(unittest.TestCase):
    def test_v3_payload_and_ed25519_signature_match_official_byte_contract(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from meapet.agent.openclaw_identity import (
            OpenClawDeviceIdentity,
            build_device_auth_payload_v3,
        )

        private_bytes = bytes(range(32))
        identity = OpenClawDeviceIdentity.from_private_bytes(private_bytes)
        expected_id = hashlib.sha256(identity.public_key_bytes).hexdigest()
        payload = build_device_auth_payload_v3(
            device_id=identity.device_id,
            client_id="webchat",
            client_mode="webchat",
            role="operator",
            scopes=("operator.read", "operator.write"),
            signed_at_ms=1_735_000_000_123,
            token="shared-token",
            nonce="challenge-nonce",
            platform="Windows",
            device_family="Desktop",
        )

        self.assertEqual(identity.device_id, expected_id)
        self.assertEqual(
            payload,
            "v3|"
            f"{expected_id}|webchat|webchat|operator|operator.read,operator.write|"
            "1735000000123|shared-token|challenge-nonce|windows|desktop",
        )
        signature = identity.sign(payload)
        decoded = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        Ed25519PublicKey.from_public_bytes(identity.public_key_bytes).verify(
            decoded,
            payload.encode("utf-8"),
        )
        self.assertNotIn("=", signature)
        self.assertNotIn("=", identity.public_key)

    def test_identity_file_round_trip_is_private(self):
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "identity.json"
            created = OpenClawDeviceIdentity.load_or_create(path)
            loaded = OpenClawDeviceIdentity.load_or_create(path)

            self.assertEqual(created.device_id, loaded.device_id)
            self.assertEqual(created.private_key_bytes, loaded.private_key_bytes)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_corrupt_identity_files_fail_closed_instead_of_rotating_identity(self):
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = root / "missing.json"
            with self.assertRaisesRegex(ValueError, "cannot be read"):
                OpenClawDeviceIdentity.load(missing)

            invalid_json = root / "invalid-json.json"
            invalid_json.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid JSON"):
                OpenClawDeviceIdentity.load(invalid_json)

            invalid_key = root / "invalid-key.json"
            invalid_key.write_text(
                json.dumps({"version": 1, "privateKey": "not*base64"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "base64url"):
                OpenClawDeviceIdentity.load(invalid_key)

            identity = OpenClawDeviceIdentity.from_private_bytes(bytes(range(32)))
            mismatched = root / "mismatched.json"
            mismatched.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "deviceId": "0" * 64,
                        "privateKey": base64.urlsafe_b64encode(
                            identity.private_key_bytes
                        ).decode("ascii").rstrip("="),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                OpenClawDeviceIdentity.load(mismatched)


class TestOpenClawAdapter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._identity_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self._identity_dir.cleanup()

    def _config(self, **overrides):
        from meapet.agent.openclaw import OpenClawConfig

        values = {
            "base_url": "ws://127.0.0.1:18789",
            "auth_token": "shared-secret",
            "session_key": "agent:main:meapet:test",
            "timeout_seconds": 2,
            "identity_path": str(
                Path(self._identity_dir.name) / "openclaw-identity.json"
            ),
        }
        values.update(overrides)
        return OpenClawConfig(**values)

    def _request(self, *, turn_id="turn-1", attachments=()):
        from meapet.agent.base import AgentTurnRequest

        return AgentTurnRequest(
            turn_id=turn_id,
            user_text="现在几点",
            history=(
                {"role": "user", "content": "不应复制到 OpenClaw"},
                {"role": "assistant", "content": "因为会话由 Gateway 保存"},
            ),
            frontend_context={
                "frontend_capabilities": {
                    "renderer": "png",
                    "supported_moods": ["neutral", "happy"],
                    "tts_enabled": True,
                    "tts_languages": ["zh"],
                    "streaming_text": True,
                    "multi_segment": True,
                },
                "companion_state": {
                    "affection_level": "熟悉",
                    "character_state": "idle",
                    "current_mood": "neutral",
                    "busy": False,
                },
            },
            tts_enabled=True,
            attachments=attachments,
        )

    def _socket_for_turn(self, *chat_frames):
        return _FakeSocket(
            (
                _challenge(),
                _response("connect", payload=_hello_ok()),
                _response("send:turn-1", payload={"runId": "run-1", "status": "started"}),
                *chat_frames,
            )
        )

    async def test_handshake_uses_v4_minimal_scopes_device_signature_and_token_body(self):
        from meapet.agent.openclaw import OpenClawAdapter
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        first, second = _valid_chunks()
        socket = self._socket_for_turn(
            _chat_event("delta", deltaText=first, seq=1),
            _chat_event("delta", deltaText=second, seq=2),
            _chat_event("final", seq=3),
        )
        connector = _Connector(socket)
        identity = OpenClawDeviceIdentity.from_private_bytes(bytes(range(32)))
        adapter = OpenClawAdapter(
            self._config(),
            connector=connector,
            identity=identity,
            clock_ms=lambda: 1_735_000_000_123,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(connector.calls[0][0], "ws://127.0.0.1:18789")
        self.assertNotIn("shared-secret", connector.calls[0][0])
        connect = socket.sent[0]
        self.assertEqual(connect["type"], "req")
        self.assertEqual(connect["id"], "connect")
        self.assertEqual(connect["method"], "connect")
        params = connect["params"]
        self.assertEqual((params["minProtocol"], params["maxProtocol"]), (4, 4))
        self.assertEqual(params["client"]["id"], "webchat")
        self.assertEqual(params["client"]["mode"], "webchat")
        self.assertEqual(params["role"], "operator")
        self.assertEqual(params["scopes"], ["operator.read", "operator.write"])
        self.assertEqual(params["caps"], [])
        self.assertEqual(params["commands"], [])
        self.assertEqual(params["permissions"], {})
        self.assertEqual(params["auth"], {"token": "shared-secret"})
        self.assertEqual(params["device"]["nonce"], "challenge-nonce")
        self.assertEqual(params["device"]["id"], identity.device_id)
        self.assertNotIn("=", params["device"]["signature"])
        self.assertNotIn("=", params["device"]["publicKey"])
        self.assertTrue(events)

    async def test_chat_send_uses_session_memory_idempotency_and_streams_contract(self):
        from meapet.agent.base import TurnCompleted
        from meapet.agent.openclaw import OpenClawAdapter
        from meapet.conversation.output_protocol import SegmentTextDelta

        first, second = _valid_chunks()
        socket = self._socket_for_turn(
            _chat_event("delta", deltaText=first, seq=1),
            _chat_event("delta", deltaText=second, seq=2),
            _chat_event("final", seq=3),
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        events = [event async for event in adapter.stream_turn(self._request())]

        send = socket.sent[1]
        self.assertEqual(send["id"], "send:turn-1")
        self.assertEqual(send["method"], "chat.send")
        params = send["params"]
        self.assertEqual(params["sessionKey"], "agent:main:meapet:test")
        self.assertEqual(params["idempotencyKey"], "turn-1")
        self.assertFalse(params["deliver"])
        self.assertIn("现在几点", params["message"])
        self.assertIn("<MEAPET_SEGMENT>", params["message"])
        self.assertIn('"renderer":"png"', params["message"])
        self.assertNotIn("不应复制到 OpenClaw", params["message"])
        text = "".join(
            event.delta for event in events if isinstance(event, SegmentTextDelta)
        )
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(text, "你好，主人")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].result.segments[0].voice_language, "zh")

    async def test_chat_send_forwards_images_through_official_attachments_field(self):
        from meapet.agent.base import ImageAttachment
        from meapet.agent.openclaw import OpenClawAdapter

        first, second = _valid_chunks()
        socket = self._socket_for_turn(
            _chat_event("delta", deltaText=first, seq=1),
            _chat_event("delta", deltaText=second, seq=2),
            _chat_event("final", seq=3),
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        request = self._request(
            attachments=(
                ImageAttachment(
                    media_type="image/jpeg",
                    data="YWJj",
                    file_name="screenshot.jpg",
                ),
            )
        )
        _events = [event async for event in adapter.stream_turn(request)]

        self.assertEqual(
            socket.sent[1]["params"]["attachments"],
            [
                {
                    "type": "image",
                    "mimeType": "image/jpeg",
                    "fileName": "screenshot.jpg",
                    "content": "YWJj",
                }
            ],
        )

    async def test_malformed_output_is_repaired_once_in_an_isolated_session(self):
        from meapet.agent.base import FormatRepairRequired, TurnCompleted
        from meapet.agent.openclaw import OpenClawAdapter

        malformed = "先保住这句文字"
        repaired = (
            "<MEAPET_SEGMENT><DISPLAY>先保住这句文字</DISPLAY>"
            '<META>{"voice_text":"先保住这句文字","voice_language":"zh",'
            '"mood":"neutral","tts_style":""}</META>'
            "</MEAPET_SEGMENT><MEAPET_DONE />"
        )
        repair_scope = hashlib.sha256(
            b"agent:main:meapet:test\x00turn-1"
        ).hexdigest()[:24]
        repair_session = f"agent:main:meapet:format-repair:{repair_scope}"
        socket = self._socket_for_turn(
            _chat_event("delta", deltaText=malformed, seq=1),
            _chat_event("final", seq=2),
            _response(
                "repair:turn-1",
                payload={"runId": "repair-run", "status": "started"},
            ),
            _chat_event(
                "delta",
                run_id="repair-run",
                sessionKey=repair_session,
                deltaText=repaired,
                seq=1,
            ),
            _chat_event(
                "final",
                run_id="repair-run",
                sessionKey=repair_session,
                seq=2,
            ),
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        events = [event async for event in adapter.stream_turn(self._request())]

        sends = [frame for frame in socket.sent if frame.get("method") == "chat.send"]
        self.assertEqual(len(sends), 2)
        repair = sends[1]
        self.assertEqual(repair["id"], "repair:turn-1")
        self.assertEqual(repair["params"]["sessionKey"], repair_session)
        self.assertEqual(
            repair["params"]["idempotencyKey"],
            "turn-1-format-repair",
        )
        self.assertIn(malformed, repair["params"]["message"])
        self.assertNotIn("现在几点", repair["params"]["message"])
        repairs = [event for event in events if isinstance(event, FormatRepairRequired)]
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "先保住这句文字",
        )
        self.assertFalse(completed[0].result.requires_repair(tts_enabled=True))

    async def test_tool_and_operation_events_expose_only_generic_safe_status(self):
        from meapet.agent.base import ToolStatus
        from meapet.agent.openclaw import OpenClawAdapter

        first, second = _valid_chunks()
        socket = self._socket_for_turn(
            {
                "type": "event",
                "event": "session.operation",
                "payload": {
                    "state": "started",
                    "operation": "private-operation-name",
                    "token": "must-not-leak",
                },
            },
            {
                "type": "event",
                "event": "session.tool",
                "payload": {
                    "state": "completed",
                    "toolName": "private-tool-name",
                    "result": "must-not-leak",
                },
            },
            _chat_event("delta", deltaText=first + second, seq=1),
            _chat_event("final", seq=2),
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        events = [event async for event in adapter.stream_turn(self._request())]

        statuses = [event for event in events if isinstance(event, ToolStatus)]
        self.assertEqual(
            [(event.state, event.safe_text) for event in statuses],
            [("started", "Agent 正在处理"), ("succeeded", "Agent 工具执行完成")],
        )
        self.assertNotIn("private", repr(statuses))
        self.assertNotIn("must-not-leak", repr(statuses))

    async def test_pairing_rejection_becomes_safe_permission_failure(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.openclaw import OpenClawAdapter

        socket = _FakeSocket(
            (
                _challenge(),
                _response(
                    "connect",
                    ok=False,
                    error={
                        "code": "DEVICE_PAIRING_REQUIRED",
                        "message": "private device details",
                        "retryable": False,
                    },
                ),
            )
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "permission")
        self.assertIn("配对", events[0].safe_message)
        self.assertNotIn("private device details", repr(events[0]))

    async def test_chat_error_and_abort_are_typed_without_private_backend_text(self):
        from meapet.agent.base import TurnCancelled, TurnFailed
        from meapet.agent.openclaw import OpenClawAdapter

        cases = (
            (
                _chat_event(
                    "error",
                    errorKind="rate_limit",
                    errorMessage="private quota details",
                    seq=1,
                ),
                TurnFailed,
                "rate_limit",
            ),
            (
                _chat_event(
                    "aborted",
                    errorMessage="private cancellation details",
                    seq=1,
                ),
                TurnCancelled,
                None,
            ),
        )
        for terminal, expected_type, category in cases:
            with self.subTest(expected_type=expected_type.__name__):
                socket = self._socket_for_turn(terminal)
                adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))
                events = [event async for event in adapter.stream_turn(self._request())]
                self.assertEqual(len(events), 1)
                self.assertIsInstance(events[0], expected_type)
                if category:
                    self.assertEqual(events[0].category, category)
                self.assertNotIn("private", repr(events[0]))

    async def test_cancel_sends_chat_abort_on_the_active_connection(self):
        from meapet.agent.base import TurnCancelled
        from meapet.agent.openclaw import OpenClawAdapter

        socket = self._socket_for_turn()
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        async def collect():
            return [event async for event in adapter.stream_turn(self._request())]

        task = asyncio.create_task(collect())
        for _ in range(100):
            if len(socket.sent) >= 2:
                break
            await asyncio.sleep(0)
        await adapter.cancel("turn-1")
        socket.push(_chat_event("aborted", seq=1))
        events = await asyncio.wait_for(task, 1)

        abort = socket.sent[2]
        self.assertEqual(abort["method"], "chat.abort")
        self.assertEqual(abort["params"]["sessionKey"], "agent:main:meapet:test")
        self.assertEqual(abort["params"]["runId"], "run-1")
        self.assertIsInstance(events[-1], TurnCancelled)

    async def test_probe_requires_chat_methods_and_returns_gateway_capabilities(self):
        from meapet.agent.openclaw import OpenClawAdapter

        socket = _FakeSocket(
            (_challenge(), _response("connect", payload=_hello_ok()))
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(socket))

        capabilities = await adapter.probe()

        self.assertEqual(capabilities.platform, "openclaw")
        self.assertEqual(capabilities.protocol, 4)
        self.assertTrue(capabilities.chat_send)
        self.assertTrue(capabilities.chat_abort)

        unsupported = _FakeSocket(
            (
                _challenge(),
                _response(
                    "connect",
                    payload=_hello_ok(methods=["health"]),
                ),
            )
        )
        adapter = OpenClawAdapter(self._config(), connector=_Connector(unsupported))
        with self.assertRaisesRegex(ValueError, "chat.send"):
            await adapter.probe()


if __name__ == "__main__":
    unittest.main()
