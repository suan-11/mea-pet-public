"""与后端和 Qt 解耦的隔离会话时间线。"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from meapet.conversation.types import ReplySegment


_TERMINAL_STATUSES = frozenset({"complete", "error", "cancelled"})


@dataclass(frozen=True)
class ConversationKey:
    mode: str
    profile_id: str
    session_id: str

    def __post_init__(self) -> None:
        mode = str(self.mode or "direct").strip().lower()
        if mode not in {"direct", "agent"}:
            raise ValueError("conversation mode must be direct or agent")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "profile_id",
            str(self.profile_id or "default").strip() or "default",
        )
        object.__setattr__(
            self,
            "session_id",
            str(self.session_id or "local").strip() or "local",
        )


@dataclass(frozen=True)
class SystemTimelineEntry:
    state: str
    safe_text: str
    created_at: float


@dataclass(frozen=True)
class TurnTranscript:
    conversation_key: ConversationKey
    turn_id: str
    source: str
    user_text: str
    segments: tuple[ReplySegment, ...]
    system_entries: tuple[SystemTimelineEntry, ...]
    created_at: float
    updated_at: float
    status: str
    error_text: str = ""

    @property
    def display_text(self) -> str:
        return "\n\n".join(
            segment.display_text for segment in self.segments if segment.display_text
        )


@dataclass
class _TurnState:
    conversation_key: ConversationKey
    turn_id: str
    source: str
    user_text: str
    created_at: float
    updated_at: float
    status: str = "streaming"
    error_text: str = ""
    segments: dict[int, ReplySegment] = field(default_factory=dict)
    system_entries: list[SystemTimelineEntry] = field(default_factory=list)

    def snapshot(self) -> TurnTranscript:
        return TurnTranscript(
            conversation_key=self.conversation_key,
            turn_id=self.turn_id,
            source=self.source,
            user_text=self.user_text,
            segments=tuple(self.segments[index] for index in sorted(self.segments)),
            system_entries=tuple(self.system_entries),
            created_at=self.created_at,
            updated_at=self.updated_at,
            status=self.status,
            error_text=self.error_text,
        )


class ConversationTimeline:
    """每个 ConversationKey 独立保留固定数量的完整交互单元。"""

    def __init__(
        self,
        max_turns: int = 5,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_turns = max(1, min(int(max_turns), 100))
        self._clock = clock
        self._lock = threading.RLock()
        self._turns: dict[ConversationKey, OrderedDict[str, _TurnState]] = {}

    def start_turn(
        self,
        key: ConversationKey,
        turn_id: str,
        *,
        source: str,
        user_text: str = "",
    ) -> TurnTranscript:
        safe_id = str(turn_id or "").strip()
        if not safe_id:
            raise ValueError("turn_id is required")
        now = self._clock()
        with self._lock:
            bucket = self._turns.setdefault(key, OrderedDict())
            existing = bucket.get(safe_id)
            if existing is not None:
                return existing.snapshot()
            bucket[safe_id] = _TurnState(
                conversation_key=key,
                turn_id=safe_id,
                source=str(source or "system").strip() or "system",
                user_text=str(user_text or "").strip(),
                created_at=now,
                updated_at=now,
            )
            while len(bucket) > self.max_turns:
                bucket.popitem(last=False)
            return bucket[safe_id].snapshot()

    def _active(self, key: ConversationKey, turn_id: str) -> _TurnState | None:
        state = self._turns.get(key, {}).get(str(turn_id or ""))
        if state is None or state.status in _TERMINAL_STATUSES:
            return None
        return state

    def update_segment_text(
        self,
        key: ConversationKey,
        turn_id: str,
        index: int,
        text: str,
    ) -> bool:
        with self._lock:
            state = self._active(key, turn_id)
            if state is None:
                return False
            part = ReplySegment(
                index=max(0, int(index)),
                display_text=str(text or ""),
                voice_text="",
                voice_language="",
                mood="neutral",
                tts_style="",
                provided_fields=frozenset({"display_text"}),
            )
            state.segments[part.index] = part
            state.updated_at = self._clock()
            return True

    def complete_segment(
        self,
        key: ConversationKey,
        turn_id: str,
        segment: ReplySegment,
    ) -> bool:
        with self._lock:
            state = self._active(key, turn_id)
            if state is None:
                return False
            state.segments[segment.index] = segment
            state.status = "awaiting_tts"
            state.updated_at = self._clock()
            return True

    def add_status(
        self,
        key: ConversationKey,
        turn_id: str,
        *,
        state: str,
        safe_text: str,
    ) -> bool:
        with self._lock:
            turn = self._active(key, turn_id)
            if turn is None:
                return False
            turn.system_entries.append(
                SystemTimelineEntry(
                    state=str(state or "running").strip().lower() or "running",
                    safe_text=str(safe_text or "").strip(),
                    created_at=self._clock(),
                )
            )
            turn.updated_at = self._clock()
            return True

    def finish_turn(self, key: ConversationKey, turn_id: str) -> bool:
        return self._finish(key, turn_id, "complete", "")

    def fail_turn(
        self,
        key: ConversationKey,
        turn_id: str,
        error_text: str,
    ) -> bool:
        return self._finish(key, turn_id, "error", error_text)

    def cancel_turn(self, key: ConversationKey, turn_id: str) -> bool:
        return self._finish(key, turn_id, "cancelled", "")

    def _finish(
        self,
        key: ConversationKey,
        turn_id: str,
        status: str,
        error_text: str,
    ) -> bool:
        with self._lock:
            state = self._active(key, turn_id)
            if state is None:
                return False
            state.status = status
            state.error_text = str(error_text or "").strip()
            state.updated_at = self._clock()
            return True

    def get(self, key: ConversationKey, turn_id: str) -> TurnTranscript | None:
        with self._lock:
            state = self._turns.get(key, {}).get(str(turn_id or ""))
            return state.snapshot() if state is not None else None

    def find(self, turn_id: str) -> TurnTranscript | None:
        with self._lock:
            for bucket in self._turns.values():
                state = bucket.get(str(turn_id or ""))
                if state is not None:
                    return state.snapshot()
        return None

    def recent(self, key: ConversationKey) -> tuple[TurnTranscript, ...]:
        with self._lock:
            return tuple(
                state.snapshot()
                for state in self._turns.get(key, {}).values()
            )

    def all_recent(self) -> tuple[TurnTranscript, ...]:
        with self._lock:
            turns = [
                state.snapshot()
                for bucket in self._turns.values()
                for state in bucket.values()
            ]
        return tuple(sorted(turns, key=lambda item: item.created_at))
