"""不落盘的全屏、区域与 Windows 应用窗口截图。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Mapping

from PIL import ImageGrab


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class CapturedImage:
    image: Any
    metadata: Mapping[str, object]


def _normalized_region(region: object) -> dict[str, int]:
    if not isinstance(region, dict):
        raise CaptureError("invalid_region", "region must contain x, y, width and height")
    try:
        result = {
            key: int(region[key])
            for key in ("x", "y", "width", "height")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureError(
            "invalid_region",
            "region must contain integer x, y, width and height",
        ) from exc
    if result["width"] <= 0 or result["height"] <= 0:
        raise CaptureError("invalid_region", "region dimensions must be positive")
    return result


def _windows_application_rect(application: str) -> tuple[tuple[int, int, int, int], str]:
    if not sys.platform.startswith("win"):
        raise CaptureError(
            "unsupported_scope",
            "application capture currently requires Windows",
        )
    query = str(application or "").strip()
    if not query:
        raise CaptureError("invalid_application", "application title is required")
    try:
        import win32gui
    except ImportError as exc:
        raise CaptureError(
            "dependency_missing",
            "application capture requires pywin32",
        ) from exc

    matches = []
    query_folded = query.casefold()

    def collect(hwnd, _extra) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if title and query_folded in title.casefold():
                matches.append((hwnd, title))
        except Exception:
            return

    try:
        win32gui.EnumWindows(collect, None)
    except Exception as exc:
        raise CaptureError("capture_failed", "could not enumerate windows") from exc
    if not matches:
        raise CaptureError("window_not_found", "application window was not found")

    hwnd, title = matches[0]
    try:
        if win32gui.IsIconic(hwnd):
            raise CaptureError("window_unavailable", "application window is minimized")
        left, top, right, bottom = (
            int(value) for value in win32gui.GetWindowRect(hwnd)
        )
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("window_unavailable", "application window disappeared") from exc
    if right <= left or bottom <= top:
        raise CaptureError("window_unavailable", "application window has no visible area")
    return (left, top, right, bottom), title


def capture_screen_image(
    *,
    scope: str = "full_screen",
    region: object = None,
    application: str = "",
) -> CapturedImage:
    """采集内存图片；调用者决定是否编码，函数本身绝不写文件。"""
    normalized_scope = str(scope or "full_screen").strip().lower()
    application_title = ""
    try:
        if normalized_scope == "full_screen":
            image = ImageGrab.grab(all_screens=True)
        elif normalized_scope == "region":
            bounds = _normalized_region(region)
            bbox = (
                bounds["x"],
                bounds["y"],
                bounds["x"] + bounds["width"],
                bounds["y"] + bounds["height"],
            )
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        elif normalized_scope == "application":
            bbox, application_title = _windows_application_rect(application)
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        else:
            raise CaptureError("unsupported_scope", "unsupported capture scope")
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("capture_failed", "screen capture failed") from exc

    width, height = image.size
    metadata = {
        "scope": normalized_scope,
        "width": int(width),
        "height": int(height),
    }
    if application_title:
        metadata["application"] = application_title[:256]
    return CapturedImage(image=image, metadata=metadata)
