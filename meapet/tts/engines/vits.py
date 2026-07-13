"""TTS 引擎 mixin（从 tts.py 拆出）"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional
from meapet.paths import project_path, project_root
from meapet.log import get_color_logger
from meapet.utils import debug_enabled

log = get_color_logger("tts")


class TtsVitsMixin:
    def _speak_vits(self, tts_text: str, output_wav: str) -> Optional[tuple[str, str]]:
        """VITS 后端推理"""
        log.info("VITS 推理…")
        t1 = time.time()
        vits_script = project_path("meapet", "tools", "vits_infer.py")
        vits_python = getattr(self, '_vits_python', self.python_exe)

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
                log.warn(f"VITS failed: rc={proc.returncode} stderr_chars={len(proc.stderr or '')}")
                if debug_enabled():
                    log.debug(f"VITS stderr [debug]: {(proc.stderr or '')[-200:]}")
                return None, ""

            if not os.path.exists(output_wav):
                log.warn("VITS: 输出文件不存在")
                return None, ""

            log.info(f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s)")
            return output_wav, "jp"
        except Exception as e:
            log.error(f"VITS exception: {type(e).__name__}")
            if debug_enabled():
                log.debug(f"VITS exception [debug]: {e!r}")
            return None, ""