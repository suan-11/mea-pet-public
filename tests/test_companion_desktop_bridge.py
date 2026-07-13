"""Companion MCP 与 Qt 桌面呈现/隐私门闩的纵向接线。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _state(*, tts=False):
    return {
        "frontend_capabilities": {
            "renderer": "png",
            "supported_moods": ["neutral", "happy"],
            "supported_motions": [],
            "tts_enabled": tts,
            "tts_languages": ["zh"],
            "streaming_text": True,
            "multi_segment": True,
        },
        "companion_state": {
            "affection_level": "熟悉",
            "character_state": "active",
            "current_mood": "neutral",
            "busy": False,
        },
    }


def _segment(text="Agent 主动问候"):
    return {
        "display_text": text,
        "voice_text": text,
        "voice_language": "zh",
        "mood": "happy",
        "tts_style": "轻声",
    }


class _Bubble:
    pass


class _Stack:
    def __init__(self):
        self.begun = []
        self.finalized = []

    def begin_message(self, text="", *, mood=None):
        bubble = _Bubble()
        self.begun.append((bubble, text, mood))
        return bubble

    def finalize_message(self, bubble, text, *, duration_ms, mood=None):
        self.finalized.append((bubble, text, duration_ms, mood))
        return True


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _Timer:
    def __init__(self, *_args):
        self.timeout = _Signal()
        self.started = False
        self.stopped = False

    def start(self, _interval):
        self.started = True

    def stop(self):
        self.stopped = True


class TestControlLifecycle(unittest.TestCase):
    def test_direct_mode_never_starts_listener_even_when_stale_toggle_is_true(self):
        from meapet.desktop.control_bridge import PetControlBridgeMixin

        class Host(PetControlBridgeMixin):
            config = {
                "llm": {"mode": "direct"},
                "agent_control": {"enabled": True},
            }

        host = Host()
        host._init_control()

        self.assertIsNone(host._control_runtime)
        self.assertIsNone(host._control_broker)

    def test_agent_mode_generates_token_persists_and_starts_once(self):
        import meapet.desktop.control_bridge as bridge

        saved = []
        runtimes = []

        class Runtime:
            def __init__(self, broker, config):
                self.broker = broker
                self.config = config
                self.starts = 0
                runtimes.append(self)

            def start(self):
                self.starts += 1

            def stop(self):
                pass

        class Host(bridge.PetControlBridgeMixin):
            config = {
                "llm": {"mode": "agent"},
                "agent_control": {
                    "enabled": True,
                    "listen_host": "127.0.0.1",
                    "allowed_agent_ip": "127.0.0.1",
                    "port": 8765,
                    "auth_token": "",
                },
            }
            _awaiting_reply = False

            def _build_agent_frontend_context(self):
                return _state()

            def _save_config(self):
                saved.append(True)

        with (
            mock.patch.object(bridge, "CompanionMcpRuntime", Runtime),
            mock.patch.object(bridge, "QTimer", _Timer),
        ):
            host = Host()
            host._init_control()

        token = host.config["agent_control"]["auth_token"]
        self.assertGreaterEqual(len(token), 43)
        self.assertEqual(saved, [True])
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0].starts, 1)
        self.assertTrue(host._control_poll_timer.started)


class TestControlPresentation(unittest.IsolatedAsyncioTestCase):
    def _host(self, *, tts_enabled=False):
        from meapet.control.broker import CompanionControlBroker
        from meapet.desktop.control_bridge import PetControlBridgeMixin

        class TTS:
            enabled = tts_enabled

        class Host(PetControlBridgeMixin):
            config = {
                "bubble_duration_ms": {"reply": 3000},
                "agent_control": {},
            }
            _awaiting_reply = False
            tts = TTS()

            def __init__(self):
                self._control_broker = CompanionControlBroker(
                    state=_state(tts=tts_enabled)
                )
                self._control_runtime = None
                self._control_say_active = False
                self._control_tts_workers = {}
                self._control_bubbles = {}
                self._bubble_stack = _Stack()
                self.moods = []
                self.positions = 0

            def _build_agent_frontend_context(self):
                return _state(tts=tts_enabled)

            def _safe_set_mood(self, mood):
                self.moods.append(mood)

            def _position_bubble(self):
                self.positions += 1

            def _play_audio(self, path):
                self.played = path

            def _confirm_control_capture(self, _request):
                return False

        return Host()

    async def test_active_say_waits_while_user_reply_is_busy_then_uses_role_bubble(self):
        host = self._host(tts_enabled=False)
        await host._control_broker.say([_segment()], request_id="active-1")
        host._awaiting_reply = True
        host._poll_control()
        self.assertEqual(host._bubble_stack.finalized, [])

        host._awaiting_reply = False
        host._poll_control()

        self.assertEqual(len(host._bubble_stack.finalized), 1)
        self.assertEqual(host._bubble_stack.finalized[0][1], "Agent 主动问候")
        self.assertEqual(host.moods, ["happy"])
        self.assertFalse(host._control_say_active)
        self.assertFalse(hasattr(host, "_agent_history"))

    async def test_capture_denial_resolves_typed_result_without_grabbing_screen(self):
        host = self._host()
        task = asyncio.create_task(
            host._control_broker.capture_screen(
                scope="full_screen",
                region=None,
                application="",
                request_id="capture-denied",
            )
        )
        await asyncio.sleep(0)

        with mock.patch(
            "meapet.desktop.control_bridge.capture_screen_image",
            side_effect=AssertionError("拒绝时不能截屏"),
        ):
            host._poll_control()

        result = await task
        self.assertEqual(result, {"status": "rejected", "code": "user_denied"})

    async def test_control_capture_encoder_returns_memory_png_without_path(self):
        from PIL import Image
        from meapet.desktop.control_bridge import encode_control_capture
        from meapet.watcher.capture import CapturedImage

        image = Image.new("RGB", (2, 3), "red")
        result = encode_control_capture(
            CapturedImage(
                image=image,
                metadata={"scope": "full_screen", "width": 2, "height": 3},
            )
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["image"]["mime_type"], "image/png")
        self.assertTrue(result["image"]["data"])
        self.assertNotIn("path", repr(result).lower())

    async def test_proactive_say_enters_isolated_timeline_as_complete_turn(self):
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline

        host = self._host(tts_enabled=False)
        host._conversation_key = ConversationKey("agent", "hermes", "session-a")
        host._conversation_timeline = ConversationTimeline(max_turns=5)
        queued = await host._control_broker.say(
            [_segment("第一段"), _segment("第二段")],
            request_id="active-timeline",
        )

        host._poll_control()

        turn = host._conversation_timeline.find(queued["queue_id"])
        self.assertEqual(turn.source, "agent_proactive")
        self.assertEqual(
            [segment.display_text for segment in turn.segments],
            ["第一段", "第二段"],
        )
        self.assertEqual(turn.status, "complete")


if __name__ == "__main__":
    unittest.main()
