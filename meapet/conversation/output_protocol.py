"""MeaPet 多分段回复协议的最终解析与增量事件提取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .types import REPLY_REQUIRED_FIELDS, ReplySegment


_SEGMENT_START_RE = re.compile(r"<MEAPET_SEGMENT\s*>", re.IGNORECASE)
_SEGMENT_BLOCK_RE = re.compile(
    r"<MEAPET_SEGMENT\s*>(.*?)</MEAPET_SEGMENT\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_OPEN_RE = re.compile(r"<DISPLAY\s*>", re.IGNORECASE)
_DISPLAY_RE = re.compile(
    r"<DISPLAY\s*>(.*?)</DISPLAY\s*>",
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(r"<META\s*>(.*?)</META\s*>", re.IGNORECASE | re.DOTALL)
_DONE_RE = re.compile(r"<MEAPET_DONE\s*/\s*>", re.IGNORECASE)
_TTS_RE = re.compile(r"<TTS>\s*(\{[^\r\n]*\})\s*</TTS>", re.IGNORECASE)
_MOOD_RE = re.compile(r"^\s*\[([^\]\r\n]+)\]\s*")


@dataclass(frozen=True)
class ProtocolIssue:
    code: str
    segment_index: Optional[int] = None
    fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    segments: Tuple[ReplySegment, ...]
    issues: Tuple[ProtocolIssue, ...]
    done: bool
    source_format: str

    def requires_repair(self, *, tts_enabled: bool) -> bool:
        if not self.segments:
            return True
        missing = [
            field
            for segment in self.segments
            for field in segment.missing_required_fields
        ]
        if "display_text" in missing:
            return True
        if tts_enabled and any(
            field in {"voice_text", "voice_language"} for field in missing
        ):
            return True
        return len(missing) >= 2


@dataclass(frozen=True)
class SegmentStarted:
    index: int


@dataclass(frozen=True)
class SegmentTextDelta:
    index: int
    delta: str


@dataclass(frozen=True)
class SegmentCompleted:
    segment: ReplySegment


@dataclass(frozen=True)
class ProtocolCompleted:
    pass


def _looks_like_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u31f0" <= char <= "\u31ff"
        for char in text
    )


def _legacy_tts_style(raw_json: str) -> str:
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    labels = []
    for key in ("emotion", "pace", "energy", "volume"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            labels.append(f"{key}={value.strip()}")
    delivery = payload.get("delivery")
    if isinstance(delivery, str) and delivery.strip():
        labels.append(delivery.strip()[:60])
    return "；".join(labels)


def _segment_from_block(block: str, index: int) -> tuple[ReplySegment, list[ProtocolIssue]]:
    issues: list[ProtocolIssue] = []
    provided = set()

    display_match = _DISPLAY_RE.search(block)
    display = ""
    if display_match:
        provided.add("display_text")
        display = display_match.group(1).strip()
    else:
        issues.append(ProtocolIssue("missing_display", index, ("display_text",)))

    metadata = {}
    meta_match = _META_RE.search(block)
    if meta_match:
        try:
            candidate = json.loads(meta_match.group(1).strip())
            if isinstance(candidate, dict):
                metadata = candidate
            else:
                issues.append(ProtocolIssue("invalid_metadata", index))
        except json.JSONDecodeError:
            issues.append(ProtocolIssue("invalid_metadata", index))
    else:
        issues.append(ProtocolIssue("missing_metadata", index))

    for field in ("voice_text", "voice_language", "mood", "tts_style"):
        if field in metadata:
            provided.add(field)

    segment = ReplySegment(
        display_text=display,
        voice_text=metadata.get("voice_text", ""),
        voice_language=metadata.get("voice_language", ""),
        mood=metadata.get("mood", "neutral"),
        tts_style=metadata.get("tts_style", ""),
        index=index,
        provided_fields=frozenset(provided),
    )
    missing = segment.missing_required_fields
    if missing:
        issues.append(ProtocolIssue("missing_required_fields", index, missing))
    return segment, issues


def _parse_meapet(source: str) -> ParseResult:
    segments = []
    issues = []
    for index, match in enumerate(_SEGMENT_BLOCK_RE.finditer(source)):
        segment, segment_issues = _segment_from_block(match.group(1), index)
        segments.append(segment)
        issues.extend(segment_issues)

    done = bool(_DONE_RE.search(source))
    if not done:
        issues.append(ProtocolIssue("missing_done"))
    if not segments:
        issues.append(ProtocolIssue("missing_segment"))
    return ParseResult(tuple(segments), tuple(issues), done, "meapet")


def _parse_legacy(source: str) -> ParseResult:
    provided = {"mood"}
    mood = "neutral"
    mood_match = _MOOD_RE.match(source)
    if mood_match:
        mood = mood_match.group(1).strip().lower() or "neutral"
        source = source[mood_match.end():]

    tts_style = ""
    tts_match = _TTS_RE.search(source)
    if tts_match:
        provided.add("tts_style")
        tts_style = _legacy_tts_style(tts_match.group(1))
        source = _TTS_RE.sub("", source)

    lines = [line.strip() for line in source.splitlines() if line.strip()]
    display = lines[0] if lines else ""
    if display:
        provided.add("display_text")

    voice = ""
    language = ""
    if len(lines) > 1 and _looks_like_japanese(lines[1]):
        voice = lines[1]
        language = "ja"
        provided.update(("voice_text", "voice_language"))
    elif display and _looks_like_japanese(display):
        voice = display
        language = "ja"
        provided.update(("voice_text", "voice_language"))

    segment = ReplySegment(
        display_text=display,
        voice_text=voice,
        voice_language=language,
        mood=mood,
        tts_style=tts_style,
        index=0,
        provided_fields=frozenset(provided),
    )
    issues = []
    if segment.missing_required_fields:
        issues.append(
            ProtocolIssue(
                "missing_required_fields",
                0,
                segment.missing_required_fields,
            )
        )
    return ParseResult((segment,), tuple(issues), True, "legacy")


def _parse_plain(source: str) -> ParseResult:
    display = source.strip()
    if not display:
        return ParseResult((), (ProtocolIssue("empty_output"),), True, "plain")
    segment = ReplySegment(
        display_text=display,
        voice_text="",
        voice_language="",
        mood="neutral",
        tts_style="",
        index=0,
        provided_fields=frozenset({"display_text"}),
    )
    return ParseResult(
        (segment,),
        (
            ProtocolIssue("missing_metadata", 0),
            ProtocolIssue(
                "missing_required_fields",
                0,
                segment.missing_required_fields,
            ),
        ),
        True,
        "plain",
    )


def parse_reply_output(source: object) -> ParseResult:
    """宽容解析完整回复，同时保留是否需要修复的验证信息。"""
    text = str(source or "").strip()
    if _SEGMENT_START_RE.search(text):
        return _parse_meapet(text)
    if _TTS_RE.search(text) or (_MOOD_RE.match(text) and "\n" in text):
        return _parse_legacy(text)
    return _parse_plain(text)


def _without_partial_close_tag(text: str, close_tag: str) -> str:
    """流式显示时保留可能属于结束标签的后缀，避免协议字符泄漏。"""
    lowered_tag = close_tag.lower()
    start = text.rfind("<")
    if start < 0:
        return text
    suffix = text[start:]
    if lowered_tag.startswith(suffix.lower()):
        return text[:start]
    return text


class MeaPetOutputStreamParser:
    """从任意分块的模型输出中提取可增量展示的文本事件。"""

    def __init__(self) -> None:
        self._raw = ""
        self._started_count = 0
        self._completed_count = 0
        self._display_lengths: dict[int, int] = {}
        self._done_emitted = False
        self._closed = False

    def feed(self, chunk: object) -> Tuple[object, ...]:
        if self._closed:
            raise RuntimeError("output parser is already closed")
        value = str(chunk or "")
        if not value:
            return ()
        self._raw += value
        events = []

        starts = list(_SEGMENT_START_RE.finditer(self._raw))
        while self._started_count < len(starts):
            events.append(SegmentStarted(self._started_count))
            self._started_count += 1

        for index, start in enumerate(starts):
            next_start = starts[index + 1].start() if index + 1 < len(starts) else len(self._raw)
            body = self._raw[start.end():next_start]
            display_open = _DISPLAY_OPEN_RE.search(body)
            if not display_open:
                continue
            after_open = body[display_open.end():]
            close_match = re.search(r"</DISPLAY\s*>", after_open, re.IGNORECASE)
            if close_match:
                visible = after_open[:close_match.start()]
            else:
                visible = _without_partial_close_tag(after_open, "</DISPLAY>")
            previous = self._display_lengths.get(index, 0)
            if len(visible) > previous:
                events.append(SegmentTextDelta(index, visible[previous:]))
                self._display_lengths[index] = len(visible)

        blocks = list(_SEGMENT_BLOCK_RE.finditer(self._raw))
        while self._completed_count < len(blocks):
            index = self._completed_count
            segment, _issues = _segment_from_block(blocks[index].group(1), index)
            events.append(SegmentCompleted(segment))
            self._completed_count += 1

        if not self._done_emitted and _DONE_RE.search(self._raw):
            events.append(ProtocolCompleted())
            self._done_emitted = True

        return tuple(events)

    def close(self, *, tts_enabled: bool) -> ParseResult:
        self._closed = True
        result = parse_reply_output(self._raw)
        # 参数属于调用契约：此处强制计算一次，尽早暴露类型/验证错误。
        result.requires_repair(tts_enabled=bool(tts_enabled))
        return result


def collect_text_deltas(events: Iterable[object], index: int) -> str:
    """测试/非 GUI 消费者使用的轻量辅助函数。"""
    return "".join(
        event.delta
        for event in events
        if isinstance(event, SegmentTextDelta) and event.index == index
    )
