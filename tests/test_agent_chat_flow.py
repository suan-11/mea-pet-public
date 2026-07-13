"""Hermes Agent 从配置到桌面气泡/TTS 的纵向聊天流。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Timer:
    def __init__(self):
        self.stopped = False
        self.deleted = False

    def stop(self):
        self.stopped = True

    def deleteLater(self):
        self.deleted = True


class _Worker:
    def __init__(self, events, *, done=True, error=None):
        self._events = tuple(events)
        self.done = done
        self.error = error
        self.deleted = False

    def take_events(self):
        events, self._events = self._events, ()
        return events

    def deleteLater(self):
        self.deleted = True


class _Bubble:
    pass


class _Stack:
    def __init__(self):
        self.begun = []
        self.updated = []
        self.finalized = []

    def begin_message(self, text="", *, mood=None):
        bubble = _Bubble()
        self.begun.append((bubble, text, mood))
        return bubble

    def update_message(self, bubble, text, *, mood=None):
        self.updated.append((bubble, text, mood))
        return True

    def finalize_message(self, bubble, text, *, duration_ms, mood=None):
        self.finalized.append((bubble, text, duration_ms, mood))
        return True


def _segment(index=0, text="你好，主人"):
    from meapet.conversation.types import ReplySegment

    return ReplySegment(
        index=index,
        display_text=text,
        voice_text=text,
        voice_language="zh",
        mood="happy",
        tts_style="轻声",
    )


def _completed(segment):
    from meapet.agent.base import TurnCompleted
    from meapet.conversation.output_protocol import ParseResult

    return TurnCompleted(
        "turn-flow",
        ParseResult((segment,), (), True, "meapet"),
    )


class TestAgentFactory(unittest.TestCase):
    def test_hermes_factory_resolves_env_and_persists_session_scope(self):
        from meapet.agent.factory import create_agent_adapter_from_config
        from meapet.agent.hermes import HermesAdapter
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "llm": {
                    "mode": "agent",
                    "agent": {
                        "kind": "hermes",
                        "base_url": "http://192.168.1.8:8642/v1",
                        "auth_token": "$HERMES_API_SERVER_KEY",
                        "history_turns": 5,
                        "tls": {"verify": True, "ca_file": ""},
                    },
                }
            }
        )

        with mock.patch.dict(
            os.environ,
            {"HERMES_API_SERVER_KEY": "env-secret"},
            clear=False,
        ):
            adapter = create_agent_adapter_from_config(config)

        self.assertIsInstance(adapter, HermesAdapter)
        self.assertEqual(adapter.config.auth_token, "env-secret")
        self.assertEqual(adapter.config.base_url, "http://192.168.1.8:8642/v1")
        self.assertTrue(config["llm"]["agent"]["session_id"])
        self.assertTrue(config["llm"]["agent"]["session_key"])
        self.assertEqual(
            adapter.config.session_id,
            config["llm"]["agent"]["session_id"],
        )

    def test_factory_does_not_treat_unimplemented_openclaw_as_generic_http(self):
        from meapet.agent.factory import create_agent_adapter_from_config
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "llm": {
                    "mode": "agent",
                    "agent": {
                        "kind": "openclaw",
                        "base_url": "ws://127.0.0.1:18789",
                        "auth_token": "token",
                    },
                }
            }
        )

        with self.assertRaisesRegex(NotImplementedError, "OpenClaw"):
            create_agent_adapter_from_config(config)


class TestAgentChatWorkerSelection(unittest.TestCase):
    def test_agent_mode_builds_agent_worker_with_frontend_context_and_history(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.workers import AgentChatWorker

        class Memory:
            @staticmethod
            def get_affection_tier():
                return 20, "熟悉", ""

        class TTS:
            enabled = False
            voice_lang = "zh"

        class Host(PetChatFlowMixin):
            config = {
                "llm": {"mode": "agent"},
                "bubble_duration_ms": {"reply": 3000},
            }
            agent_adapter = object()
            memory = Memory()
            tts = TTS()
            _use_live2d = False
            _standby = False
            _awaiting_reply = True
            _agent_history = [
                {"role": "user", "content": "上一问"},
                {"role": "assistant", "content": "上一答"},
            ]

        host = Host()
        worker = host._make_chat_worker("这次的问题")

        self.assertIsInstance(worker, AgentChatWorker)
        request = worker.request
        self.assertEqual(request.user_text, "这次的问题")
        self.assertEqual(request.history, tuple(host._agent_history))
        self.assertFalse(request.tts_enabled)
        self.assertEqual(
            request.frontend_context["frontend_capabilities"]["renderer"],
            "png",
        )
        self.assertEqual(
            request.frontend_context["companion_state"]["affection_level"],
            "熟悉",
        )
        self.assertEqual(request.turn_id, host._active_agent_turn_id)


class TestAgentChatPolling(unittest.TestCase):
    def _host(self, *, tts_enabled=False):
        from meapet.agent.presentation import AgentTurnPresentation
        from meapet.desktop.chat_flow import PetChatFlowMixin

        class TTS:
            enabled = tts_enabled

        class Host(PetChatFlowMixin):
            config = {
                "llm": {"mode": "agent", "agent": {"history_turns": 5}},
                "bubble_duration_ms": {"reply": 3000, "thinking": 0},
                "tts": {"sync_with_audio": True},
            }
            tts = TTS()
            _awaiting_reply = True
            _last_user_msg = "用户问题"
            _agent_history = []
            _agent_bubbles = {}
            _chat_poll = _Timer()
            _chat_timeout = _Timer()

            def __init__(self):
                self._bubble_stack = _Stack()
                self._agent_presentation = AgentTurnPresentation(
                    tts_enabled=tts_enabled,
                    reply_min_duration_ms=3000,
                )
                self.positions = 0
                self.played = []
                self.system_messages = []

            def _position_bubble(self, **_kwargs):
                self.positions += 1

            def _safe_set_mood(self, mood):
                self.last_mood = mood

            def _play_audio(self, path):
                self.played.append(path)

            def _show_bubble(self, text, duration_ms=None, mood=None):
                self.system_messages.append((text, duration_ms, mood))

            def show_reply(self, *_args, **_kwargs):
                raise AssertionError("Agent 系统状态/错误不能走角色回复路径")

        return Host()

    def test_no_tts_stream_updates_and_finalizes_one_bubble(self):
        from meapet.conversation.output_protocol import (
            SegmentCompleted,
            SegmentStarted,
            SegmentTextDelta,
        )

        segment = _segment()
        host = self._host(tts_enabled=False)
        host._chat_worker = _Worker(
            (
                SegmentStarted(0),
                SegmentTextDelta(0, "你好"),
                SegmentTextDelta(0, "，主人"),
                SegmentCompleted(segment),
                _completed(segment),
            )
        )

        host._poll_chat()

        stack = host._bubble_stack
        self.assertEqual(len(stack.begun), 1)
        bubble = stack.begun[0][0]
        self.assertEqual(
            stack.updated,
            [(bubble, "你好", None), (bubble, "你好，主人", None)],
        )
        self.assertEqual(
            stack.finalized,
            [(bubble, "你好，主人", 3000, "happy")],
        )
        self.assertFalse(host._awaiting_reply)
        self.assertEqual(
            host._agent_history,
            [
                {"role": "user", "content": "用户问题"},
                {"role": "assistant", "content": "你好，主人"},
            ],
        )
        self.assertTrue(host._chat_poll.stopped)
        self.assertTrue(host._chat_timeout.stopped)

    def test_tts_reply_stays_hidden_until_audio_ready_and_busy_until_playback_ends(self):
        from meapet.conversation.output_protocol import SegmentCompleted
        import meapet.desktop.chat_flow as chat_flow

        segment = _segment()
        host = self._host(tts_enabled=True)
        host._chat_worker = _Worker((SegmentCompleted(segment), _completed(segment)))
        scheduled = []

        class VoiceWorker:
            def __init__(
                self,
                _tts,
                text,
                mood="neutral",
                style="",
                language="",
            ):
                self.text = text
                self.mood = mood
                self.style = style
                self.language = language
                self.done = False
                self.result = None

            def start(self):
                pass

            def get_result(self):
                return self.result

        with mock.patch.object(chat_flow, "TTSWorker", VoiceWorker):
            host._poll_chat()

        self.assertEqual(host._bubble_stack.begun, [])
        self.assertEqual(host._bubble_stack.finalized, [])
        self.assertTrue(host._awaiting_reply)
        voice_worker = host._agent_tts_workers[0]
        self.assertEqual(voice_worker.text, "你好，主人")
        self.assertEqual(voice_worker.mood, "happy")
        self.assertEqual(voice_worker.style, "轻声")
        self.assertEqual(voice_worker.language, "zh")

        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "reply.wav"
            wav.write_bytes(b"RIFF" + b"\x00" * 64)
            voice_worker.result = f"{wav}|zh"
            voice_worker.done = True
            host._get_wav_duration_ms = lambda _path: 1200
            host._tts_poll = _Timer()
            with mock.patch.object(
                chat_flow.QTimer,
                "singleShot",
                side_effect=lambda delay, callback: scheduled.append((delay, callback)),
            ):
                host._poll_tts()

            self.assertEqual(len(host._bubble_stack.begun), 1)
            bubble = host._bubble_stack.begun[0][0]
            self.assertEqual(
                host._bubble_stack.finalized,
                [(bubble, "你好，主人", 3000, "happy")],
            )
            self.assertEqual(host.played, [str(wav)])
            self.assertTrue(host._awaiting_reply)
            self.assertEqual(scheduled[0][0], 1200)

            scheduled[0][1]()

        self.assertFalse(host._awaiting_reply)
        self.assertEqual(host._agent_history[-1]["content"], "你好，主人")

    def test_agent_failure_is_neutral_system_message_and_does_not_write_history(self):
        from meapet.agent.base import TurnFailed

        host = self._host(tts_enabled=True)
        host._chat_worker = _Worker(
            (
                TurnFailed(
                    "turn-flow",
                    "authentication",
                    "Agent 认证失败，请检查访问令牌。",
                ),
            )
        )

        host._poll_chat()

        self.assertEqual(
            host.system_messages,
            [("Agent 认证失败，请检查访问令牌。", 10000, None)],
        )
        self.assertFalse(host._awaiting_reply)
        self.assertEqual(host._agent_history, [])
        self.assertFalse(hasattr(host, "last_mood"))


if __name__ == "__main__":
    unittest.main()
