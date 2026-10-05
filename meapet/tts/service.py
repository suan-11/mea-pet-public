"""
梅尔桌宠 - TTS 语音合成模块
通过子进程调用 GPT-SoVITS / VITS / MiMo
"""
from __future__ import annotations

from meapet.paths import data_path, project_path

import os
import sys
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Mapping, Optional

from meapet.config.defaults import (
    DEFAULT_GSV_GPT_MODEL,
    DEFAULT_GSV_GPT_WEIGHTS_DIR,
    DEFAULT_GSV_SOVITS_MODEL,
    DEFAULT_GSV_SOVITS_WEIGHTS_DIR,
    DEFAULT_MEA_PET_MIMO_API_BASE,
    DEFAULT_MIMO_TTS_CLONE_MODEL,
    DEFAULT_MIMO_TTS_MODEL,
)
from meapet.config.normalizers import normalize_gsv_ref_language
from meapet.utils import audio_cache_key, legacy_audio_cache_name
from meapet.log import get_color_logger

log = get_color_logger("tts")

from meapet.tts.common import (
    DEFAULT_VITS_CONFIG_NAME,
    DEFAULT_VITS_MODEL_NAME,
    DEFAULT_VITS_SPEAKER,
    auto_install_gsv_deps,
    is_git_lfs_pointer,
    is_model_artifact_ready,
    is_pet_executable,
    module_present,
    prefix_python,
    resolve_external_python,
    resolve_vits_route,
    resolve_vits_speaker,
    vits_config_path,
    vits_model_path,
    _is_frozen,
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
    should_skip_tts_due_to_language_mismatch,
)
from meapet.tts.translation import TranslationService


def gsv_python_candidates(
    *,
    home: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    executable: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
) -> tuple[str, ...]:
    """返回可移植的 GPT-SoVITS Python 候选路径。

    不扫描整块磁盘，也不假设开发者盘符或某个发布日期。环境变量、当前用户
    目录和 Conda 的标准目录足以覆盖向导安装与手工解压场景；源码模式最后才
    回落到当前解释器。
    """
    env = dict(os.environ if environ is None else environ)
    home_path = Path(home or os.path.expanduser("~"))
    current_executable = Path(executable or sys.executable)
    is_frozen = _is_frozen() if frozen is None else bool(frozen)

    candidates: list[Path] = []
    for key in ("CONDA_PREFIX", "VIRTUAL_ENV"):
        prefix = str(env.get(key) or "").strip()
        if prefix:
            candidates.extend(
                (Path(prefix) / "python.exe", Path(prefix) / "bin" / "python")
            )

    for directory in (
        "GPT-SoVITS",
        "GPT-SoVITS-v2pro",
        "GPT_SoVITS",
    ):
        candidates.append(
            Path(prefix_python(home_path / directory / "runtime"))
        )

    for root_key in ("ProgramFiles", "ProgramData", "LOCALAPPDATA"):
        root = str(env.get(root_key) or "").strip()
        if root:
            candidates.append(
                Path(prefix_python(Path(root) / "GPT-SoVITS" / "runtime"))
            )

    for conda_root in (
        home_path / "miniconda3",
        home_path / "anaconda3",
    ):
        candidates.extend(
            (
                Path(prefix_python(conda_root / "envs" / "GPTSoVits")),
                Path(prefix_python(conda_root / "envs" / "gpt-sovits")),
                Path(prefix_python(conda_root)),
            )
        )

    if not is_frozen:
        candidates.append(current_executable)
        for command in ("python", "python3"):
            found = shutil.which(command)
            if found:
                candidates.append(Path(found))

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate)
        key = os.path.normcase(os.path.abspath(value))
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)


class MeaTTS(TtsMimoMixin, TtsGsvMixin, TtsVitsMixin):
    """梅尔语音合成：通过子进程调用 GPT-SoVITS v2pro"""

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.config = cfg
        tts_cfg = cfg.get("tts", {})

        self.enabled = tts_cfg.get("enabled", True)

        # ═══ GPT-SoVITS runtime Python 路径 ═══
        # 优先使用配置文件或环境变量；若均未设置，自动检测常见安装路径。
        # 打包版里 sys.executable 是 MeaPet.exe，绝不能当 Python 用。
        self.python_exe = resolve_external_python(
            tts_cfg.get("python_exe", "") or os.environ.get("MEA_PET_GSV_PYTHON", "")
        )

        if not self.python_exe:
            # 去重 + 按存在过滤；跳过 pet exe
            _seen = set()
            for _p in gsv_python_candidates():
                if is_pet_executable(_p) or not os.path.isfile(_p):
                    continue
                _rp = os.path.realpath(_p)
                if _rp in _seen:
                    continue
                _seen.add(_rp)
                self.python_exe = _p
                log.info(f"Detected GPT-SoVITS: {_p}")
                break
            if not self.python_exe:
                if _is_frozen():
                    log.warning(
                        "[frozen] No real Python found for GSV inference. "
                        "Use in-process VITS or MiMo cloud TTS, or configure "
                        "an external GPT-SoVITS runtime Python."
                    )
                else:
                    self.python_exe = resolve_external_python(sys.executable)
                    if self.python_exe:
                        log.warning(
                            f"No GPT-SoVITS found, falling back to: {self.python_exe}"
                        )

        # 最终再校验：绝不能保留 pet exe
        self.python_exe = resolve_external_python(self.python_exe)
        if not self.python_exe:
            log.warning("python_exe unset or invalid for local GSV subprocess TTS")

        # GSV 根：conda env 装在源码树外时，解释器路径反推不出根，只能由配置给。
        # 缺省为空串——子进程仍会按 GPT_SoVITS/TTS_infer_pack 标记自行上溯。
        self.gsv_root = str(tts_cfg.get("gsv_root", "") or "").strip()

        # 推理脚本路径
        from meapet.paths import project_root
        base_dir = project_root()
        self.infer_script = tts_cfg.get(
            "infer_script",
            project_path("meapet", "tools", "gsv_infer.py")
        )

        # 模型路径（从配置读取，无默认硬编码路径）
        self.gpt_weights_dir = tts_cfg.get(
            "gpt_weights_dir",
            DEFAULT_GSV_GPT_WEIGHTS_DIR,
        )
        self.sovits_weights_dir = tts_cfg.get(
            "sovits_weights_dir",
            DEFAULT_GSV_SOVITS_WEIGHTS_DIR,
        )

        # 具体模型文件
        self.gpt_model = tts_cfg.get("gpt_model", DEFAULT_GSV_GPT_MODEL)
        self.sovits_model = tts_cfg.get(
            "sovits_model",
            DEFAULT_GSV_SOVITS_MODEL,
        )
        # 模型路径转绝对（子进程会切换目录，相对路径会失效）
        gpt_dir = tts_cfg.get(
            "gpt_weights_dir",
            DEFAULT_GSV_GPT_WEIGHTS_DIR,
        )
        sv_dir = tts_cfg.get(
            "sovits_weights_dir",
            DEFAULT_GSV_SOVITS_WEIGHTS_DIR,
        )
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

        # 输出目录（可写缓存，便携打包落在 _internal）
        raw_output = tts_cfg.get("output_dir") or data_path("audio_cache")
        if not os.path.isabs(raw_output):
            raw_output = os.path.normpath(os.path.join(base_dir, raw_output))
        self.output_dir = raw_output
        os.makedirs(self.output_dir, exist_ok=True)

        # 子进程超时（秒）
        self.timeout = tts_cfg.get("timeout", 60)

        # 翻译用于目标语朗读校正和"不受支持语言"兜底，不参与模型故障回退。
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
                or os.environ.get("MEA_PET_MIMO_API_KEY", "")
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
        # 外部 VITS Python 优先：向导里配置的 vits_python / 可用解释器。
        # 只有在没有外部解释器时，打包版才默认走进程内 torch。
        raw_vits_python = str(tts_cfg.get("vits_python") or "").strip()
        configured_vits_python = resolve_external_python(raw_vits_python)
        # 选路判据只有这一处（meapet.tts.common.resolve_vits_route）：
        # 健康日志、speak、以及引擎 mixin 都读同一个结果，不可能再两处口径。
        # `vits_inprocess` 是三态：没这个键 = 用户没表态（用打包默认值）。
        self._vits_inprocess_pref = (
            bool(tts_cfg.get("vits_inprocess"))
            if "vits_inprocess" in tts_cfg
            else None
        )
        # 配置里写了 vits_python 但解析不出来（空 / pet exe / 不在盘上）是
        # 最容易被"填了保存了"骗过去的一格，构造函数末尾会为此留一行读数。
        self._vits_configured_python = configured_vits_python
        self._vits_configured_python_rejected = bool(
            raw_vits_python and not configured_vits_python
        )
        # 兼容旧读者（日志 / 旧测试）：_vits_python / _vits_inprocess 现在都从
        # 选路结果派生，不再各自算一份。
        self._vits_python = configured_vits_python
        self._vits_inprocess = self._vits_route().inprocess

        # 旋钮生产者：以前 _vits_model/_vits_config/_vits_speaker 只有读者没有
        # 写者，两条路各自 hardcode 默认值；现在由配置落成属性，两路共用。
        self._vits_model = str(
            tts_cfg.get("vits_model") or vits_model_path()
        )
        self._vits_config = str(
            tts_cfg.get("vits_config") or vits_config_path()
        )
        self._vits_speaker = str(
            tts_cfg.get("vits_speaker") or DEFAULT_VITS_SPEAKER
        )
        self._vits_speaker_warning = self._check_vits_speaker(
            self._vits_config, self._vits_speaker
        )

        if raw_vits_python and not configured_vits_python:
            # 配置里写着解释器、实际却解析不出来（空串 / pet exe / 不在盘上）
            # 是最容易被"填了保存了"骗过去的一格，必须留读数。
            log.warning(
                "TTS: tts.vits_python=%r 不是可用的 Python 解释器"
                "（空 / 指向 MeaPet 自己 / 不在盘上），已按未配置处理。",
                raw_vits_python[:200],
            )
        if self._vits_route().ignored_inprocess_pref:
            log.warning(
                "TTS: tts.vits_inprocess=true 被外部解释器优先级覆盖，"
                "实际走子进程（%s）。",
                self._vits_route().describe(),
            )
        if self._vits_speaker_warning:
            log.warning("TTS: %s", self._vits_speaker_warning)
        self.voice_lang = (tts_cfg.get("voice_lang") or "jp")

        # MiMo 云端 TTS（与对话共用 Key / api_base，也可单独覆盖）
        self.mimo_api_key = _resolved_tts_key
        self.mimo_api_base = (
            tts_cfg.get("api_base", "")
            or (llm_cfg.get("api_base", "") if llm_cfg.get("backend") == "mimo" else "")
            or DEFAULT_MEA_PET_MIMO_API_BASE
        )
        self.mimo_model = tts_cfg.get("model", DEFAULT_MIMO_TTS_MODEL)
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
            self.mimo_model = DEFAULT_MIMO_TTS_CLONE_MODEL

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
        elif self._vits_mode:
            # VITS 不再借用 GSV 的 "(subprocess)" 抬头与 GPT/SoVITS 字段：
            # 它有自己的两条路和旋钮，日志要能直接读出选了哪条。
            model_path, config_path, speaker = self._vits_knobs()
            log.info(
                "MeaTTS (VITS) | engine=%s | %s | speaker=%s | model=%s | config=%s",
                self.engine,
                self._vits_route().describe(),
                speaker,
                os.path.basename(model_path),
                os.path.basename(config_path),
            )
        else:
            log.info(
                f"MeaTTS v2 (subprocess) | engine={self.engine} | "
                f"python={os.path.basename(self.python_exe)} | "
                f"GPT={self.gpt_model} | SoVITS={self.sovits_model} | "
                f"top_k={self.top_k} top_p={self.top_p} temp={self.temperature}"
            )

    def _vits_route(self):
        """本实例的 VITS 选路结果 —— 健康检查与 speak 的唯一判据。

        以前 health_check 自带一份恒真式、engines/vits.py 自带另一份，于是
        健康日志可以自称 ``mode=subprocess`` 而引擎实际走另一条路。现在两处
        都调这里。
        """
        fallback = ""
        if self._vits_configured_python or self._vits_inprocess_pref is not True:
            # 显式关掉进程内、又没有外部解释器时不留回落：打包版的
            # self.python_exe 就是 pet exe，拿它跑脚本只会再开一个桌宠实例。
            fallback = self.python_exe
        return resolve_vits_route(
            external_python=self._vits_configured_python,
            inprocess_pref=self._vits_inprocess_pref,
            frozen=_is_frozen(),
            fallback_python=fallback,
        )

    # 引擎 mixin 经这个可调用属性取宿主判据（见 engines/vits.py::_vits_route）。
    _vits_route_decision = _vits_route

    @staticmethod
    def _check_vits_speaker(config_path: str, speaker: str) -> str:
        """按 finetune_speaker.json 校验说话人；不在表里就返回要出声的警告。

        只读 JSON 的 speakers 表，不加载模型（探针税不落在 speak 路径上）。
        """
        if not speaker:
            return ""
        try:
            import json

            with open(config_path, "r", encoding="utf-8") as f:
                hps = json.load(f)
            speakers = hps.get("speakers") if isinstance(hps, dict) else None
        except FileNotFoundError:
            return ""
        except Exception as exc:
            log.warning(
                "TTS: 读 VITS 配置失败，说话人无法校验: %s: %s",
                type(exc).__name__,
                exc,
            )
            return ""
        _speaker_id, warning = resolve_vits_speaker(speakers, speaker)
        return warning or ""

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
            route = self._vits_route()
            # 健康检查与 speak 用同一个 route：模型/配置也取引擎真正会用的
            # 那两个路径，不再对着硬编码默认值验 A 而实际合成 B。
            model_path, config_path, _speaker = self._vits_knobs()
            model_ok = is_model_artifact_ready(model_path)
            config_ok = os.path.isfile(config_path)
            core_ok = os.path.isdir(project_path("vits_core"))
            script_ok = os.path.isfile(
                project_path("meapet", "tools", "vits_infer.py")
            )
            external_py = route.external_python
            if not route.inprocess:
                # mode 由 route.describe() 给出，这里不重复一份可能分叉的字符串。
                # checks 里不放 "python" 键：health_check 从不验解释器里装了什么包，
                # 硬写 python=True 是谎报——空 env（有解释器、无包）因此被放行到
                # speak() 才撞 ModuleNotFoundError（回归锁在 tests/test_vits_deps_probe.py）。
                # 而 describe() 已经把"用的是哪个解释器"打成 python=<basename>，
                # 同一行再出现一个 python=True 只会让两个"python"互相打架。
                # 依赖探针也不在这里跑：全栈 import 本机实测 12–20 s（热/冷页缓存），
                # 而 health_check 在 speak() 路径上，那笔税由向导线程代付。
                checks = {
                    "script": script_ok,
                    "model": model_ok,
                    "config": config_ok,
                }
                if not external_py:
                    # 显式关掉进程内、又没有外部解释器：两路都没有，别报绿。
                    self._deps_ready = False
                else:
                    # 子进程脚本缺失时仍可回退进程内
                    self._deps_ready = (
                        model_ok and config_ok and (script_ok or core_ok)
                    )
            else:
                # 进程内那条路的"解释器"就是本进程，所以 torch 在不在本进程里
                # 是它的前置事实——老代码只验 core/model/config 三个磁盘事实，
                # 于是源码态（宿主 .venv 无 torch）能打出
                #   Health (vits): core=True model=True config=True mode=inprocess
                # 而同一轮 speak() 撞 ModuleNotFoundError: No module named 'torch'
                # （Windows 用户报的就是这个形状）。子进程分支本轮刚拆掉同形的
                # python=True 谎报，这一支不能继续留着一半。
                # 判据用 find_spec 而不是 import torch：前者实测 0.1–0.4 ms，
                # 后者 3914 ms（全栈探针 12–20 s），health_check 在 speak() 路径上。
                # 残余盲区如实留着：find_spec 证不了"能寻到但加载不起来"
                # （打包版 DLL/so 起不来那一格仍由 speak 的异常分支出声）。
                torch_ok = module_present("torch")
                checks = {
                    "core": core_ok,
                    "model": model_ok,
                    "config": config_ok,
                    "torch": torch_ok,
                }
                self._deps_ready = all(
                    [core_ok, model_ok, config_ok, torch_ok]
                )
                if not torch_ok:
                    log.warning(
                        "Health (vits): 本进程寻不到 torch，进程内那条路不可用"
                        "——配 tts.vits_python 指向带 torch 的解释器，"
                        "或去掉 tts.vits_inprocess 让它走默认选路"
                    )

            log.info(
                "Health (vits): "
                + " ".join(f"{name}={ok}" for name, ok in checks.items())
                + f" {route.describe()}"
                + (f" model={model_path}" if not model_ok else "")
                + (f" config={config_path}" if not config_ok else "")
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
        if os.environ.get("MEA_PET_ALLOW_DOWNLOAD", "").strip() == "1":
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

        # 兼容旧的按情绪目录，但只认"同语言 wav + txt"。
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
                f"chars={len(translated)}"
            )
            log.trace(
                lambda value=translated: (
                    f"[tts] 机器翻译结果 {source_language}->{target_language}:\n{value}"
                )
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
                f"chars={len(translated)}"
            )
            log.trace(
                lambda value=translated: (
                    f"[tts] 机器翻译结果 {source_language}->{target_language}:\n{value}"
                )
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
        log.info(f"[tts] 合成原文 chars={len(clean)}")
        log.trace(lambda value=clean: f"[tts] 合成原文:\n{value}")
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
            f"chars={len(translated)}"
        )
        log.trace(
            lambda value=translated: (
                f"[tts] 翻译后文本 {source_lang}->{target_lang}:\n{value}"
            )
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
        log.info(f"[tts] 合成原文 chars={len(clean)}")
        log.trace(lambda value=clean: f"[tts] 合成原文:\n{value}")
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
            f"chars={len(translated)}"
        )
        log.trace(
            lambda value=translated: (
                f"[tts] 翻译后文本 {source_lang}->{target_lang}:\n{value}"
            )
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
            # 验的是引擎真正会用的那个模型（旋钮有生产者后两者可以不同）。
            vits_model, vits_config, _speaker = self._vits_knobs()
            if not is_model_artifact_ready(vits_model):
                if is_git_lfs_pointer(vits_model):
                    log.error("TTS: VITS 模型仍是 Git LFS pointer，不会自动拉取")
                else:
                    log.error(f"TTS: VITS 模型文件不存在，跳过合成: {vits_model}")
                return None
            if not os.path.isfile(vits_config):
                log.error(f"TTS: VITS 配置不存在，跳过合成: {vits_config}")
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
            log.trace(lambda: f"TTS: 跳过文本 [debug]: {clean[:40]}")
            return None, ""

        target_language = self._normalize_voice_lang(
            language or self.voice_lang
        )
        log.info(
            f"TTS: chars={len(clean)} mood={mood} engine={self.engine} "
            f"lang={target_language}"
        )
        log.trace(lambda: f"TTS [debug]: {clean[:60]}")

        # ── 新增：文件触发回复的语言不匹配检查（草案 §五）──
        # 在 plan_tts_language 之前，先检测 voice_text 是否与目标语言明显不符。
        # 若 should_skip_tts_due_to_language_mismatch 返回 True，则跳过 TTS，
        # 仅显示气泡（原文气泡仍会显示）。
        if should_skip_tts_due_to_language_mismatch(clean, target_language):
            log.info(
                f"TTS: 语言不匹配，跳过语音（文件触发回复含非目标语言内容）"
                f" lang={target_language} chars={len(clean)}"
            )
            return None

        prepared = self._prepare_tts_text(clean, target_language)
        if prepared is None:
            return None
        tts_text, synthesis_language = prepared
        log.info(
            f"[tts] 最终合成文本 lang={synthesis_language} "
            f"chars={len(tts_text)}"
        )
        log.trace(lambda value=tts_text: f"[tts] 最终合成文本:\n{value}")

        # 输出文件
        output_wav = self._new_output_wav_path()

        # ── MiMo 云端：clone 参考也按最终合成语言挑选 ──
        if self._mimo_mode:
            lang_tag = synthesis_language
            log.info(f"MiMo 合成: lang={lang_tag} chars={len(tts_text)}")
            log.trace(lambda: f"MiMo 合成: {tts_text[:60]}")
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
        log.trace(lambda: f"合成: {tts_text[:60]}")

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

        # ── 新增：async 路径也做语言不匹配检查 ──
        if should_skip_tts_due_to_language_mismatch(clean, target_language):
            log.info(
                f"TTS: 语言不匹配，跳过语音（async，文件触发回复含非目标语言内容）"
                f" lang={target_language} chars={len(clean)}"
            )
            return None

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
                f"chars={len(tts_text)}"
            )
            log.trace(lambda value=tts_text: f"[tts] 最终合成文本:\n{value}")
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
            cache_dir = data_path("voice_cache")
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
            log.trace(lambda: f"[prerender] text [debug]: {text!r}")
            result = self.speak(text, mood)
            wav, tts_lang = result if result else (None, "")
            if wav and tts_lang:
                cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                if os.path.exists(cache_path):
                    log.info(f"[cache] existing entry chars={len(text)} (overwriting)")
                shutil.move(wav, cache_path)
                results[text] = cache_path
                log.info(f"[prerender] completed chars={len(text)} lang={tts_lang}")
                log.trace(lambda: f"[prerender] output [debug]: {cache_path}")
            else:
                log.warning(f"[prerender] failed chars={len(text)}")
        return results

    def get_cached(self, text: str, cache_dir: str = None) -> Optional[str]:
        """获取已缓存语音路径（优先当前 voice_lang 前缀，再回退 jp_/无前缀）"""
        if cache_dir is None:
            cache_dirs = []
            for candidate in (
                data_path("voice_cache"),
                project_path("voice_cache"),
            ):
                if candidate not in cache_dirs:
                    cache_dirs.append(candidate)
        else:
            cache_dirs = [cache_dir]
        safe = audio_cache_key(text)
        if not safe:
            return None
        prefixes = []
        if self._mimo_mode and not (self.translate_enabled and self.voice_lang == "jp"):
            prefixes.append("zh")
        prefixes.append(self.voice_lang or "jp")
        if "jp" not in prefixes:
            prefixes.append("jp")
        for cache_dir in cache_dirs:
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
