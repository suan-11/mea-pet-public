"""OpenAI 兼容适配器的单元测试。"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OpenAIAdapterTests(unittest.IsolatedAsyncioTestCase):
    """测试 OpenAIAdapter 的核心行为。"""

    def _make_adapter(self, **overrides):
        """构造一个 OpenAIAdapter，使用 mocked httpx client。"""
        from meapet.agent.openai_adapter import OpenAIAdapter, OpenAIConfig

        config = OpenAIConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=1024,
            timeout_seconds=30,
        )
        # 应用覆盖
        for k, v in overrides.items():
            object.__setattr__(config, k, v)

        adapter = OpenAIAdapter(config)
        # Mock the httpx client
        adapter._client = MagicMock()
        return adapter, config

    def _make_request(self, **overrides):
        """构造一个 AgentTurnRequest。"""
        from meapet.agent.base import AgentTurnRequest
        req = AgentTurnRequest(
            turn_id="test-turn-1",
            user_text="你好",
            tts_enabled=True,
        )
        for k, v in overrides.items():
            object.__setattr__(req, k, v)
        return req

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------
    def test_config_from_dict_with_openai_url(self):
        from meapet.agent.openai_adapter import OpenAIConfig
        cfg = OpenAIConfig.from_dict({
            "base_url": "https://models.example.com/v1",
            "api_key": "sk-abc",
            "model": "custom-model",
            "temperature": 0.5,
            "max_tokens": 2048,
        })
        self.assertEqual(cfg.base_url, "https://models.example.com/v1")
        self.assertEqual(cfg.api_key, "sk-abc")
        self.assertEqual(cfg.model, "custom-model")
        self.assertEqual(cfg.temperature, 0.5)
        self.assertEqual(cfg.max_tokens, 2048)

    def test_config_from_dict_falls_back_to_default(self):
        from meapet.agent.openai_adapter import OpenAIConfig, DEFAULT_OPENAI_BASE_URL
        cfg = OpenAIConfig.from_dict({})
        self.assertEqual(cfg.base_url, DEFAULT_OPENAI_BASE_URL)
        self.assertEqual(cfg.model, "")
        self.assertEqual(cfg.temperature, 0.7)

    def test_config_from_dict_accepts_host_alias(self):
        from meapet.agent.openai_adapter import OpenAIConfig
        cfg = OpenAIConfig.from_dict({"host": "http://localhost:8000/v1"})
        self.assertEqual(cfg.base_url, "http://localhost:8000/v1")

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------
    def test_build_messages_includes_system_prompt(self):
        adapter, _ = self._make_adapter()
        req = self._make_request()
        messages = adapter._build_messages(req)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("MEAPET_SEGMENT", messages[0]["content"])
        self.assertIn("DISPLAY", messages[0]["content"])

    def test_build_messages_includes_user_text(self):
        adapter, _ = self._make_adapter()
        req = self._make_request(user_text="今天天气怎么样？")
        messages = adapter._build_messages(req)

        user_msg = messages[-1]
        self.assertEqual(user_msg["role"], "user")
        self.assertEqual(user_msg["content"][0]["type"], "text")
        self.assertEqual(user_msg["content"][0]["text"], "今天天气怎么样？")

    def test_build_messages_includes_image_attachments(self):
        adapter, _ = self._make_adapter()
        from meapet.agent.base import ImageAttachment
        att = ImageAttachment(
            media_type="image/png",
            data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII=",
            file_name="test.png",
        )
        req = self._make_request(attachments=(att,))
        messages = adapter._build_messages(req)

        user_msg = messages[-1]
        image_part = next(p for p in user_msg["content"] if p["type"] == "image_url")
        self.assertEqual(image_part["image_url"]["url"].startswith("data:image/png;base64,"), True)

    def test_build_messages_includes_history(self):
        adapter, _ = self._make_adapter()
        req = self._make_request(history=(
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ))
        messages = adapter._build_messages(req)

        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "之前的问题")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "之前的回答")

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def test_capabilities_default(self):
        from meapet.agent.openai_adapter import OpenAICapabilities
        caps = OpenAICapabilities.from_config(
            self._make_adapter()[0]._config
        )
        self.assertTrue(caps.streaming)
        self.assertTrue(caps.repair)

    def test_capabilities_detect_vision_for_vision_models(self):
        from meapet.agent.openai_adapter import OpenAICapabilities
        adapter, _ = self._make_adapter(model="gpt-4o")
        caps = OpenAICapabilities.from_config(adapter._config)
        self.assertTrue(caps.vision)

    # ------------------------------------------------------------------
    # 流式解析
    # ------------------------------------------------------------------
    async def test_chat_stream_successful_response(self):
        adapter, _ = self._make_adapter()

        # Mock streaming response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = AsyncMock(return_value=iter([
            'data: {"choices":[{"delta":{"content":"你好"}}]}',
            'data: {"choices":[{"delta":{"content":"，世界"}}]}',
            'data: [DONE]',
        ]))

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        # 应该产出一个 TurnCompleted
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].__class__.__name__, "TurnCompleted")
        self.assertEqual(results[0].turn_id, "test-turn-1")

    async def test_chat_stream_empty_response_yields_failure(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = AsyncMock(return_value=iter([
            'data: {"choices":[{}]}',
            'data: [DONE]',
        ]))

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].__class__.__name__, "TurnFailed")
        self.assertEqual(results[0].category, "empty_response")

    async def test_chat_stream_api_error_yields_failure(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.aread = AsyncMock(return_value=b'{"error":"unauthorized"}')

        adapter._client.stream = MagicMock(side_effect=httpx_exception(401))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].__class__.__name__, "TurnFailed")
        self.assertEqual(results[0].category, "api_error")
        self.assertTrue(results[0].retryable)

    async def test_chat_stream_network_error_yields_failure(self):
        adapter, _ = self._make_adapter()

        adapter._client.stream = MagicMock(side_effect=ConnectionError("DNS failed"))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].__class__.__name__, "TurnFailed")
        self.assertEqual(results[0].category, "network_error")
        self.assertTrue(results[0].retryable)

    async def test_chat_stream_cancellation(self):
        import threading
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = AsyncMock(return_value=iter([
            'data: {"choices":[{"delta":{"content":"部分"}}]}',
        ]))

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        cancel_event = threading.Event()
        cancel_event.set()  # 已取消

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req, cancel_event=cancel_event):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].__class__.__name__, "TurnCancelled")

    # ------------------------------------------------------------------
    # 格式修复
    # ------------------------------------------------------------------
    async def test_repair_format_success(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "<MEAPET_SEGMENT>修复后</MEAPET_SEGMENT>"}}]
        })

        adapter._client.post = AsyncMock(return_value=mock_response)

        result = await adapter.repair_format("畸形内容", request=self._make_request())
        self.assertIsNotNone(result)
        self.assertIn("修复后", result)

    async def test_repair_format_failure_returns_none(self):
        adapter, _ = self._make_adapter()
        adapter._client.post = AsyncMock(side_effect=Exception("API down"))
        result = await adapter.repair_format("畸形内容")
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    async def test_close_calls_client_aclose(self):
        adapter, _ = self._make_adapter()
        adapter._client.aclose = AsyncMock()
        await adapter.close()
        adapter._client.aclose.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# 辅助类
# ---------------------------------------------------------------------------

class AsyncContextManager:
    """简单的异步上下文管理器包装器。"""
    def __init__(self, obj):
        self.obj = obj
    async def __aenter__(self):
        return self.obj
    async def __aexit__(self, *args):
        pass


def httpx_exception(status_code=500):
    """构造一个 httpx.HTTPStatusError。"""
    import httpx
    request = MagicMock()
    response = MagicMock()
    response.status_code = status_code
    return httpx.HTTPStatusError("error", request=request, response=response)


if __name__ == "__main__":
    unittest.main()
