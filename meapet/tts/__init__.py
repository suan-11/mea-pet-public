"""TTS package public surface (lazy exports to keep infer scripts light)."""

from __future__ import annotations

from typing import Any

__all__ = ["MeaTTS"]


def __getattr__(name: str) -> Any:
    if name == "MeaTTS":
        from meapet.tts.service import MeaTTS

        return MeaTTS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
