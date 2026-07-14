"""供应商无关的直接模型请求与增量事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from meapet.agent.base import ImageAttachment


def _normalize_content_parts(
    parts: list[object],
    *,
    role: str,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for part in parts:
        if not isinstance(part, Mapping):
            raise ValueError("content parts must be mappings")
        part_type = str(part.get("type") or "").strip().lower()
        if part_type == "text":
            text = str(part.get("text") or "")
            if not text:
                raise ValueError("text content part is empty")
            normalized.append({"type": "text", "text": text})
            continue
        if part_type != "image":
            raise ValueError("image content parts must use canonical image data")
        if role != "user":
            raise ValueError("image content is only allowed in user messages")
        try:
            attachment = ImageAttachment(
                media_type=str(part.get("media_type") or ""),
                data=str(part.get("data") or ""),
            )
        except ValueError as exc:
            raise ValueError(f"image content part is invalid: {exc}") from exc
        normalized.append(attachment.canonical_part())
    if not normalized:
        raise ValueError("message content parts are empty")
    return normalized


@dataclass(frozen=True)
class CanonicalChatRequest:
    model: str
    messages: Tuple[Mapping[str, object], ...]
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = True
    response_format: Mapping[str, object] | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model = str(self.model or "").strip()
        if not model:
            raise ValueError("model is required")
        try:
            temperature = float(self.temperature)
        except (TypeError, ValueError) as exc:
            raise ValueError("temperature must be numeric") from exc
        try:
            max_tokens = int(self.max_tokens)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_tokens must be an integer") from exc
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        normalized_messages = []
        for message in self.messages or ():
            if not isinstance(message, Mapping):
                raise ValueError("messages must contain mappings")
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            if role not in {"system", "developer", "user", "assistant"}:
                raise ValueError("message role is unsupported")
            if not isinstance(content, (str, list)):
                raise ValueError("message content must be text or content parts")
            normalized_content: object = content
            if isinstance(content, list):
                normalized_content = _normalize_content_parts(
                    content,
                    role=role,
                )
            normalized_messages.append(
                {"role": role, "content": normalized_content}
            )
        if not normalized_messages:
            raise ValueError("messages are required")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "messages", tuple(normalized_messages))
        object.__setattr__(self, "temperature", temperature)
        object.__setattr__(self, "max_tokens", max_tokens)
        object.__setattr__(self, "stream", bool(self.stream))
        if self.response_format is not None:
            object.__setattr__(self, "response_format", dict(self.response_format))
        object.__setattr__(self, "extra", dict(self.extra or {}))


@dataclass(frozen=True)
class TextDelta:
    delta: str


@dataclass(frozen=True)
class ReasoningDelta:
    """只供日志/诊断消费；桌面呈现层必须忽略。"""

    delta: str


@dataclass(frozen=True)
class UsageEvent:
    usage: Mapping[str, object]


@dataclass(frozen=True)
class StreamDone:
    reason: str = ""
