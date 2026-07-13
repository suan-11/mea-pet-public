"""Agent 反向控制桌宠的四工具核心契约。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _segment(text="主动问候", *, language="zh"):
    return {
        "display_text": text,
        "voice_text": text,
        "voice_language": language,
        "mood": "happy",
        "tts_style": "轻声",
    }


def _state():
    return {
        "frontend_capabilities": {
            "renderer": "png",
            "supported_moods": ["neutral", "happy"],
            "supported_motions": ["wave"],
            "tts_enabled": True,
            "tts_languages": ["zh", "ja"],
            "streaming_text": True,
            "multi_segment": True,
            "private_path": "D:/secret/model",
        },
        "companion_state": {
            "affection_level": "熟悉",
            "character_state": "active",
            "current_mood": "neutral",
            "busy": False,
            "raw_memory": "不得返回",
        },
        "api_key": "不得返回",
    }


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class TestCompanionSayQueue(unittest.IsolatedAsyncioTestCase):
    async def test_say_requires_all_voice_fields_and_deduplicates_request_id(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state())
        invalid = _segment()
        del invalid["voice_language"]

        rejected = await broker.say([invalid], request_id="bad-1")
        first = await broker.say([_segment()], request_id="say-1")
        duplicate = await broker.say([_segment("不能重复入队")], request_id="say-1")

        self.assertEqual(rejected["status"], "invalid")
        self.assertIn("voice_language", rejected["missing_fields"])
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["position"], 1)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["queue_id"], first["queue_id"])
        self.assertEqual(broker.say_queue_size, 1)

    async def test_user_reply_has_priority_and_expired_active_say_is_dropped(self):
        from meapet.control.broker import CompanionControlBroker

        clock = _Clock()
        broker = CompanionControlBroker(
            state=_state(),
            say_ttl_seconds=5,
            clock=clock,
        )
        await broker.say([_segment()], request_id="say-priority")
        broker.set_user_busy(True)
        self.assertIsNone(broker.take_ready_say())

        broker.set_user_busy(False)
        clock.value += 6
        self.assertIsNone(broker.take_ready_say())
        self.assertEqual(broker.say_queue_size, 0)

    async def test_queue_limit_returns_typed_busy_without_evicting_oldest(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state(), max_say_queue=1)
        first = await broker.say([_segment("第一条")], request_id="say-a")
        second = await broker.say([_segment("第二条")], request_id="say-b")

        self.assertEqual(first["status"], "queued")
        self.assertEqual(second, {"status": "busy", "code": "queue_full"})
        command = broker.take_ready_say()
        self.assertEqual(command.request_id, "say-a")


class TestCompanionStateAndExpression(unittest.IsolatedAsyncioTestCase):
    async def test_get_state_returns_only_public_capabilities_and_summary(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state())
        result = await broker.get_state()

        self.assertEqual(
            set(result),
            {"frontend_capabilities", "companion_state"},
        )
        self.assertNotIn("private_path", repr(result))
        self.assertNotIn("raw_memory", repr(result))
        self.assertNotIn("api_key", repr(result))

    async def test_express_rejects_unsupported_values_without_fallback(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state())

        rejected = await broker.express(
            mood="angry",
            motion="",
            request_id="express-bad",
        )
        accepted = await broker.express(
            mood="happy",
            motion="wave",
            request_id="express-ok",
        )

        self.assertEqual(rejected["status"], "unsupported")
        self.assertEqual(rejected["field"], "mood")
        self.assertEqual(accepted["status"], "queued")
        command = broker.take_expressions()[0]
        self.assertEqual((command.mood, command.motion), ("happy", "wave"))


class TestCompanionCapture(unittest.IsolatedAsyncioTestCase):
    async def test_each_capture_creates_confirmation_request_and_returns_no_path(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state(), capture_timeout_seconds=1)
        task = asyncio.create_task(
            broker.capture_screen(
                scope="region",
                region={"x": 1, "y": 2, "width": 30, "height": 40},
                application="",
                request_id="capture-1",
            )
        )
        await asyncio.sleep(0)
        request = broker.take_capture_requests()[0]
        self.assertEqual(request.scope, "region")
        self.assertEqual(request.region["width"], 30)

        broker.resolve_capture(
            request.capture_id,
            {
                "status": "approved",
                "image": {"mime_type": "image/png", "data": "aW1hZ2U="},
                "metadata": {"width": 30, "height": 40, "scope": "region"},
            },
        )
        result = await task

        self.assertEqual(result["status"], "approved")
        self.assertNotIn("path", repr(result).lower())

        second = asyncio.create_task(
            broker.capture_screen(
                scope="full_screen",
                region=None,
                application="",
                request_id="capture-2",
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(broker.take_capture_requests()), 1)
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second

    async def test_capture_rejects_unknown_scope_before_prompt(self):
        from meapet.control.broker import CompanionControlBroker

        broker = CompanionControlBroker(state=_state())
        result = await broker.capture_screen(
            scope="desktop_magic",
            region=None,
            application="",
            request_id="capture-invalid",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["code"], "unsupported_scope")
        self.assertEqual(broker.take_capture_requests(), ())


class TestCompanionMcpSurface(unittest.IsolatedAsyncioTestCase):
    async def test_server_exposes_exactly_four_namespaced_tools(self):
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.mcp_server import build_companion_mcp

        server = build_companion_mcp(CompanionControlBroker(state=_state()))
        tools = await server.list_tools()

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "meapet.say",
                "meapet.express",
                "meapet.get_state",
                "meapet.capture_screen",
            },
        )


if __name__ == "__main__":
    unittest.main()
