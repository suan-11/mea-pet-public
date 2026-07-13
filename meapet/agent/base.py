"""Agent 适配器与前端编排器之间的稳定边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from meapet.conversation.output_protocol import ParseResult


@dataclass(frozen=True)
class AgentTurnRequest:
    turn_id: str
    user_text: str
    history: Tuple[Mapping[str, object], ...] = ()
    frontend_context: Mapping[str, object] = field(default_factory=dict)
    tts_enabled: bool = False

    def __post_init__(self) -> None:
        turn_id = str(self.turn_id or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        if len(turn_id) > 256 or any(char in turn_id for char in "\r\n\x00"):
            raise ValueError("turn_id is not a safe request identifier")
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "user_text", str(self.user_text or "").strip())
        object.__setattr__(self, "history", tuple(self.history or ()))
        object.__setattr__(self, "frontend_context", dict(self.frontend_context or {}))
        object.__setattr__(self, "tts_enabled", bool(self.tts_enabled))


@dataclass(frozen=True)
class ToolStatus:
    state: str
    safe_text: str


@dataclass(frozen=True)
class FormatRepairRequired:
    result: ParseResult


@dataclass(frozen=True)
class TurnCompleted:
    turn_id: str
    result: ParseResult


@dataclass(frozen=True)
class TurnFailed:
    turn_id: str
    category: str
    safe_message: str
    retryable: bool = False


@dataclass(frozen=True)
class TurnCancelled:
    turn_id: str
