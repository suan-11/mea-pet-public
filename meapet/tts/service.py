"""
梅尔桌宠 - TTS 语音合成模块
通过子进程调用 GPT-SoVITS / VITS / MiMo
"""
from __future__ import annotations

from meapet.paths import project_path

import os
import sys
import shutil
import unicodedata
import uuid
from typing import Optional

from meapet.config.normalizers import normalize_gsv_ref_language
from meapet.utils import audio_cache_key, legacy_audio_cache_name, debug_enabled
from meapet.log import get_color_logger

log = get_color_logger("tts")

from meapet.tts.common import (
    auto_install_gsv_deps,
    is_git_lfs_pointer,
    is_model_artifact_ready,
)
from meapet.tts.common import _get_import_name as _get_import_name
from meapet.tts.engines.gsv import TtsGsvMixin
from meapet.tts.engines.mimo import TtsMimoMixin
from meapet.tts.engines.vits import TtsVitsMixin
from meapet.tts.language_policy import (
    canonical_tts_language,
    detect_script_language,
    plan_tts_language,
    voice_text_language_relation,
)
from meapet.tts.translation import TranslationService


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
                    log.info(f"检测到 GPT-SoVITS: {_p}")
                    break
            if not self.python_exe:
                self.python_exe = sys.executable
                log.warning(f"未找到 GPT-SoVITS，降级至当前解释器: {self.python_exe}")

        # 验证 python_exe 是否存在
        if not os.path.isfile(self.python_exe):
            log.warning(f"python_exe 不存在: {self.python_exe}")
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
        gsv_ref_raw = str(tts_cfg.get("gsv_ref_wav") or "").strip()
        if gsv_ref_raw and not os.path.isabs(gsv_ref_raw):
            gsv_ref_raw = os.path.join(base_dir, gsv_ref_raw)
        self.gsv_ref_wav = os.path.normpath(gsv_ref_raw) if gsv_ref_raw else ""
        self.gsv_ref_lang = normalize_gsv_ref_language(
            tts_cfg.get("gsv_ref_lang")
        )
        self.reference_audios = {}
        raw_references = tts_cfg.get("reference_audios")
        if isinstance(raw_references, dict):
            for raw_language, raw_entry in raw_references.items():
                language = normalize_gsv_ref_language(raw_language)
                if isinstance(raw_entry, dict):
                    ref_path = str(raw_entry.get("path") or "").strip()
                    ref_text = str(raw_entry.get("text") or "").strip()
                else:
                    ref_path = str(raw_entry or "").strip()
                    ref_text = ""
                if ref_path and not os.path.isabs(ref_path):
                    ref_path = os.path.join(base_dir, ref_path)
                if ref_path or ref_text:
                    self.reference_audios[language] = {
                        "path": os.path.normpath(ref_path) if ref_path else "",
                        "text": ref_text,
                    }
        if self.gsv_ref_wav and self.gsv_ref_lang not in self.reference_audios:
            self.reference_audios[self.gsv_ref_lang] = {
                "path": self.gsv_ref_wav,
                "text": "",
            }

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

        # 翻译用于目标语朗读校正和“不受支持语言”兜底，不参与模型故障回退。
        self.translate_enabled = bool(tts_cfg.get("translate_to_jp", False))
        self.translate_target_language = canonical_tts_language(
            tts_cfg.get("translate_target_language")
            or tts_cfg.get("voice_lang")
            or "jp"
        )
        self.prefer_model_voice_translation = bool(
            tts_cfg.get("prefer_model_voice_translation", True)
        )
        llm_cfg = cfg.get("llm", {}) or {}
        try:
            from meapet.config.store import resolve_tts_api_key

            _resolved_tts_key = resolve_tts_api_key(tts_cfg, llm_cfg)
        except Exception:
            _resolved_tts_key = (
                tts_cfg.get("api_key", "")
                or (
                    llm_cfg.get("api_key", "")
                    if (llm_cfg.get("backend") or "").lower() == "mimo"
                    else ""
                )
                or os.environ.get("MIMO_API_KEY", "")
            )
        # 机器翻译使用 translators 的固定服务池；不复用任何 LLM 或模型密钥。
        self.translation_service = TranslationService()
        raw_supported_languages = tts_cfg.get("supported_languages")
        self._configured_supported_languages = tuple(
            language
            for language in (
                canonical_tts_language(value)
                for value in (
                    raw_supported_languages
                    if isinstance(raw_supported_languages, (list, tuple))
                    else ()
                )
            )
            if language
        )

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
            log.info(
                f"MeaTTS (MiMo cloud) | model={self.mimo_model} | "
                f"voice={self.mimo_voice}{clone_info} | base={self.mimo_api_base} | "
                f"key={'yes' if self.mimo_api_key else 'NO'}"
            )
        else:
            log.info(
                f"MeaTTS v2 (subprocess) | engine={self.engine} | "
                f"python={os.path.basename(self.python_exe)} | "
                f"GPT={self.gpt_model} | SoVITS={self.sovits_model} | "
                f"top_k={self.top_k} top_p={self.top_p} temp={self.temperature}"
            )

    def health_check(self) -> bool:
        """检查关键文件是否存在，并确保依赖已安装"""
        if self._mimo_mode:
            key_ok = bool(self.mimo_api_key)
            base_ok = bool(self.mimo_api_base)
            log.info(
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
            log.info(
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
        log.info(
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
                    log.warning(
                        f"{label} 模型仍是 Git LFS pointer；"
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
                    log.warning("GSV 依赖安装不完全，TTS 可能失败")
                else:
                    log.warning("GSV 依赖未齐（默认不自动下载）")

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
                log.warning("MiMo TTS: 缺少 api_key 或 api_base")
            return ok
        if self._vits_mode:
            return self.health_check()
        if not self._deps_attempted:
            self._deps_attempted = True
            allow = self._allow_auto_install()
            if auto_install_gsv_deps(self.python_exe, allow_download=allow):
                self._deps_ready = True
                return True
            log.warning("GSV 依赖未就绪")
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

    def supported_languages(self) -> tuple[str, ...]:
        """返回当前引擎能够安全合成的语言。"""
        if self._configured_supported_languages:
            return tuple(dict.fromkeys(self._configured_supported_languages))
        if self._mimo_mode and not self._mimo_voiceclone:
            return ("zh", "en", "jp")
        if self._vits_mode:
            return ("jp",)

        languages = []
        for raw_language, raw_entry in self.reference_audios.items():
            path = (
                str(raw_entry.get("path") or "").strip()
                if isinstance(raw_entry, dict)
                else str(raw_entry or "").strip()
            )
            if path and os.path.isfile(path):
                languages.append(canonical_tts_language(raw_language))

        if self._mimo_mode and self._mimo_voiceclone and self.mimo_clone_ref:
            if os.path.isfile(self.mimo_clone_ref):
                detected = self._detect_lang_from_path(self.mimo_clone_ref)
                languages.append(
                    canonical_tts_language(detected or self.voice_lang)
                )

        # 兼容旧的按情绪目录，但只认“同语言 wav + txt”。
        if not self._mimo_mode and os.path.isdir(self.ref_dir):
            for folder, _dirs, files in os.walk(self.ref_dir):
                lowered = {name.lower() for name in files}
                for name in lowered:
                    if not name.endswith(".wav"):
                        continue
                    stem = name[:-4]
                    if f"{stem}.txt" not in lowered:
                        continue
                    if stem.startswith(("jp_", "ja_")):
                        languages.append("jp")
                    elif stem.startswith(("zh_", "cn_")):
                        languages.append("zh")
                    elif stem.startswith("en_"):
                        languages.append("en")
        return tuple(dict.fromkeys(language for language in languages if language))

    def _language_plan(self, requested_language: str):
        return plan_tts_language(
            requested_language,
            supported_languages=self.supported_languages(),
            translation_enabled=self.translate_enabled,
            translation_available=self._translation_available(),
            preferred_translation_language=self.translate_target_language,
        )

    def _translation_available(self) -> bool:
        service = getattr(self, "translation_service", None)
        return bool(service is not None and getattr(service, "available", False))

    def _translate_text(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        """通过固定的非 LLM 翻译服务池翻译。"""
        service = getattr(self, "translation_service", None)
        if service is None or not getattr(service, "available", False):
            return ""
        translated = str(
            service.translate(text, source_language, target_language) or ""
        ).strip()
        if translated and canonical_tts_language(target_language) == "jp":
            translated = self._clean_jp(translated)
        if translated:
            log.info(
                f"[tts] 机器翻译完成: {source_language}->{target_language} "
                f"chars={len(translated)}\n{translated}"
            )
        else:
            log.warning(
                f"[tts] 机器翻译失败: {source_language}->{target_language}"
            )
        return translated

    async def _translate_text_async(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        service = getattr(self, "translation_service", None)
        if service is None or not getattr(service, "available", False):
            return ""
        translated = str(
            await service.translate_async(text, source_language, target_language) or ""
        ).strip()
        if translated and canonical_tts_language(target_language) == "jp":
            translated = self._clean_jp(translated)
        if translated:
            log.info(
                f"[tts] 机器翻译完成: {source_language}->{target_language} "
                f"chars={len(translated)}\n{translated}"
            )
        else:
            log.warning(
                f"[tts] 机器翻译失败: {source_language}->{target_language}"
            )
        return translated

    def _prepare_tts_text(
        self,
        clean: str,
        requested_language: str,
    ) -> Optional[tuple[str, str]]:
        action, source_lang, target_lang, reason = self._select_tts_text_route(
            clean,
            requested_language,
        )
        log.info(f"[tts] 合成原文:\n{clean}")
        if action == "skip":
            log.warning(f"TTS: 跳过语音 reason={reason}")
            return None
        if action == "direct":
            log.info(
                f"[tts] 朗读来源 source=model reason={reason} "
                f"lang={target_lang} chars={len(clean)}"
            )
            return clean, target_lang

        log.info(
            f"[tts] 朗读来源 source=machine_translation reason={reason} "
            f"{source_lang}->{target_lang} chars={len(clean)}"
        )
        log.info(
            f"[tts] 开始翻译 {source_lang}->{target_lang} chars={len(clean)}"
        )
        translated = self._translate_text(clean, source_lang, target_lang)
        if not translated:
            log.warning(
                "TTS: 翻译失败，跳过语音；原文气泡仍会显示"
            )
            return None
        log.info(
            f"[tts] 翻译后文本 {source_lang}->{target_lang} "
            f"chars={len(translated)}:\n{translated}"
        )
        return translated, target_lang

    def _select_tts_text_route(
        self,
        clean: str,
        requested_language: str,
    ) -> tuple[str, str, str, str]:
        """返回 action/source/target/reason，不执行网络请求。"""
        claimed = canonical_tts_language(requested_language)
        supported = self.supported_languages()
        target = self._configured_or_default_target()
        translation_available = self._translation_available()
        prefer_effective = bool(
            self.prefer_model_voice_translation and translation_available
        )

        if prefer_effective:
            if not target or target not in supported:
                return "skip", claimed, target, "configured_target_unsupported"
            relation = voice_text_language_relation(clean, target)
            if relation == "match":
                return "direct", target, target, "target_text_match"
            if relation == "ambiguous" and claimed == target:
                return "direct", target, target, "target_text_ambiguous"
            source = self._translation_source_language(clean, claimed)
            reason = (
                "declared_language_differs_from_target"
                if relation == "ambiguous"
                else "voice_text_differs_from_target"
            )
            return "translate", source, target, reason

        plan = self._language_plan(requested_language)
        if plan.action == "skip":
            return "skip", claimed, "", plan.reason or "language_plan_skip"

        synthesis = plan.synthesis_language
        if plan.action == "direct":
            relation = voice_text_language_relation(clean, synthesis)
            if relation != "mismatch":
                return "direct", synthesis, synthesis, f"declared_text_{relation}"
            if not self.translate_enabled or not translation_available:
                return "skip", claimed, synthesis, "confirmed_language_mismatch"
            source = self._translation_source_language(clean, claimed)
            return "translate", source, synthesis, "confirmed_language_mismatch"

        source = self._translation_source_language(clean, claimed)
        return "translate", source, synthesis, "unsupported_output_language"

    def _configured_or_default_target(self) -> str:
        return canonical_tts_language(
            self.translate_target_language or self.voice_lang or "jp"
        )

    def _translation_source_language(
        self,
        clean: str,
        claimed_language: str,
    ) -> str:
        """选择机器翻译源语言：脚本可确认则优先，否则使用模型声明。"""
        claimed = canonical_tts_language(claimed_language)
        observed = detect_script_language(clean)
        if observed in {"zh", "jp", "en"}:
            return observed
        return claimed or "zh"

    async def _prepare_tts_text_async(
        self,
        clean: str,
        requested_language: str,
    ) -> Optional[tuple[str, str]]:
        action, source_lang, target_lang, reason = self._select_tts_text_route(
            clean,
            requested_language,
        )
        log.info(f"[tts] 合成原文:\n{clean}")
        if action == "skip":
            log.warning(f"TTS: 跳过语音 reason={reason}")
            return None
        if action == "direct":
            log.info(
                f"[tts] 朗读来源 source=model reason={reason} "
                f"lang={target_lang} chars={len(clean)}"
            )
            return clean, target_lang

        log.info(
            f"[tts] 朗读来源 source=machine_translation reason={reason} "
            f"{source_lang}->{target_lang} chars={len(clean)}"
        )
        log.info(
            f"[tts] 开始翻译 {source_lang}->{target_lang} chars={len(clean)}"
        )
        translated = await self._translate_text_async(
            clean,
            source_lang,
            target_lang,
        )
        if not translated:
            log.warning(
                "TTS: 翻译失败，跳过语音；原文气泡仍会显示"
            )
            return None
        log.info(
            f"[tts] 翻译后文本 {source_lang}->{target_lang} "
            f"chars={len(translated)}:\n{translated}"
        )
        return translated, target_lang

    # 保留旧内部入口；实现已切换为非 LLM 机器翻译服务池。
    def _translate_to_jp(self, text: str) -> str:
        return self._translate_text(text, "zh", "jp")

    def _text_has_kana(self, text: str) -> bool:
        return any("\u3040" <= c <= "\u30ff" for c in (text or ""))

    def _prepare_jp_tts_text(self, clean: str) -> str:
        if self._text_has_kana(clean):
            return clean
        if not self.translate_enabled or not self._translation_available():
            return ""
        return self._translate_to_jp(clean)

    def _new_output_wav_path(self) -> str:
        """生成并发安全的 TTS 输出路径。"""
        return os.path.join(self.output_dir, f"mea_{uuid.uuid4().hex}.wav")

    def speak(
        self,
        text: str,
        mood: str = "neutral",
        style: str = "",
        language: str = "",
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
                    log.error("TTS: GPT 模型仍是 Git LFS pointer，不会自动拉取")
                else:
                    log.error(f"TTS: GPT 模型文件不存在，跳过合成: {self.gpt_path}")
                return None
            if not is_model_artifact_ready(self.sovits_path):
                if is_git_lfs_pointer(self.sovits_path):
                    log.error("TTS: SoVITS 模型仍是 Git LFS pointer，不会自动拉取")
                else:
                    log.error(f"TTS: SoVITS 模型文件不存在，跳过合成: {self.sovits_path}")
                return None
        elif self._vits_mode:
            vits_model = project_path("vits_models", "G_latest.pth")
            if not is_model_artifact_ready(vits_model):
                if is_git_lfs_pointer(vits_model):
                    log.error("TTS: VITS 模型仍是 Git LFS pointer，不会自动拉取")
                return None

        # 确保依赖就绪
        if not self._ensure_deps():
            log.warning("TTS: 依赖未就绪，跳过合成")
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
            log.warning(f"TTS: 跳过无实际内容的文本 chars={len(clean)}")
            if debug_enabled():
                log.debug(f"TTS: 跳过文本 [debug]: {clean[:40]}")
            return None, ""

        target_language = self._normalize_voice_lang(
            language or self.voice_lang
        )
        log.info(
            f"TTS: chars={len(clean)} mood={mood} engine={self.engine} "
            f"lang={target_language}"
        )
        if debug_enabled():
            log.debug(f"TTS [debug]: {clean[:60]}")

        prepared = self._prepare_tts_text(clean, target_language)
        if prepared is None:
            return None
        tts_text, synthesis_language = prepared
        log.info(
            f"[tts] 最终合成文本 lang={synthesis_language} "
            f"chars={len(tts_text)}:\n{tts_text}"
        )

        # 输出文件
        output_wav = self._new_output_wav_path()

        # ── MiMo 云端：clone 参考也按最终合成语言挑选 ──
        if self._mimo_mode:
            lang_tag = synthesis_language
            log.info(f"MiMo 合成: lang={lang_tag} chars={len(tts_text)}")
            if debug_enabled():
                log.debug(f"MiMo 合成: {tts_text[:60]}")
            return self._speak_mimo(
                tts_text,
                output_wav,
                mood=mood,
                lang_tag=lang_tag,
                style=style,
                voice_language=synthesis_language,
            )

        # ── 本地引擎：只获取最终合成语言的参考音频 ──
        ref_wav, ref_text, ref_lang = self._get_ref_paths(
            mood,
            voice_language=synthesis_language,
        )
        if not ref_wav and not self._vits_mode:
            log.warning(f"TTS: no ref for mood={mood}")
            return None, ""

        text_lang = self._gsv_language_label(synthesis_language)

        log.info(
            f"合成: lang={self._gsv_language_tag(text_lang)} "
            f"chars={len(tts_text)}"
        )
        if debug_enabled():
            log.debug(f"合成: {tts_text[:60]}")

        if self._vits_mode:
            return self._speak_vits(tts_text, output_wav)
        return self._speak_gsv(
            tts_text,
            output_wav,
            mood,
            ref_wav,
            ref_text,
            ref_lang,
            text_lang=text_lang,
        )


    async def speak_async(
        self,
        text: str,
        mood: str = "neutral",
        style: str = "",
        language: str = "",
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
        target_language = self._normalize_voice_lang(
            language or self.voice_lang
        )

        if self._mimo_mode and hasattr(self, "_speak_mimo_async"):
            prepared = await self._prepare_tts_text_async(
                clean,
                target_language,
            )
            if prepared is None:
                return None
            tts_text, lang_tag = prepared
            log.info(
                f"[tts] 最终合成文本 lang={lang_tag} "
                f"chars={len(tts_text)}:\n{tts_text}"
            )
            return await self._speak_mimo_async(
                tts_text,
                output_wav,
                mood=mood,
                lang_tag=lang_tag,
                style=style,
                voice_language=lang_tag,
            )

        return await asyncio.to_thread(
            self.speak,
            text,
            mood,
            style,
            target_language,
        )


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
            log.info(f"[prerender] chars={len(text)} mood={mood}")
            if debug_enabled():
                log.debug(f"[prerender] text [debug]: {text!r}")
            result = self.speak(text, mood)
            wav, tts_lang = result if result else (None, "")
            if wav and tts_lang:
                cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                if os.path.exists(cache_path):
                    log.info(f"[cache] existing entry chars={len(text)} (overwriting)")
                shutil.move(wav, cache_path)
                results[text] = cache_path
                log.info(f"[prerender] completed chars={len(text)} lang={tts_lang}")
                if debug_enabled():
                    log.debug(f"[prerender] output [debug]: {cache_path}")
            else:
                log.warning(f"[prerender] failed chars={len(text)}")
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
