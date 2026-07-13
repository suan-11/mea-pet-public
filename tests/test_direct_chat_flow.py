"""直连模型协议层到统一桌面呈现的纵向契约。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _valid_output(text="你好，主人"):
    return (
        f"<MEAPET_SEGMENT><DISPLAY>{text}</DISPLAY>"
        f'<META>{{"voice_text":"{text}","voice_language":"zh",'
        '"mood":"happy","tts_style":"轻声"}</META>'
        "</MEAPET_SEGMENT><MEAPET_DONE />"
    )


class _FakeProtocolClient:
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
    def _engine(self, client):
        from meapet.chat.engine import ChatEngine

        engine = ChatEngine(
            backend="custom",
            protocol="openai_chat",
            api_base="https://models.example.test/v1",
            model="model-test",
            api_key="secret",
            max_tokens=900,
            direct_client=client,
        )
        engine.available = True
        return engine

    async def test_stream_turn_adds_meapet_persona_and_shared_output_protocol(self):
        from meapet.agent.base import TurnCompleted
        from meapet.conversation.output_protocol import SegmentTextDelta

        output = _valid_output()
        client = _FakeProtocolClient((tuple(output),))
        engine = self._engine(client)

        events = [event async for event in engine.stream_turn(_request())]

        self.assertEqual(len(client.requests), 1)
        canonical = client.requests[0]
        self.assertEqual(canonical.model, "model-test")
        self.assertEqual(canonical.max_tokens, 900)
        self.assertTrue(canonical.stream)
        system = canonical.messages[0]["content"]
        self.assertIn("你是梅尔", system)
        self.assertIn("<MEAPET_SEGMENT>", system)
        self.assertIn('"renderer":"png"', system)
        self.assertNotIn("第三行", system)
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

        client = _FakeProtocolClient((( _valid_output("看到了"),),))
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

        self.assertEqual(
            client.requests[0].messages[-1],
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "现在几点"},
                    {
                        "type": "image",
                        "media_type": "image/jpeg",
                        "data": "YWJj",
                    },
                ],
            },
        )
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
        self.assertIn("纯格式转换器", serialized)
        self.assertEqual(
            sum(isinstance(event, FormatRepairRequired) for event in events),
            1,
        )
        completed = [event for event in events if isinstance(event, TurnCompleted)]
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
        self.assertEqual(engine.history, [{"role": "system", "content": engine.history[0]["content"]}])

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
    def test_nested_direct_profile_is_the_runtime_source_of_truth(self):
        from meapet.chat.engine import create_engine_from_config
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "llm": {
                    "mode": "direct",
                    "backend": "ollama",
                    "model": "legacy-must-not-win",
                    "direct": {
                        "provider": "custom",
                        "protocol": "anthropic_messages",
                        "api_base": "https://api.anthropic.test/v1",
                        "host": "",
                        "model": "claude-test",
                        "api_key": "$CUSTOM_MODEL_KEY",
                        "temperature": 0.25,
                        "max_tokens": 1234,
                    },
                }
            }
        )

        with mock.patch.dict(
            os.environ,
            {"CUSTOM_MODEL_KEY": "env-secret"},
            clear=False,
        ):
            engine = create_engine_from_config(config)

        self.assertEqual(engine.backend, "custom")
        self.assertEqual(engine.protocol, "anthropic_messages")
        self.assertEqual(engine.api_base, "https://api.anthropic.test/v1")
        self.assertEqual(engine.model, "claude-test")
        self.assertEqual(engine.api_key, "env-secret")
        self.assertEqual(engine.temperature, 0.25)
        self.assertEqual(engine.max_tokens, 1234)
        self.assertTrue(engine.available)

    def test_legacy_profiles_infer_protocol_without_user_migration(self):
        from meapet.chat.engine import create_engine_from_config
        from meapet.config.store import normalize_config

        ollama = create_engine_from_config(
            normalize_config(
                {"llm": {"backend": "ollama", "model": "qwen-test"}}
            )
        )
        deepseek = create_engine_from_config(
            normalize_config(
                {
                    "llm": {
                        "backend": "deepseek",
                        "api_key": "secret",
                        "model": "deepseek-test",
                    }
                }
            )
        )

        self.assertEqual(ollama.protocol, "ollama_chat")
        self.assertEqual(deepseek.protocol, "openai_chat")


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

        self.assertIsInstance(worker, AgentChatWorker)
        self.assertIs(worker.adapter, host.chat_engine)
        self.assertEqual(worker.request.user_text, "直连问题")
        self.assertEqual(worker.request.history, ())
        self.assertFalse(worker.request.tts_enabled)
        self.assertEqual(host._active_agent_turn_id, worker.request.turn_id)
        self.assertFalse(host._agent_presentation.tts_enabled)


if __name__ == "__main__":
    unittest.main()
