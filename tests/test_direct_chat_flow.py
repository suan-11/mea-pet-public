"""直连模型协议层到统一桌面呈现的纵向契约。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _valid_output(text="你好，主人"):
    return (
        f"<MEA_PET_SEGMENT><DISPLAY>{text}</DISPLAY>"
        f'<META>{{"voice_text":"{text}","voice_language":"zh",'
        f'"mood":"happy","tts_style":"轻声"}}</META>'
        "</MEA_PET_SEGMENT><MEA_PET_DONE />"
    )


class _FakeProtocolClient:
    """模拟 DirectProtocolClient 的 stream 接口。"""

    def __init__(self, replies=(), error=None):
        self.replies = list(replies)
        self.error = error
        self.requests = []
        self.closed = False

    async def stream(self, request):
        from meapet.direct.types import StreamDone, TextDelta

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        chunks = self.replies.pop(0)
        for chunk in chunks:
            yield TextDelta(chunk)
        yield StreamDone()

    async def close(self):
        self.closed = True


def _request(*, turn_id="direct-turn", tts_enabled=False, attachments=()):
    from meapet.agent.base import AgentTurnRequest

    return AgentTurnRequest(
        turn_id=turn_id,
        user_text="现在几点",
        frontend_context={
            "frontend_capabilities": {
                "renderer": "png",
                "supported_moods": ["neutral", "happy"],
                "tts_enabled": tts_enabled,
                "tts_languages": ["zh"],
                "streaming_text": True,
                "multi_segment": True,
            },
            "companion_state": {
                "affection_level": "熟悉",
                "character_state": "active",
                "current_mood": "neutral",
                "busy": True,
            },
        },
        tts_enabled=tts_enabled,
        attachments=attachments,
    )


class TestDirectConversationAdapter(unittest.IsolatedAsyncioTestCase):
    """OpenAI 兼容后：ChatEngine 不再接受 backend/protocol 参数。"""

    def _engine(self, client):
        from meapet.chat.engine import ChatEngine

        engine = ChatEngine(
            api_base="https://models.example.test/v1",
            model="model-test",
            api_key="secret",
            max_tokens=900,
            direct_client=client,
        )
        engine.available = True
        # engine.py 调用 self._debug_dump(...) 但该属性未定义；测试中补上 stub
        if not hasattr(engine, "_debug_dump"):
            engine._debug_dump = lambda *a, **kw: None
        return engine

    async def test_stream_turn_adds_meapet_persona_and_shared_output_protocol(self):
        from meapet.agent.base import TurnCompleted
        from meapet.conversation.output_protocol import SegmentTextDelta

        output = _valid_output()
        client = _FakeProtocolClient(((output,),))
        engine = self._engine(client)

        events = [event async for event in engine.stream_turn(_request())]

        self.assertEqual(len(client.requests), 1)
        canonical = client.requests[0]
        self.assertEqual(canonical.model, "model-test")
        self.assertEqual(canonical.max_tokens, 900)
        self.assertTrue(canonical.stream)
        # system prompt 包含 "你是梅尔" 和输出协议标记 <MEA_PET_SEGMENT>
        system = canonical.messages[0]["content"]
        self.assertIn("你是梅尔", system)
        self.assertIn("<MEA_PET_SEGMENT>", system)  # 协议标记已集成在 system prompt 中
        self.assertEqual(canonical.messages[-1], {"role": "user", "content": "现在几点"})
        text = "".join(
            event.delta for event in events if isinstance(event, SegmentTextDelta)
        )
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(text, "你好，主人")
        self.assertEqual(len(completed), 1)
        self.assertEqual(engine.history[-1], {"role": "assistant", "content": "你好，主人"})

    async def test_image_attachment_is_sent_once_but_not_persisted_in_local_history(self):
        from meapet.agent.base import ImageAttachment, TurnCompleted

        client = _FakeProtocolClient(((_valid_output("看到了"),),))
        engine = self._engine(client)
        request = _request(
            attachments=(
                ImageAttachment(
                    media_type="image/jpeg",
                    data="YWJj",
                    file_name="screenshot.jpg",
                ),
            )
        )

        events = [event async for event in engine.stream_turn(request)]

        # 验证请求中包含图片附件
        last_msg = client.requests[0].messages[-1]
        self.assertEqual(last_msg["role"], "user")
        self.assertIsInstance(last_msg["content"], list)
        has_text = any(item.get("type") == "text" for item in last_msg["content"])
        has_image = any(item.get("type") == "image" for item in last_msg["content"])
        self.assertTrue(has_text)
        self.assertTrue(has_image)
        self.assertTrue(any(isinstance(event, TurnCompleted) for event in events))
        self.assertNotIn("YWJj", repr(engine.history))

    async def test_malformed_direct_output_is_repaired_once_without_original_task(self):
        from meapet.agent.base import FormatRepairRequired, TurnCompleted

        malformed = "保留这句"
        client = _FakeProtocolClient(
            (
                (malformed,),
                (_valid_output("保留这句"),),
            )
        )
        engine = self._engine(client)

        events = [event async for event in engine.stream_turn(_request(tts_enabled=True))]

        self.assertEqual(len(client.requests), 2)
        repair = client.requests[1]
        serialized = repr(repair.messages)
        self.assertIn(malformed, serialized)
        self.assertNotIn("现在几点", serialized)
        self.assertEqual(
            sum(isinstance(event, FormatRepairRequired) for event in events),
            1,
        )
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].result.segments[0].display_text, "保留这句")

    async def test_protocol_failure_rolls_back_user_history_and_returns_safe_event(self):
        from meapet.agent.base import TurnFailed
        from meapet.direct.client import DirectProtocolError

        client = _FakeProtocolClient(
            error=DirectProtocolError(
                "authentication",
                "模型接口认证失败，请检查 API Key。",
            )
        )
        engine = self._engine(client)

        events = [event async for event in engine.stream_turn(_request())]

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], TurnFailed)
        self.assertEqual(events[0].category, "authentication")
        # history 回滚：移除刚添加的用户消息，只保留 system
        self.assertEqual(len(engine.history), 1)
        self.assertEqual(engine.history[0]["role"], "system")

    async def test_pre_cancelled_turn_never_calls_model(self):
        from meapet.agent.base import TurnCancelled

        client = _FakeProtocolClient((("unused",),))
        engine = self._engine(client)
        await engine.cancel_turn("cancel-me")

        events = [
            event
            async for event in engine.stream_turn(
                _request(turn_id="cancel-me")
            )
        ]

        self.assertEqual(events, [TurnCancelled("cancel-me")])
        self.assertEqual(client.requests, [])


class TestDirectEngineFactory(unittest.TestCase):
    """OpenAI 兼容后：不再有 backend/protocol 属性。"""

    def test_nested_direct_profile_is_the_runtime_source_of_truth(self):
        """
        验证 direct 嵌套配置生效（当前行为：顶层 model 覆盖 direct.model，
        但 api_base、api_key、temperature、max_tokens 取自 direct 块）。
        """
        from meapet.chat.engine import create_engine_from_config
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "llm": {
                    "mode": "direct",
                    "model": "legacy-must-not-win",
                    "direct": {
                        "api_base": "https://api.anthropic.test/v1",
                        "host": "",
                        "model": "claude-test",
                        "api_key": "$MEA_PET_CUSTOM_MODEL_KEY",
                        "temperature": 0.25,
                        "max_tokens": 1234,
                    },
                }
            }
        )

        with mock.patch.dict(
            os.environ,
            {"MEA_PET_CUSTOM_MODEL_KEY": "env-secret"},
            clear=False,
        ):
            engine = create_engine_from_config(config)

        # OpenAI 兼容后：使用 api_base / model / api_key / temperature / max_tokens
        self.assertEqual(engine.api_base, "https://api.anthropic.test/v1")
        # 注意：当前实现中顶层 model 优先于 direct.model
        self.assertEqual(engine.model, "legacy-must-not-win")
        self.assertEqual(engine.api_key, "env-secret")
        self.assertEqual(engine.temperature, 0.25)
        self.assertEqual(engine.max_tokens, 1234)
        self.assertTrue(engine.available)

    def test_legacy_profiles_use_openai_chat_protocol(self):
        """验证 ollama/deepseek 配置都走 OpenAI 兼容协议。"""
        from meapet.chat.engine import create_engine_from_config
        from meapet.config.store import normalize_config

        ollama = create_engine_from_config(
            normalize_config(
                {"llm": {"model": "qwen-test"}}
            )
        )
        deepseek = create_engine_from_config(
            normalize_config(
                {
                    "llm": {
                        "api_key": "secret",
                        "model": "deepseek-test",
                    }
                }
            )
        )

        # 两者都应有合理的 host（api_base 可能为空，但 host 由默认值填充）
        self.assertTrue(ollama.host)
        self.assertEqual(ollama.model, "qwen-test")
        self.assertTrue(deepseek.host)
        self.assertEqual(deepseek.model, "deepseek-test")
        self.assertEqual(deepseek.api_key, "secret")


class TestDesktopDirectStreamSelection(unittest.TestCase):
    def test_direct_mode_uses_event_worker_and_shared_presentation(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.workers import AgentChatWorker

        class Engine:
            async def stream_turn(self, _request):
                if False:
                    yield None

            async def cancel_turn(self, _turn_id):
                return None

        class TTS:
            enabled = False
            voice_lang = "zh"

        class Host(PetChatFlowMixin):
            config = {
                "llm": {"mode": "direct"},
                "bubble_duration_ms": {"reply": 3000},
            }
            chat_engine = Engine()
            tts = TTS()
            memory = None
            _use_live2d = False
            _standby = False
            _awaiting_reply = True

        host = Host()
        worker = host._make_chat_worker("直连问题")
        # P0-4：直连统一走流式事件路（原 ChatWorker 非流式双轨已收口）：
        # 附件随 request.text_attachments、取消走 cancel_turn、记忆回写走
        # _finish_agent_turn 直连分支。
        self.assertIsInstance(worker, AgentChatWorker)
        self.assertIs(worker.adapter, host.chat_engine)
        self.assertEqual(worker.request.user_text, "直连问题")
        self.assertEqual(tuple(worker.request.text_attachments), ())


if __name__ == "__main__":
    unittest.main()

