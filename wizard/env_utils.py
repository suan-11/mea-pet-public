"""Dependency install and download utilities."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from PyQt5.QtCore import QObject, pyqtSignal

class WorkerSignals(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)


def pip_install(packages: list) -> bool:
    """安装 Python 包，返回是否成功"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + packages,
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception:
        return False


def check_installed(package: str) -> bool:
    """检查 Python 包是否已安装（兼容包名与 import 名差异）"""
    import_aliases = {
        "pywin32": ("win32api", "win32gui", "pythoncom"),
        "live2d-py": ("live2d",),
        "PyQt5": ("PyQt5",),
        "PyOpenGL": ("OpenGL",),
        "pillow": ("PIL",),
        "requests": ("requests",),
        "httpx": ("httpx",),
        "jieba": ("jieba",),
        "pip": ("pip",),
    }
    names = import_aliases.get(package, (package.replace("-", "_"),))
    for name in names:
        try:
            __import__(name)
            return True
        except ImportError:
            continue
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            capture_output=True, timeout=10, check=True
        )
        return True
    except Exception:
        return False


def download_file(url: str, dest: str, progress_callback=None):
    """通过 HTTPS 原子下载文件；失败时保留已有目标文件。"""
    import time as _time

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False

    dest = os.path.abspath(dest)
    dest_dir = os.path.dirname(dest) or os.curdir
    temp_path = ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
            if urllib.parse.urlparse(final_url).scheme.lower() != "https":
                raise ValueError("下载重定向降级为非 HTTPS")
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536  # 64KB，减少更新频率
            last_report = 0
            last_pct = -1
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=dest_dir,
                prefix=f".{os.path.basename(dest)}.",
                suffix=".download",
                delete=False,
            ) as f:
                temp_path = f.name
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = _time.time()
                    if progress_callback and now - last_report >= 0.2:
                        last_report = now
                        if total > 0:
                            pct = int(downloaded / total * 100)
                            if pct != last_pct:
                                last_pct = pct
                                progress_callback(pct)
                        else:
                            # 无 Content-Length 时给个脉冲效果（50% 表示正在下载）
                            progress_callback(-1)
                if total > 0 and downloaded != total:
                    raise OSError(
                        f"下载不完整：expected={total} actual={downloaded}"
                    )
                f.flush()
                os.fsync(f.fileno())
            # 完成后确保 100%（无论是否已知 Content-Length）
            if progress_callback and (total == 0 or last_pct != 100):
                progress_callback(100)
        os.replace(temp_path, dest)
        temp_path = ""
        return True
    except Exception:
        return False
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ═══════════════════════════════════════
# 页面：环境检测
# ═══════════════════════════════════════
