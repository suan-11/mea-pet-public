"""直连模型四种线协议的 Canonical 事件契约。"""

from __future__ import annotations

import json
import sys
import unittest
from unittest import mock
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sse(*payloads: object) -> bytes:
    chunks = []
    for payload in payloads:
        if isinstance(payload, tuple):
            event, data = payload
            chunks.append(f"event: {event}\ndata: {json.dumps(data)}\n\n")
        elif payload == "[DONE]":
            chunks.append("data: [DONE]\n\n")
        else:
            chunks.append(f"data: {json.dumps(payload)}\n\n")
    return "".join(chunks).encode("utf-8")


def _request():
    from meapet.direct.types import CanonicalChatRequest

    return CanonicalChatRequest(
        model="model-test",
        messages=(
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "hello"},
        ),
        temperature=0.35,
        max_tokens=777,
        stream=True,
    )


class TestDirectProtocolConfig(unittest.TestCase):
    def test_normalizes_urls_and_rejects_embedded_credentials_or_query(self):
        from meapet.direct.client import DirectProtocolConfig

        config = DirectProtocolConfig(
            protocol="openai_chat",
            base_url="https://models.example.test/v1/",
            api_key="secret",
        )
        self.assertEqual(config.base_url, "https://models.example.test/v1")

        for url in (
            "models.example.test/v1",
            "ftp://models.example.test/v1",
            "https://user:pass@models.example.test/v1",
            "https://models.example.test/v1?key=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                DirectProtocolConfig(protocol="openai_chat", base_url=url)

        with self.assertRaisesRegex(ValueError, "protocol"):
            DirectProtocolConfig(
                protocol="unknown",
                base_url="https://models.example.test/v1",
            )

    def test_canonical_request_validates_model_limits_roles_and_content(self):
        from meapet.direct.types import CanonicalChatRequest

        base = {
            "model": "m",
            "messages": ({"role": "user", "content": "hi"},),
        }
        invalid = (
            ({**base, "model": ""}, "model"),
            ({**base, "temperature": "bad"}, "temperature"),
            ({**base, "temperature": 3}, "temperature"),
            ({**base, "max_tokens": "bad"}, "max_tokens"),
            ({**base, "max_tokens": 0}, "max_tokens"),
            ({**base, "messages": ()}, "messages"),
            ({**base, "messages": ("not-a-message",)}, "mappings"),
            (
                {**base, "messages": ({"role": "tool", "content": "x"},)},
                "role",
            ),
            (
                {**base, "messages": ({"role": "user", "content": 7},)},
                "content",
            ),
        )
        for values, pattern in invalid:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(
                ValueError,
                pattern,
            ):
                CanonicalChatRequest(**values)

        request = CanonicalChatRequest(
            **base,
            response_format={"type": "json_object"},
            extra={"seed": 7},
        )
        self.assertEqual(request.response_format, {"type": "json_object"})
        self.assertEqual(request.extra, {"seed": 7})

    def test_multimodal_parts_are_rendered_for_each_native_protocol(self):
        from meapet.direct.client import (
            DirectProtocolConfig,
            _anthropic_spec,
            _ollama_spec,
            _openai_chat_spec,
            _responses_spec,
        )
        from meapet.direct.types import CanonicalChatRequest

        request = CanonicalChatRequest(
            model="vision-test",
            messages=(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请看这张图"},
                        {
                            "type": "image",
                            "media_type": "image/jpeg",
                            "data": "YWJj",
                        },
                    ],
                },
            ),
        )

        def config(protocol):
            return DirectProtocolConfig(
                protocol=protocol,
                base_url="https://models.example.test/v1",
            )

        openai = _openai_chat_spec(config("openai_chat"), request).body
        self.assertEqual(
            openai["messages"][0]["content"],
            [
                {"type": "text", "text": "请看这张图"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,YWJj"},
                },
            ],
        )

        ollama = _ollama_spec(config("ollama_chat"), request).body
        self.assertEqual(
            ollama["messages"][0],
            {"role": "user", "content": "请看这张图", "images": ["YWJj"]},
        )

        responses = _responses_spec(
            config("openai_responses"),
            request,
        ).body
        self.assertEqual(
            responses["input"][0]["content"],
            [
                {"type": "input_text", "text": "请看这张图"},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,YWJj",
                },
            ],
        )

        anthropic = _anthropic_spec(
            config("anthropic_messages"),
            request,
        ).body
        self.assertEqual(
            anthropic["messages"][0]["content"],
            [
                {"type": "text", "text": "请看这张图"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "YWJj",
                    },
                },
            ],
        )

    def test_canonical_multimodal_parts_reject_remote_urls_and_bad_base64(self):
        from meapet.direct.types import CanonicalChatRequest

        for image_part in (
            {"type": "image_url", "image_url": {"url": "https://private.test/a"}},
            {"type": "image", "media_type": "image/jpeg", "data": "not base64"},
            {"type": "image", "media_type": "text/plain", "data": "YWJj"},
        ):
            with self.subTest(part=image_part), self.assertRaisesRegex(
                ValueError,
                "image",
            ):
                CanonicalChatRequest(
                    model="vision-test",
                    messages=(
                        {"role": "user", "content": [image_part]},
                    ),
                )


class TestDirectProtocolClient(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, protocol, handler, *, base_url, api_key="secret"):
        from meapet.direct.client import DirectProtocolClient, DirectProtocolConfig

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        adapter = DirectProtocolClient(
            DirectProtocolConfig(
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=5,
            ),
            client=client,
        )
        return [event async for event in adapter.stream(_request())]

    async def test_openai_chat_uses_chat_completions_sse_and_falls_back_reasoning_to_content(self):
        """Ollama qwen3.5 等模型将实际文本放在 reasoning 字段，
        content 始终为空。此时 reasoning 应兜底到 TextDelta。"""
        from meapet.direct.types import ReasoningDelta, StreamDone, TextDelta

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(
                    {
                        "choices": [
                            {"delta": {"reasoning_content": "private thought",
                                       "content": ""}}
                        ]
                    },
                    {"choices": [{"delta": {"content": "你"}}]},
                    {"choices": [{"delta": {"content": "好"}}]},
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                    },
                    "[DONE]",
                ),
            )

        events = await self._collect(
            "openai_chat",
            handler,
            base_url="https://models.example.test/v1",
        )

        self.assertEqual(seen["url"], "https://models.example.test/v1/chat/completions")
        headers = {key.lower(): value for key, value in seen["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer secret")
        self.assertEqual(
            seen["body"],
            {
                "model": "model-test",
                "messages": [
                    {"role": "system", "content": "system rules"},
                    {"role": "user", "content": "hello"},
                ],
                "temperature": 0.35,
                "max_tokens": 777,
                "stream": True,
                "stream_options": {"include_usage": True},
                "think": False,
            },
        )
        # content 为空时 reasoning 兜底到 TextDelta → "private thought你好"
        self.assertEqual(
            "".join(event.delta for event in events if isinstance(event, TextDelta)),
            "private thought你好",
        )
        # reasoning 被消费后不再有 ReasoningDelta
        self.assertEqual(
            "".join(
                event.delta for event in events if isinstance(event, ReasoningDelta)
            ),
            "",
        )
        self.assertIsInstance(events[-1], StreamDone)

    async def test_ollama_chat_uses_ndjson_and_native_generation_options(self):
        from meapet.direct.types import ReasoningDelta, StreamDone, TextDelta

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            lines = (
                {"message": {"role": "assistant", "thinking": "secret"}, "done": False},
                {"message": {"role": "assistant", "content": "你"}, "done": False},
                {"message": {"role": "assistant", "content": "好"}, "done": False},
                {"message": {"role": "assistant", "content": ""}, "done": True},
            )
            content = "".join(json.dumps(item) + "\n" for item in lines).encode()
            return httpx.Response(
                200,
                headers={"Content-Type": "application/x-ndjson"},
                content=content,
            )

        events = await self._collect(
            "ollama_chat",
            handler,
            base_url="http://127.0.0.1:11434/",
            api_key="",
        )

        self.assertEqual(seen["url"], "http://127.0.0.1:11434/api/chat")
        self.assertNotIn(
            "authorization",
            {key.lower(): value for key, value in seen["headers"].items()},
        )
        body = seen["body"]
        self.assertEqual(body["model"], "model-test")
        self.assertEqual(body["messages"], list(_request().messages))
        self.assertTrue(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["keep_alive"], "5m")
        self.assertEqual(body["options"]["temperature"], 0.35)
        self.assertEqual(body["options"]["num_predict"], 777)
        self.assertEqual(
            "".join(event.delta for event in events if isinstance(event, TextDelta)),
            "你好",
        )
        self.assertEqual(
            "".join(
                event.delta for event in events if isinstance(event, ReasoningDelta)
            ),
            "secret",
        )
        self.assertIsInstance(events[-1], StreamDone)

    async def test_openai_responses_uses_native_input_and_event_types(self):
        from meapet.direct.types import ReasoningDelta, StreamDone, TextDelta

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(
                    {
                        "type": "response.reasoning_text.delta",
                        "delta": "private thought",
                    },
                    {"type": "response.output_text.delta", "delta": "你"},
                    {"type": "response.output_text.delta", "delta": "好"},
                    {
                        "type": "response.completed",
                        "response": {"usage": {"input_tokens": 5, "output_tokens": 2}},
                    },
                ),
            )

        events = await self._collect(
            "openai_responses",
            handler,
            base_url="https://models.example.test/v1",
        )

        self.assertEqual(seen["url"], "https://models.example.test/v1/responses")
        self.assertEqual(
            seen["body"],
            {
                "model": "model-test",
                "input": [
                    {"role": "system", "content": "system rules"},
                    {"role": "user", "content": "hello"},
                ],
                "temperature": 0.35,
                "max_output_tokens": 777,
                "stream": True,
            },
        )
        self.assertEqual(
            "".join(event.delta for event in events if isinstance(event, TextDelta)),
            "你好",
        )
        self.assertEqual(
            "".join(
                event.delta for event in events if isinstance(event, ReasoningDelta)
            ),
            "private thought",
        )
        self.assertIsInstance(events[-1], StreamDone)

    async def test_anthropic_messages_extracts_top_level_system_and_ignores_thinking_ui(self):
        from meapet.direct.types import ReasoningDelta, StreamDone, TextDelta

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "thinking_delta",
                                "thinking": "private thought",
                            },
                        },
                    ),
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 1,
                            "delta": {"type": "text_delta", "text": "你"},
                        },
                    ),
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 1,
                            "delta": {"type": "text_delta", "text": "好"},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ),
            )

        events = await self._collect(
            "anthropic_messages",
            handler,
            base_url="https://api.anthropic.test/v1",
        )

        self.assertEqual(seen["url"], "https://api.anthropic.test/v1/messages")
        headers = {key.lower(): value for key, value in seen["headers"].items()}
        self.assertEqual(headers["x-api-key"], "secret")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", headers)
        self.assertEqual(
            seen["body"],
            {
                "model": "model-test",
                "system": "system rules",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.35,
                "max_tokens": 777,
                "stream": True,
            },
        )
        self.assertEqual(
            "".join(event.delta for event in events if isinstance(event, TextDelta)),
            "你好",
        )
        self.assertEqual(
            "".join(
                event.delta for event in events if isinstance(event, ReasoningDelta)
            ),
            "private thought",
        )
        self.assertIsInstance(events[-1], StreamDone)

    async def test_http_and_midstream_errors_are_typed_and_never_expose_secret_body(self):
        from meapet.direct.client import DirectProtocolError

        cases = (
            (401, "authentication", False),
            (403, "permission", False),
            (429, "rate_limit", True),
            (503, "backend_unavailable", True),
        )
        for status, category, retryable in cases:
            with self.subTest(status=status):
                calls = {"count": 0}

                def handler(_request: httpx.Request, code=status) -> httpx.Response:
                    calls["count"] += 1
                    return httpx.Response(code, text="private upstream body secret")

                with mock.patch("meapet.direct.client.asyncio.sleep", new=mock.AsyncMock()):
                    with self.assertRaises(DirectProtocolError) as raised:
                        await self._collect(
                            "openai_chat",
                            handler,
                            base_url="https://models.example.test/v1",
                        )
                self.assertEqual(raised.exception.category, category)
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertNotIn("private upstream body", repr(raised.exception))
                self.assertNotIn("secret", repr(raised.exception))
                # 可重试错误会在首事件前连试 3 次；不可重试只打一次。
                self.assertEqual(calls["count"], 3 if retryable else 1)

        def stream_error(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/x-ndjson"},
                content=b'{"error":"private model details"}\n',
            )

        with self.assertRaises(DirectProtocolError) as raised:
            await self._collect(
                "ollama_chat",
                stream_error,
                base_url="http://127.0.0.1:11434",
                api_key="",
            )
        self.assertEqual(raised.exception.category, "backend")
        self.assertNotIn("private model details", repr(raised.exception))


    async def test_retryable_network_error_is_retried_before_success(self):
        from meapet.direct.types import StreamDone, TextDelta

        attempts = {"count": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise httpx.ConnectError("temporary network blip")
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(
                    {"choices": [{"delta": {"content": "重试后成功"}}]},
                    "[DONE]",
                ),
            )

        with mock.patch("meapet.direct.client.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            events = await self._collect(
                "openai_chat",
                handler,
                base_url="https://models.example.test/v1",
            )
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.4, 0.8],
        )
        self.assertEqual(
            "".join(event.delta for event in events if isinstance(event, TextDelta)),
            "重试后成功",
        )
        self.assertIsInstance(events[-1], StreamDone)

    async def test_retry_stops_after_first_event_reaches_the_caller(self):
        from meapet.direct.client import (
            DirectProtocolClient,
            DirectProtocolConfig,
            DirectProtocolError,
        )
        from meapet.direct.types import CanonicalChatRequest, TextDelta

        client = DirectProtocolClient(
            DirectProtocolConfig(
                protocol="openai_chat",
                base_url="https://models.example.test/v1",
            )
        )
        request = CanonicalChatRequest(
            model="model-test",
            messages=({"role": "user", "content": "hello"},),
        )
        attempts = {"count": 0}

        async def partial_stream(_request, *, attempt=1):
            attempts["count"] += 1
            yield TextDelta("已经显示")
            raise DirectProtocolError(
                "connection",
                "无法连接模型接口，请检查地址和网络。",
                True,
            )

        client._stream_once = partial_stream
        emitted = []
        with mock.patch(
            "meapet.direct.client.asyncio.sleep",
            new=mock.AsyncMock(),
        ) as sleep:
            with self.assertRaises(DirectProtocolError):
                async for event in client.stream(request):
                    emitted.append(event)

        self.assertEqual(attempts["count"], 1)
        self.assertEqual([event.delta for event in emitted], ["已经显示"])
        sleep.assert_not_awaited()

    async def test_non_retryable_auth_error_is_not_retried(self):
        from meapet.direct.client import DirectProtocolError

        attempts = {"count": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            return httpx.Response(401, text="nope")

        with mock.patch("meapet.direct.client.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            with self.assertRaises(DirectProtocolError) as raised:
                await self._collect(
                    "openai_chat",
                    handler,
                    base_url="https://models.example.test/v1",
                )
        self.assertEqual(raised.exception.category, "authentication")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(attempts["count"], 1)
        sleep.assert_not_awaited()

    async def test_malformed_stream_is_protocol_error_not_partial_success(self):
        from meapet.direct.client import DirectProtocolError

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"data: {not-json}\n\n",
            )

        with self.assertRaises(DirectProtocolError) as raised:
            await self._collect(
                "openai_chat",
                handler,
                base_url="https://models.example.test/v1",
            )
        self.assertEqual(raised.exception.category, "protocol")


if __name__ == "__main__":
    unittest.main()
