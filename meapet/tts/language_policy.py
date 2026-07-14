"""Decide whether a reply can be spoken directly, translated, or skipped."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from meapet.config.normalizers import canonical_tts_language


_CHINESE_SIGNAL_CHARS = frozenset(
    "的了吗呢吧啊呀嘛么这那哪谁我你他她它们很没还会要让给把被在和与"
    "就都而着过为从到对里下来说看想能需讲闲喵"
)


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
    translation_available: bool,
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
    if not translation_available:
        return TtsLanguagePlan(
            "skip",
            requested,
            reason="translation_unavailable_for_unsupported_language",
        )

    preferred = canonical_tts_language(preferred_translation_language)
    target = preferred if preferred in supported else supported[0]
    return TtsLanguagePlan("translate", requested, target)


def detect_script_language(text: object) -> str:
    """从正文脚本特征推断语言桶；不能可靠判定时返回 ``unknown``。"""
    value = str(text or "").strip()
    if not value:
        return "unknown"

    has_kana = any(
        "\u3040" <= ch <= "\u30ff" or "\u31f0" <= ch <= "\u31ff"
        for ch in value
    )
    if has_kana:
        return "jp"

    cjk = [ch for ch in value if "\u4e00" <= ch <= "\u9fff"]
    if cjk:
        # 纯汉字既可能是中文，也可能是没有假名的日文（如“東京”“最高”）。
        # 只有出现较明确的现代汉语信号时才确认中文，否则保留为 ambiguous。
        if any(ch in _CHINESE_SIGNAL_CHARS for ch in cjk):
            return "zh"
        return "unknown"

    latin_words = []
    current = []
    for char in value:
        if ("A" <= char <= "Z") or ("a" <= char <= "z"):
            current.append(char)
        elif current:
            latin_words.append("".join(current))
            current = []
    if current:
        latin_words.append("".join(current))
    # 单个拉丁词也可能是人名、品牌名或缩写；完整短语才确认英语。
    if len(latin_words) >= 2:
        return "en"
    return "unknown"


def voice_text_language_relation(
    voice_text: object,
    voice_language: object,
) -> str:
    """返回正文与目标语言的 ``match`` / ``mismatch`` / ``ambiguous``。"""
    text = str(voice_text or "").strip()
    claimed = canonical_tts_language(voice_language)
    if not text or not claimed:
        return "mismatch"
    observed = detect_script_language(text)
    if observed == "unknown":
        return "ambiguous"
    return "match" if observed == claimed else "mismatch"


def voice_text_matches_language(voice_text: object, voice_language: object) -> bool:
    """voice_text 正文是否与 voice_language 声称一致（零网络）。"""
    return voice_text_language_relation(voice_text, voice_language) == "match"


__all__ = [
    "TtsLanguagePlan",
    "canonical_tts_language",
    "detect_script_language",
    "plan_tts_language",
    "voice_text_language_relation",
    "voice_text_matches_language",
]
