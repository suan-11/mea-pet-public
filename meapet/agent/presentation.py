"""把统一 Agent 事件转换为不依赖 Qt 的气泡、TTS 与播放动作。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from meapet.agent.base import (
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.conversation.output_protocol import (
    SegmentCompleted,
    SegmentStarted,
    SegmentTextDelta,
)
from meapet.conversation.types import ReplySegment
from meapet.tts.sentence_splitter import SentenceBuffer, _BOUNDARIES


@dataclass(frozen=True)
class BeginBubble:
    index: int


@dataclass(frozen=True)
class UpdateBubble:
    index: int
    text: str


@dataclass(frozen=True)
class FinalizeBubble:
    segment: ReplySegment
    duration_ms: int
    wav_path: str = ""
    # P0-4 句级模式：气泡归属的父段 index（None=沿用 segment.index，旧行为）。
    bubble_index: int | None = None


@dataclass(frozen=True)
class CancelTTS:
    """撤销已提交的临时句级 TTS（翻译/重写降级时），调用方停 worker 与播放。"""
    indices: tuple[int, ...]


@dataclass(frozen=True)
class SetMood:
    """段 META 到达后把真实情绪补到呈现层（句级 provisional 阶段只有 neutral）。"""
    mood: str


@dataclass(frozen=True)
class SubmitTTS:
    segment: ReplySegment


@dataclass(frozen=True)
class PlayAudio:
    index: int
    wav_path: str
    duration_ms: int


@dataclass(frozen=True)
class ShowStatus:
    state: str
    safe_text: str


@dataclass(frozen=True)
class RequestFormatRepair:
    result: object


@dataclass(frozen=True)
class FinishTurn:
    turn_id: str


@dataclass(frozen=True)
class FailTurn:
    turn_id: str
    category: str
    safe_message: str


@dataclass(frozen=True)
class CancelTurn:
    turn_id: str


def _voice_norm(text: str) -> str:
    """比对 voice_text 与已提交句文本：去空白与句读边界（切句会丢边界符）。"""
    return "".join(
        ch for ch in str(text or "")
        if not ch.isspace() and ch not in _BOUNDARIES
    )


class AgentTurnPresentation:
    """维护单轮呈现顺序；调用方只需执行返回的不可变动作。"""

    def __init__(
        self,
        *,
        tts_enabled: bool,
        reply_min_duration_ms: int = 3000,
        supported_moods: tuple[str, ...] = (),
        sentence_streaming: bool = False,
    ) -> None:
        self.tts_enabled = bool(tts_enabled)
        # P0-4：句级流式 TTS（仅 tts 开启时有意义）。默认关=旧行为逐段提交，
        # 契约测试钉住的形状不变；开启后显示文本流式切句、首句音频就绪即呈现。
        self.sentence_streaming = bool(sentence_streaming) and self.tts_enabled
        self.reply_min_duration_ms = max(0, int(reply_min_duration_ms))
        self.supported_moods = frozenset(
            str(mood or "").strip().lower()
            for mood in supported_moods
            if str(mood or "").strip()
        )
        self._texts: dict[int, str] = {}
        self._begun: set[int] = set()
        self._segments: dict[int, ReplySegment] = {}
        self._tts_results: dict[int, tuple[str, int]] = {}
        self._next_present_index = 0
        self._playing_index: int | None = None
        self._turn_id = ""
        self._expected_segments: int | None = None
        self._finished = False
        # 句级模式状态：seq 是提交序号（句级伪段与整段降级项共用一套序号空间）
        self._seq = 0
        self._sent_buffers: dict[int, SentenceBuffer] = {}
        self._sent_provisional: dict[int, list[int]] = {}
        self._sent_submitted_text: dict[int, str] = {}
        self._bubble_index: dict[int, int] = {}
        self._cancelled: set[int] = set()

    def consume(self, event: object) -> tuple[object, ...]:
        if self._finished:
            return ()
        if isinstance(event, ToolStatus):
            return (ShowStatus(event.state, event.safe_text),)
        if isinstance(event, SegmentStarted):
            self._begun.add(event.index)
            self._texts.setdefault(event.index, "")
            if self.tts_enabled:
                return ()
            return (BeginBubble(event.index),)
        if isinstance(event, SegmentTextDelta):
            current = self._texts.get(event.index, "") + event.delta
            self._texts[event.index] = current
            if self.tts_enabled:
                if self.sentence_streaming:
                    return self._consume_display_delta(event.index, event.delta)
                return ()
            actions = []
            if event.index not in self._begun:
                self._begun.add(event.index)
                actions.append(BeginBubble(event.index))
            actions.append(UpdateBubble(event.index, current))
            return tuple(actions)
        if isinstance(event, SegmentCompleted):
            segment = event.segment
            if self.supported_moods and segment.mood not in self.supported_moods:
                segment = replace(segment, mood="neutral")
            self._segments[segment.index] = segment
            self._texts[segment.index] = segment.display_text
            if not self.tts_enabled:
                return (
                    FinalizeBubble(
                        segment,
                        duration_ms=self.reply_min_duration_ms,
                        wav_path="",
                    ),
                )
            if self.sentence_streaming:
                return self._complete_segment_sentences(segment)
            missing = set(segment.missing_required_fields)
            if missing.intersection({"voice_text", "voice_language"}):
                self._tts_results[segment.index] = ("", 0)
                return self._drain_ready()
            return (SubmitTTS(segment),)
        if isinstance(event, FormatRepairRequired):
            return (RequestFormatRepair(event.result),)
        if isinstance(event, TurnCompleted):
            self._turn_id = event.turn_id
            self._expected_segments = (
                self._seq if self.sentence_streaming
                else len(event.result.segments)
            )
            if not self.tts_enabled:
                return self._finish_if_ready(force=True)
            return self._finish_if_ready()
        if isinstance(event, TurnFailed):
            self._finished = True
            return (
                FailTurn(
                    turn_id=event.turn_id,
                    category=event.category,
                    safe_message=event.safe_message,
                ),
            )
        if isinstance(event, TurnCancelled):
            self._finished = True
            return (CancelTurn(event.turn_id),)
        return ()

    def tts_ready(
        self,
        index: int,
        wav_path: str,
        *,
        audio_duration_ms: int,
    ) -> tuple[object, ...]:
        if self._finished or index not in self._segments:
            return ()
        self._tts_results[index] = (
            str(wav_path or ""),
            max(0, int(audio_duration_ms)),
        )
        return self._drain_ready()

    def audio_finished(self, index: int) -> tuple[object, ...]:
        if self._finished or self._playing_index != index:
            return ()
        self._playing_index = None
        self._next_present_index = index + 1
        return self._drain_ready()

    def _drain_ready(self) -> tuple[object, ...]:
        actions = []
        while self._playing_index is None:
            index = self._next_present_index
            if index in self._cancelled:
                self._next_present_index += 1
                continue
            if index not in self._tts_results or index not in self._segments:
                break
            wav_path, audio_duration_ms = self._tts_results.pop(index)
            segment = self._segments[index]
            duration_ms = self.reply_min_duration_ms
            if wav_path and audio_duration_ms > 0:
                duration_ms = max(duration_ms, audio_duration_ms + 500)
            actions.append(
                FinalizeBubble(
                    segment,
                    duration_ms=duration_ms,
                    wav_path=wav_path,
                    bubble_index=self._bubble_index.get(index),
                )
            )
            if wav_path:
                self._playing_index = index
                actions.append(PlayAudio(index, wav_path, audio_duration_ms))
                break
            self._next_present_index += 1
        actions.extend(self._finish_if_ready())
        return tuple(actions)

    def _finish_if_ready(self, *, force: bool = False) -> tuple[object, ...]:
        if self._finished or not self._turn_id:
            return ()
        if not force:
            if self._expected_segments is None or self._playing_index is not None:
                return ()
            next_index = self._next_present_index
            while next_index in self._cancelled:
                next_index += 1
            if next_index < self._expected_segments:
                return ()
        self._finished = True
        return (FinishTurn(self._turn_id),)

    # ---------- P0-4 句级流式 ----------

    def _consume_display_delta(self, index: int, delta: str) -> tuple[object, ...]:
        """显示文本增量 → 整句即刻提交 TTS（provisional，META 未到）。"""
        buf = self._sent_buffers.get(index)
        if buf is None:
            buf = SentenceBuffer()
            self._sent_buffers[index] = buf
        actions: list[object] = []
        for sentence in buf.feed(delta):
            seq = self._seq
            self._seq += 1
            pseudo = ReplySegment(
                display_text=self._texts.get(index, ""),
                voice_text=sentence,
                voice_language="",
                mood="neutral",
                tts_style="",
                index=seq,
            )
            self._segments[seq] = pseudo
            self._bubble_index[seq] = index
            self._sent_provisional.setdefault(index, []).append(seq)
            self._sent_submitted_text[index] = (
                self._sent_submitted_text.get(index, "") + sentence
            )
            actions.append(SubmitTTS(pseudo))
        return tuple(actions)

    def _submit_segment_item(self, segment: ReplySegment) -> SubmitTTS:
        """整段作为一个提交项（短回复无句界 / 翻译降级共用）。"""
        seq = self._seq
        self._seq += 1
        item = replace(segment, index=seq)
        self._segments[seq] = item
        self._bubble_index[seq] = segment.index
        return SubmitTTS(item)

    def _cancel_provisional(self, segment_index: int) -> CancelTTS:
        seqs = tuple(self._sent_provisional.get(segment_index, ()))
        self._cancelled.update(seqs)
        for seq in seqs:
            self._segments.pop(seq, None)
            self._tts_results.pop(seq, None)
        if self._playing_index in seqs:
            self._playing_index = None
        return CancelTTS(seqs)

    def _complete_segment_sentences(self, segment: ReplySegment) -> tuple[object, ...]:
        """META 到达：校验 provisional 句与 voice_text 的一致性再收尾。

        voice_text 与显示文本一致（无翻译）→ 补交尾巴 + 真实情绪；
        不一致（翻译/重写）→ 撤销 provisional、整段降级重交（路线图 P0-4）。
        """
        provisional = self._sent_provisional.get(segment.index, ())
        buf = self._sent_buffers.get(segment.index)
        tail = buf.flush() if buf is not None else ""
        missing = set(segment.missing_required_fields)
        if missing.intersection({"voice_text", "voice_language"}):
            actions: list[object] = []
            if provisional:
                actions.append(self._cancel_provisional(segment.index))
            seq = self._seq
            self._seq += 1
            self._segments[seq] = replace(segment, index=seq)
            self._bubble_index[seq] = segment.index
            self._tts_results[seq] = ("", 0)
            actions.extend(self._drain_ready())
            return tuple(actions)
        if not provisional:
            return (self._submit_segment_item(segment),)
        submitted = self._sent_submitted_text.get(segment.index, "")
        if _voice_norm(submitted + tail) != _voice_norm(segment.voice_text):
            return (
                self._cancel_provisional(segment.index),
                self._submit_segment_item(segment),
            )
        actions = []
        if tail:
            seq = self._seq
            self._seq += 1
            pseudo = ReplySegment(
                display_text=segment.display_text,
                voice_text=tail,
                voice_language=segment.voice_language,
                mood=segment.mood,
                tts_style=segment.tts_style,
                index=seq,
            )
            self._segments[seq] = pseudo
            self._bubble_index[seq] = segment.index
            actions.append(SubmitTTS(pseudo))
        actions.append(SetMood(segment.mood))
        return tuple(actions)
