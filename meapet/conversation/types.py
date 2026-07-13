"""统一对话层使用的无 UI、无网络数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Tuple


REPLY_REQUIRED_FIELDS = frozenset(
    {"display_text", "voice_text", "voice_language", "mood", "tts_style"}
)

_LANGUAGE_ALIASES = {
    "cn": "zh-CN",
    "chinese": "zh",
    "eng": "en",
    "english": "en",
    "ja-jp": "ja-JP",
    "japanese": "ja",
    "jp": "ja",
    "jpn": "ja",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-Hans",
    "zh-tw": "zh-TW",
}


def normalize_voice_language(value: object) -> str:
    """把常见别名规范为稳定的 BCP-47 风格语言码。"""
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lowered]

    parts = [part for part in raw.split("-") if part]
    if not parts:
        return ""
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            normalized.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        else:
            normalized.append(part)
    return "-".join(normalized)


def _unique_non_empty(values: Iterable[object]) -> Tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class ReplySegment:
    """一个可以独立显示、合成和播放的角色回复分段。"""

    display_text: str
    voice_text: str
    voice_language: str
    mood: str
    tts_style: str
    index: int = 0
    provided_fields: frozenset[str] = field(
        default_factory=lambda: REPLY_REQUIRED_FIELDS,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_text", str(self.display_text or "").strip())
        object.__setattr__(self, "voice_text", str(self.voice_text or "").strip())
        object.__setattr__(
            self,
            "voice_language",
            normalize_voice_language(self.voice_language),
        )
        object.__setattr__(self, "mood", str(self.mood or "").strip().lower())
        object.__setattr__(self, "tts_style", str(self.tts_style or "").strip())
        object.__setattr__(self, "provided_fields", frozenset(self.provided_fields))

    @property
    def missing_required_fields(self) -> Tuple[str, ...]:
        """返回缺失或非法的必需字段；显式空 tts_style 合法。"""
        missing = set(REPLY_REQUIRED_FIELDS - self.provided_fields)
        if not self.display_text:
            missing.add("display_text")
        if not self.voice_text:
            missing.add("voice_text")
        if not self.voice_language:
            missing.add("voice_language")
        if not self.mood:
            missing.add("mood")
        return tuple(sorted(missing))


@dataclass(frozen=True)
class FrontendCapabilities:
    renderer: str
    supported_moods: Tuple[str, ...] = ()
    supported_motions: Tuple[str, ...] = ()
    tts_enabled: bool = False
    tts_languages: Tuple[str, ...] = ()
    streaming_text: bool = True
    multi_segment: bool = True

    def __post_init__(self) -> None:
        renderer = str(self.renderer or "png").strip().lower() or "png"
        moods = _unique_non_empty(
            str(value or "").strip().lower() for value in self.supported_moods
        )
        motions = _unique_non_empty(self.supported_motions)
        languages = _unique_non_empty(
            normalize_voice_language(value) for value in self.tts_languages
        )
        object.__setattr__(self, "renderer", renderer)
        object.__setattr__(self, "supported_moods", moods)
        object.__setattr__(self, "supported_motions", motions)
        object.__setattr__(self, "tts_enabled", bool(self.tts_enabled))
        object.__setattr__(self, "tts_languages", languages)
        object.__setattr__(self, "streaming_text", bool(self.streaming_text))
        object.__setattr__(self, "multi_segment", bool(self.multi_segment))

    def to_dict(self) -> dict:
        return {
            "renderer": self.renderer,
            "supported_moods": list(self.supported_moods),
            "supported_motions": list(self.supported_motions),
            "tts_enabled": self.tts_enabled,
            "tts_languages": list(self.tts_languages),
            "streaming_text": self.streaming_text,
            "multi_segment": self.multi_segment,
        }


@dataclass(frozen=True)
class CompanionState:
    affection_level: str = ""
    character_state: str = ""
    current_mood: str = "neutral"
    busy: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "affection_level",
            str(self.affection_level or "").strip(),
        )
        object.__setattr__(
            self,
            "character_state",
            str(self.character_state or "").strip(),
        )
        object.__setattr__(
            self,
            "current_mood",
            str(self.current_mood or "neutral").strip().lower() or "neutral",
        )
        object.__setattr__(self, "busy", bool(self.busy))

    def to_dict(self) -> dict:
        return {
            "affection_level": self.affection_level,
            "character_state": self.character_state,
            "current_mood": self.current_mood,
            "busy": self.busy,
        }
