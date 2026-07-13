"""Agent 事件到气泡/TTS 动作的时序契约。"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _segment(index: int, text: str):
    from meapet.conversation.types import ReplySegment

    return ReplySegment(
        index=index,
        display_text=text,
        voice_text=f"朗读：{text}",
        voice_language="zh",
        mood="happy" if index == 0 else "neutral",
        tts_style="轻声",
    )


def _completed_turn(*segments):
    from meapet.agent.base import TurnCompleted
    from meapet.conversation.output_protocol import ParseResult

    return TurnCompleted(
        turn_id="turn-1",
        result=ParseResult(
            segments=tuple(segments),
            issues=(),
            done=True,
            source_format="meapet",
        ),
    )


class TestAgentPresentationWithoutTts(unittest.TestCase):
    def test_streaming_updates_one_bubble_and_finishes_after_turn(self):
        from meapet.agent.presentation import (
            AgentTurnPresentation,
            BeginBubble,
            FinalizeBubble,
            FinishTurn,
            UpdateBubble,
        )
        from meapet.conversation.output_protocol import (
            SegmentCompleted,
            SegmentStarted,
            SegmentTextDelta,
        )

        segment = _segment(0, "你好，主人")
        controller = AgentTurnPresentation(tts_enabled=False, reply_min_duration_ms=3000)

        self.assertEqual(controller.consume(SegmentStarted(0)), (BeginBubble(0),))
        self.assertEqual(
            controller.consume(SegmentTextDelta(0, "你好")),
            (UpdateBubble(0, "你好"),),
        )
        self.assertEqual(
            controller.consume(SegmentTextDelta(0, "，主人")),
            (UpdateBubble(0, "你好，主人"),),
        )
        self.assertEqual(
            controller.consume(SegmentCompleted(segment)),
            (FinalizeBubble(segment, duration_ms=3000, wav_path=""),),
        )
        self.assertEqual(
            controller.consume(_completed_turn(segment)),
            (FinishTurn("turn-1"),),
        )


class TestAgentPresentationWithTts(unittest.TestCase):
    def test_tts_can_finish_out_of_order_but_presents_and_plays_in_order(self):
        from meapet.agent.presentation import (
            AgentTurnPresentation,
            FinalizeBubble,
            FinishTurn,
            PlayAudio,
            SubmitTTS,
        )
        from meapet.conversation.output_protocol import SegmentCompleted

        first = _segment(0, "第一段")
        second = _segment(1, "第二段")
        controller = AgentTurnPresentation(tts_enabled=True, reply_min_duration_ms=3000)

        self.assertEqual(
            controller.consume(SegmentCompleted(first)),
            (SubmitTTS(first),),
        )
        self.assertEqual(
            controller.consume(SegmentCompleted(second)),
            (SubmitTTS(second),),
        )
        self.assertEqual(controller.consume(_completed_turn(first, second)), ())

        # 第二段先生成，必须等待第一段。
        self.assertEqual(
            controller.tts_ready(1, "/tmp/second.wav", audio_duration_ms=1200),
            (),
        )
        self.assertEqual(
            controller.tts_ready(0, "/tmp/first.wav", audio_duration_ms=4200),
            (
                FinalizeBubble(first, duration_ms=4700, wav_path="/tmp/first.wav"),
                PlayAudio(0, "/tmp/first.wav", duration_ms=4200),
            ),
        )
        self.assertEqual(
            controller.audio_finished(0),
            (
                FinalizeBubble(second, duration_ms=3000, wav_path="/tmp/second.wav"),
                PlayAudio(1, "/tmp/second.wav", duration_ms=1200),
            ),
        )
        self.assertEqual(
            controller.audio_finished(1),
            (FinishTurn("turn-1"),),
        )

    def test_failed_segment_tts_shows_silent_text_and_does_not_block_next(self):
        from meapet.agent.presentation import (
            AgentTurnPresentation,
            FinalizeBubble,
            PlayAudio,
        )
        from meapet.conversation.output_protocol import SegmentCompleted

        first = _segment(0, "语音失败")
        second = _segment(1, "语音成功")
        controller = AgentTurnPresentation(tts_enabled=True, reply_min_duration_ms=2500)
        controller.consume(SegmentCompleted(first))
        controller.consume(SegmentCompleted(second))

        self.assertEqual(
            controller.tts_ready(1, "/tmp/ok.wav", audio_duration_ms=1000),
            (),
        )
        self.assertEqual(
            controller.tts_ready(0, "", audio_duration_ms=0),
            (
                FinalizeBubble(first, duration_ms=2500, wav_path=""),
                FinalizeBubble(second, duration_ms=2500, wav_path="/tmp/ok.wav"),
                PlayAudio(1, "/tmp/ok.wav", duration_ms=1000),
            ),
        )

    def test_invalid_voice_metadata_is_not_submitted_to_tts(self):
        from meapet.agent.presentation import AgentTurnPresentation, FinalizeBubble
        from meapet.conversation.output_protocol import SegmentCompleted
        from meapet.conversation.types import ReplySegment

        invalid = ReplySegment(
            index=0,
            display_text="还能显示",
            voice_text="",
            voice_language="",
            mood="neutral",
            tts_style="",
            provided_fields=frozenset({"display_text", "mood", "tts_style"}),
        )
        controller = AgentTurnPresentation(tts_enabled=True, reply_min_duration_ms=3000)

        self.assertEqual(
            controller.consume(SegmentCompleted(invalid)),
            (FinalizeBubble(invalid, duration_ms=3000, wav_path=""),),
        )


class TestAgentPresentationStatusAndFailure(unittest.TestCase):
    def test_tool_status_stays_a_system_status_not_a_role_bubble(self):
        from meapet.agent.base import ToolStatus
        from meapet.agent.presentation import AgentTurnPresentation, ShowStatus

        controller = AgentTurnPresentation(tts_enabled=False)

        self.assertEqual(
            controller.consume(ToolStatus("started", "正在查资料")),
            (ShowStatus("started", "正在查资料"),),
        )

    def test_typed_failure_releases_turn_without_tts_or_role_actions(self):
        from meapet.agent.base import TurnFailed
        from meapet.agent.presentation import AgentTurnPresentation, FailTurn

        controller = AgentTurnPresentation(tts_enabled=True)
        failure = TurnFailed(
            "turn-1",
            "authentication",
            "Agent 认证失败，请检查访问令牌。",
        )

        self.assertEqual(
            controller.consume(failure),
            (
                FailTurn(
                    turn_id="turn-1",
                    category="authentication",
                    safe_message="Agent 认证失败，请检查访问令牌。",
                ),
            ),
        )


class TestAgentChatWorker(unittest.TestCase):
    def test_worker_exposes_stream_events_before_completion(self):
        from meapet.agent.base import AgentTurnRequest, ToolStatus, TurnCompleted
        from meapet.desktop.workers import AgentChatWorker
        from meapet.conversation.output_protocol import ParseResult

        class Adapter:
            async def stream_turn(self, request):
                yield ToolStatus("started", "正在处理")
                await __import__("asyncio").sleep(0.05)
                yield TurnCompleted(
                    request.turn_id,
                    ParseResult((), (), True, "meapet"),
                )

            async def cancel(self, _turn_id):
                pass

        worker = AgentChatWorker(
            Adapter(),
            AgentTurnRequest(turn_id="turn-worker", user_text="你好"),
        )
        worker.start()

        deadline = time.time() + 2
        first_batch = ()
        while time.time() < deadline:
            first_batch = worker.take_events()
            if first_batch:
                break
            time.sleep(0.005)

        self.assertEqual(first_batch, (ToolStatus("started", "正在处理"),))
        self.assertFalse(worker.done)

        while not worker.done and time.time() < deadline:
            time.sleep(0.005)
        final_batch = worker.take_events()
        self.assertTrue(worker.done)
        self.assertEqual(len(final_batch), 1)
        self.assertIsInstance(final_batch[0], TurnCompleted)


class TestDialogueBubbleStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_stack_updates_and_finalizes_the_same_bubble(self):
        from meapet.desktop.widgets import DialogueBubbleStack

        stack = DialogueBubbleStack(max_bubbles=3)
        self.addCleanup(stack.close_all)

        bubble = stack.begin_message(mood="neutral")
        stack.update_message(bubble, "边生成")
        stack.update_message(bubble, "边生成边显示")
        stack.finalize_message(
            bubble,
            "边生成边显示文字",
            duration_ms=4000,
            mood="happy",
        )

        self.assertEqual(stack.bubbles, (bubble,))
        self.assertEqual(bubble.text_label.text(), "边生成边显示文字")
        self.assertEqual(bubble._container.mood, "happy")
        self.assertTrue(bubble._hide_timer.isActive())


if __name__ == "__main__":
    unittest.main()
