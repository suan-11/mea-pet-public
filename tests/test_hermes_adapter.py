"""Hermes 官方 API Server 适配器契约。"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _chat_chunk(content=None, finish_reason=None) -> str:
    delta = {} if content is None else {"content": content}
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "hermes-agent",
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish_reason}
            ],
        },
        ensure_ascii=False,
    )


def _valid_sse() -> bytes:
    first = "<MEAPET_SEGMENT><DISPLAY>你好"
    second = (
        "，主人</DISPLAY>"
        '<META>{"voice_text":"你好，主人","voice_language":"zh",'
        '"mood":"happy","tts_style":"轻声"}</META>'
        "</MEAPET_SEGMENT><MEAPET_DONE />"
    )
    tool_running = json.dumps(
        {
            "tool": "terminal",
            "toolCallId": "call-secret",
            "status": "running",
            "label": "正在查资料",
            "args": {"token": "must-not-leak"},
        },
        ensure_ascii=False,
    )
    tool_done = json.dumps(
        {
            "tool": "terminal",
            "toolCallId": "call-secret",
            "status": "completed",
            "label": "资料已整理",
        },
        ensure_ascii=False,
    )
    return (
        f"data: {_chat_chunk()}\n\n"
        f"event: hermes.tool.progress\ndata: {tool_running}\n\n"
        f"data: {_chat_chunk(first)}\n\n"
        ": keepalive\n\n"
        f"data: {_chat_chunk(second)}\n\n"
        f"event: hermes.tool.progress\ndata: {tool_done}\n\n"
        f"data: {_chat_chunk(finish_reason='stop')}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")


class TestHermesConfig(unittest.TestCase):
    def test_base_url_accepts_host_or_v1_without_duplicating_path(self):
        from meapet.agent.hermes import HermesConfig

        host = HermesConfig(
            base_url="http://127.0.0.1:8642/",
            auth_token="token",
        )
        versioned = HermesConfig(
            base_url="http://127.0.0.1:8642/v1",
            auth_token="token",
        )

        self.assertEqual(
            host.endpoint("/v1/chat/completions"),
            "http://127.0.0.1:8642/v1/chat/completions",
        )
        self.assertEqual(
            versioned.endpoint("/v1/chat/completions"),
            "http://127.0.0.1:8642/v1/chat/completions",
        )

    def test_session_headers_reject_control_characters_and_excess_length(self):
        from meapet.agent.hermes import HermesConfig

        with self.assertRaisesRegex(ValueError, "session_id"):
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="token",
                session_id="bad\r\nheader",
            )
        with self.assertRaisesRegex(ValueError, "session_key"):
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="token",
                session_key="x" * 257,
            )

    def test_base_url_rejects_embedded_credentials_query_and_unknown_scheme(self):
        from meapet.agent.hermes import HermesConfig

        invalid_urls = (
            "http://user:password@127.0.0.1:8642",
            "http://127.0.0.1:8642?token=secret",
            "ws://127.0.0.1:8642",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "base_url"):
                HermesConfig(base_url=url, auth_token="token")


class TestHermesAdapter(unittest.IsolatedAsyncioTestCase):
    def _request(self, *, turn_id="turn-1", history=()):
        from meapet.agent.base import AgentTurnRequest

        return AgentTurnRequest(
            turn_id=turn_id,
            user_text="现在几点",
            history=history,
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
        )

    async def test_probe_uses_bearer_and_parses_official_capabilities(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["authorization"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={
                    "object": "hermes.api_server.capabilities",
                    "platform": "hermes-agent",
                    "model": "hermes-agent",
                    "auth": {"type": "bearer", "required": True},
                    "features": {
                        "chat_completions": True,
                        "run_submission": True,
                    },
                    "session_key_header": "X-Hermes-Session-Key",
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://127.0.0.1:8642/v1",
                auth_token="top-secret",
            ),
            client=client,
        )

        capabilities = await adapter.probe()

        self.assertEqual(seen["url"], "http://127.0.0.1:8642/v1/capabilities")
        self.assertEqual(seen["authorization"], "Bearer top-secret")
        self.assertEqual(capabilities.platform, "hermes-agent")
        self.assertEqual(capabilities.model, "hermes-agent")
        self.assertTrue(capabilities.chat_completions)
        self.assertTrue(capabilities.features["run_submission"])

    async def test_missing_token_fails_before_any_network_request(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(base_url="http://127.0.0.1:8642", auth_token=""),
            client=client,
        )

        with self.assertRaisesRegex(ValueError, "auth_token"):
            await adapter.probe()
        self.assertEqual(calls, [])

    async def test_probe_rejects_a_non_hermes_endpoint(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"platform": "some-proxy", "features": {}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(base_url="http://127.0.0.1:8642", auth_token="secret"),
            client=client,
        )

        with self.assertRaisesRegex(ValueError, "not a Hermes"):
            await adapter.probe()

    async def test_stream_turn_sends_session_headers_recent_history_and_format_only_prompt(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_valid_sse(),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://192.168.1.8:8642",
                auth_token="secret",
                model="hermes-agent",
                session_id="transcript-a",
                session_key="agent:main:meapet:user",
                history_turns=1,
            ),
            client=client,
        )
        history = (
            {"role": "user", "content": "旧用户"},
            {"role": "assistant", "content": "旧回复"},
            {"role": "user", "content": "最近用户"},
            {"role": "assistant", "content": "最近回复"},
        )

        events = [event async for event in adapter.stream_turn(self._request(history=history))]

        self.assertEqual(
            seen["url"],
            "http://192.168.1.8:8642/v1/chat/completions",
        )
        headers = {key.lower(): value for key, value in seen["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer secret")
        self.assertEqual(headers["x-hermes-session-id"], "transcript-a")
        self.assertEqual(
            headers["x-hermes-session-key"],
            "agent:main:meapet:user",
        )
        self.assertEqual(headers["idempotency-key"], "turn-1")

        body = seen["body"]
        self.assertTrue(body["stream"])
        self.assertEqual(body["model"], "hermes-agent")
        self.assertEqual(
            body["messages"][1:],
            [
                {"role": "user", "content": "最近用户"},
                {"role": "assistant", "content": "最近回复"},
                {"role": "user", "content": "现在几点"},
            ],
        )
        system = body["messages"][0]["content"]
        self.assertIn("<MEAPET_SEGMENT>", system)
        self.assertIn("voice_language", system)
        self.assertIn('"renderer":"png"', system)
        self.assertNotIn("猫娘", system)
        self.assertTrue(events)

    async def test_stream_maps_text_tool_lifecycle_and_final_result_without_raw_tool_data(self):
        from meapet.agent.base import ToolStatus, TurnCompleted
        from meapet.agent.hermes import HermesAdapter, HermesConfig
        from meapet.conversation.output_protocol import (
            ProtocolCompleted,
            SegmentCompleted,
            SegmentTextDelta,
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_valid_sse(),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="secret",
            ),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        tool_events = [event for event in events if isinstance(event, ToolStatus)]
        text_events = [event for event in events if isinstance(event, SegmentTextDelta)]
        completed_segments = [
            event for event in events if isinstance(event, SegmentCompleted)
        ]
        turns = [event for event in events if isinstance(event, TurnCompleted)]

        self.assertEqual(
            [(event.state, event.safe_text) for event in tool_events],
            [("started", "正在查资料"), ("succeeded", "资料已整理")],
        )
        self.assertNotIn("terminal", repr(tool_events))
        self.assertNotIn("must-not-leak", repr(tool_events))
        self.assertEqual("".join(event.delta for event in text_events), "你好，主人")
        self.assertEqual(len(completed_segments), 1)
        self.assertEqual(completed_segments[0].segment.voice_language, "zh")
        self.assertTrue(any(isinstance(event, ProtocolCompleted) for event in events))
        self.assertEqual(len(turns), 1)
        self.assertFalse(turns[0].result.requires_repair(tts_enabled=True))

    async def test_http_auth_error_becomes_safe_typed_failure(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"message": "private server diagnostic"}},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="wrong",
            ),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "authentication")
        self.assertNotIn("private server diagnostic", repr(events[0]))
        self.assertNotIn("wrong", repr(events[0]))

    async def test_other_http_errors_keep_distinct_safe_categories(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        cases = (
            (403, "permission", False),
            (429, "rate_limit", True),
            (503, "backend_unavailable", True),
            (418, "protocol", False),
        )
        for status_code, category, retryable in cases:
            with self.subTest(status_code=status_code):
                def handler(
                    _request: httpx.Request,
                    code: int = status_code,
                ) -> httpx.Response:
                    return httpx.Response(code, text="private upstream details")

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                adapter = HermesAdapter(
                    HermesConfig(
                        base_url="http://127.0.0.1:8642",
                        auth_token="secret",
                    ),
                    client=client,
                )
                try:
                    events = [
                        event
                        async for event in adapter.stream_turn(self._request())
                    ]
                finally:
                    await client.aclose()

                self.assertEqual(len(events), 1)
                self.assertIsInstance(events[0], TurnFailed)
                self.assertEqual(events[0].category, category)
                self.assertEqual(events[0].retryable, retryable)
                self.assertNotIn("private upstream details", repr(events[0]))

    async def test_non_sse_success_becomes_protocol_failure(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(base_url="http://127.0.0.1:8642", auth_token="secret"),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "protocol")

    async def test_connection_failure_becomes_retryable_typed_failure(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("private network details", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(base_url="http://127.0.0.1:8642", auth_token="secret"),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "connection")
        self.assertTrue(events[0].retryable)
        self.assertNotIn("private network details", repr(events[0]))

    async def test_unformatted_text_requests_one_repair_but_keeps_best_effort_result(self):
        from meapet.agent.base import FormatRepairRequired, TurnCompleted
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        body = (
            f"data: {_chat_chunk('先保住这句文字')}\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(base_url="http://127.0.0.1:8642", auth_token="secret"),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        repairs = [event for event in events if isinstance(event, FormatRepairRequired)]
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].result.segments[0].display_text, "先保住这句文字")

    async def test_malformed_sse_json_becomes_protocol_failure(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"data: {not-json}\n\ndata: [DONE]\n\n",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="secret",
            ),
            client=client,
        )

        events = [event async for event in adapter.stream_turn(self._request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "protocol")

    async def test_cancelled_turn_stops_before_opening_stream(self):
        from meapet.agent.base import TurnCancelled
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="http://127.0.0.1:8642",
                auth_token="secret",
            ),
            client=client,
        )
        await adapter.cancel("turn-cancelled")

        events = [
            event
            async for event in adapter.stream_turn(
                self._request(turn_id="turn-cancelled")
            )
        ]

        self.assertEqual(events, [TurnCancelled(turn_id="turn-cancelled")])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
