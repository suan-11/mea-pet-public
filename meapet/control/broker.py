"""线程安全的 Companion 控制命令、队列与隐私边界。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping

from meapet.conversation.types import ReplySegment


_PUBLIC_CAPABILITY_FIELDS = (
    "renderer",
    "supported_moods",
    "supported_motions",
    "tts_enabled",
    "tts_languages",
    "streaming_text",
    "multi_segment",
)
_PUBLIC_STATE_FIELDS = (
    "affection_level",
    "character_state",
    "current_mood",
    "busy",
)
_CAPTURE_SCOPES = frozenset({"full_screen", "region", "application"})


@dataclass(frozen=True)
class SayCommand:
    queue_id: str
    request_id: str
    segments: tuple[ReplySegment, ...]
    expires_at: float


@dataclass(frozen=True)
class ExpressionCommand:
    request_id: str
    mood: str
    motion: str


@dataclass(frozen=True)
class CaptureRequest:
    capture_id: str
    request_id: str
    scope: str
    region: Mapping[str, int] | None
    application: str


def _safe_request_id(value: object, prefix: str) -> str:
    result = str(value or "").strip()
    if not result:
        return f"{prefix}-{uuid.uuid4().hex}"
    if len(result) > 256 or any(char in result for char in "\r\n\x00"):
        raise ValueError("request_id is not a safe identifier")
    return result


def _public_state(value: object) -> dict:
    source = value if isinstance(value, dict) else {}
    raw_capabilities = source.get("frontend_capabilities")
    raw_companion = source.get("companion_state")
    capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}
    companion = raw_companion if isinstance(raw_companion, dict) else {}
    return {
        "frontend_capabilities": {
            key: copy.deepcopy(capabilities[key])
            for key in _PUBLIC_CAPABILITY_FIELDS
            if key in capabilities
        },
        "companion_state": {
            key: copy.deepcopy(companion[key])
            for key in _PUBLIC_STATE_FIELDS
            if key in companion
        },
    }


class CompanionControlBroker:
    """MCP 工作线程和 Qt 主线程之间的最小线程安全桥。"""

    def __init__(
        self,
        *,
        state: dict | None = None,
        max_say_queue: int = 8,
        say_ttl_seconds: float = 120.0,
        capture_timeout_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._state = _public_state(state)
        self._max_say_queue = max(1, min(int(max_say_queue), 100))
        self._say_ttl_seconds = max(1.0, float(say_ttl_seconds))
        self._capture_timeout_seconds = max(
            0.05,
            float(capture_timeout_seconds),
        )
        self._say_queue: deque[SayCommand] = deque()
        self._say_dedupe: dict[str, tuple[dict, float]] = {}
        self._user_busy = False
        self._expressions: deque[ExpressionCommand] = deque()
        self._expression_dedupe: dict[str, dict] = {}
        self._capture_queue: deque[CaptureRequest] = deque()
        self._capture_pending: dict[
            str,
            concurrent.futures.Future,
        ] = {}
        self._capture_request_ids: dict[str, str] = {}

    def update_state(self, state: dict) -> None:
        with self._lock:
            self._state = _public_state(state)

    def set_user_busy(self, busy: bool) -> None:
        with self._lock:
            self._user_busy = bool(busy)
            self._state.setdefault("companion_state", {})["busy"] = bool(busy)

    def _prune_say(self) -> None:
        now = self._clock()
        self._say_queue = deque(
            command
            for command in self._say_queue
            if command.expires_at > now
        )
        self._say_dedupe = {
            request_id: cached
            for request_id, cached in self._say_dedupe.items()
            if cached[1] > now
        }

    @staticmethod
    def _parse_segments(payloads) -> tuple[tuple[ReplySegment, ...], tuple[str, ...]]:
        if not isinstance(payloads, (list, tuple)) or not payloads:
            return (), ("segments",)
        segments = []
        missing = set()
        for index, payload in enumerate(payloads):
            if not isinstance(payload, dict):
                missing.add(f"segments[{index}]")
                continue
            segment = ReplySegment(
                index=index,
                display_text=payload.get("display_text", ""),
                voice_text=payload.get("voice_text", ""),
                voice_language=payload.get("voice_language", ""),
                mood=payload.get("mood", ""),
                tts_style=payload.get("tts_style", ""),
                provided_fields=frozenset(payload),
            )
            missing.update(segment.missing_required_fields)
            segments.append(segment)
        return tuple(segments), tuple(sorted(missing))

    async def say(self, segments, *, request_id: str = "") -> dict:
        try:
            safe_id = _safe_request_id(request_id, "say")
        except ValueError:
            return {"status": "invalid", "code": "invalid_request_id"}
        parsed, missing = self._parse_segments(segments)
        if missing:
            return {
                "status": "invalid",
                "code": "invalid_segments",
                "missing_fields": list(missing),
            }

        with self._lock:
            self._prune_say()
            cached = self._say_dedupe.get(safe_id)
            if cached is not None:
                result = dict(cached[0])
                result["duplicate"] = True
                return result
            if len(self._say_queue) >= self._max_say_queue:
                return {"status": "busy", "code": "queue_full"}

            queue_id = f"say-{uuid.uuid4().hex}"
            expires_at = self._clock() + self._say_ttl_seconds
            command = SayCommand(queue_id, safe_id, parsed, expires_at)
            self._say_queue.append(command)
            result = {
                "status": "queued",
                "queue_id": queue_id,
                "position": len(self._say_queue),
                "duplicate": False,
            }
            self._say_dedupe[safe_id] = (dict(result), expires_at)
            return result

    @property
    def say_queue_size(self) -> int:
        with self._lock:
            self._prune_say()
            return len(self._say_queue)

    def take_ready_say(self) -> SayCommand | None:
        with self._lock:
            self._prune_say()
            if self._user_busy or not self._say_queue:
                return None
            return self._say_queue.popleft()

    async def get_state(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    async def express(
        self,
        *,
        mood: str = "",
        motion: str = "",
        request_id: str = "",
    ) -> dict:
        mood = str(mood or "").strip().lower()
        motion = str(motion or "").strip()
        if not mood and not motion:
            return {"status": "invalid", "code": "empty_expression"}
        try:
            safe_id = _safe_request_id(request_id, "express")
        except ValueError:
            return {"status": "invalid", "code": "invalid_request_id"}

        with self._lock:
            duplicate = self._expression_dedupe.get(safe_id)
            if duplicate is not None:
                result = dict(duplicate)
                result["duplicate"] = True
                return result
            capabilities = self._state.get("frontend_capabilities") or {}
            moods = set(capabilities.get("supported_moods") or ())
            motions = set(capabilities.get("supported_motions") or ())
            if mood and mood not in moods:
                return {
                    "status": "unsupported",
                    "field": "mood",
                    "value": mood,
                }
            if motion and motion not in motions:
                return {
                    "status": "unsupported",
                    "field": "motion",
                    "value": motion,
                }
            self._expressions.append(ExpressionCommand(safe_id, mood, motion))
            result = {"status": "queued", "duplicate": False}
            self._expression_dedupe[safe_id] = dict(result)
            return result

    def take_expressions(self) -> tuple[ExpressionCommand, ...]:
        with self._lock:
            result = tuple(self._expressions)
            self._expressions.clear()
            return result

    @staticmethod
    def _normalize_region(region) -> dict[str, int] | None:
        if not isinstance(region, dict):
            return None
        try:
            normalized = {
                key: int(region[key])
                for key in ("x", "y", "width", "height")
            }
        except (KeyError, TypeError, ValueError):
            return None
        if normalized["width"] <= 0 or normalized["height"] <= 0:
            return None
        return normalized

    async def capture_screen(
        self,
        *,
        scope: str = "full_screen",
        region=None,
        application: str = "",
        request_id: str = "",
    ) -> dict:
        scope = str(scope or "full_screen").strip().lower()
        if scope not in _CAPTURE_SCOPES:
            return {"status": "invalid", "code": "unsupported_scope"}
        normalized_region = self._normalize_region(region)
        if scope == "region" and normalized_region is None:
            return {"status": "invalid", "code": "invalid_region"}
        try:
            safe_id = _safe_request_id(request_id, "capture")
        except ValueError:
            return {"status": "invalid", "code": "invalid_request_id"}

        with self._lock:
            existing_capture_id = self._capture_request_ids.get(safe_id)
            future = (
                self._capture_pending.get(existing_capture_id)
                if existing_capture_id
                else None
            )
            if future is None:
                capture_id = f"capture-{uuid.uuid4().hex}"
                future = concurrent.futures.Future()
                self._capture_pending[capture_id] = future
                self._capture_request_ids[safe_id] = capture_id
                self._capture_queue.append(
                    CaptureRequest(
                        capture_id=capture_id,
                        request_id=safe_id,
                        scope=scope,
                        region=normalized_region,
                        application=str(application or "").strip()[:256],
                    )
                )
            else:
                capture_id = existing_capture_id

        try:
            wrapped = asyncio.wrap_future(future)
            return await asyncio.wait_for(
                asyncio.shield(wrapped),
                timeout=self._capture_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._discard_capture(capture_id, safe_id)
            return {"status": "timeout", "code": "confirmation_timeout"}
        except asyncio.CancelledError:
            self._discard_capture(capture_id, safe_id)
            raise

    def _discard_capture(self, capture_id: str, request_id: str) -> None:
        with self._lock:
            self._capture_pending.pop(capture_id, None)
            if self._capture_request_ids.get(request_id) == capture_id:
                self._capture_request_ids.pop(request_id, None)
            self._capture_queue = deque(
                item
                for item in self._capture_queue
                if item.capture_id != capture_id
            )

    def take_capture_requests(self) -> tuple[CaptureRequest, ...]:
        with self._lock:
            result = tuple(self._capture_queue)
            self._capture_queue.clear()
            return result

    @staticmethod
    def _sanitize_capture_result(result: object) -> dict:
        source = result if isinstance(result, dict) else {}
        status = str(source.get("status") or "error")
        if status != "approved":
            clean = {"status": status}
            if source.get("code"):
                clean["code"] = str(source["code"])
            return clean
        raw_image = source.get("image")
        image = raw_image if isinstance(raw_image, dict) else {}
        raw_metadata = source.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        return {
            "status": "approved",
            "image": {
                "mime_type": str(image.get("mime_type") or "image/png"),
                "data": str(image.get("data") or ""),
            },
            "metadata": {
                key: copy.deepcopy(metadata[key])
                for key in ("width", "height", "scope", "application")
                if key in metadata
            },
        }

    def resolve_capture(self, capture_id: str, result: dict) -> bool:
        with self._lock:
            future = self._capture_pending.pop(capture_id, None)
            request_id = ""
            for candidate, mapped_id in tuple(self._capture_request_ids.items()):
                if mapped_id == capture_id:
                    request_id = candidate
                    self._capture_request_ids.pop(candidate, None)
                    break
        if future is None or future.done():
            return False
        future.set_result(self._sanitize_capture_result(result))
        return True
