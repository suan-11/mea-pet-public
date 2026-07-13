"""Decide whether a reply can be spoken directly, translated, or skipped."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from meapet.config.normalizers import canonical_tts_language


def _unique_languages(values: Iterable[object]) -> tuple[str, ...]:
    languages = []
    seen = set()
    for value in values:
        language = canonical_tts_language(value)
        if not language or language in seen:
            continue
        seen.add(language)
        languages.append(language)
    return tuple(languages)


@dataclass(frozen=True)
class TtsLanguagePlan:
    """A side-effect-free routing decision for one reply segment."""

    action: str
    requested_language: str
    synthesis_language: str = ""
    reason: str = ""

    @property
    def requires_translation(self) -> bool:
        return self.action == "translate"


def plan_tts_language(
    requested_language: object,
    *,
    supported_languages: Iterable[object],
    translation_enabled: bool,
    translation_api_configured: bool,
    preferred_translation_language: object = "",
) -> TtsLanguagePlan:
    """Plan multilingual synthesis without silently changing languages."""
    requested = canonical_tts_language(requested_language)
    supported = _unique_languages(supported_languages)

    if not requested:
        return TtsLanguagePlan("skip", "", reason="missing_output_language")
    if requested in supported:
        return TtsLanguagePlan("direct", requested, requested)
    if not supported:
        return TtsLanguagePlan(
            "skip",
            requested,
            reason="no_supported_tts_language",
        )
    if not translation_enabled:
        return TtsLanguagePlan(
            "skip",
            requested,
            reason="translation_disabled_for_unsupported_language",
        )
    if not translation_api_configured:
        return TtsLanguagePlan(
            "skip",
            requested,
            reason="translation_api_unavailable_for_unsupported_language",
        )

    preferred = canonical_tts_language(preferred_translation_language)
    target = preferred if preferred in supported else supported[0]
    return TtsLanguagePlan("translate", requested, target)


__all__ = [
    "TtsLanguagePlan",
    "canonical_tts_language",
    "plan_tts_language",
]
