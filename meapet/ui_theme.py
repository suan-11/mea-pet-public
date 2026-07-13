"""MeaPet 的跨窗口语义化 UI 设计令牌。"""

from __future__ import annotations

import os

import math
import re
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_PALETTE = {
    "canvas": "#0E1020",
    "surface": "#17192D",
    "surface_elevated": "#20233D",
    "surface_input": "#111326",
    "primary": "#FF91B4",
    "primary_hover": "#FFA8C4",
    "on_primary": "#26131B",
    "secondary": "#FFB36B",
    "accent": "#A69BFF",
    "text_primary": "#F8F8FC",
    "text_secondary": "#CACCE0",
    "text_muted": "#9FA3BC",
    "border": "#3B3E5B",
    "border_strong": "#555A7B",
    "focus": "#C0B9FF",
    "success": "#70DDB0",
    "warning": "#F4CC75",
    "danger": "#FF8892",
    "on_danger": "#2A1014",
}

PALETTE: Mapping[str, str] = MappingProxyType(_PALETTE)

DISPLAY_FONT_NAME = "LXGW WenKai"
DISPLAY_FONT_FAMILY = f'"{DISPLAY_FONT_NAME}"'
BUNDLED_DISPLAY_FONT_PATH = (
    Path(__file__).resolve().parent / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"
)

if sys.platform == "win32":
    FALLBACK_BODY_FONT_NAME = "Microsoft YaHei UI"
elif sys.platform == "darwin":
    FALLBACK_BODY_FONT_NAME = "PingFang SC"
else:
    FALLBACK_BODY_FONT_NAME = "Noto Sans CJK SC"

# 正文和展示文字统一使用随项目分发的霞鹜文楷，避免各平台回退到古早系统字体。
BODY_FONT_NAME = DISPLAY_FONT_NAME
FONT_FAMILY = f'"{BODY_FONT_NAME}"'
MONO_FONT_FAMILY = '"Cascadia Code", "JetBrains Mono", "Cascadia Mono", Consolas, monospace'

_APPLICATION_FONT_FAMILIES: tuple[str, ...] = ()
_APPLICATION_BASE_FONT_POINT_SIZE: float | None = None
_APPLICATION_BASE_FONT_PIXEL_SIZE: int | None = None

UI_FONT_SCALE_MIN = 0.8
UI_FONT_SCALE_MAX = 1.5
UI_FONT_SCALE_DEFAULT = 1.0
_UI_FONT_SCALE = UI_FONT_SCALE_DEFAULT
_BASE_STYLESHEET_PROPERTY = "_meapetBaseStylesheet"
_SCALED_STYLESHEET_PROPERTY = "_meapetScaledStylesheet"
_FONT_SIZE_PATTERN = re.compile(
    r"(?P<prefix>font-size\s*:\s*)(?P<size>\d+(?:\.\d+)?)px",
    re.IGNORECASE,
)

MIN_TARGET_SIZE = 44

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_8 = 32

RADIUS_SMALL = 8
RADIUS_MEDIUM = 12
RADIUS_LARGE = 18


def ensure_application_fonts() -> tuple[str, ...]:
    """加载随项目分发的霞鹜文楷，并把它设为 Qt 全局默认字体。"""
    global _APPLICATION_FONT_FAMILIES
    global _APPLICATION_BASE_FONT_PIXEL_SIZE
    global _APPLICATION_BASE_FONT_POINT_SIZE

    from PyQt5.QtGui import QFontDatabase
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return _APPLICATION_FONT_FAMILIES

    if not _APPLICATION_FONT_FAMILIES and BUNDLED_DISPLAY_FONT_PATH.is_file():
        font_id = QFontDatabase.addApplicationFont(
            str(BUNDLED_DISPLAY_FONT_PATH)
        )
        if font_id >= 0:
            _APPLICATION_FONT_FAMILIES = tuple(
                QFontDatabase.applicationFontFamilies(font_id)
            )

    resolved_family = (
        _APPLICATION_FONT_FAMILIES[0]
        if _APPLICATION_FONT_FAMILIES
        else FALLBACK_BODY_FONT_NAME
    )
    app_font = app.font()
    if (
        _APPLICATION_BASE_FONT_POINT_SIZE is None
        and _APPLICATION_BASE_FONT_PIXEL_SIZE is None
    ):
        if app_font.pointSizeF() > 0:
            _APPLICATION_BASE_FONT_POINT_SIZE = app_font.pointSizeF()
        elif app_font.pixelSize() > 0:
            _APPLICATION_BASE_FONT_PIXEL_SIZE = app_font.pixelSize()

    app_font.setFamily(resolved_family)
    if _APPLICATION_BASE_FONT_POINT_SIZE is not None:
        app_font.setPointSizeF(
            _APPLICATION_BASE_FONT_POINT_SIZE * _UI_FONT_SCALE
        )
    elif _APPLICATION_BASE_FONT_PIXEL_SIZE is not None:
        app_font.setPixelSize(
            max(1, round(_APPLICATION_BASE_FONT_PIXEL_SIZE * _UI_FONT_SCALE))
        )
    app.setFont(app_font)
    return _APPLICATION_FONT_FAMILIES


def normalize_ui_font_scale(value: object) -> float:
    """把任意配置值规范到受支持的字体缩放范围。"""
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return UI_FONT_SCALE_DEFAULT
    if not math.isfinite(scale):
        return UI_FONT_SCALE_DEFAULT
    return min(max(scale, UI_FONT_SCALE_MIN), UI_FONT_SCALE_MAX)


def get_ui_font_scale() -> float:
    """返回当前进程使用的全局界面字体缩放。"""
    return _UI_FONT_SCALE


def set_ui_font_scale(scale: object) -> float:
    """设置全局界面字体缩放，并同步 Qt 默认字体。"""
    global _UI_FONT_SCALE

    _UI_FONT_SCALE = normalize_ui_font_scale(scale)
    ensure_application_fonts()
    return _UI_FONT_SCALE


def scale_stylesheet_font_sizes(
    stylesheet: str,
    scale: object | None = None,
) -> str:
    """只缩放 QSS 中的 ``font-size: Npx``，不改变布局尺寸。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else normalize_ui_font_scale(scale)
    )

    def replace(match: re.Match[str]) -> str:
        size = max(1, round(float(match.group("size")) * factor))
        return f"{match.group('prefix')}{size}px"

    return _FONT_SIZE_PATTERN.sub(replace, stylesheet or "")


def set_scaled_stylesheet(widget, stylesheet: str, scale: object | None = None) -> str:
    """给控件应用可重复缩放的 QSS，并保留一份未缩放基准。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else normalize_ui_font_scale(scale)
    )
    base = stylesheet or ""
    scaled = scale_stylesheet_font_sizes(base, factor)
    widget.setProperty(_BASE_STYLESHEET_PROPERTY, base)
    widget.setProperty(_SCALED_STYLESHEET_PROPERTY, scaled)
    widget.setStyleSheet(scaled)
    return scaled


def apply_ui_font_scale(root, scale: object | None = None) -> float:
    """缩放控件树中的显式 QSS；重复预览不会发生倍率累乘。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else set_ui_font_scale(scale)
    )

    from PyQt5.QtWidgets import QWidget

    widgets = (root, *root.findChildren(QWidget))
    for widget in widgets:
        current = widget.styleSheet()
        if not current:
            continue
        base = widget.property(_BASE_STYLESHEET_PROPERTY)
        last_scaled = widget.property(_SCALED_STYLESHEET_PROPERTY)
        if not isinstance(base, str) or current != last_scaled:
            base = current
        scaled = scale_stylesheet_font_sizes(base, factor)
        widget.setProperty(_BASE_STYLESHEET_PROPERTY, base)
        widget.setProperty(_SCALED_STYLESHEET_PROPERTY, scaled)
        if current != scaled:
            widget.setStyleSheet(scaled)
    return factor


def rgba(color: str, alpha: int) -> str:
    """把 ``#RRGGBB`` 转成 Qt 样式表可用的 ``rgba`` 字符串。"""
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"颜色必须使用 #RRGGBB 格式: {color!r}")
    if not 0 <= alpha <= 255:
        raise ValueError(f"alpha 必须在 0..255 之间: {alpha}")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def contrast_ratio(foreground: str, background: str) -> float:
    """返回两个 ``#RRGGBB`` 颜色的 WCAG 2.x 对比度。"""
    foreground_luminance = _relative_luminance(foreground)
    background_luminance = _relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: str) -> float:
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"颜色必须使用 #RRGGBB 格式: {color!r}")
    channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def resolve_reduced_motion(config_value: object | None = None) -> bool:
    """合并配置项、显式环境变量与常见系统减少动画启发式。

    优先级：
    1. ``config_value`` 若为 True → 开启
    2. 环境变量 ``MEAPET_REDUCED_MOTION``
    3. Linux: ``gsettings`` / ``org.gnome.desktop.interface enable-animations``
    4. 默认 False
    """
    if config_value is True or str(config_value).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if config_value is False:
        # 用户在配置中明确关闭时，仍允许环境变量强制开启
        env = os.environ.get("MEAPET_REDUCED_MOTION", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return True
        if env in {"0", "false", "no", "off"}:
            return False
    else:
        env = os.environ.get("MEAPET_REDUCED_MOTION", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return True
        if env in {"0", "false", "no", "off"}:
            return False

    # 系统启发式（失败则忽略）
    try:
        import subprocess
        import shutil

        if shutil.which("gsettings"):
            out = subprocess.run(
                [
                    "gsettings",
                    "get",
                    "org.gnome.desktop.interface",
                    "enable-animations",
                ],
                capture_output=True,
                text=True,
                timeout=0.4,
                check=False,
            )
            val = (out.stdout or "").strip().lower()
            if val in {"false", "0"}:
                return True
    except Exception:
        pass
    return False


def apply_reduced_motion_env(enabled: bool) -> None:
    """把减少动画偏好写入进程环境，供气泡/输入等模块读取。"""
    if enabled:
        os.environ["MEAPET_REDUCED_MOTION"] = "1"
    else:
        # 仅在我们写入 1 时清理；若用户外部强制 0/1 也统一落到当前偏好
        os.environ.pop("MEAPET_REDUCED_MOTION", None)
