"""
梅尔桌宠 - 通用工具模块
统一存放跨文件共享的辅助函数
"""
import sys
import hashlib
import io
import ipaddress
import os
import re
import time
import ctypes
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Optional


# 日志/打印时需要打码的键名（大小写不敏感）
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|password|token|secret|access[_-]?key|private[_-]?key)",
    re.I,
)
_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)", re.I)
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b")
_KEY_VALUE_RE = re.compile(
    r"(\b(?:api[_-]?key|password|token|secret|access[_-]?key|private[_-]?key)"
    r"\b\s*[:=]\s*)([\"']?)([^\s,;}\]]+)",
    re.I,
)


def debug_enabled() -> bool:
    """仅在用户显式设置 MEAPET_DEBUG=1 时启用载荷级调试日志。"""
    return os.environ.get("MEAPET_DEBUG", "").strip() == "1"


def safe_print(*args, **kwargs):
    """GUI 安全版 print — 写入 stderr 确保 PyQt 下可见；自动脱敏"""
    try:
        text = " ".join(str(a) for a in args)
        text = redact_text(text)
        print(text, file=sys.stderr, flush=True)
    except Exception:
        pass


def log_error(context: str, message: str, log_dir: str = None):
    """仅在有错误时写入日志，避免无意义的磁盘 I/O；内容脱敏"""
    if log_dir is None:
        from meapet.paths import project_root
        log_dir = project_root()
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(log_dir, "chat_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{context}] {redact_text(str(message))}\n")
    except Exception:
        pass


def ensure_utf8_stdout():
    """Windows 下确保 stdout 是 UTF-8

    安全做法：如果 stdout 已是 TextIOWrapper，只重新配置编码；
    否则创建一个新的 TextIOWrapper。
    多次调用安全，不会因 GC 关闭底层 buffer。
    """
    if sys.platform == "win32":
        try:
            if isinstance(sys.stdout, io.TextIOWrapper):
                # 已是 TextIOWrapper，只改编码，不重新包装
                if sys.stdout.encoding.upper() != "UTF-8":
                    sys.stdout.reconfigure(encoding="utf-8")
            else:
                # 不是 TextIOWrapper（如 BytesIO），包装一次
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except (ValueError, OSError, AttributeError):
            pass


def mask_secret(value: str, keep: int = 4) -> str:
    """将密钥显示为 sk-****abcd 形式"""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if len(s) <= keep * 2:
        return "***"
    return f"{s[:keep]}…{s[-keep:]}"


def redact_text(text: str) -> str:
    """对自由文本中的 Bearer、sk- 及常见 key=value 凭据打码。"""
    if not text:
        return text
    out = _BEARER_RE.sub(lambda m: m.group(1) + mask_secret(m.group(2)), text)
    out = _SK_RE.sub(lambda m: mask_secret(m.group(1)), out)

    def _mask_key_value(match: re.Match) -> str:
        quote = match.group(2)
        raw = match.group(3)
        closing_quote = quote if quote and raw.endswith(quote) else ""
        if closing_quote:
            raw = raw[:-1]
        return (
            match.group(1)
            + quote
            + mask_secret(raw)
            + closing_quote
        )

    out = _KEY_VALUE_RE.sub(_mask_key_value, out)
    return out


def redact_mapping(data: Any, depth: int = 0) -> Any:
    """递归拷贝 dict/list，对密钥字段打码（不修改原对象）"""
    if depth > 8:
        return "<max-depth>"
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = mask_secret(str(v)) if v else ""
            else:
                out[k] = redact_mapping(v, depth + 1)
        return out
    if isinstance(data, list):
        return [redact_mapping(x, depth + 1) for x in data]
    if isinstance(data, str):
        return redact_text(data)
    return data


def audio_cache_key(text: str, length: int = 24) -> str:
    """为语音缓存生成不暴露原文、不可路径穿越的稳定键。"""
    value = str(text or "").strip()
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:max(16, min(int(length), len(digest)))]


def legacy_audio_cache_name(text: str, max_length: int = 120) -> str:
    """只用于查找旧缓存；移除跨平台文件名危险字符并限制长度。"""
    value = str(text or "")
    value = value.replace("……", "").replace("（", "").replace("）", "")
    value = value.replace(" ", "_").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    return value.strip(" .")[:max_length]


# ========================
# Watcher 规范化 / 云端门闩
# ========================

DEFAULT_WATCHER_INTERVAL = {
    "min_ms": 180000,
    "max_ms": 360000,
}


def normalize_watcher(raw: Optional[dict]) -> dict:
    """规范化 watcher 开关字段（缺省安全：关观察、禁云端）"""
    raw = raw or {}
    interval = raw.get("interval") if isinstance(raw.get("interval"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "allow_cloud": bool(raw.get("allow_cloud", False)),
        "require_confirm": bool(raw.get("require_confirm", True)),
        "confirm_once_session": bool(raw.get("confirm_once_session", False)),
        "interval": {
            "min_ms": int(interval.get("min_ms", DEFAULT_WATCHER_INTERVAL["min_ms"])),
            "max_ms": int(interval.get("max_ms", DEFAULT_WATCHER_INTERVAL["max_ms"])),
        },
    }


def cloud_vision_allowed(settings: dict, is_cloud_backend: bool) -> bool:
    """
    云端识图门闩：非云端后端恒 True（不上传）；
    云端后端必须 allow_cloud=True。
    settings 可为完整 config 或含 watcher 的视图。
    """
    if not is_cloud_backend:
        return True
    w = (settings or {}).get("watcher") or {}
    return bool(w.get("allow_cloud", False))


def is_loopback_url(url: str) -> bool:
    """纯本地判断 HTTP(S) URL 是否明确指向回环地址，不执行 DNS 查询。"""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        host = parsed.hostname
        if not host:
            return False
        if host.lower() == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


# ========================
# audio_cache 清理
# ========================

def cleanup_audio_cache(
    cache_dir: str,
    max_files: int = 40,
    max_age_hours: float = 48.0,
    prefix: str = "mea_",
) -> Dict[str, int]:
    """
    清理 TTS 输出缓存：
    - 删除超过 max_age_hours 的 wav
    - 若仍超过 max_files，按 mtime 删最旧的
    返回 {"removed": n, "kept": m}
    """
    removed = 0
    kept = 0
    try:
        if not cache_dir or not os.path.isdir(cache_dir):
            return {"removed": 0, "kept": 0}
        now = time.time()
        max_age = max_age_hours * 3600.0
        files = []
        for name in os.listdir(cache_dir):
            if not name.lower().endswith(".wav"):
                continue
            if prefix and not name.startswith(prefix):
                continue
            path = os.path.join(cache_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            age = now - st.st_mtime
            if age > max_age:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
            else:
                files.append((st.st_mtime, path))
        files.sort()  # oldest first
        overflow = len(files) - max_files
        if overflow > 0:
            for _, path in files[:overflow]:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
            files = files[overflow:]
        kept = len(files)
    except Exception:
        pass
    return {"removed": removed, "kept": kept}


def enable_vt():
    """开启 stdout 和 stderr 的 VT 转译支持"""
    if sys.platform != 'win32':
        return True

    kernel32 = ctypes.windll.kernel32
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    # logging 默认输出到 stderr(-12)，但也可能配置为 stdout(-11)
    # 必须同时开启两个流的 VT 支持
    handles = [-11, -12]
    success_count = 0

    for handle_id in handles:
        try:
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(handle, new_mode):
                    success_count += 1
        except Exception:
            pass

    return success_count > 0