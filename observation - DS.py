"""Structured, bounded relay output from a dedicated vision model."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VisionObservation:
    summary: str
    application: str = ""
    activity: str = "unknown"
    notable_text: tuple[str, ...] = ()
    sensitive: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "application": self.application,
            "activity": self.activity,
            "notable_text": list(self.notable_text),
            "sensitive": self.sensitive,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _clean_notable_items(notable: list[str]) -> list[str]:
    """过滤噪音项：过短、日志时间戳、日志级别标记等"""
    time_pattern = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
    log_level_pattern = re.compile(r'^\[(INFO|DEBUG|WARNING|ERROR)\]\s+')
    cleaned = []
    for item in notable:
        if len(item) < 10:
            continue
        if time_pattern.search(item):
            continue
        if log_level_pattern.match(item):
            continue
        cleaned.append(item)
    return cleaned


def parse_vision_observation(raw: object) -> VisionObservation | None:
    text = str(raw or "").strip()
    if not text:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        summary = _bounded_text(text, 800)
        return VisionObservation(summary=summary) if summary else None
    if not isinstance(payload, dict):
        return None

    # ---- 处理 summary 嵌套 JSON 的情况 ----
    summary_raw = payload.get("summary", "")
    # 如果 summary 本身是 JSON 字符串，尝试解析内部字段
    if isinstance(summary_raw, str) and summary_raw.strip().startswith("{"):
        try:
            inner = json.loads(summary_raw)
            if isinstance(inner, dict):
                # 用内层的 summary 覆盖外层
                if inner.get("summary"):
                    summary_raw = inner["summary"]
                # 如果内层有 notable_text，优先使用内层的数据
                if "notable_text" in inner:
                    payload["notable_text"] = inner["notable_text"]
        except json.JSONDecodeError:
            pass

    summary = _bounded_text(summary_raw, 800)
    if not summary:
        return None

    # ---- 提取 notable_text ----
    raw_notable = payload.get("notable_text")
    notable = []
    if isinstance(raw_notable, list):
        for value in raw_notable[:10]:
            item = _bounded_text(value, 120)
            if item and item not in notable:
                notable.append(item)

    # ---- 清洗 notable_text ----
    notable = _clean_notable_items(notable)

    # ---- 特殊情况：无有效 notable 且 activity 为 unknown 时，转 idle ----
    activity = _bounded_text(payload.get("activity"), 64) or "unknown"
    if not notable and activity == "unknown":
        return VisionObservation(
            summary="当前桌面无明确活动。",
            application=_bounded_text(payload.get("application"), 120),
            activity="idle",
            notable_text=(),
            sensitive=bool(payload.get("sensitive", False)),
        )

    return VisionObservation(
        summary=summary,
        application=_bounded_text(payload.get("application"), 120),
        activity=activity,
        notable_text=tuple(notable),
        sensitive=bool(payload.get("sensitive", False)),
    )


__all__ = ["VisionObservation", "parse_vision_observation"]