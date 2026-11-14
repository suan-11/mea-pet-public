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
import math
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from meapet.config.normalizers import (
    canonical_tts_language,
    clamp_float,
    clamp_int,
    normalize_click_feedback_weight,
    normalize_feedback_keywords,
    normalize_gsv_ref_language,
    normalize_input_device_index,
    normalize_poetry_sources,
    normalize_voice_mode,
)
from meapet.config.defaults import (
    DEFAULT_AGENT_LINK_WS_URL,
    DEFAULT_AGENT_CONTROL,
    DEFAULT_AGENT_HISTORY_TURNS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    DEFAULT_BUBBLE_DURATIONS,
    DEFAULT_CLICK_FEEDBACK_CRAWLER,
    DEFAULT_CLICK_FEEDBACK_POETRY,
    DEFAULT_CLICK_FEEDBACK_POETRY_SOURCES,
    DEFAULT_CLICK_FEEDBACK_WEIGHTS,
    DEFAULT_CONTROL_PORT,
    DEFAULT_HERMES_WS_URL,
    DEFAULT_LIVE2D_WINDOW_MASK,
    DEFAULT_LIVE2D_WINDOW_SHAPE,
    DEFAULT_MEA_PET_MIMO_API_BASE,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OPENAI_API_BASE,
    DEFAULT_OPENCLAW_WS_URL,
    DEFAULT_WATCHER_INTERVAL,
)
from meapet.log import get_color_logger

_log = get_color_logger("config")
from meapet.config import providers as _providers
from meapet.ui_theme import (
    normalize_pet_size_factor,
    normalize_text_color,
    normalize_theme_mode,
    normalize_ui_font_scale,
)
from meapet.utils import mask_secret, normalize_watcher
from meapet.vision.policy import normalize_vision_mode
from meapet.paths import normalize_voice_asr_precision


# 通用 LLM 环境变量（未知/未标注 backend 时的兜底）。
# MEA_PET_API_KEY 是跨后端的通用兜底；厂商专属变量见 URL 探测。
ENV_LLM_KEY = ("MEA_PET_OPENAI_API_KEY", "MEA_PET_API_KEY")
# 仅作 URL 级 env 探测复用；direct.provider 一律保存为 custom。
ENV_LLM_KEY_BY_FAMILY = {
    "deepseek": ("MEA_PET_DEEPSEEK_API_KEY", "MEA_PET_API_KEY"),
    "mimo": ("MEA_PET_MIMO_API_KEY", "XIAOMIMEA_PET_MIMO_API_KEY", "MEA_PET_API_KEY"),
    "openai": ("MEA_PET_OPENAI_API_KEY", "MEA_PET_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY", "MEA_PET_API_KEY"),
}
# 旧名兼容：部分测试/调用仍引用 BY_BACKEND。
ENV_LLM_KEY_BY_BACKEND = ENV_LLM_KEY_BY_FAMILY
ENV_TTS_KEY = ("MEA_PET_MIMO_API_KEY", "XIAOMIMEA_PET_MIMO_API_KEY", "MEA_PET_API_KEY")
ENV_TRANSLATE_KEY = ("MEA_PET_TRANSLATE_API_KEY",)
ENV_VISION_KEY = ("MEA_PET_MIMO_API_KEY", "XIAOMIMEA_PET_MIMO_API_KEY", "MEA_PET_API_KEY")
#: 点击反馈「爬虫」那一档的 Tavily 凭据：留空即 keyless 档（实测免注册可用），
#: 填了才发 Bearer。环境变量名与其余键同族（MEA_PET_ 前缀）。
ENV_TAVILY_KEY = ("MEA_PET_TAVILY_API_KEY",)

# 直连协议默认；显式 protocol 始终优先。provider 品牌不再参与分流。
PROTOCOL_BY_ENDPOINT_FAMILY = {
    "ollama": "ollama_chat",
    "mimo": "openai_chat",
    "anthropic": "anthropic_messages",
    "deepseek": "openai_chat",
    "openai": "openai_chat",
    "custom": "openai_chat",
}
# 旧名：历史代码/测试可能仍 import。
PROTOCOL_BY_PROVIDER = PROTOCOL_BY_ENDPOINT_FAMILY

# 从 providers 预设注册表补齐新供应商的协议与密钥环境变量映射（单一数据源）。
# 用 setdefault：不覆盖上面已有的 5 个内置 family（保持既有行为）。
for _preset in _providers.all_presets():
    PROTOCOL_BY_ENDPOINT_FAMILY.setdefault(_preset.id, _preset.protocol)
    if _preset.env_keys:
        ENV_LLM_KEY_BY_FAMILY.setdefault(_preset.id, _preset.family_env_keys)

_DIRECT_PROVIDERS = frozenset({"custom"})
# 旧顶层 backend 属于 Agent 类时，无 mode 配置迁去 agent。
_AGENT_KINDS = frozenset({"hermes", "openclaw", "agent_link"})
# 支持独立识图解析的视觉后端（vision.backend，不是 llm.provider）。
_VISION_BACKENDS = frozenset({"ollama", "mimo"})

# 旧导出名兼容；真实值统一维护在 ``meapet.config.defaults``。
DEFAULT_API_BASE = DEFAULT_OPENAI_API_BASE

_ENV_PLACEHOLDERS = ("", "$ENV", "${ENV}", "env", "ENV")


def infer_agent_kind(raw_base: object) -> Tuple[str, str]:
    """``llm.agent.kind`` 没写时，按地址反推网关类型，第二条是判据文本。

    三条判据都是各网关**自己的地址签名**，不是按端口大小猜：``18789`` 是 OpenClaw 的默认口，
    ``agent-link`` 是 Agent Link 的路径，``9119`` 与 ``/api/ws`` 是 Hermes 的原生 WS。
    三种默认地址（``defaults.py``）都长这样，所以**不能**拿 "是 ws:// 就算 agent_link" 当判据——
    那会把 ``ws://127.0.0.1:9119/api/ws`` 那份本来正确的 hermes 猜成另一路。
    三条都不命中才退回历史默认 hermes，此时判据文本明说是猜的，调用方须把它说出来。
    """
    lowered = str(raw_base or "").strip().lower()
    if "18789" in lowered:
        return "openclaw", "地址里有 18789（OpenClaw 默认口）"
    if "agent-link" in lowered or "agent_link" in lowered:
        return "agent_link", "地址里有 agent-link 那段路径（Agent Link 网关）"
    if ":9119" in lowered or "/api/ws" in lowered:
        return "hermes", "地址里有 9119 或 /api/ws（Hermes 原生 WS）"
    return "hermes", "地址里认不出任何网关签名，按历史默认**猜**的"


def _endpoint_family_from_text(text: object) -> str:
    """对单个地址字符串做能力族识别；空串或未识别返回 ""。"""
    value = str(text or "").strip().lower()
    if not value:
        return ""
    if "xiaomimimo" in value or "mimo.mi.com" in value:
        return "mimo"
    if "anthropic" in value:
        return "anthropic"
    if "deepseek" in value:
        return "deepseek"
    # 预设注册表里的其余供应商（moonshot / zhipu / qwen / groq / lmstudio 等）。
    # 放在 loopback 判定之前：LM Studio 这类本地 OpenAI 兼容服务的地址签名更具体，
    # 不应被下面宽泛的 localhost→ollama 规则吞掉。ollama 仍走既有弱信号逻辑。
    _preset_hit = _providers.detect_preset_by_url(value)
    if _preset_hit is not None and _preset_hit.id != "ollama":
        return _preset_hit.id
    if "11434" in value or "localhost" in value or "127.0.0.1" in value:
        return "ollama"
    if "openai.com" in value:
        return "openai"
    return ""


def detect_endpoint_family(*parts: object) -> str:
    """从 API 地址识别能力族（仅用于协议/密钥/联动，不写入 provider）。

    按参数优先级逐个判断，不拼接：云厂商命中立即返回；loopback/ollama
    信号最弱，不能压过前面已出现的非空自定义 api_base（否则 ChatEngine
    默认 host=127.0.0.1:11434 会把所有云端 endpoint 误判成 ollama）。

    返回: ollama | mimo | deepseek | anthropic | openai | ""
    """
    saw_unrecognized = False
    ollama_fallback = ""
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        family = _endpoint_family_from_text(text)
        if family == "ollama":
            if not ollama_fallback:
                ollama_fallback = "ollama"
            continue
        if family:
            return family
        # 非空但未识别（自定义 OpenAI 兼容地址等）：保留为强于默认 host 的信号
        saw_unrecognized = True
    if saw_unrecognized:
        return ""
    return ollama_fallback


def normalize_direct_provider(provider: object = None) -> str:
    """对话直连身份标签：始终 custom。厂商品牌不是传输后端。"""
    return "custom"


def infer_direct_protocol(
    provider: object = None,
    api_base: object = "",
    host: object = "",
) -> str:
    """按端点地址推断直连协议；显式 protocol 由调用方优先保留。

    provider 参数保留仅为旧调用兼容，不再参与分流。
    """
    family = detect_endpoint_family(api_base, host)
    if family:
        return PROTOCOL_BY_ENDPOINT_FAMILY.get(family, "openai_chat")
    # 旧配置可能只剩 provider 标签、地址为空：尽量从标签兜底协议。
    legacy = str(provider or "").strip().lower()
    if legacy in PROTOCOL_BY_ENDPOINT_FAMILY:
        return PROTOCOL_BY_ENDPOINT_FAMILY[legacy]
    return "openai_chat"


def _env_names_for_api_base(api_base: object, host: object = "") -> Tuple[str, ...]:
    """URL 级环境变量提示：custom provider 仍可读厂商专属 key。"""
    family = detect_endpoint_family(api_base, host)
    if family in ENV_LLM_KEY_BY_FAMILY:
        return ENV_LLM_KEY_BY_FAMILY[family]
    return ()


def llm_endpoint_family(llm_cfg: Optional[dict] = None) -> str:
    """从 llm/direct 配置识别端点能力族。

    优先看 api_base/host；旧配置仅有 provider/backend 标签时再回落标签。
    """
    llm = llm_cfg or {}
    direct = llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
    # api_base 优先于 legacy host，避免默认 127.0.0.1:11434 覆盖云端地址
    family = detect_endpoint_family(
        direct.get("api_base"),
        llm.get("api_base"),
        direct.get("host"),
        llm.get("host"),
    )
    if family:
        return family
    for raw in (
        direct.get("provider"),
        llm.get("backend"),
        llm.get("provider"),
    ):
        key = str(raw or "").strip().lower()
        if key in {"mimo", "ollama", "deepseek", "anthropic", "openai"}:
            return key
    return ""

DEFAULT_BUBBLE = DEFAULT_BUBBLE_DURATIONS

# 旧导出名兼容
DEFAULT_WATCHER_INTERVAL = DEFAULT_WATCHER_INTERVAL  # 来自 defaults 导入


def project_root() -> str:
    from meapet.paths import project_root as _pr
    return _pr()


def config_path(name: str = "config.json") -> str:
    """返回配置文件路径。

    在 PyInstaller 打包模式下使用 ``sys._MEIPASS``
    （即 ``dist/MeaPet/_internal/``），配置与运行库在一起，
    整个 dist/ 文件夹可以整体分发便携版。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / name)
    return os.path.join(project_root(), name)


def resolve_startup_config_path(
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """返回与当前工作目录无关的启动配置路径。

    搜索顺序（仅打包模式）：
    1. ``_MEIPASS / config.json``（用户保存的配置）
    2. ``_MEIPASS / config.example.json``（内置默认配置）

    开发模式下：
    1. ``root / config.json``
    2. ``root / config.example.json``
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        user_cfg = meipass / "config.json"
        if user_cfg.is_file():
            return str(user_cfg)
        return str(meipass / "config.example.json")

    # 开发模式
    base = Path(root) if root is not None else Path(project_root())
    primary = base / "config.json"
    if primary.is_file():
        return str(primary)
    return str(base / "config.example.json")


def resolve_writable_config_path(
    path: Optional[Union[str, os.PathLike[str]]] = None,
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """把启动/读取路径映射为可写的 config.json。

    从 config.example.json 启动时，首次保存必须落到 ``_MEIPASS``
    （即 ``dist/MeaPet/_internal/config.json``），
    与内置运行库在一起，整体便携分发。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / "config.json")

    base = Path(root) if root is not None else Path(project_root())
    if path is None or str(path).strip() == "":
        return str(base / "config.json")
    candidate = Path(path)
    if candidate.name == "config.example.json":
        return str(candidate.with_name("config.json"))
    return str(candidate)


def resolve_resource_path(
    path: Union[str, os.PathLike[str]] = "",
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """把相对资源路径锚定到项目根，避免依赖进程 cwd。

    绝对路径原样规范化；空字符串返回空字符串。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    base = Path(root) if root is not None else Path(project_root())
    return str((base / p).resolve())


def _first_env(names: Tuple[str, ...]) -> str:
    for n in names:
        if not n:
            continue
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


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
    """PATCH 式写入：与磁盘已有字段 deep-merge 后再 normalize。

    调用方应传入完整运行时 config；磁盘上仅存在于文件、未加载进内存的字段
    也会被保留（避免向导/局部更新冲掉其它键）。
    """
    cpath = path or config_path()
    existing = load_json(cpath, {})
    merged = _deep_merge(existing, config)
    save_json(cpath, normalize_config(merged))


def _llm_env_names(backend: object) -> Tuple[str, ...]:
    """按端点族选择可用的环境变量集合，避免跨厂商误用密钥。"""
    key = str(backend or "").strip().lower()
    return ENV_LLM_KEY_BY_FAMILY.get(key, ENV_LLM_KEY)


def resolve_llm_api_key(llm_cfg: dict) -> str:
    """解析 LLM API Key，优先 agent.api_key > llm.api_key > env。"""
    agent = llm_cfg.get("agent") if isinstance(llm_cfg.get("agent"), dict) else {}
    agent_key = resolve_secret(agent.get("api_key", ""), ENV_LLM_KEY)
    if agent_key:
        return agent_key
    return resolve_secret(llm_cfg.get("api_key", ""), ENV_LLM_KEY)


def resolve_direct_api_key(llm_cfg: dict) -> str:
    """解析显式 direct profile；环境变量仍优先于文件值。

    provider 一律视为 custom；按 api_base/host URL 探测厂商专属 env
   （MEA_PET_DEEPSEEK_API_KEY / MEA_PET_MIMO_API_KEY 等），再回落通用 MEAPET/OPENAI key。
    """
    direct = llm_cfg.get("direct") if isinstance(llm_cfg.get("direct"), dict) else {}
    api_base = (
        str(direct.get("api_base") or "").strip()
        or str(llm_cfg.get("api_base") or "").strip()
    )
    host = (
        str(direct.get("host") or "").strip()
        or str(llm_cfg.get("host") or "").strip()
    )
    url_names = _env_names_for_api_base(api_base, host)
    if url_names:
        # 识别出厂商端点：只读该厂商专属变量 + 中立的 MEA_PET_API_KEY，
        # 绝不并入 MEA_PET_OPENAI_API_KEY，避免环境里的 OpenAI 密钥压过显式文件密钥
        # 并被发往 DeepSeek/MiMo/Anthropic 等第三方端点（跨厂商凭据泄露）。
        env_names = url_names
    else:
        env_names = ENV_LLM_KEY

    value = resolve_secret(
        str(direct.get("api_key") or ""),
        env_names,
    )
    if value:
        return value
    return resolve_llm_api_key(llm_cfg)


def resolve_tts_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    """解析 TTS Key；仅当对话端点是 MiMo 地址时才允许复用其密钥。"""
    key = resolve_secret(tts_cfg.get("api_key", ""), ENV_TTS_KEY)
    if key:
        return key
    llm = llm_cfg or {}
    if llm_endpoint_family(llm) == "mimo":
        return resolve_llm_api_key(llm)
    return ""


def resolve_translate_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    """读取旧版翻译密钥；绝不复用对话模型密钥。"""
    return resolve_secret(
        tts_cfg.get("translate_api_key", ""),
        ENV_TRANSLATE_KEY,
    )


def resolve_vision_backend(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析视觉后端：显式 vision.backend 优先，其次跟随可识图的对话端点族，
    否则回退本地 ollama（云端对话端点不支持独立识图跟随）。"""
    explicit = str(vision_cfg.get("backend") or "").strip().lower()
    if explicit in _VISION_BACKENDS:
        return explicit
    family = llm_endpoint_family(llm_cfg)
    if family in _VISION_BACKENDS:
        return family
    return "ollama"


def resolve_vision_api_key(vision_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    """解析视觉 API Key：密钥按端点族隔离，仅同族才允许复用 llm 密钥。"""
    backend = resolve_vision_backend(vision_cfg, llm_cfg)
    env_names = ENV_VISION_KEY if backend == "mimo" else ()
    key = resolve_secret(vision_cfg.get("api_key", ""), env_names)
    if key:
        return key
    llm = llm_cfg or {}
    if llm_endpoint_family(llm) == backend:
        return resolve_llm_api_key(llm)
    return ""


def resolve_click_feedback_crawler_key(config: dict) -> str:
    """点击反馈「爬虫」的 Tavily 凭据；空串＝走 keyless 档。

    不复用 ``llm`` 那把 key：两把钥匙开的是不同的门，拿 LLM 的 key 去发 Bearer 只会
    换来一个 ``401``——而实测「无效 key 不退回 keyless」，填错比不填更坏。
    """
    click_feedback = (config or {}).get("click_feedback") or {}
    crawler = click_feedback.get("crawler") or {}
    if not isinstance(crawler, dict):
        return ""
    return resolve_secret(crawler.get("api_key", ""), ENV_TAVILY_KEY)


def resolve_vision_api_base(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析视觉 API 地址：显式配置优先；仅同端点族才继承 llm 地址，
    否则回退该后端自己的默认地址（禁止把截图发往其它厂商的端点）。

    ollama 只认 host：残留的云端 api_base 一律忽略，避免确认走本地、
    实际上传到 MiMo 默认地址的错位。
    """
    backend = resolve_vision_backend(vision_cfg, llm_cfg)
    if backend == "ollama":
        return resolve_vision_host(vision_cfg, llm_cfg)
    explicit = (vision_cfg.get("api_base") or "").strip()
    if explicit:
        return explicit
    llm = llm_cfg or {}
    if llm_endpoint_family(llm) == backend:
        direct = llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
        inherited = (
            str(direct.get("api_base") or "").strip()
            or str(llm.get("api_base") or "").strip()
        )
        if inherited:
            return inherited
    if backend == "mimo":
        return DEFAULT_MEA_PET_MIMO_API_BASE
    return DEFAULT_API_BASE


def resolve_vision_host(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    """解析视觉主机地址：显式配置优先；仅同端点族才继承 llm 主机。"""
    explicit = (vision_cfg.get("host") or "").strip()
    if explicit:
        return explicit
    backend = resolve_vision_backend(vision_cfg, llm_cfg)
    llm = llm_cfg or {}
    if llm_endpoint_family(llm) == backend:
        direct = llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
        inherited = (
            str(direct.get("host") or "").strip()
            or str(llm.get("host") or "").strip()
        )
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


def _normalize_llm_contract(value: object) -> dict:
    """补齐 direct/agent 显式结构并隔离两类传输协议。

    ``direct`` 保存 HTTP 模型协议；``agent`` 只保存 Hermes/OpenClaw
    WebSocket Gateway 配置。旧 Hermes API Server 与曾经的
    ``openai_compatible`` Agent 配置不会继续把 HTTP 端点混入 Agent。
    """
    llm = copy.deepcopy(value) if isinstance(value, dict) else {}
    backend = str(llm.get("backend") or "").strip().lower()
    requested_mode = str(llm.get("mode") or "").strip().lower()
    if requested_mode not in {"direct", "agent"}:
        # 旧配置无 mode：Agent 类 backend 迁去 agent，其余默认 direct
        requested_mode = "agent" if backend in _AGENT_KINDS else "direct"

    legacy_agent = (
        copy.deepcopy(llm.get("agent"))
        if isinstance(llm.get("agent"), dict)
        else {}
    )
    legacy_agent_kind = str(
        legacy_agent.get("kind") or backend or ""
    ).strip().lower()
    legacy_agent_base = str(
        legacy_agent.get("base_url")
        or legacy_agent.get("bridge_url")
        or ""
    ).strip()
    if not legacy_agent_base and legacy_agent_kind not in _AGENT_KINDS:
        legacy_agent_base = str(
            llm.get("api_base") or llm.get("host") or ""
        ).strip()
    legacy_local_hermes_http = legacy_agent_base.lower().rstrip("/") in {
        "http://127.0.0.1:8642",
        "http://localhost:8642",
    }
    legacy_http_model_agent = bool(
        requested_mode == "agent"
        and legacy_agent_kind not in _AGENT_KINDS
        and legacy_agent_base.lower().startswith(("http://", "https://"))
        and not legacy_local_hermes_http
    )
    if legacy_http_model_agent:
        # 旧版曾把 OpenAI-compatible 模型端点放进 agent。它不是 Agent
        # Gateway，迁入 direct 才能保留可用性且不虚构一个 /api/ws 地址。
        requested_mode = "direct"

    # ---- direct 段 ----
    direct = copy.deepcopy(llm.get("direct")) if isinstance(llm.get("direct"), dict) else {}
    if legacy_http_model_agent:
        if not str(direct.get("api_base") or "").strip():
            direct["api_base"] = legacy_agent_base
        if not str(direct.get("host") or "").strip():
            direct["host"] = legacy_agent_base
        if not str(direct.get("api_key") or "").strip():
            direct["api_key"] = str(
                legacy_agent.get("api_key")
                or legacy_agent.get("auth_token")
                or ""
            ).strip()
        for key in (
            "model",
            "protocol",
            "temperature",
            "max_tokens",
            "timeout_seconds",
        ):
            if key in legacy_agent and key not in direct:
                direct[key] = copy.deepcopy(legacy_agent[key])
    # 身份标签一律 custom；协议/密钥/联动改由 api_base/host 推断。
    legacy_provider = str(direct.get("provider") or backend or "").strip().lower()
    direct["provider"] = "custom"
    direct.setdefault("api_base", str(llm.get("api_base") or "").strip())
    direct.setdefault("host", str(llm.get("host") or "").strip())
    direct.setdefault("api_key", str(llm.get("api_key") or "").strip())
    direct.setdefault("temperature", llm.get("temperature", 0.7))
    direct.setdefault("max_tokens", llm.get("max_tokens", 4096))
    # 供应商自定义请求头：只保留字符串键值，非法结构直接丢弃。
    raw_headers = direct.get("headers")
    if isinstance(raw_headers, dict):
        cleaned_headers = {
            str(k).strip(): str(v)
            for k, v in raw_headers.items()
            if str(k or "").strip()
        }
        if cleaned_headers:
            direct["headers"] = cleaned_headers
        else:
            direct.pop("headers", None)
    else:
        direct.pop("headers", None)
    # 高级配置：超时（秒）、按供应商生效的代理、Anthropic 扩展思考。
    try:
        timeout_value = float(direct.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        timeout_value = 0.0
    if timeout_value > 0:
        direct["timeout_seconds"] = timeout_value
    else:
        direct.pop("timeout_seconds", None)
    proxy_value = str(direct.get("proxy") or "").strip()
    if proxy_value:
        direct["proxy"] = proxy_value
    else:
        direct.pop("proxy", None)
    raw_thinking = direct.get("thinking")
    if isinstance(raw_thinking, dict):
        thinking_type = str(raw_thinking.get("type") or "").strip().lower()
        effort = str(raw_thinking.get("effort") or "").strip().lower()
        try:
            budget = int(raw_thinking.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        if thinking_type == "adaptive" or budget > 0:
            cleaned_thinking: Dict[str, object] = {}
            if thinking_type:
                cleaned_thinking["type"] = thinking_type
            if budget > 0:
                cleaned_thinking["budget"] = budget
            if effort:
                cleaned_thinking["effort"] = effort
            direct["thinking"] = cleaned_thinking
        else:
            direct.pop("thinking", None)
    else:
        direct.pop("thinking", None)
    # 显式 protocol 优先；缺省时按端点地址（再回落旧 provider 标签）推断。
    if not str(direct.get("protocol") or "").strip():
        direct["protocol"] = infer_direct_protocol(
            legacy_provider,
            api_base=direct.get("api_base"),
            host=direct.get("host"),
        )
    else:
        direct["protocol"] = str(direct.get("protocol") or "").strip().lower()
    llm_model = str(llm.get("model") or "").strip()
    if llm_model and not (
        legacy_http_model_agent
        and str(direct.get("model") or "").strip()
    ):
        direct["model"] = llm_model
    else:
        direct.setdefault("model", "")
    try:
        direct_tokens = int(direct.get("max_tokens"))
        legacy_tokens = int(llm.get("max_tokens", 512))
    except (TypeError, ValueError):
        direct_tokens = legacy_tokens = 0
    if direct_tokens == 512 and legacy_tokens == 512:
        direct["max_tokens"] = 4096
        llm["max_tokens"] = 4096

    # ---- agent 段（原生 WebSocket Gateway） ----
    agent = {} if legacy_http_model_agent else legacy_agent

    raw_kind = str(agent.get("kind") or backend or "").strip().lower()
    raw_base = str(
        agent.get("base_url") or agent.get("bridge_url") or ""
    ).strip()
    if raw_kind not in _AGENT_KINDS:
        raw_kind, kind_basis = infer_agent_kind(raw_base)
        _log.warning(
            f"llm.agent.kind 没写：{kind_basis}，按 {raw_kind} 走"
            "（要钉住请在 llm.agent.kind 写明 hermes/openclaw/agent_link）"
        )
    agent["kind"] = raw_kind

    # 8642 是 Hermes HTTP API Server，原生 WS 由 ``hermes serve`` 默认在
    # 9119 的 /api/ws 提供。精确识别旧本机默认，避免生成不存在的 :8642/ws。
    if raw_kind == "hermes":
        default_url = DEFAULT_HERMES_WS_URL
        lowered = raw_base.lower().rstrip("/")
        if lowered in {
            "http://127.0.0.1:8642",
            "http://localhost:8642",
            DEFAULT_OPENAI_API_BASE,
        }:
            raw_base = default_url
        elif lowered.startswith(("http://", "https://")):
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit(raw_base)
            scheme = "wss" if parsed.scheme.lower() == "https" else "ws"
            path = parsed.path.rstrip("/")
            if not path:
                path = "/api/ws"
            elif not path.endswith("/api/ws"):
                path = f"{path}/api/ws"
            raw_base = urlunsplit((scheme, parsed.netloc, path, "", ""))
        elif not lowered.startswith(("ws://", "wss://")):
            raw_base = default_url
    elif raw_kind == "openclaw":
        default_url = DEFAULT_OPENCLAW_WS_URL
        if not raw_base.lower().startswith(("ws://", "wss://")):
            raw_base = default_url
    else:
        default_url = DEFAULT_AGENT_LINK_WS_URL
        if not raw_base.lower().startswith(("ws://", "wss://")):
            raw_base = default_url
    agent["base_url"] = raw_base or default_url

    auth_token = str(
        agent.get("auth_token")
        or agent.get("api_key")
        or (llm.get("api_key") if requested_mode == "agent" else "")
        or ""
    ).strip()
    agent["auth_token"] = auth_token
    agent["device_id"] = str(agent.get("device_id") or "").strip()
    agent["session_id"] = str(agent.get("session_id") or "").strip()
    agent["session_key"] = str(agent.get("session_key") or "").strip()
    agent["extensions"] = (
        copy.deepcopy(agent.get("extensions"))
        if isinstance(agent.get("extensions"), dict)
        else {}
    )
    remote_session_id = str(
        agent.get("remote_session_id") or ""
    ).strip()
    if remote_session_id:
        agent["remote_session_id"] = remote_session_id
    else:
        agent.pop("remote_session_id", None)
    agent["model"] = str(
        agent.get("model")
        or (llm.get("model") if requested_mode == "agent" else "")
        or ""
    ).strip()
    try:
        timeout_seconds = float(agent.get("timeout_seconds", DEFAULT_AGENT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_AGENT_TIMEOUT_SECONDS
    agent["timeout_seconds"] = (
        timeout_seconds if timeout_seconds > 0 else DEFAULT_AGENT_TIMEOUT_SECONDS
    )
    try:
        history_turns = int(agent.get("history_turns", 5))
    except (TypeError, ValueError):
        history_turns = 5
    agent["history_turns"] = max(0, min(history_turns, 100))
    agent["allow_insecure_ws"] = bool(
        agent.get("allow_insecure_ws", False)
        or agent.get("allow_insecure_http", False)
    )
    identity_path = str(agent.get("identity_path") or "").strip()
    if identity_path:
        agent["identity_path"] = identity_path
    else:
        agent.pop("identity_path", None)

    # TLS
    tls = copy.deepcopy(agent.get("tls")) if isinstance(agent.get("tls"), dict) else {}
    tls.setdefault("verify", True)
    tls.setdefault("ca_file", "")
    agent["tls"] = tls

    # HTTP LLM 参数只属于 direct；这里仅迁移旧 api_key 到 auth_token。
    for legacy_key in (
        "api_key",
        "temperature",
        "max_tokens",
        "allow_insecure_http",
        "bridge_url",
    ):
        agent.pop(legacy_key, None)

    llm["mode"] = requested_mode
    llm["direct"] = direct
    llm["agent"] = agent
    # 顶层 backend 只作兼容标签；实际选择以 mode + 子段为准。
    if requested_mode == "direct":
        llm["backend"] = "custom"
    else:
        llm["backend"] = raw_kind
    return llm


def _normalize_agent_control(value: object) -> dict:
    control = copy.deepcopy(value) if isinstance(value, dict) else {}
    for key, default in DEFAULT_AGENT_CONTROL.items():
        control.setdefault(key, default)
    control["enabled"] = bool(control.get("enabled", False))
    control["allow_insecure_http"] = bool(
        control.get("allow_insecure_http", False)
    )
    control["listen_host"] = (
        str(control.get("listen_host") or "127.0.0.1").strip() or "127.0.0.1"
    )
    control["allowed_agent_ip"] = (
        str(control.get("allowed_agent_ip") or "127.0.0.1").strip()
        or "127.0.0.1"
    )
    try:
        port = int(control.get("port", 8765))
    except (TypeError, ValueError):
        port = 8765
    control["port"] = port if 1 <= port <= 65535 else 8765
    for key in ("auth_token", "cert_file", "key_file", "ca_file"):
        control[key] = str(control.get(key) or "").strip()
    return control


def _normalize_onebot(value: object) -> dict:
    """规范化 OneBot 段；schema 与默认值由 ``meapet.onebot.config`` 提供。"""
    from meapet.onebot.config import normalize_onebot_config

    return normalize_onebot_config(value)


def _clamp_ratio(value: object, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:  # NaN
        number = default
    return max(lo, min(hi, number))


def normalize_live2d_window_mask(value: object) -> dict:
    """规范化 Live2D 视觉视口的历史椭圆参数（比例 0–1）。"""
    raw = value if isinstance(value, dict) else {}
    defaults = DEFAULT_LIVE2D_WINDOW_MASK
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "cx": _clamp_ratio(raw.get("cx", defaults["cx"]), defaults["cx"], 0.05, 0.95),
        "cy": _clamp_ratio(raw.get("cy", defaults["cy"]), defaults["cy"], 0.05, 0.95),
        "rw": _clamp_ratio(raw.get("rw", defaults["rw"]), defaults["rw"], 0.10, 0.55),
        "rh": _clamp_ratio(raw.get("rh", defaults["rh"]), defaults["rh"], 0.10, 0.55),
    }


def normalize_live2d_placement_anchor(
    value: object,
    window_mask: object = None,
) -> dict:
    """规范化模型站立锚点（完整 Live2D 画布归一化坐标）。

    历史配置没有锚点：启用裁剪时迁移为当前视觉视口的底部中心；关闭
    裁剪时使用完整画布底部中心。这样升级本身不会改变桌宠位置。
    """
    mask = normalize_live2d_window_mask(window_mask)
    if mask["enabled"]:
        fallback_x = float(mask["cx"])
        fallback_y = min(1.0, float(mask["cy"]) + float(mask["rh"]))
    else:
        fallback_x = 0.5
        fallback_y = 1.0

    raw = value if isinstance(value, dict) else {}
    return {
        "x": round(
            _clamp_ratio(raw.get("x", fallback_x), fallback_x, 0.0, 1.0),
            6,
        ),
        "y": round(
            _clamp_ratio(raw.get("y", fallback_y), fallback_y, 0.0, 1.0),
            6,
        ),
    }


MAX_LIVE2D_WINDOW_SHAPE_CONTOURS = 32
MAX_LIVE2D_WINDOW_SHAPE_POINTS = 128


def normalize_live2d_window_shape(value: object) -> dict:
    """规范化静态 Live2D 窗口多边形，限制复杂度以保护 GUI 性能。"""
    raw = value if isinstance(value, dict) else {}
    raw_contours = raw.get("contours")
    if not isinstance(raw_contours, (list, tuple)):
        raw_contours = ()

    contours = []
    for raw_contour in raw_contours:
        if len(contours) >= MAX_LIVE2D_WINDOW_SHAPE_CONTOURS:
            break
        if not isinstance(raw_contour, dict):
            continue
        operation = str(raw_contour.get("operation") or "add").strip().lower()
        if operation not in ("add", "subtract"):
            continue
        raw_points = raw_contour.get("points")
        if not isinstance(raw_points, (list, tuple)):
            continue

        points = []
        for raw_point in raw_points:
            if len(points) >= MAX_LIVE2D_WINDOW_SHAPE_POINTS:
                break
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                continue
            try:
                x = float(raw_point[0])
                y = float(raw_point[1])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(x) or not math.isfinite(y):
                continue
            point = [
                round(max(0.0, min(1.0, x)), 6),
                round(max(0.0, min(1.0, y)), 6),
            ]
            if not points or point != points[-1]:
                points.append(point)

        if len(points) >= 2 and points[0] == points[-1]:
            points.pop()
        if len({tuple(point) for point in points}) < 3:
            continue
        contours.append({"operation": operation, "points": points})

    return {
        "enabled": bool(
            raw.get("enabled", DEFAULT_LIVE2D_WINDOW_SHAPE["enabled"])
        ),
        "contours": contours,
    }


def _normalize_reference_audios(tts: dict) -> dict:
    """规范化每语言固定参考音频，并只读迁移旧单条 GSV 配置。"""
    raw_mapping = tts.get("reference_audios")
    mapping = {}
    if isinstance(raw_mapping, dict):
        for raw_language, raw_entry in raw_mapping.items():
            language = normalize_gsv_ref_language(raw_language)
            if isinstance(raw_entry, dict):
                path = str(raw_entry.get("path") or "").strip()
                text = str(raw_entry.get("text") or "").strip()
            else:
                path = str(raw_entry or "").strip()
                text = ""
            if path or text:
                mapping[language] = {"path": path, "text": text}

    legacy_path = str(tts.get("gsv_ref_wav") or "").strip()
    legacy_language = normalize_gsv_ref_language(tts.get("gsv_ref_lang"))
    if legacy_path and legacy_language not in mapping:
        mapping[legacy_language] = {"path": legacy_path, "text": ""}
    return mapping


def normalize_config(config: dict) -> dict:
    """补全默认字段、规范化 watcher / bubble / display / tts.sync"""
    cfg = copy.deepcopy(config or {})

    cfg["llm"] = _normalize_llm_contract(cfg.get("llm"))
    cfg.setdefault("vision", {})
    cfg.setdefault("tts", {})
    cfg.setdefault("display", {})
    cfg.setdefault("character", {})
    live2d = cfg.get("live2d") if isinstance(cfg.get("live2d"), dict) else {}
    live2d["window_mask"] = normalize_live2d_window_mask(live2d.get("window_mask"))
    live2d["placement_anchor"] = normalize_live2d_placement_anchor(
        live2d.get("placement_anchor"),
        live2d["window_mask"],
    )
    live2d["window_shape"] = normalize_live2d_window_shape(
        live2d.get("window_shape")
    )
    cfg["live2d"] = live2d
    cfg["agent_control"] = _normalize_agent_control(cfg.get("agent_control"))
    cfg["onebot"] = _normalize_onebot(cfg.get("onebot"))

    # bubble
    bub = cfg.get("bubble_duration_ms") if isinstance(cfg.get("bubble_duration_ms"), dict) else {}
    for k, v in DEFAULT_BUBBLE.items():
        bub.setdefault(k, v)
    cfg["bubble_duration_ms"] = bub

    # display
    disp = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
    disp.setdefault("scale", 0.5)
    disp.setdefault("fps", 30)
    disp["size_factor"] = normalize_pet_size_factor(
        disp.get("size_factor", 1.0)
    )
    disp["font_scale"] = normalize_ui_font_scale(
        disp.get("font_scale", 1.0)
    )
    disp["reduced_motion"] = bool(disp.get("reduced_motion", False))
    cfg["display"] = disp

    # UI 一次性引导等非敏感本地状态
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    ui["first_run_hint_shown"] = bool(ui.get("first_run_hint_shown", False))
    try:
        timeline_turns = int(ui.get("timeline_turns", 5))
    except (TypeError, ValueError):
        timeline_turns = 5
    ui["timeline_turns"] = max(0, min(timeline_turns, 100))
    ui["theme"] = normalize_theme_mode(ui.get("theme"))
    ui["text_color"] = normalize_text_color(ui.get("text_color"))
    cfg["ui"] = ui

    # TTS：有音频时气泡始终晚于播放结束；旧开关仅保留配置兼容。
    tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    tts["sync_with_audio"] = True
    tts["gsv_ref_wav"] = str(tts.get("gsv_ref_wav") or "").strip()
    tts["gsv_ref_lang"] = normalize_gsv_ref_language(
        tts.get("gsv_ref_lang")
    )
    tts["reference_audios"] = _normalize_reference_audios(tts)
    tts["translate_to_jp"] = bool(tts.get("translate_to_jp", False))
    tts["translate_target_language"] = canonical_tts_language(
        tts.get("translate_target_language")
        or tts.get("voice_lang")
        or "jp"
    )
    tts["prefer_model_voice_translation"] = bool(
        tts.get("prefer_model_voice_translation", True)
    )
    raw_supported = tts.get("supported_languages")
    if isinstance(raw_supported, (list, tuple)):
        supported = []
        for value in raw_supported:
            language = canonical_tts_language(value)
            if language and language not in supported:
                supported.append(language)
        tts["supported_languages"] = supported
    else:
        tts.pop("supported_languages", None)
    # P1-4 轻量 ONNX 引擎：与 vits 那几格同纪律——空串表示"没配"，
    # 数值收敛到引擎接受的区间，非法值退回默认而不是把引擎搞成拒绝加载。
    tts["onnx_model_dir"] = str(tts.get("onnx_model_dir") or "").strip()
    tts["onnx_speaker"] = clamp_int(tts.get("onnx_speaker"), 0, 0, 1000)
    tts["onnx_speed"] = clamp_float(tts.get("onnx_speed"), 1.0, 0.3, 3.0)
    tts["onnx_num_threads"] = clamp_int(tts.get("onnx_num_threads"), 2, 1, 16)
    cfg["tts"] = tts

    # watcher 统一结构（interval 内嵌，不再用顶层 watcher_interval）
    w_in = cfg.get("watcher") if isinstance(cfg.get("watcher"), dict) else {}
    if "interval" not in w_in or not isinstance(w_in.get("interval"), dict):
        top_wi = cfg.get("watcher_interval") if isinstance(cfg.get("watcher_interval"), dict) else {}
        if top_wi:
            w_in = dict(w_in)
            w_in["interval"] = {
                "min_ms": int(top_wi.get("min_ms", DEFAULT_WATCHER_INTERVAL["min_ms"])),
                "max_ms": int(top_wi.get("max_ms", DEFAULT_WATCHER_INTERVAL["max_ms"])),
            }
    w = normalize_watcher(w_in)
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
    raw_capture = (
        watcher_out.get("capture")
        if isinstance(watcher_out.get("capture"), dict)
        else {}
    )
    scope = str(raw_capture.get("scope") or "full_screen").strip().lower()
    if scope not in {"full_screen", "region", "application"}:
        scope = "full_screen"
    region = raw_capture.get("region")
    normalized_region = None
    if isinstance(region, dict):
        try:
            candidate = {
                key: int(region[key])
                for key in ("x", "y", "width", "height")
            }
            if candidate["width"] > 0 and candidate["height"] > 0:
                normalized_region = candidate
        except (KeyError, TypeError, ValueError):
            normalized_region = None
    if scope == "region" and normalized_region is None:
        scope = "full_screen"
    application = str(raw_capture.get("application") or "").strip()[:256]
    if scope == "application" and not application:
        scope = "full_screen"
    watcher_out["capture"] = {
        "scope": scope,
        "region": normalized_region if scope == "region" else None,
        "application": application if scope == "application" else "",
    }

    vision = (
        copy.deepcopy(cfg.get("vision"))
        if isinstance(cfg.get("vision"), dict)
        else {}
    )
    if "mode" in vision:
        vision_mode = normalize_vision_mode(vision.get("mode"))
    else:
        legacy_enabled = bool(
            vision.get("enabled", watcher_out.get("enabled", False))
        )
        vision_mode = "relay" if legacy_enabled else "disabled"
    vision["mode"] = vision_mode
    vision["enabled"] = vision_mode != "disabled"
    vision["main_model_supports_images"] = bool(
        vision.get("main_model_supports_images", False)
    )
    if vision_mode == "disabled":
        watcher_out["enabled"] = False
    cfg["vision"] = vision
    cfg["watcher"] = watcher_out

    # voice_input 规范化（默认关闭）
    voice = (
        copy.deepcopy(cfg.get("voice_input"))
        if isinstance(cfg.get("voice_input"), dict)
        else {}
    )
    # faster_whisper 时代留下的键，切到 sherpa-onnx 后无人读取，读到即清
    for dead_key in ("engine", "model", "device"):
        voice.pop(dead_key, None)
    voice.setdefault("enabled", False)
    voice.setdefault("language", "zh")
    voice.setdefault("auto_send", False)
    # 档位词汇表在 meapet.paths 的单一真值表里，这里只把非法值收敛到默认档；
    # 设备号 None = 自动选路，非负整数 = 用户点名的那台。
    voice["precision"] = normalize_voice_asr_precision(voice.get("precision"))
    voice["input_device_index"] = normalize_input_device_index(
        voice.get("input_device_index")
    )
    # P1-3 全双工。模式默认点按切换（老行为）；唤醒词默认关闭——常驻监听必须
    # 是用户单独的一次显式点头（隐私档位与 watcher 同档），不与 enabled 捆绑。
    voice["mode"] = normalize_voice_mode(voice.get("mode"))
    voice["ptt_hotkey"] = str(voice.get("ptt_hotkey") or "").strip()
    vad_cfg = voice.get("vad") if isinstance(voice.get("vad"), dict) else {}
    voice["vad"] = {
        "threshold": clamp_float(vad_cfg.get("threshold"), 0.5, 0.0, 1.0),
        "min_silence_duration": clamp_float(
            vad_cfg.get("min_silence_duration"), 0.5, 0.05, 5.0
        ),
        "min_speech_duration": clamp_float(
            vad_cfg.get("min_speech_duration"), 0.25, 0.05, 5.0
        ),
        "max_speech_duration": clamp_float(
            vad_cfg.get("max_speech_duration"), 20.0, 1.0, 120.0
        ),
    }
    wake = voice.get("wake_word") if isinstance(voice.get("wake_word"), dict) else {}
    voice["wake_word"] = {
        "enabled": bool(wake.get("enabled", False)),
        "keywords_file": str(wake.get("keywords_file") or "").strip(),
    }
    voice["echo_gate_tail_ms"] = clamp_int(
        voice.get("echo_gate_tail_ms"), 300, 0, 5000
    )
    cfg["voice_input"] = voice

    # fidus 定位规范化（默认关闭：代价可见——秒级校准 + 屏幕闪现）
    fidus = (
        copy.deepcopy(cfg.get("fidus"))
        if isinstance(cfg.get("fidus"), dict)
        else {}
    )
    fidus.setdefault("enabled", False)
    # 测量前请先按 Mod+V 转浮动：平铺下窗口按合成器预设出现，尺寸请求会被吃
    # （人工现测 2026-10-02），交互态量出来的四向空档因此不可信。默认问，勾「不再提示」后不问。
    fidus.setdefault("ask_float", True)
    cfg["fidus"] = fidus

    # 点击反馈：预制语音之外那两个走网的新源，默认关（隐私档位与屏幕观察同档：
    # 默认关 ＋ 用户在设置里启用那一刻弹一次告知）。
    click_feedback = (
        copy.deepcopy(cfg.get("click_feedback"))
        if isinstance(cfg.get("click_feedback"), dict)
        else {}
    )
    crawler = (
        copy.deepcopy(click_feedback.get("crawler"))
        if isinstance(click_feedback.get("crawler"), dict)
        else {}
    )
    crawler["enabled"] = bool(
        crawler.get("enabled", DEFAULT_CLICK_FEEDBACK_CRAWLER["enabled"])
    )
    crawler["keywords"] = normalize_feedback_keywords(
        crawler.get("keywords", DEFAULT_CLICK_FEEDBACK_CRAWLER["keywords"])
    )
    crawler["api_key"] = str(
        crawler.get("api_key", DEFAULT_CLICK_FEEDBACK_CRAWLER["api_key"]) or ""
    ).strip()
    crawler["privacy_acknowledged"] = bool(
        crawler.get(
            "privacy_acknowledged",
            DEFAULT_CLICK_FEEDBACK_CRAWLER["privacy_acknowledged"],
        )
    )
    poetry = (
        copy.deepcopy(click_feedback.get("poetry"))
        if isinstance(click_feedback.get("poetry"), dict)
        else {}
    )
    poetry["enabled"] = bool(
        poetry.get("enabled", DEFAULT_CLICK_FEEDBACK_POETRY["enabled"])
    )
    # 源词汇表只有一个真值（defaults 里那两枚名字）；表外的名字直接丢掉，不给"配了但不认"的悬空态
    poetry["sources"] = normalize_poetry_sources(
        poetry.get("sources", DEFAULT_CLICK_FEEDBACK_POETRY["sources"]),
        DEFAULT_CLICK_FEEDBACK_POETRY_SOURCES,
    )
    poetry["keywords"] = normalize_feedback_keywords(
        poetry.get("keywords", DEFAULT_CLICK_FEEDBACK_POETRY["keywords"])
    )
    # 权重只住在 weights 那一格：poetry 里若出现同名键一律丢掉，不给它第二处真值的机会
    poetry.pop("weights", None)
    poetry["privacy_acknowledged"] = bool(
        poetry.get(
            "privacy_acknowledged",
            DEFAULT_CLICK_FEEDBACK_POETRY["privacy_acknowledged"],
        )
    )
    weights = (
        copy.deepcopy(click_feedback.get("weights"))
        if isinstance(click_feedback.get("weights"), dict)
        else {}
    )
    for weight_key in ("preset", "crawler", "poetry"):
        weights[weight_key] = normalize_click_feedback_weight(
            weights.get(weight_key, DEFAULT_CLICK_FEEDBACK_WEIGHTS[weight_key]),
            default=int(DEFAULT_CLICK_FEEDBACK_WEIGHTS[weight_key]),
        )
    click_feedback["crawler"] = crawler
    click_feedback["poetry"] = poetry
    click_feedback["weights"] = weights
    cfg["click_feedback"] = click_feedback

    # ---------- audio 音量规范化（0-100%） ----------
    audio = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
    audio.setdefault("sfx_volume_percent", 80)
    audio.setdefault("tts_volume_percent", 90)
    try:
        audio["sfx_volume_percent"] = max(0, min(100, int(audio["sfx_volume_percent"])))
    except (TypeError, ValueError):
        audio["sfx_volume_percent"] = 80
    try:
        audio["tts_volume_percent"] = max(0, min(100, int(audio["tts_volume_percent"])))
    except (TypeError, ValueError):
        audio["tts_volume_percent"] = 90
    cfg["audio"] = audio


    return cfg


def load_config(path: Optional[str] = None) -> dict:
    """加载统一 config.json 并补全默认字段。"""
    cpath = path or config_path()
    return normalize_config(load_json(cpath, {}))


def scrub_secrets(config: dict) -> dict:
    out = copy.deepcopy(config or {})
    if "llm" in out and isinstance(out["llm"], dict):
        out["llm"]["api_key"] = ""
        direct = out["llm"].get("direct")
        if isinstance(direct, dict):
            direct["api_key"] = ""
        agent = out["llm"].get("agent")
        if isinstance(agent, dict):
            # 旧导出结构仍可能包含 agent.api_key；保留字段形状但清空，
            # 避免兼容读取方因键缺失失败，同时不会泄露已迁移的凭据。
            if "api_key" in agent:
                agent["api_key"] = ""
            agent["auth_token"] = ""
    if "tts" in out and isinstance(out["tts"], dict):
        out["tts"]["api_key"] = ""
        out["tts"]["translate_api_key"] = ""
    if "vision" in out and isinstance(out["vision"], dict):
        out["vision"]["api_key"] = ""
    if "agent_control" in out and isinstance(out["agent_control"], dict):
        out["agent_control"]["auth_token"] = ""
    voice = out.get("voice_input")
    if isinstance(voice, dict):
        # whisper 时代可能留过键的用户配置：导出时整键去掉，不再注入一个空字段
        voice.pop("api_key", None)
    click_feedback = out.get("click_feedback")
    if isinstance(click_feedback, dict):
        crawler = click_feedback.get("crawler")
        if isinstance(crawler, dict):
            crawler["api_key"] = ""
    if "onebot" in out and isinstance(out["onebot"], dict):
        onebot = out["onebot"]
        for section in ("forward_ws", "reverse_ws", "http_api", "http_post", "hub"):
            block = onebot.get(section)
            if not isinstance(block, dict):
                continue
            for key in ("access_token", "push_token"):
                if key in block:
                    block[key] = ""
    return out


def secret_status(config: dict) -> Dict[str, str]:
    llm = config.get("llm") or {}
    tts = config.get("tts") or {}
    vision = config.get("vision") or {}
    llm_key = resolve_llm_api_key(llm)
    tts_key = resolve_tts_api_key(tts, llm)
    tr_key = resolve_translate_api_key(tts, llm)
    vis_key = resolve_vision_api_key(vision, llm)
    tavily_key = resolve_click_feedback_crawler_key(config)

    def src(file_val: str, resolved: str, envs: Tuple[str, ...]) -> str:
        if not resolved:
            return "missing"
        env_hit = _first_env(envs)
        if env_hit and resolved == env_hit:
            return "env:" + ",".join(envs[:2])
        if (file_val or "").strip():
            return "file"
        return "unknown"

    crawler = ((config.get("click_feedback") or {}).get("crawler")) or {}
    return {
        "llm": src(llm.get("api_key", ""), llm_key, ENV_LLM_KEY),
        "tts": src(tts.get("api_key", ""), tts_key, ENV_TTS_KEY),
        "translate": src(tts.get("translate_api_key", ""), tr_key, ENV_TRANSLATE_KEY),
        "vision": src(vision.get("api_key", ""), vis_key, ENV_VISION_KEY),
        "tavily": src(
            crawler.get("api_key", "") if isinstance(crawler, dict) else "",
            tavily_key,
            ENV_TAVILY_KEY,
        ),
        "llm_preview": mask_secret(llm_key) if llm_key else "",
    }
