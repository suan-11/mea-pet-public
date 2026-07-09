"""
梅尔桌宠 - 通用工具模块
统一存放跨文件共享的辅助函数
"""
import sys
import io
import os
from datetime import datetime


def safe_print(*args, **kwargs):
    """GUI 安全版 print — 写入 stderr 确保 PyQt 下可见"""
    try:
        print(*args, **kwargs, file=sys.stderr, flush=True)
    except Exception:
        pass


def log_error(context: str, message: str, log_dir: str = None):
    """仅在有错误时写入日志，避免无意义的磁盘 I/O"""
    if log_dir is None:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(log_dir, "chat_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{context}] {message}\n")
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
