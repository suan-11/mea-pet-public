"""
梅尔桌宠 - TTS 语音合成模块
通过子进程调用 GPT-SoVITS / VITS / MiMo
"""
from __future__ import annotations

from meapet.paths import project_path

import os
import sys
import json
import shutil
import unicodedata
import uuid
from typing import Optional

from meapet.utils import audio_cache_key, legacy_audio_cache_name

from meapet.tts.common import (
    _safe_print,
    _debug_print,
    auto_install_gsv_deps,
    is_git_lfs_pointer,
    is_model_artifact_ready,
)
from meapet.tts.common import _get_import_name as _get_import_name
from meapet.tts.engines.gsv import TtsGsvMixin
from meapet.tts.engines.mimo import TtsMimoMixin
from meapet.tts.engines.vits import TtsVitsMixin


class MeaTTS(TtsMimoMixin, TtsGsvMixin, TtsVitsMixin):
    """梅尔语音合成：通过子进程调用 GPT-SoVITS v2pro"""

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.config = cfg
        tts_cfg = cfg.get("tts", {})

        self.enabled = tts_cfg.get("enabled", True)

        # ═══ GPT-SoVITS runtime Python 路径 ═══
        # 优先使用配置文件或环境变量；若均未设置，自动检测常见安装路径
        self.python_exe = tts_cfg.get("python_exe", "") or \
            os.environ.get("GSV_PYTHON", "")

        if not self.python_exe or not os.path.isfile(self.python_exe):
            # 自动检测常见安装路径（引导安装解压的 GPT-SoVITS 整合包）
            _home = os.path.expanduser("~")
            _candidates = [
                # GPT-SoVITS v2pro 整合包（解压后常见位置）
                r"D:\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\runtime\python.exe",
                r"D:\GPT-SoVITS-v2pro\GPT-SoVITS-v2pro\runtime\python.exe",
                r"C:\Program Files\GPT-SoVITS\runtime\python.exe",
                # 用户目录下的解压版
                os.path.join(_home, "GPT-SoVITS-v2pro", "runtime", "python.exe"),
                # 当前脚本的解释器（GUI 同进程）
                sys.executable,
            ]
            # 扫描常见 conda 环境（setup_wizard 创建或手动安装的）
            for _conda_root in (_home, r"C:\ProgramData", r"C:\Users"):
                for _maybe in (
                    os.path.join(_conda_root, "miniconda3", "envs", "GPTSoVits", "python.exe"),
                    os.path.join(_conda_root, "miniconda3", "python.exe"),
                    os.path.join(_conda_root, "anaconda3", "envs", "GPTSoVits", "python.exe"),
                    os.path.join(_conda_root, "anaconda3", "python.exe"),
                ):
                    _candidates.append(_maybe)
            # 去重 + 按存在过滤
            _seen = set()
            for _p in _candidates:
                _rp = os.path.realpath(_p) if os.path.isfile(_p) else None
                if _rp and _rp not in _seen:
                    _seen.add(_rp)
                    self.python_exe = _p
                    _safe_print(f"  → 检测到 GPT-SoVITS: {_p}")
                    break
            if not self.python_exe:
                self.python_exe = sys.executable
                _safe_print(f"  ⚠ 未找到 GPT-SoVITS，降级至当前解释器: {self.python_exe}")

        # 验证 python_exe 是否存在
        if not os.path.isfile(self.python_exe):
            _safe_print(f"  ⚠ python_exe 不存在: {self.python_exe}")
            self.python_exe = sys.executable

        # 推理脚本路径
        from meapet.paths import project_root
        base_dir = project_root()
        self.infer_script = tts_cfg.get(
            "infer_script",
            project_path("meapet", "tools", "gsv_infer.py")
        )

        # 模型路径（从配置读取，无默认硬编码路径）
        self.gpt_weights_dir = tts_cfg.get("gpt_weights_dir", "./models/GPT_weights")
        self.sovits_weights_dir = tts_cfg.get("sovits_weights_dir", "./models/SoVITS_weights")

        # 具体模型文件
        self.gpt_model = tts_cfg.get("gpt_model", "mea_pro-e50.ckpt")
        self.sovits_model = tts_cfg.get("sovits_model", "mea_pro_e24_s13704.pth")
        # 模型路径转绝对（子进程会切换目录，相对路径会失效）
        gpt_dir = tts_cfg.get("gpt_weights_dir", "./models/GPT_weights")
        sv_dir = tts_cfg.get("sovits_weights_dir", "./models/SoVITS_weights")
        self.gpt_path = os.path.normpath(os.path.join(base_dir, gpt_dir, self.gpt_model))
        self.sovits_path = os.path.normpath(os.path.join(base_dir, sv_dir, self.sovits_model))

        # 参考音频目录（转绝对路径）
        ref_dir_raw = tts_cfg.get("ref_dir", "GPT-Sovits")
        self.ref_dir = os.path.normpath(ref_dir_raw if os.path.isabs(ref_dir_raw) else os.path.join(base_dir, ref_dir_raw))

        # 合成参数（平衡稳定性和完整性）
        # top_k/top_p/temperature 太低会导致 GPT 提前截断（只输出语气词）
        # 太高会导致乱说/重复，在两者间取平衡
        self.top_k = tts_cfg.get("top_k", 15)
        self.top_p = tts_cfg.get("top_p", 0.8)
        self.temperature = tts_cfg.get("temperature", 0.6)
        self.repetition_penalty = tts_cfg.get("repetition_penalty", 1.35)
        self.speed = tts_cfg.get("speed", 1.0)
        self.sample_steps = tts_cfg.get("sample_steps", 8)

        # 输出目录
        self.output_dir = tts_cfg.get(
            "output_dir",
            project_path("audio_cache")
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # 子进程超时（秒）
        self.timeout = tts_cfg.get("timeout", 60)

        # 翻译配置（中文 → 日语，使用翻译 API）
        self.translate_enabled = tts_cfg.get("translate_to_jp", True)
        llm_cfg = cfg.get("llm", {}) or {}
        # 密钥：环境变量优先（见 config_store）
        try:
            from meapet.config.store import resolve_translate_api_key, resolve_tts_api_key
            self.translate_api_key = resolve_translate_api_key(tts_cfg, llm_cfg)
            _resolved_tts_key = resolve_tts_api_key(tts_cfg, llm_cfg)
        except Exception:
            self.translate_api_key = tts_cfg.get("translate_api_key", "")
            _resolved_tts_key = (
                tts_cfg.get("api_key", "")
                or (
                    llm_cfg.get("api_key", "")
                    if (llm_cfg.get("backend") or "").lower() == "mimo"
                    else ""
                )
                or os.environ.get("MIMO_API_KEY", "")
            )
        self.translate_model = tts_cfg.get("translate_model", "	deepseek-v4-flash")

        # ═══ 后端配置 ═══
        engine = tts_cfg.get("engine", "gpt_sovits")
        self.engine = engine
        self._vits_mode = engine == "vits" or tts_cfg.get("vits_mode", False)
        self._mimo_mode = engine == "mimo"
        self._vits_python = tts_cfg.get("vits_python", "") or self.python_exe
        self.voice_lang = (tts_cfg.get("voice_lang") or "jp")

        # MiMo 云端 TTS（与对话共用 Key / api_base，也可单独覆盖）
        self.mimo_api_key = _resolved_tts_key
        self.mimo_api_base = (
            tts_cfg.get("api_base", "")
            or (llm_cfg.get("api_base", "") if llm_cfg.get("backend") == "mimo" else "")
            or "https://api.xiaomimimo.com/v1"
        )
        self.mimo_model = tts_cfg.get("model", "mimo-v2.5-tts")
        self.mimo_voice = tts_cfg.get("voice", "冰糖")
        # 可选：固定风格提示；空则按 mood 自动生成
        self.mimo_style = tts_cfg.get("style", "")
        # voice-clone：参考音频路径（可用 voice_cache / GPT-Sovits 下的 wav/mp3）
        from meapet.paths import project_root
        base_dir = project_root()
        clone_raw = (
            tts_cfg.get("clone_ref")
            or tts_cfg.get("voice_ref")
            or tts_cfg.get("ref_wav")
            or ""
        ).strip()
        if clone_raw and not os.path.isabs(clone_raw):
            clone_raw = os.path.normpath(os.path.join(base_dir, clone_raw))
        self.mimo_clone_ref = clone_raw
        self.mimo_clone_dir = tts_cfg.get("clone_dir", "./voice_cache")
        if self.mimo_clone_dir and not os.path.isabs(self.mimo_clone_dir):
            self.mimo_clone_dir = os.path.normpath(
                os.path.join(base_dir, self.mimo_clone_dir)
            )
        # 模型名含 voiceclone，或 voice=clone / 配置了 clone_ref 时启用克隆
        model_l = (self.mimo_model or "").lower()
        voice_l = (self.mimo_voice or "").lower()
        self._mimo_voiceclone = (
            "voiceclone" in model_l
            or voice_l in ("clone", "voiceclone", "voice-clone")
            or bool(tts_cfg.get("voice_clone"))
            or bool(self.mimo_clone_ref)
        )
        if self._mimo_voiceclone and "voiceclone" not in model_l:
            self.mimo_model = "mimo-v2.5-tts-voiceclone"

        # 自检依赖（默认不自动安装）
        self._deps_ready = False
        self._deps_attempted = False
        self._mimo_clone_voice_uri = None  # 缓存 data URI，避免每次读盘

        if self._mimo_mode:
            clone_info = ""
            if self._mimo_voiceclone:
                clone_info = f" | clone_ref={os.path.basename(self.mimo_clone_ref) if self.mimo_clone_ref else 'auto'}"
            _safe_print(
                f"🎤 MeaTTS (MiMo cloud) | model={self.mimo_model} | "
                f"voice={self.mimo_voice}{clone_info} | base={self.mimo_api_base} | "
                f"key={'yes' if self.mimo_api_key else 'NO'}"
            )
        else:
            _safe_print(
                f"🎤 MeaTTS v2 (subprocess) | engine={self.engine} | "
                f"python={os.path.basename(self.python_exe)} | "
                f"GPT={self.gpt_model} | SoVITS={self.sovits_model} | "
                f"top_k={self.top_k} top_p={self.top_p} temp={self.temperature}"
            )

    def health_check(self) -> bool:
        """检查关键文件是否存在，并确保依赖已安装"""
        if self._mimo_mode:
            key_ok = bool(self.mimo_api_key)
            base_ok = bool(self.mimo_api_base)
            _safe_print(
                f"Health (mimo): key={key_ok} base={base_ok} "
                f"model={self.mimo_model} voice={self.mimo_voice}"
            )
            self._deps_ready = key_ok and base_ok
            return self._deps_ready

        if self._vits_mode:
            checks = {
                "python": os.path.isfile(self._vits_python),
                "script": os.path.isfile(
                    project_path("meapet", "tools", "vits_infer.py")
                ),
                "model": is_model_artifact_ready(
                    project_path("vits_models", "G_latest.pth")
                ),
                "config": os.path.isfile(
                    project_path("vits_models", "finetune_speaker.json")
                ),
            }
            self._deps_ready = all(checks.values())
            _safe_print(
                "Health (vits): "
                + " ".join(f"{name}={ok}" for name, ok in checks.items())
            )
            return self._deps_ready

        gpt_ok = is_model_artifact_ready(self.gpt_path)
        s2_ok = is_model_artifact_ready(self.sovits_path)
        python_ok = os.path.exists(self.python_exe)
        script_ok = os.path.exists(self.infer_script)
        ref_ok = all(
            os.path.exists(os.path.join(self.ref_dir, t))
            for t in ["normal", "soft", "clam"]
        )
        _safe_print(
            f"Health: python={python_ok} script={script_ok} "
            f"GPT={gpt_ok} SoVITS={s2_ok} Refs={ref_ok}"
        )
        all_ok = all([python_ok, script_ok, gpt_ok, s2_ok, ref_ok])

        if not all_ok:
            for label, path in (
                ("GPT", self.gpt_path),
                ("SoVITS", self.sovits_path),
            ):
                if is_git_lfs_pointer(path):
                    _safe_print(
                        f"  ⚠ {label} 模型仍是 Git LFS pointer；"
                        "请手动准备真实模型文件（程序不会自动拉取）"
                    )
            self._deps_ready = False
            return False

        # 默认只检查；允许下载时才 pip 安装
        if all_ok and not self._deps_attempted:
            self._deps_attempted = True
            allow = self._allow_auto_install()
            if auto_install_gsv_deps(self.python_exe, allow_download=allow):
                self._deps_ready = True
            else:
                if allow:
                    _safe_print("  ⚠ GSV 依赖安装不完全，TTS 可能失败")
                else:
                    _safe_print("  ⚠ GSV 依赖未齐（默认不自动下载）")

        return bool(all_ok and self._deps_ready)

    def _allow_auto_install(self) -> bool:
        if os.environ.get("MEAPET_ALLOW_DOWNLOAD", "").strip() == "1":
            return True
        return bool(self.config.get("tts", {}).get("auto_install_deps", False))

    def _ensure_deps(self):
        """speak 前确保依赖就绪；默认只检测，不自动下载"""
        if self._deps_ready:
            return True
        # 云端 / VITS 不需要本地 GSV 依赖
        if self._mimo_mode:
            ok = bool(self.mimo_api_key and self.mimo_api_base)
            self._deps_ready = ok
            if not ok:
                _safe_print("  ⚠ MiMo TTS: 缺少 api_key 或 api_base")
            return ok
        if self._vits_mode:
            return self.health_check()
        if not self._deps_attempted:
            self._deps_attempted = True
            allow = self._allow_auto_install()
            if auto_install_gsv_deps(self.python_exe, allow_download=allow):
                self._deps_ready = True
                return True
            _safe_print("  ⚠ GSV 依赖未就绪")
        return False

    # 日语后处理：替换不常见/粗俗词为 GPT-SoVITS 模型更友好的表达
    JP_CLEAN_MAP = {
        "クソ": "だめ",
        "糞": "ごみ",
        "死ね": "やめて",
        "うざい": "いや",
        "うるせえ": "うるさい",
        "ダセえ": "ださい",
        "ムカつく": "いらいらする",
    }

    def _translate_to_jp(self, text: str) -> str:
        """将中文翻译成日语：依次尝试多个免费翻译源，最后回退 DeepSeek API"""
        # 1) 本地翻译库（依次尝试多个源）
        try:
            import translators as ts
        except ImportError:
            _safe_print("  translate: 未安装 translators（不会自动下载）。"
                        "可选: pip install translators，或配置 translate_api_key 走 DeepSeek。")
            ts = None

        if ts is not None:
            # 国内可用翻译源（按成功率排序）
            for svc in ("alibaba", "iflytek", "sogou", "bing", "google"):
                try:
                    jp = ts.translate_text(text, translator=svc,
                                           from_language='zh', to_language='ja')
                    if jp and len(jp) >= 2:
                        jp = self._clean_jp(jp)
                        _safe_print(f"  translate ({svc}): chars={len(jp)}")
                        _debug_print(f"  translate ({svc}) [debug]: {jp[:80]}")
                        return jp
                except Exception as e:
                    _safe_print(f"  translate ({svc}) failed: {type(e).__name__}")
                    _debug_print(f"  translate ({svc}) exception [debug]: {e!r}")
                    continue

        # 2) 回退：DeepSeek API
        if not self.translate_api_key:
            _safe_print("  translate: no API key, local also failed")
            return ""
        import urllib.request
        prompt = (
            "Translate this Chinese cat-girl line to natural Japanese. "
            "End sentences with 'nya'. Output ONLY the translation.\n\n"
            f"Chinese: {text}\n"
            "Japanese:"
        )
        payload = json.dumps({
            "model": self.translate_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 128,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.translate_api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                jp = result["choices"][0]["message"]["content"].strip()
                jp = jp.replace("\n", "").strip()
                jp = self._clean_jp(jp)
                if jp and len(jp) >= 2:
                    _safe_print(f"  translate (api): chars={len(jp)}")
                    _debug_print(f"  translate (api) [debug]: {jp[:80]}")
                    return jp
        except Exception as e:
            _safe_print(f"  translate (api) failed: {type(e).__name__}")
            _debug_print(f"  translate (api) exception [debug]: {e!r}")
        return ""


    def _text_has_kana(self, text: str) -> bool:
        return any("\u3040" <= c <= "\u30ff" for c in (text or ""))

    def _prepare_jp_tts_text(self, clean: str) -> str:
        """
        日语合成文本准备：
        1) 已是日语（含假名）→ 直接用，不走翻译
        2) 否则若 translate_enabled → 再尝试翻译（回退）
        """
        if self._text_has_kana(clean):
            _safe_print("  → 已是日语，跳过翻译")
            return clean
        if not self.translate_enabled:
            return clean
        _safe_print("  → 无日语行，回退翻译…")
        jp = self._translate_to_jp(clean)
        if jp and len(jp) >= 2:
            return jp
        return clean

    def _new_output_wav_path(self) -> str:
        """生成并发安全的 TTS 输出路径。"""
        return os.path.join(self.output_dir, f"mea_{uuid.uuid4().hex}.wav")

    def speak(
        self,
        text: str,
        mood: str = "neutral",
        style: str = "",
    ) -> Optional[tuple[str, str]]:
        """
        文字 → 语音，返回 (wav_path, lang)
        - engine=mimo: 云端 MiMo TTS（中文/英文音色，不强制译日语）
        - 其它本地引擎: 默认仍走日语合成
        """
        if not self.enabled:
            return None

        if not text or not text.strip():
            return None

        # 本地 GSV 才需要模型文件
        if not self._mimo_mode and not self._vits_mode:
            if not is_model_artifact_ready(self.gpt_path):
                if is_git_lfs_pointer(self.gpt_path):
                    _safe_print("❌ TTS: GPT 模型仍是 Git LFS pointer，不会自动拉取")
                else:
                    _safe_print(f"❌ TTS: GPT 模型文件不存在，跳过合成: {self.gpt_path}")
                return None
            if not is_model_artifact_ready(self.sovits_path):
                if is_git_lfs_pointer(self.sovits_path):
                    _safe_print("❌ TTS: SoVITS 模型仍是 Git LFS pointer，不会自动拉取")
                else:
                    _safe_print(f"❌ TTS: SoVITS 模型文件不存在，跳过合成: {self.sovits_path}")
                return None
        elif self._vits_mode:
            vits_model = project_path("vits_models", "G_latest.pth")
            if not is_model_artifact_ready(vits_model):
                if is_git_lfs_pointer(vits_model):
                    _safe_print("❌ TTS: VITS 模型仍是 Git LFS pointer，不会自动拉取")
                return None

        # 确保依赖就绪
        if not self._ensure_deps():
            _safe_print("TTS: 依赖未就绪，跳过合成")
            return None, ""

        # 去除表情标记、动作括号、对话标记
        import re
        clean = re.sub(r'【.*?】', '', text).strip()
        clean = re.sub(r'\[.*?\]', '', clean).strip()
        # 去掉小动作括号（如「（伸懒腰）」「（尾巴晃了晃）」）
        clean = re.sub(r'（[^）]*）', '', clean).strip()
        clean = re.sub(r'\([^)]*\)', '', clean).strip()
        clean = clean.strip()

        if len(clean) < 1:
            return None, ""
        # 检查文本是否包含任何可发音内容（字母、数字、汉字等）
        if not any(unicodedata.category(c).startswith(('L', 'N')) for c in clean):
            _safe_print(f"  TTS: 跳过无实际内容的文本 chars={len(clean)}")
            _debug_print(f"  TTS: 跳过文本 [debug]: {clean[:40]}")
            return None, ""

        _safe_print(f"🔊 TTS: chars={len(clean)} mood={mood} engine={self.engine}")
        _debug_print(f"🔊 TTS [debug]: {clean[:60]}")

        # 输出文件
        output_wav = self._new_output_wav_path()

        # ── MiMo 云端：语言跟随 voice_lang；clone 参考也会按同语言挑选 ──
        if self._mimo_mode:
            vlang = (self.voice_lang or "jp").strip().lower()
            want_jp = vlang in ("jp", "ja", "jpn", "japanese", "日文", "日语")
            if want_jp:
                tts_text = self._prepare_jp_tts_text(clean)
                lang_tag = "jp"
            elif vlang in ("en", "eng", "english", "英文", "英语"):
                tts_text = clean
                lang_tag = "en"
            else:
                tts_text = clean
                lang_tag = "zh"
            _safe_print(f"  → MiMo 合成: lang={lang_tag} chars={len(tts_text)}")
            _debug_print(f"  → MiMo 合成 [debug]: {tts_text[:60]}")
            return self._speak_mimo(
                tts_text,
                output_wav,
                mood=mood,
                lang_tag=lang_tag,
                style=style,
            )

        # ── 本地引擎：获取参考音频 + 中文→日语 ──
        ref_wav, ref_text, ref_lang = self._get_ref_paths(mood)
        if not ref_wav and not self._vits_mode:
            _safe_print(f"TTS: no ref for mood={mood}")
            return None, ""

        has_kana = any(
            c in clean for c in
            "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
            "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゃゅょっ"
            "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
            "ニャにゃ"
        )
        if has_kana:
            _safe_print("  → 已是日语，跳过翻译")
            tts_text = clean
        elif self.translate_enabled:
            _safe_print("  → 无日语行，回退翻译…")
            jp = self._translate_to_jp(clean)
            if jp and len(jp) >= 2:
                tts_text = jp
            else:
                tts_text = clean
        else:
            tts_text = clean

        _safe_print(f"  → 合成: lang=jp chars={len(tts_text)}")
        _debug_print(f"  → 合成 [debug]: {tts_text[:60]}")

        if self._vits_mode:
            return self._speak_vits(tts_text, output_wav)
        return self._speak_gsv(tts_text, output_wav, mood, ref_wav, ref_text, ref_lang)


    async def speak_async(
        self,
        text: str,
        mood: str = "neutral",
        style: str = "",
    ):
        """async 入口：MiMo 走 httpx；本地 GSV/VITS 仍 to_thread(子进程)。"""
        import asyncio
        import re
        import unicodedata

        if not self.enabled or not text or not text.strip():
            return None

        clean = re.sub(r"【.*?】", "", text).strip()
        clean = re.sub(r"\[.*?\]", "", clean).strip()
        clean = re.sub(r"（[^）]*）", "", clean).strip()
        clean = re.sub(r"\([^)]*\)", "", clean).strip()
        if len(clean) < 1:
            return None, ""
        if not any(unicodedata.category(c).startswith(("L", "N")) for c in clean):
            return None, ""

        output_wav = self._new_output_wav_path()

        if self._mimo_mode and hasattr(self, "_speak_mimo_async"):
            vlang = (getattr(self, "voice_lang", "") or "jp").strip().lower()
            want_jp = vlang in ("jp", "ja", "jpn", "japanese", "日文", "日语")
            if want_jp:
                tts_text = await asyncio.to_thread(self._prepare_jp_tts_text, clean)
                lang_tag = "jp"
            elif vlang in ("en", "eng", "english", "英文", "英语"):
                tts_text = clean
                lang_tag = "en"
            else:
                tts_text = clean
                lang_tag = "zh"
            return await self._speak_mimo_async(
                tts_text,
                output_wav,
                mood=mood,
                lang_tag=lang_tag,
                style=style,
            )

        return await asyncio.to_thread(self.speak, text, mood, style)


    def pre_render_batch(
        self, texts_with_moods: list[tuple[str, str]], cache_dir: str = None
    ) -> dict[str, str]:
        """
        预合成一批文本 → {text: wav_path}
        texts_with_moods: [(text, mood), ...]
        缓存文件命名: {lang}_{safe}.wav
        """
        if cache_dir is None:
            cache_dir = project_path("voice_cache")
        os.makedirs(cache_dir, exist_ok=True)

        results = {}
        for text, mood in texts_with_moods:
            if not text or not text.strip():
                continue
            safe = audio_cache_key(text)
            if not safe:
                continue
            # 先合成才能知道语言——暂用临时名，合成完再改名
            _safe_print(f"[prerender] chars={len(text)} mood={mood}")
            _debug_print(f"[prerender] text [debug]: {text!r}")
            result = self.speak(text, mood)
            wav, tts_lang = result if result else (None, "")
            if wav and tts_lang:
                cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                if os.path.exists(cache_path):
                    _safe_print(f"[cache] existing entry chars={len(text)} (overwriting)")
                shutil.move(wav, cache_path)
                results[text] = cache_path
                _safe_print(f"[prerender] completed chars={len(text)} lang={tts_lang}")
                _debug_print(f"[prerender] output [debug]: {cache_path}")
            else:
                _safe_print(f"[prerender] failed chars={len(text)}")
        return results

    def get_cached(self, text: str, cache_dir: str = None) -> Optional[str]:
        """获取已缓存语音路径（优先当前 voice_lang 前缀，再回退 jp_/无前缀）"""
        if cache_dir is None:
            cache_dir = project_path("voice_cache")
        safe = audio_cache_key(text)
        if not safe:
            return None
        prefixes = []
        if self._mimo_mode and not (self.translate_enabled and self.voice_lang == "jp"):
            prefixes.append("zh")
        prefixes.append(self.voice_lang or "jp")
        if "jp" not in prefixes:
            prefixes.append("jp")
        for prefix in prefixes:
            cache_path = os.path.join(cache_dir, f"{prefix}_{safe}.wav")
            if os.path.exists(cache_path):
                return cache_path
        # 只读兼容旧的人类可读文件名；新缓存一律使用哈希键。
        legacy_name = legacy_audio_cache_name(text)
        if legacy_name:
            for prefix in prefixes:
                legacy = os.path.join(cache_dir, f"{prefix}_{legacy_name}.wav")
                if os.path.exists(legacy):
                    return legacy
            legacy = os.path.join(cache_dir, f"{legacy_name}.wav")
            if os.path.exists(legacy):
                return legacy
        return None


if __name__ == "__main__":
    tts = MeaTTS()
    print(f"Enabled: {tts.enabled}")
    print(f"Models exist: {tts.health_check()}")
    result = tts.speak("主人，语音测试成功啦喵！", mood="happy")
    if result and result[0]:
        wav, lang = result
        print(f"Output: {wav}  lang={lang}")
    else:
        print("TTS failed")
