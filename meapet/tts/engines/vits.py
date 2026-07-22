"""TTS engine mixin (extracted from tts.py)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional
from meapet.paths import project_path, project_root
from meapet.log import get_color_logger

log = get_color_logger("tts")


class TtsVitsMixin:
    def _speak_vits(self, tts_text: str, output_wav: str) -> Optional[tuple[str, str]]:
        """VITS backend inference.

        In frozen mode (PyInstaller), ``self.python_exe`` may be empty or
        point to the pet exe — skip subprocess calls to avoid spawning a
        duplicate MeaPet instance.
        """
        vits_python = getattr(self, "_vits_python", None) or getattr(
            self, "python_exe", None
        )
        if not vits_python:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                log.error(
                    "VITS TTS 不可用：未配置 VITS Python 路径。"
                    "请在配置向导的「语音设置」中设置后重试。"
                )
            else:
                log.warning(
                    "[frozen] No real Python interpreter for VITS inference. "
                    "Skipping local TTS."
                )
            return None, ""
        log.info("VITS inference...")
        t1 = time.time()
        vits_script = project_path("meapet", "tools", "vits_infer.py")

        try:
            proc = subprocess.run(
                [vits_python, vits_script, "--text", f"[JA]{tts_text}[JA]", "--output", output_wav,
                 "--noise_scale", "0.667", "--noise_scale_w", "0.6", "--length_scale", "1.0"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout,
                cwd=project_root(),
            )
            elapsed = time.time() - t1
            log.info(f"VITS 返回 (rc={proc.returncode}, {elapsed:.1f}s)")

            if proc.returncode != 0:
                log.warning(f"VITS failed: rc={proc.returncode} stderr_chars={len(proc.stderr or '')}")
                log.track(lambda: f"VITS stderr [debug]: {(proc.stderr or '')[-200:]}")
                return None, ""

            if not os.path.exists(output_wav):
                log.warning("VITS: 输出文件不存在")
                return None, ""

            log.info(f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s)")
            return output_wav, "jp"
        except Exception as e:
            log.error(f"VITS exception: {type(e).__name__}: {e}")
            return None, ""