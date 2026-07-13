"""不依赖配置存储状态的轻量值规范化函数。"""

from __future__ import annotations


_GSV_REF_LANGUAGE_ALIASES = {
    "jp": "jp",
    "ja": "jp",
    "jpn": "jp",
    "japanese": "jp",
    "日文": "jp",
    "日语": "jp",
    "zh": "zh",
    "cn": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "汉语": "zh",
    "en": "en",
    "eng": "en",
    "english": "en",
    "英文": "en",
    "英语": "en",
}


def normalize_gsv_ref_language(value: object) -> str:
    """把 GPT-SoVITS 参考音频语言规范为 ``jp`` / ``zh`` / ``en``。"""
    raw = str(value or "jp").strip().lower()
    return _GSV_REF_LANGUAGE_ALIASES.get(raw, "jp")


def canonical_tts_language(value: object) -> str:
    """把 BCP-47 或常见别名压缩为 TTS 使用的主语言标签。"""
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    primary = raw.split("-", 1)[0]
    if primary in ("ja", "jp") or raw in ("jpn", "japanese", "日文", "日语"):
        return "jp"
    if primary in ("zh", "cn") or raw in ("chinese", "中文", "汉语"):
        return "zh"
    if primary == "en" or raw in ("eng", "english", "英文", "英语"):
        return "en"
    return primary
