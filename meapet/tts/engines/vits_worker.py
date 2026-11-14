"""常驻 VITS 推理 worker 管理器（P0-4，消除逐句 torch 冷启动税）。

vits_infer.py --serve 在外部解释器里把模型加载一次，之后走 stdin/stdout
JSON 行协议。本模块负责：起进程、等就绪信号、按 id 配对请求/响应、
进程死亡/超时后响亮降级（调用方回落一次性子进程），下次请求自动重拉。

仅本地子进程、无端口暴露。读 stdout 用独立线程 + 队列：Windows 上
selectors 不支持管道，阻塞 readline 又做不了超时——队列 get(timeout)
是跨平台的最小正确姿势。
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import uuid

from meapet.log import get_color_logger
from meapet.tts.common import hidden_subprocess_kwargs

log = get_color_logger("tts")


class VitsServeWorker:
    """一个 (解释器, 脚本, 模型, 配置) 组合的常驻推理进程。"""

    def __init__(
        self,
        python_exe: str,
        script_path: str,
        *,
        model: str = "",
        config: str = "",
        startup_timeout: float = 180.0,
        request_timeout: float = 180.0,
        popen=subprocess.Popen,
    ) -> None:
        self.python_exe = str(python_exe)
        self.script_path = str(script_path)
        self.model = str(model or "")
        self.config = str(config or "")
        self.startup_timeout = float(startup_timeout)
        self.request_timeout = float(request_timeout)
        self._popen = popen
        self._proc = None
        self._lines: queue.Queue = queue.Queue()
        self._lock = threading.Lock()

    def _read_stdout(self, proc) -> None:
        with contextlib.suppress(Exception):
            for line in proc.stdout:
                self._lines.put(line)
        # EOF 哨兵无论如何要放：等待方必须能醒
        self._lines.put(None)

    def _spawn(self) -> None:
        argv = [self.python_exe, self.script_path, "--serve"]
        if self.model:
            argv += ["--model", self.model]
        if self.config:
            argv += ["--config", self.config]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        proc = self._popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **hidden_subprocess_kwargs(),
        )
        self._proc = proc
        self._lines = queue.Queue()
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True).start()
        # 等就绪信号（模型加载可能几十秒，给足 startup_timeout）。
        # 容忍杂行：旧版脚本/第三方库可能漏打印到 stdout，跳过非协议行，
        # 但 EOF 与超时仍是硬失败。
        import time as _time

        deadline = _time.monotonic() + self.startup_timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                self._kill()
                raise RuntimeError(
                    f"VITS serve 就绪超时（>{self.startup_timeout:.0f}s）"
                )
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                self._kill()
                raise RuntimeError(
                    f"VITS serve 就绪超时（>{self.startup_timeout:.0f}s）"
                ) from None
            if line is None:
                self._kill()
                raise RuntimeError("VITS serve 进程未就绪即退出")
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("event") == "ready":
                break
        # 预热：torch 首次推理有一次性 ~10s 热身税（2026-11-14 实测 11.35s）。
        # 挪进启动阶段，用户首句就是热推理（首音 p50 < 1.2s 的验收前提）。
        warm_path = os.path.join(
            tempfile.gettempdir(), f"meapet-vits-warm-{os.getpid()}.wav"
        )
        try:
            self._request_locked("[ZH]预热。[ZH]", warm_path)
        finally:
            with contextlib.suppress(OSError):
                os.remove(warm_path)
        log.info("VITS serve 就绪: %s", os.path.basename(self.python_exe))

    def _ensure_started(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            self._spawn()
            return True
        except Exception as exc:
            log.warning("VITS serve 启动失败: %s: %s", type(exc).__name__, exc)
            return False

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()

    def synthesize(self, text: str, output_wav: str, *, speaker: str = "") -> bool:
        """常驻推理一次。失败返回 False（调用方降级），不留半死进程。"""
        with self._lock:
            if not self._ensure_started():
                return False
            return self._request_locked(text, output_wav, speaker=speaker)

    def _request_locked(self, text: str, output_wav: str, *, speaker: str = "") -> bool:
        """已持锁、进程已就绪时发一发请求（启动预热与用户请求共用）。"""
        rid = uuid.uuid4().hex
        req = {"id": rid, "text": text, "output": output_wav}
        if speaker:
            req["speaker"] = speaker
        try:
            self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except Exception:
            self._kill()
            return False
        while True:
            try:
                line = self._lines.get(timeout=self.request_timeout)
            except queue.Empty:
                log.warning("VITS serve 请求超时（>%.0fs），杀进程降级", self.request_timeout)
                self._kill()
                return False
            if line is None:
                self._kill()
                return False
            try:
                resp = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if resp.get("id") != rid:
                continue
            if not resp.get("ok"):
                log.warning("VITS serve 合成失败: %s", resp.get("error"))
                return False
            return os.path.exists(output_wav)

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            with contextlib.suppress(Exception):
                proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
