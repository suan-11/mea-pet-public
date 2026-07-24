"""TTS engine mixin (extracted from tts.py)."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from meapet.paths import project_path, project_root
from meapet.log import get_color_logger
from meapet.tts.common import hidden_subprocess_kwargs, resolve_external_python

log = get_color_logger("tts")


class TtsVitsMixin:
    def _vits_external_python(self) -> str:
        return resolve_external_python(
            getattr(self, "_vits_python", None)
            or getattr(self, "python_exe", None)
        )

    def _speak_vits(self, tts_text: str, output_wav: str) -> Optional[tuple[str, str]]:
        """VITS backend inference.

        Prefer a configured external Python + ``vits_infer.py`` when available
        (most reliable on Windows frozen builds). Fall back to in-process torch
        when no external interpreter is configured, or when the subprocess path
        is unavailable.
        """
        external_py = self._vits_external_python()
        force_inprocess = bool(getattr(self, "_vits_inprocess", False))
        # Explicit vits_inprocess=true still allows external fallback on failure.
        prefer_subprocess = bool(external_py) and not (
            force_inprocess and not external_py
        )
        # If user configured an external python, always prefer it over broken
        # bundled torch DLLs — even when frozen defaulted to in-process.
        if external_py:
            prefer_subprocess = True

        if prefer_subprocess and external_py:
            result = self._speak_vits_subprocess(tts_text, output_wav, external_py)
            if result[0]:
                return result
            log.warning(
                "VITS subprocess failed; trying in-process torch as fallback"
            )
            return self._speak_vits_inprocess(tts_text, output_wav)

        result = self._speak_vits_inprocess(tts_text, output_wav)
        if result[0]:
            return result
        if external_py:
            log.warning(
                "VITS in-process failed; retrying with external Python %s",
                external_py,
            )
            return self._speak_vits_subprocess(tts_text, output_wav, external_py)
        return None, ""

    def _speak_vits_subprocess(
        self,
        tts_text: str,
        output_wav: str,
        vits_python: str,
    ) -> Optional[tuple[str, str]]:
        log.info("VITS inference (subprocess)...")
        t1 = time.time()
        vits_script = project_path("meapet", "tools", "vits_infer.py")
        if not os.path.isfile(vits_script):
            log.error(f"VITS script missing: {vits_script}")
            return None, ""

        try:
            env = os.environ.copy()
            # Windows consoles often default to GBK; vits_core prints phonemes
            # that include IPA symbols and crash with UnicodeEncodeError.
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            proc = subprocess.run(
                [
                    vits_python,
                    vits_script,
                    "--text",
                    f"[JA]{tts_text}[JA]",
                    "--output",
                    output_wav,
                    "--noise_scale",
                    "0.667",
                    "--noise_scale_w",
                    "0.6",
                    "--length_scale",
                    "1.0",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=project_root(),
                env=env,
                **hidden_subprocess_kwargs(),
            )
            elapsed = time.time() - t1
            log.info(f"VITS 返回 (rc={proc.returncode}, {elapsed:.1f}s)")

            if proc.returncode != 0:
                log.warning(
                    f"VITS failed: rc={proc.returncode} stderr_chars={len(proc.stderr or '')}"
                )
                log.track(
                    lambda: f"VITS stderr [debug]: {(proc.stderr or '')[-400:]}"
                )
                if proc.stderr:
                    log.warning(f"VITS stderr tail: {(proc.stderr or '')[-300:]}")
                return None, ""

            if not os.path.exists(output_wav):
                log.warning("VITS: 输出文件不存在")
                return None, ""

            log.info(f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s)")
            return output_wav, "jp"
        except Exception as e:
            log.error(f"VITS exception: {type(e).__name__}: {e}")
            return None, ""

    def _speak_vits_inprocess(
        self, tts_text: str, output_wav: str
    ) -> Optional[tuple[str, str]]:
        log.info("VITS inference (in-process)...")
        t1 = time.time()
        try:
            from meapet.tts.engines.vits_runtime import synthesize_vits

            model_path = getattr(self, "_vits_model", None) or project_path(
                "vits_models", "G_latest.pth"
            )
            config_path = getattr(self, "_vits_config", None) or project_path(
                "vits_models", "finetune_speaker.json"
            )
            synthesize_vits(
                f"[JA]{tts_text}[JA]",
                output_wav,
                model_path=model_path,
                config_path=config_path,
                speaker=str(getattr(self, "_vits_speaker", None) or "Mea"),
            )
            elapsed = time.time() - t1
            if not os.path.exists(output_wav):
                log.warning("VITS in-process: 输出文件不存在")
                return None, ""
            log.info(
                f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s, in-process)"
            )
            return output_wav, "jp"
        except Exception as e:
            log.error(f"VITS in-process exception: {type(e).__name__}: {e}")
            return None, ""
