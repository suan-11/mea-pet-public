"""TTS shared utilities and constants (used by tts.py and engine mixins)."""
from __future__ import annotations

import glob
import os
import subprocess
import sys

from meapet.dependencies import (
    resolve_pip_index_url,
    resolve_torch_index_url,
)
from meapet.log import get_color_logger

log = get_color_logger("tts")


def _is_frozen() -> bool:
    """Check if running in a PyInstaller-frozen environment."""
    try:
        from meapet.paths import is_frozen

        return is_frozen()
    except Exception:
        return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def is_pet_executable(path: str | None) -> bool:
    """True when *path* is the frozen MeaPet launcher (not a real Python)."""
    if not path:
        return False
    try:
        if not _is_frozen():
            return False
        return os.path.realpath(path) == os.path.realpath(sys.executable)
    except Exception:
        return False


def resolve_external_python(path: str | None) -> str:
    """Return *path* only when it is a real on-disk interpreter, else ``\"\"``."""
    raw = (path or "").strip()
    if not raw:
        return ""
    if is_pet_executable(raw):
        return ""
    if not os.path.isfile(raw):
        return ""
    return raw


def _is_windows(windows: bool | None) -> bool:
    return (os.name == "nt") if windows is None else bool(windows)


def venv_python(env_dir: "os.PathLike[str] | str", *, windows: bool | None = None) -> str:
    """Interpreter of a ``python -m venv`` environment (``Scripts`` on Windows)."""
    if _is_windows(windows):
        return os.path.join(env_dir, "Scripts", "python.exe")
    return os.path.join(env_dir, "bin", "python")


def prefix_python(env_dir: "os.PathLike[str] | str", *, windows: bool | None = None) -> str:
    """Interpreter that sits directly in *env_dir* — conda envs and the GSV
    整合包 ``runtime/`` folder, which use a flat prefix on Windows."""
    if _is_windows(windows):
        return os.path.join(env_dir, "python.exe")
    return os.path.join(env_dir, "bin", "python")


def env_site_packages(env_dir: "os.PathLike[str] | str", *, windows: bool | None = None) -> str:
    """Purelib of an environment directory.

    POSIX venv/conda put site-packages under a Python-version segment
    (``lib/python3.12/site-packages``); globbing keeps that version out of
    the source.  When nothing matches, the unversioned path is returned so
    callers' ``isdir`` guards fail cleanly instead of guessing a file.
    """
    if _is_windows(windows):
        return os.path.join(env_dir, "Lib", "site-packages")
    matches = sorted(
        glob.glob(os.path.join(env_dir, "lib", "python3.*", "site-packages"))
    )
    if matches:
        return matches[0]
    return os.path.join(env_dir, "lib", "site-packages")


# ═══════════════════════════════════════════
# VITS 选路与旋钮口径（唯一来源）
# ═══════════════════════════════════════════
#
# VITS 有两条推理路：外部解释器子进程（vits_infer.py）与进程内 torch
# （vits_runtime.py）。两条路的取值口径必须完全一致，否则会出现"健康检查
# 验 A、一条路合成 B"这类检查绿而合成炸的组合。
#
# 下面三个常量是模型位置的唯一来源：service 的健康检查、speak 前置检查、
# 引擎的默认值、子进程 argv 的默认值都取自这里。

DEFAULT_VITS_MODEL_NAME = "G_latest.pth"
DEFAULT_VITS_CONFIG_NAME = "finetune_speaker.json"
DEFAULT_VITS_SPEAKER = "Mea"


def vits_model_path() -> str:
    """内置 VITS 模型权重路径。"""
    from meapet.paths import project_path

    return project_path("vits_models", DEFAULT_VITS_MODEL_NAME)


def vits_config_path() -> str:
    """内置 VITS 模型配置（说话人表）路径。"""
    from meapet.paths import project_path

    return project_path("vits_models", DEFAULT_VITS_CONFIG_NAME)


def module_present(name: str) -> bool:
    """本进程能不能**寻址到**某个模块——只查 import 元数据，不执行 import。

    给进程内那条路的健康检查用：`find_spec` 本机实测 0.1–0.4 ms，同一个 env 里
    真 `import torch` 是 3914 ms、全栈依赖探针是 12–20 s，后两笔都不能落在
    `speak()` 路径上。

    它证明不了模块**加载得起来**：打包版里 torch 在 `sys._MEIPASS` 寻得到，
    而 DLL/so 起不来的话 import 照样失败（那一格由 `vits_runtime.py` 的
    `Failed to load bundled torch` 分支管）。所以这个判据只能往"缺失"方向用
    ——False 一定不可用；True 只说"找到了"，不代表就绪。
    """
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # 父包 __init__ 自己抛 ImportError 时 find_spec 会连带抛出；对"这条路
        # 能不能走"的结论，"名字不存在"和"父包坏了"是同一格。
        return False


def _is_speaker_table(speakers: object) -> bool:
    """说话人表判定一律走鸭子类型，不能 ``isinstance(x, dict)``。

    ``finetune_speaker.json`` 经 ``vits_core.utils.get_hparams_from_file`` 读出来
    后，``hps.speakers`` 是一个 ``HParams`` 实例：它有 ``__contains__`` /
    ``__getitem__`` / ``keys``，**但不继承 dict**。老代码的
    ``isinstance(speaker_ids, dict)`` 因此恒为假 —— 说话人查表从来没有真正
    执行过，`--speaker` 一直是死的。
    """
    return speakers is not None and hasattr(speakers, "__contains__") and hasattr(
        speakers, "__getitem__"
    )


def resolve_vits_speaker(
    speakers: object, requested: str | None
) -> tuple[int, str | None]:
    """把说话人名解析成 id，并如实报告回落。

    返回 ``(speaker_id, warning)``。``warning`` 为 None 表示请求被满足。
    名字不在 ``finetune_speaker.json`` 里时静默换成 0 号音色，正是
    "换错音色没人数得清"的来源，所以这里必须把回落返回给调用方去出声。
    """
    if not _is_speaker_table(speakers):
        if requested:
            return 0, (
                f"VITS 配置没有说话人表，已忽略请求的说话人 {requested!r}，使用 0 号"
            )
        return 0, None
    if not requested:
        return 0, None
    if requested not in speakers:
        try:
            available = ", ".join(repr(name) for name in speakers.keys())
        except Exception:
            available = "?"
        return 0, (
            f"VITS 说话人 {requested!r} 不在模型里（可用: {available or '(空)'}），"
            "已静默换到 0 号音色"
        )
    try:
        return int(speakers[requested]), None
    except (TypeError, ValueError):
        return 0, f"VITS 说话人 {requested!r} 的 id 不是整数，使用 0 号"


class VitsRoute:
    """VITS 选路结果：两路共用的单一判据。"""

    __slots__ = ("inprocess", "external_python", "reason", "ignored_inprocess_pref")

    def __init__(
        self,
        *,
        inprocess: bool,
        external_python: str,
        reason: str,
        ignored_inprocess_pref: bool = False,
    ) -> None:
        self.inprocess = inprocess
        self.external_python = external_python
        self.reason = reason
        self.ignored_inprocess_pref = ignored_inprocess_pref

    @property
    def mode(self) -> str:
        return "inprocess" if self.inprocess else "subprocess"

    def describe(self) -> str:
        return (
            f"mode={self.mode} reason={self.reason}"
            + (
                f" python={os.path.basename(self.external_python)}"
                if self.external_python
                else ""
            )
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VitsRoute {self.describe()}>"


def resolve_vits_route(
    *,
    external_python: str | None = None,
    configured_python: str | None = None,
    inprocess_pref: bool | None = None,
    frozen: bool | None = None,
    fallback_python: str | None = None,
) -> VitsRoute:
    """决定 VITS 走子进程还是进程内 —— 全仓库唯一的判据。

    取值口径（``inprocess_pref`` 是「三态」：None=用户没说话）：

    1. 用户显式 ``tts.vits_inprocess`` 优先于打包默认值。
    2. 打包版（frozen）在没有外部解释器时默认走进程内。
    3. **外部解释器可用时一律优先走子进程**，包括用户声明
       ``vits_inprocess: true`` 的情况；此时
       :attr:`VitsRoute.ignored_inprocess_pref` 为 True，调用方**必须出声**。
       这条优先级是有意的（打包版自带 torch DLL 可能加载不了），所以保留，
       但不再无声吞掉用户的显式声明。

    ``external_python`` 已是解析结果时直接传；否则传 ``configured_python``，
    由本函数经 :func:`resolve_external_python` 解析（空串 / pet exe / 不存在
    都算没有外部解释器）。
    """
    if external_python is None:
        external_python = resolve_external_python(configured_python)
    external_python = external_python or ""

    if frozen is None:
        frozen = _is_frozen()

    explicit_inprocess = inprocess_pref is True
    if inprocess_pref is None:
        want_inprocess = bool(frozen and not external_python)
        reason = "frozen_no_external" if want_inprocess else "not_frozen"
    else:
        want_inprocess = explicit_inprocess
        reason = "explicit_inprocess" if explicit_inprocess else "explicit_subprocess"

    if external_python:
        # 这条分支就是老代码里那个恒真式的结果：external_py 一旦可用，
        # prefer_subprocess 必为 True，vits_inprocess 影响不了任何结论。
        return VitsRoute(
            inprocess=False,
            external_python=external_python,
            reason=(
                "explicit_inprocess_overridden_by_external"
                if explicit_inprocess
                else "external_configured"
            ),
            ignored_inprocess_pref=explicit_inprocess,
        )

    if want_inprocess:
        return VitsRoute(
            inprocess=True,
            external_python="",
            reason=reason if explicit_inprocess else "frozen_no_external",
        )

    # 用户没有声明进程内、又没有外部解释器：回落到本进程解释器
    # （源码运行时就是真 Python；打包版下这条路不可用，由选路方报错）。
    return VitsRoute(
        inprocess=False,
        external_python=resolve_external_python(fallback_python),
        reason="no_external_python_fallback",
    )


def hidden_subprocess_kwargs() -> dict:
    """Kwargs so Windows console Python does not flash a black terminal window.

    Prefer ``CREATE_NO_WINDOW`` (Win 3.7+). Also set STARTUPINFO as a belt-and-
    suspenders for older hosts. No-op on non-Windows.
    """
    if os.name != "nt":
        return {}
    kwargs: dict = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs


_LFS_POINTER_HEADER = b"version https://git-lfs.github.com/spec/v1"


def is_git_lfs_pointer(path: str) -> bool:
    """只读检测 Git LFS pointer；不会调用 git-lfs 或下载文件。"""
    try:
        with open(path, "rb") as f:
            return f.read(len(_LFS_POINTER_HEADER)) == _LFS_POINTER_HEADER
    except OSError:
        return False


def is_model_artifact_ready(path: str) -> bool:
    """模型文件必须存在，且不能仍是 Git LFS pointer。"""
    return bool(path and os.path.isfile(path) and not is_git_lfs_pointer(path))


VITS_DEPS_PROBE_TIMEOUT = 90


def probe_vits_deps(py_exe: str, infer_script: str) -> tuple[str, str]:
    """问 *py_exe* "能不能 import 推理依赖"，判据交给交付脚本自己。

    返回 ``("ok"|"missing"|"unknown", detail)``。``unknown`` 表示这一问没有答案
    （没解释器、脚本不在、超时、别的崩溃），调用方**不应**据此判不就绪——
    那会把"探针自己坏了"变成"用户环境坏了"的新误报。
    """
    py_exe = (py_exe or "").strip()
    if not py_exe or is_pet_executable(py_exe):
        return "unknown", "no external python"
    if not os.path.isfile(infer_script):
        return "unknown", "infer script missing"
    if _is_frozen() and not os.path.isfile(py_exe):
        return "unknown", "frozen and python path not on disk"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            [
                py_exe,
                infer_script,
                "--check-deps",
                # --text/--output 是脚本的必填项，这条路不会用到它们
                "--text",
                "probe",
                "--output",
                os.devnull,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VITS_DEPS_PROBE_TIMEOUT,
            env=env,
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return "unknown", f"probe timeout ({VITS_DEPS_PROBE_TIMEOUT}s)"
    except Exception as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0 and "OK:deps_loaded" in (proc.stdout or ""):
        return "ok", "deps importable"
    stderr = proc.stderr or ""
    if "ModuleNotFoundError" in stderr or "ImportError" in stderr:
        tail = next(
            (ln.strip() for ln in stderr.splitlines() if "Error" in ln),
            stderr[-120:],
        )
        return "missing", tail
    return "unknown", f"rc={proc.returncode} {(stderr or proc.stdout)[-160:]}"



# ═══════════════════════════════════════════
# GSV 子进程依赖自动安装
# ═══════════════════════════════════════════

# pip 包名 → Python import 名映射（两者不一致时）
GSV_MODULE_MAP = {
    "PyYAML": "yaml",
    "split-lang": "split_lang",
    "jieba_fast": "jieba_fast",
}


def _get_import_name(pkg_name: str) -> str:
    """从 pip 包名推导 Python import 名"""
    base = pkg_name.split(">")[0].split("<")[0].split("=")[0].strip()
    return GSV_MODULE_MAP.get(base, base.replace("-", "_"))


# GPT-SoVITS-CPUFast 推理所需的基础 Python 包
GSV_REQUIRED_PACKAGES = [
    "torch",
    "torchaudio",
    "soundfile",
    "numpy<2.0",
    "einops",
    "PyYAML",
    "tqdm",
    "pypinyin",
    "av",
    "fast_langdetect>=0.3.1",
    "split-lang",
    "wordsegment",
    "tokenizers",
    "transformers",
    "gradio",
    "pydantic<=2.10.6",
    "jieba",
]

# 可选包（部分可降级/跳过）
GSV_OPTIONAL_PACKAGES = [
    "jieba_fast",        # 有 C++ 编译要求，装不上用 jieba + 垫片
    "pyopenjtalk>=0.4.1", # 日语文本前端
    "g2p_en",            # 英文音素
    "g2pk2",             # 韩语音素
    "ko_pron",           # 韩语处理
    "ToJyutping",        # 粤语拼音
]

GSV_PIP_INDEX = resolve_pip_index_url()
TORCH_INDEX_URL = resolve_torch_index_url()


def _has_module(py_exe: str, module_name: str) -> bool:
    """Check whether a given Python can import *module_name*.

    In frozen mode (PyInstaller), ``sys.executable`` is the pet exe, not a
    real Python interpreter — running it as a subprocess would spawn a new
    MeaPet instance.  Return False immediately in that case.
    """
    if _is_frozen():
        log.warning(
            "[frozen] Skipping module check for %r — "
            "sys.executable is the pet exe, not a Python interpreter.",
            module_name,
        )
        return False
    try:
        r = subprocess.run(
            [py_exe, "-c", f"import {module_name}; print('ok')"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0 and 'ok' in r.stdout
    except Exception:
        return False


def _install_modules(py_exe: str, packages: list[str],
                     extra_index: str = None) -> bool:
    """``pip install`` *packages* into *py_exe*.

    Returns True only when every package installed successfully.
    In frozen mode this always returns False — ``sys.executable`` is the
    pet exe and cannot run pip.
    """
    if _is_frozen():
        log.warning(
            "[frozen] Cannot pip install — sys.executable is the pet exe. "
            "Install dependencies manually, or use MiMo cloud TTS."
        )
        return False
    cmd = [py_exe, "-m", "pip", "install", "--timeout", "120"]
    if extra_index:
        # 有专用 index（如 PyTorch）时用它做主源，公共源可通过环境变量配置
        cmd.extend(["--index-url", extra_index])
        cmd.extend(["--extra-index-url", GSV_PIP_INDEX])
    else:
        cmd.extend(["-i", GSV_PIP_INDEX])
    cmd.extend(packages)
    try:
        log.info(f"pip install {len(packages)} 个包 …")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0:
            log.warning(f"  pip 失败: {r.stderr[-200:]}")
            return False
        log.info("pip 成功")
        return True
    except Exception as e:
        log.error(f"pip 异常: {e}")
        return False


def auto_install_gsv_deps(py_exe: str, allow_download: bool = False) -> bool:
    """Check GSV dependencies; pip-installs only when *allow_download* is True.

    In frozen mode all subprocess operations are skipped — ``sys.executable``
    is the pet exe, not a Python interpreter.  Returns False immediately.
    """
    if _is_frozen():
        log.warning(
            "[frozen] GSV deps cannot be auto-installed. "
            "Use MiMo cloud TTS or install a real Python runtime separately."
        )
        return False
    log.info(f"Checking GSV deps (Python: {py_exe})")

    # Quickly scan which packages are missing.
    missing = []
    for pkg in GSV_REQUIRED_PACKAGES:
        mod = _get_import_name(pkg)
        if not _has_module(py_exe, mod):
            missing.append(pkg)

    if not missing:
        log.info("所有 GSV 依赖已安装")
        return True

    if not allow_download:
        log.warning(f"缺少 {len(missing)} 个依赖：{', '.join(missing[:6])}{'…' if len(missing)>6 else ''}")
        log.warning("  → 默认不自动 pip 安装。请手动安装，或设置 MEA_PET_ALLOW_DOWNLOAD=1 / tts.auto_install_deps=true")
        return False

    log.info(f"缺少 {len(missing)} 个依赖，按需安装 …")
    # 拆成两批：torch 系用 PyTorch 官方源，其余使用可配置的公共包源
    torch_pkgs = [p for p in missing if p in ("torch", "torchaudio")]
    other_pkgs = [p for p in missing if p not in ("torch", "torchaudio")]

    ok = True
    if torch_pkgs:
        ok = _install_modules(py_exe, torch_pkgs, extra_index=TORCH_INDEX_URL) and ok
    if other_pkgs:
        ok = _install_modules(py_exe, other_pkgs) and ok

    if not ok:
        log.error("pip 安装失败，请检查网络或手动安装")
        return False

    # 最终验证
    still = [p for p in GSV_REQUIRED_PACKAGES
             if not _has_module(py_exe, _get_import_name(p))]
    if still:
        log.warning(f"仍有 {len(still)} 个包未装: {still}")
        return False

    log.info("所有依赖安装完成")
    return True


# ========================
# 情感 → 参考音频映射
# ========================
MOOD_TO_REF = {
    # 平静/正面 → normal
    "neutral":      "normal",
    "happy":        "normal",
    "curious":      "normal",
    "surprised":    "normal",
    "talking":      "normal",
    "intrigued":    "normal",
    # 悲伤/忧郁/害羞 → soft
    "sad":          "soft",
    "melancholy":   "soft",
    "shy":          "soft",
    "embarrassed":  "soft",
    "teary":        "soft",
    "wistful":      "soft",
    # 恼怒 → clam
    "annoyed":      "clam",
}

# 语言常量（始终使用日语合成）
LANG_TTS = "日文"
