"""平台检测与路径常量"""
from __future__ import annotations

import os
import platform as _platform
import shutil
import sys
from typing import List, Tuple

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
PYTHON_CHECK_NAME = "Python 3.10+"


def python_runtime_compatibility(version_info=None) -> tuple[bool, str]:
    """区分桌宠核心运行能力与本地 VITS 的推荐 Python 范围。"""
    version = version_info or sys.version_info
    try:
        major = int(version.major)
        minor = int(version.minor)
        micro = int(version.micro)
    except (AttributeError, TypeError, ValueError):
        return False, "无法识别 Python 版本（需要 3.10+）"

    version_text = f"{major}.{minor}.{micro}"
    if major != 3 or minor < 10:
        return False, f"{version_text}（需要 Python 3.10+）"
    if minor >= 13:
        return (
            True,
            f"{version_text}（桌宠可运行；本地 VITS 推荐 3.10–3.12）",
        )
    return True, version_text

def detect_platform() -> dict:
    """检测当前运行平台，供环境检测与按需安装使用。"""
    system = _platform.system()  # Windows / Linux / Darwin
    machine = (_platform.machine() or "").lower()
    release = _platform.release() or ""
    version = _platform.version() or ""

    if system == "Windows":
        os_key = "windows"
        os_label = "Windows"
    elif system == "Darwin":
        os_key = "macos"
        os_label = "macOS"
    elif system == "Linux":
        os_key = "linux"
        os_label = "Linux"
    else:
        os_key = "unknown"
        os_label = system or "Unknown"

    # WSL 识别
    is_wsl = False
    if os_key == "linux":
        try:
            with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
                ver = f.read().lower()
            is_wsl = ("microsoft" in ver) or ("wsl" in ver)
        except Exception:
            is_wsl = "microsoft" in version.lower() or "wsl" in release.lower()
        if is_wsl:
            os_label = "Linux (WSL)"

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("i386", "i686", "x86"):
        arch = "x86"
    else:
        arch = machine or "unknown"

    return {
        "os_key": os_key,
        "os_label": os_label,
        "system": system,
        "arch": arch,
        "machine": machine or "unknown",
        "release": release,
        "is_windows": os_key == "windows",
        "is_linux": os_key == "linux",
        "is_macos": os_key == "macos",
        "is_wsl": is_wsl,
        "python": f"{_platform.python_version()}",
        "display": f"{os_label} · {arch} · Python {_platform.python_version()}",
    }


PLATFORM = detect_platform()


def platform_checklist() -> list:
    """按平台返回环境检测项 [(name, hint, required), ...]。"""
    items = [
        (
            PYTHON_CHECK_NAME,
            "桌宠核心运行环境；本地 VITS 推荐 3.10–3.12",
            True,
        ),
        ("pip", "Python 包管理器", True),
        ("PyQt5", "窗口界面库", True),
        ("requests", "HTTP 请求库（兼容）", True),
        ("httpx", "异步 HTTP（对话/TTS/识图必需）", True),
        ("jieba", "中文分词库（记忆/嵌入必需）", True),
    ]
    if PLATFORM["is_windows"]:
        items.append(("pywin32", "Windows 窗口控制（命中区域等）", True))
    # Live2D / OpenGL 全平台可选
    items.append(("live2d-py", "Live2D 模型渲染（可选，失败则用 PNG）", False))
    items.append(("PyOpenGL", "OpenGL 渲染（可选，Live2D 相关）", False))
    items.append(("Ollama", "本地 AI 后端（可选）", False))
    return items


def ollama_install_hint() -> str:
    """当前平台安装 Ollama 的说明（不自动下载）。"""
    if PLATFORM["is_windows"]:
        return (
            "Windows：可从 https://ollama.com/download 下载安装包，\n"
            "或在本页点「安装」按需下载 OllamaSetup.exe（需确认）。"
        )
    if PLATFORM["is_macos"]:
        return (
            "macOS：推荐 `brew install ollama`，\n"
            "或从 https://ollama.com/download 下载官方包。本向导不自动下载。"
        )
    # linux / wsl
    return (
        "Linux：推荐按官网脚本手动安装：\n"
        "  curl -fsSL https://ollama.com/install.sh | sh\n"
        "或使用发行版包管理器。本向导默认不自动下载。"
    )
