"""TTS 引擎 mixin（从 tts.py 拆出）"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Optional
from meapet.paths import project_root
from meapet.log import get_color_logger
from meapet.utils import debug_enabled
from meapet.tts.common import LANG_TTS, MOOD_TO_REF

log = get_color_logger("tts")


class TtsGsvMixin:
    def _speak_gsv(self, tts_text: str, output_wav: str, mood: str,
                    ref_wav: str, ref_text: str, ref_lang: str) -> Optional[tuple[str, str]]:
        """GPT-SoVITS 后端推理（原逻辑）"""
        # 获取参考音频
        payload = {
            "ref_wav": ref_wav,
            "prompt_text": ref_text,
            "prompt_language": LANG_TTS,
            "text": tts_text,
            "text_language": ref_lang,
            "gpt_path": self.gpt_path,
            "sovits_path": self.sovits_path,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed": self.speed,
            "sample_steps": self.sample_steps,
            "output_wav": output_wav,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)

        log.info(f"ref={os.path.basename(ref_wav)} lang={ref_lang}")
        log.info(f"text_len={len(tts_text)} chars payload_size={len(payload_json)} bytes")

        try:
            cmd = [self.python_exe, self.infer_script]
            log.info("启动推理子进程…")
            t1 = time.time()

            proc = subprocess.run(
                cmd,
                input=payload_json.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
                cwd=project_root(),
            )
            elapsed = time.time() - t1
            # 用 replace 忽略无法解码的字节（GPT-SoVITS 可能会输出 GBK 编码的中文日志）
            stdout_text = proc.stdout.decode('utf-8', errors='replace')
            stderr_text = proc.stderr.decode('utf-8', errors='replace')

            log.info(f"子进程返回 (rc={proc.returncode}, {elapsed:.1f}s)")
            if stderr_text.strip():
                log.warn(f"stderr chars={len(stderr_text.strip())}")
                if debug_enabled():
                    log.debug(f"stderr [debug]: {stderr_text.strip()[-200:]}")

            if proc.returncode != 0:
                log.error(f"TTS subprocess failed: rc={proc.returncode}")
                if stderr_text.strip() and debug_enabled():
                    log.debug(f"stderr [debug]: {stderr_text[:300]}")
                return None, ""

            # 取最后一行非空 JSON
            lines = [l.strip() for l in stdout_text.split('\n') if l.strip()]
            if not lines:
                log.warn("TTS: 子进程无输出")
                return None, ""
            last_line = lines[-1]
            result = json.loads(last_line)

            if not result.get("ok"):
                err = result.get('error', 'unknown')
                log.error(f"TTS subprocess error chars={len(str(err))}")
                if debug_enabled():
                    log.debug(f"TTS subprocess error [debug]: {err}")
                if result.get("captured"):
                    captured = str(result["captured"])
                    log.warn(f"captured chars={len(captured)}")
                    if debug_enabled():
                        log.debug(f"captured [debug]: {captured[:300]}")
                return None, ""

            duration = result.get("duration", 0)
            result_lang = "jp"
            log.info(f"SoVITS output: {os.path.basename(output_wav)} ({duration}s) lang={result_lang}")
            return output_wav, result_lang

        except subprocess.TimeoutExpired:
            log.error(f"TTS timeout ({self.timeout}s)")
            return None, ""
        except json.JSONDecodeError as e:
            log.error(f"TTS JSON parse error: {type(e).__name__}")
            if 'last_line' in locals() and debug_enabled():
                log.debug(f"  last_line [debug]: {last_line[:200]}")
            return None, ""
        except Exception as e:
            log.error(f"TTS subprocess error: {type(e).__name__}")
            import traceback
            if debug_enabled():
                log.debug(traceback.format_exc())
            return None, ""

    def _get_ref_paths(self, mood: str) -> tuple:
        """
        根据情绪获取 (参考音频路径, 参考文本, 语言)
        返回 (wav_path, ref_text, lang) 或 (None, None, None)
        根据 voice_lang 选择对应语言的参考文件（zh_* / jp_*）
        """
        ref_type = MOOD_TO_REF.get(mood, "normal")
        ref_folder = os.path.join(self.ref_dir, ref_type)

        # 与配置 voice_lang 对齐；缺省日语（本地 GSV 传统路径）
        vlang = (getattr(self, "voice_lang", "") or "jp").strip().lower()
        if vlang in ("zh", "cn", "zh-cn", "zh_cn", "chinese", "中文", "汉语"):
            prefixes = ("zh_", "cn_")
            lang_label = "中文"
        elif vlang in ("en", "eng", "english", "英文", "英语"):
            prefixes = ("en_",)
            lang_label = "英文"
        else:
            prefixes = ("jp_", "ja_")
            lang_label = LANG_TTS  # 日文

        def _scan(prefs: tuple[str, ...]):
            wav_file = None
            txt_file = None
            try:
                for f in os.listdir(ref_folder):
                    fl = f.lower()
                    if "~" in f:
                        continue
                    for p in prefs:
                        if fl.startswith(p) and fl.endswith(".wav"):
                            wav_file = os.path.join(ref_folder, f)
                        elif fl.startswith(p) and fl.endswith(".txt"):
                            txt_file = os.path.join(ref_folder, f)
            except FileNotFoundError:
                return None, None
            return wav_file, txt_file

        wav_file, txt_file = _scan(prefixes)

        # 目标语言缺失时回退另一套，避免完全无参考
        if not wav_file or not txt_file:
            if prefixes[0].startswith("zh") or prefixes[0].startswith("en"):
                fallback = ("jp_", "ja_")
                fallback_label = "日文"
            else:
                fallback = ("zh_", "cn_")
                fallback_label = "中文"
            alt_wav, alt_txt = _scan(fallback)
            if alt_wav and alt_txt:
                log.warn(
                    f"无 {prefixes[0]}* 参考，回退 {os.path.basename(alt_wav)}"
                )
                wav_file, txt_file = alt_wav, alt_txt
                lang_label = fallback_label

        if not wav_file or not txt_file:
            if not os.path.isdir(ref_folder):
                log.warn(f"Ref folder not found: {ref_folder}")
            else:
                log.warn(f"No ref audio for mood={mood} type={ref_type}")
            return None, None, None

        with open(txt_file, "r", encoding="utf-8") as f:
            ref_text = f.read().strip()

        return wav_file, ref_text, lang_label

    def _clean_jp(self, text: str) -> str:
        """后处理日语文本，替换模型训练数据中不常见的粗俗/敏感词"""
        import re
        for bad, good in self.JP_CLEAN_MAP.items():
            text = text.replace(bad, good)
        # 压缩连续重复的 にゃ/ニャ（娇喘数据会导致模型哼唧而非清晰发音）
        text = re.sub(r'(にゃ){2,}', 'にゃ', text)
        text = re.sub(r'(ニャ){2,}', 'ニャ', text)
        return text