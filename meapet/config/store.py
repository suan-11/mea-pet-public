"""
统一配置：只使用 config.json

结构（与 config.example.json 对齐）：
- llm / vision / tts / live2d / character / sprite_dir
- display（含 size_factor / font_scale）
- watcher（含 interval）
- bubble_duration_ms
- tts.sync_with_audio

密钥：环境变量优先于 config 明文（见 resolve_*）。
"""
from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from meapet.config.normalizers import normalize_gsv_ref_language
from meapet.ui_theme import normalize_ui_font_scale
from meapet.utils import mask_secret, normalize_watcher


# backend / 字段 → 候选环境变量（按顺序）
ENV_LLM_KEY = {
    "deepseek": ("DEEPSEEK_API_KEY", "MEAPET_API_KEY"),
    "mimo": ("MIMO_API_KEY", "XIAOMIMIMO_API_KEY", "MEAPET_API_KEY"),
    "ollama": (),
    "openclaw": (),
}

ENV_TTS_KEY = ("MIMO_API_KEY", "XIAOMIMIMO_API_KEY")
ENV_TRANSLATE_KEY = ("TRANSLATE_API_KEY", "DEEPSEEK_API_KEY")
ENV_VISION_KEY = ENV_LLM_KEY["mimo"]

SUPPORTED_VISION_BACKENDS = {"ollama", "mimo"}
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MIMO_API_BASE = "https://api.xiaomimimo.com/v1"

_ENV_PLACEHOLDERS = ("", "$ENV", "${ENV}", "env", "ENV")

DEFAULT_BUBBLE = {
    "default": 5000,
    "reply": 8000,
    "watch": 7000,
    "interaction": 3000,
    "thinking": 0,
}

DEFAULT_WATCHER_INTERVAL = {"min_ms": 180000, "max_ms": 360000}


def project_root() -> str:
    from meapet.paths import project_root as _pr
    return _pr()


def config_path(name: str = "config.json") -> str:
    return os.path.join(project_root(), name)


def resolve_startup_config_path(
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """返回与当前工作目录无关的启动配置路径。"""
    base = Path(root) if root is not None else Path(project_root())
    primary = base / "config.json"
    if primary.is_file():
        return str(primary)
    return str(base / "config.example.json")



def _first_env(names: Tuple[str, ...]) -> str:
    for n in names:
        if not n:
            continue
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""




# 小米官方 API model id（不要用 HuggingFace 仓库名 XiaomiMiMo/...）
# 文档: https://mimo.mi.com/docs/en-US/quick-start/summary/model
MIMO_MODEL_ALIASES = {
    "xiaomimimo/mimo-v2.5": "mimo-v2.5",
    "xiaomimimo/mimo-v2.5-pro": "mimo-v2.5-pro",
    "mimo-v2.5": "mimo-v2.5",
    "mimo-v2.5-pro": "mimo-v2.5-pro",
    "mimo": "mimo-v2.5",
    "minicpm-v": "mimo-v2.5",  # 误填时给 vision 一条生路
    "qwen3.5:4b": "mimo-v2.5",  # 同上
}

def normalize_mimo_model_id(model: str, *, for_vision: bool = False) -> str:
    """把常见错误/别名映射成官方 API model id。

    默认使用多模态 `mimo-v2.5`（对话/识图通用）。
    仅当用户显式写 pro 相关名字时才映射到 `mimo-v2.5-pro`。
    """
    raw = (model or "").strip()
    if not raw:
        return "mimo-v2.5"
    key = raw.lower()
    if key in MIMO_MODEL_ALIASES:
        return MIMO_MODEL_ALIASES[key]
    # HF 风格: XiaomiMiMo/MiMo-V2.5 / XiaomiMiMo/MiMo-V2.5-Pro
    if "mimo-v2.5-pro" in key or "mimo_v2.5_pro" in key or "mimo-v2.5pro" in key:
        return "mimo-v2.5-pro"
    if "mimo-v2.5" in key or "mimo_v2.5" in key or key.endswith("mimo-v2.5"):
        return "mimo-v2.5"
    if raw.startswith("XiaomiMiMo/") or raw.startswith("xiaomimimo/"):
        # HF 仓库名默认落到多模态基座，不默认 pro
        return "mimo-v2.5"
    return raw

def resolve_secret(file_value: str = "", env_names: Tuple[str, ...] = ()) -> str:
    env_val = _first_env(env_names)
    raw = (file_value or "").strip()
    if raw.startswith("${") and raw.endswith("}") and len(raw) > 3:
        return os.environ.get(raw[2:-1], "").strip() or env_val
    if raw.startswith("$") and len(raw) > 1 and raw[1:].replace("_", "").isalnum():
        return os.environ.get(raw[1:], "").strip() or env_val
    if raw in _ENV_PLACEHOLDERS or raw.upper() == "$ENV":
        return env_val
    if env_val:
        return env_val
    return raw


def save_config(config: dict, path: Optional[str] = None) -> None:
    cpath = path or config_path()
    existing = load_json(cpath, {})
    merged = _deep_merge(existing, config)
    save_json(cpath, normalize_config(merged))



def resolve_llm_api_key(llm_cfg: dict) -> str:
    backend = (llm_cfg.get("backend") or "ollama").lower()
    names = ENV_LLM_KEY.get(backend, ("MEAPET_API_KEY",))
    return resolve_secret(llm_cfg.get("api_key", ""), names)


def resolve_tts_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    llm_cfg = llm_cfg or {}
    resolved = resolve_secret(tts_cfg.get("api_key", ""), ENV_TTS_KEY)
    if resolved:
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        return resolve_llm_api_key(llm_cfg)
    return ""


def resolve_translate_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    llm_cfg = llm_cfg or {}
    resolved = resolve_secret(
        tts_cfg.get("translate_api_key", ""),
        ENV_TRANSLATE_KEY,
    )
    if resolved:
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "deepseek":
        return resolve_llm_api_key(llm_cfg)
    return ""


def resolve_vision_backend(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析实际识图后端；不支持视觉的对话后端安全回退到本地 Ollama。"""
    llm_cfg = llm_cfg or {}
    backend = (
        vision_cfg.get("backend")
        or llm_cfg.get("backend")
        or "ollama"
    ).lower()
    return backend if backend in SUPPORTED_VISION_BACKENDS else "ollama"


def resolve_vision_api_key(vision_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    llm_cfg = llm_cfg or {}
    backend = resolve_vision_backend(vision_cfg, llm_cfg)
    if backend != "mimo":
        return ""
    resolved = resolve_secret(
        vision_cfg.get("api_key", ""),
        ENV_LLM_KEY["mimo"],
    )
    if resolved:
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        return resolve_llm_api_key(llm_cfg)
    return ""


def resolve_vision_api_base(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析 MiMo 识图地址，禁止继承其它供应商的 API 地址。"""
    llm_cfg = llm_cfg or {}
    if resolve_vision_backend(vision_cfg, llm_cfg) != "mimo":
        return ""
    explicit = (vision_cfg.get("api_base") or "").strip()
    if explicit:
        return explicit
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        inherited = (llm_cfg.get("api_base") or "").strip()
        if inherited:
            return inherited
    return DEFAULT_MIMO_API_BASE


def resolve_vision_host(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析 Ollama 识图地址，禁止继承云端对话后端的地址。"""
    llm_cfg = llm_cfg or {}
    explicit = (vision_cfg.get("host") or "").strip()
    if explicit:
        return explicit
    if (llm_cfg.get("backend") or "").lower() == "ollama":
        inherited = (llm_cfg.get("host") or "").strip()
        if inherited:
            return inherited
    return DEFAULT_OLLAMA_HOST


def load_json(path: str, default: Optional[dict] = None) -> dict:
    default = default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else copy.deepcopy(default)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return copy.deepcopy(default)


def save_json(path: str, data: dict) -> None:
    """原子写入 JSON；数据内容（包括现有 Key）原样保存。"""
    target = os.path.abspath(path)
    parent = os.path.dirname(target) or os.curdir
    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        pass

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, target)
        tmp_path = ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base or {})
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out



def normalize_config(config: dict) -> dict:
    """补全默认字段、规范化 watcher / bubble / display / tts.sync"""
    cfg = copy.deepcopy(config or {})

    cfg.setdefault("llm", {})
    cfg.setdefault("vision", {})
    cfg.setdefault("tts", {})
    cfg.setdefault("display", {})
    cfg.setdefault("character", {})
    cfg.setdefault("live2d", {})

    # bubble
    bub = cfg.get("bubble_duration_ms") if isinstance(cfg.get("bubble_duration_ms"), dict) else {}
    for k, v in DEFAULT_BUBBLE.items():
        bub.setdefault(k, v)
    cfg["bubble_duration_ms"] = bub

    # display
    disp = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
    disp.setdefault("scale", 0.5)
    disp.setdefault("fps", 30)
    disp.setdefault("size_factor", 1.0)
    disp["font_scale"] = normalize_ui_font_scale(
        disp.get("font_scale", 1.0)
    )
    disp["reduced_motion"] = bool(disp.get("reduced_motion", False))
    cfg["display"] = disp

    # UI 一次性引导等非敏感本地状态
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    ui["first_run_hint_shown"] = bool(ui.get("first_run_hint_shown", False))
    cfg["ui"] = ui

    # TTS：音频同步 + 可选固定 GPT-SoVITS 参考音频
    tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    if "sync_with_audio" not in tts:
        tts["sync_with_audio"] = False
    else:
        tts["sync_with_audio"] = bool(tts["sync_with_audio"])
    tts["gsv_ref_wav"] = str(tts.get("gsv_ref_wav") or "").strip()
    tts["gsv_ref_lang"] = normalize_gsv_ref_language(
        tts.get("gsv_ref_lang")
    )
    cfg["tts"] = tts

    # watcher 统一结构（interval 内嵌，不再用顶层 watcher_interval）
    w_in = cfg.get("watcher") if isinstance(cfg.get("watcher"), dict) else {}
    # 兼容旧顶层 watcher_interval
    if "interval" not in w_in or not isinstance(w_in.get("interval"), dict):
        top_wi = cfg.get("watcher_interval") if isinstance(cfg.get("watcher_interval"), dict) else {}
        if top_wi:
            w_in = dict(w_in)
            w_in["interval"] = {
                "min_ms": int(top_wi.get("min_ms", DEFAULT_WATCHER_INTERVAL["min_ms"])),
                "max_ms": int(top_wi.get("max_ms", DEFAULT_WATCHER_INTERVAL["max_ms"])),
            }
    w = normalize_watcher(w_in)
    # normalize_watcher 已含 interval；强制安全底线
    w["require_confirm"] = True
    w["confirm_once_session"] = False
    watcher_out = copy.deepcopy(w_in)
    interval_out = (
        copy.deepcopy(watcher_out.get("interval"))
        if isinstance(watcher_out.get("interval"), dict)
        else {}
    )
    interval_out.update(w["interval"])
    watcher_out.update({
        "enabled": w["enabled"],
        "allow_cloud": w["allow_cloud"],
        "require_confirm": True,
        "confirm_once_session": False,
        "interval": interval_out,
    })
    cfg["watcher"] = watcher_out
    # 保留旧 watcher_interval 和未知字段，避免规范化时删除用户配置。
    return cfg




def load_config(path: Optional[str] = None) -> dict:
    """加载统一 config.json 并补全默认字段。"""
    cpath = path or config_path()
    return normalize_config(load_json(cpath, {}))


def scrub_secrets(config: dict) -> dict:
    out = copy.deepcopy(config or {})
    if "llm" in out and isinstance(out["llm"], dict):
        out["llm"]["api_key"] = ""
    if "tts" in out and isinstance(out["tts"], dict):
        out["tts"]["api_key"] = ""
        out["tts"]["translate_api_key"] = ""
    if "vision" in out and isinstance(out["vision"], dict):
        out["vision"]["api_key"] = ""
    return out


def secret_status(config: dict) -> Dict[str, str]:
    llm = config.get("llm") or {}
    tts = config.get("tts") or {}
    vision = config.get("vision") or {}
    llm_key = resolve_llm_api_key(llm)
    tts_key = resolve_tts_api_key(tts, llm)
    tr_key = resolve_translate_api_key(tts, llm)
    vis_key = resolve_vision_api_key(vision, llm)

    def src(file_val: str, resolved: str, envs: Tuple[str, ...]) -> str:
        if not resolved:
            return "missing"
        env_hit = _first_env(envs)
        if env_hit and resolved == env_hit:
            return "env:" + ",".join(envs[:2])
        if (file_val or "").strip():
            return "file"
        return "unknown"

    return {
        "llm": src(llm.get("api_key", ""), llm_key, ENV_LLM_KEY.get((llm.get("backend") or "").lower(), ("MEAPET_API_KEY",))),
        "tts": src(tts.get("api_key", ""), tts_key, ENV_TTS_KEY),
        "translate": src(tts.get("translate_api_key", ""), tr_key, ENV_TRANSLATE_KEY),
        "vision": src(vision.get("api_key", ""), vis_key, ENV_VISION_KEY),
        "llm_preview": mask_secret(llm_key) if llm_key else "",
    }
