"""TTS 共享工具与常量（供 tts.py 与引擎 mixin 使用）"""
import os
import subprocess
from meapet.log import get_color_logger

log = get_color_logger("tts")


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

GSV_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"


def _has_module(py_exe: str, module_name: str) -> bool:
    """检查指定 Python 能否 import 某模块"""
    try:
        r = subprocess.run(
            [py_exe, "-c", f"import {module_name}; print('ok')"],
            capture_output=True, text=True, timeout=15
        )
        return r.returncode == 0 and 'ok' in r.stdout
    except Exception:
        return False


def _install_modules(py_exe: str, packages: list[str],
                     extra_index: str = None) -> bool:
    """pip install 包列表到指定 Python，返回是否全部成功"""
    cmd = [py_exe, "-m", "pip", "install", "--timeout", "120"]
    if extra_index:
        # 有专用 index（如 PyTorch）时用它做主源，清华做备用
        cmd.extend(["--index-url", extra_index])
        cmd.extend(["--extra-index-url", GSV_PIP_INDEX])
    else:
        cmd.extend(["-i", GSV_PIP_INDEX])
    cmd.extend(packages)
    try:
        log.info(f"pip install {len(packages)} 个包 …")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0:
            log.warn(f"  pip 失败: {r.stderr[-200:]}")
            return False
        log.info("pip 成功")
        return True
    except Exception as e:
        log.error(f"pip 异常: {e}")
        return False


def auto_install_gsv_deps(py_exe: str, allow_download: bool = False) -> bool:
    """检查 GSV 依赖；仅当 allow_download=True 时才 pip 安装（默认不自动下载）"""
    log.info(f"检查 GSV 依赖 (Python: {py_exe})")

    # 快速检查缺了哪些
    missing = []
    for pkg in GSV_REQUIRED_PACKAGES:
        mod = _get_import_name(pkg)
        if not _has_module(py_exe, mod):
            missing.append(pkg)

    if not missing:
        log.info("所有 GSV 依赖已安装")
        return True

    if not allow_download:
        log.warn(f"缺少 {len(missing)} 个依赖：{', '.join(missing[:6])}{'…' if len(missing)>6 else ''}")
        log.warn("  → 默认不自动 pip 安装。请手动安装，或设置 MEAPET_ALLOW_DOWNLOAD=1 / tts.auto_install_deps=true")
        return False

    log.info(f"缺少 {len(missing)} 个依赖，按需安装 …")
    # 拆成两批：torch 系用 PyTorch 官方源，其余用清华源
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
        log.warn(f"仍有 {len(still)} 个包未装: {still}")
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