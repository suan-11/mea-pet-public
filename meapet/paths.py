"""Project root and path helpers."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# meapet/ is inside project root (or sys._MEIPASS when frozen)
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

_LEGACY_HOME_DATA = Path.home() / ".meapet"
_MIGRATION_DONE = False


def is_frozen() -> bool:
    """True when running under PyInstaller (onedir/onefile)."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def project_root() -> str:
    return str(PROJECT_ROOT)


def project_path(*parts: str) -> str:
    return str(PROJECT_ROOT.joinpath(*parts))


def get_install_dir() -> str:
    """Directory that contains the frozen executable, or the project root."""
    if is_frozen():
        return str(Path(sys.executable).resolve().parent)
    return project_root()


def get_data_dir() -> str:
    """Return a writable directory for runtime data (logs, cache, config saves).

    Source / development mode: ``PROJECT_ROOT``.
    PyInstaller onedir (portable): ``sys._MEIPASS`` (``dist/MeaPet/_internal``),
    so config, memory DB, caches and logs travel with the distribution folder.
    """
    if is_frozen():
        data_dir = Path(sys._MEIPASS)
    else:
        data_dir = PROJECT_ROOT
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(data_dir)


def data_path(*parts: str) -> str:
    """Join path parts under :func:`get_data_dir`."""
    return str(Path(get_data_dir()).joinpath(*parts))


VOICE_ASR_MODEL_REPO = (
    "pkufool/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
)
VOICE_ASR_MODEL_CACHE_NAME = VOICE_ASR_MODEL_REPO.replace("/", "--")

# 单一真值表：档位 → 运行时真正读取的文件。完整性校验与下载 allow_patterns 都从这张表导出，
# 免得出现「下了 fp32 却仍读 int8」这种两处各说各话的第三种状态。
VOICE_ASR_MODEL_FILES = {
    "int8": {
        "encoder": "encoder-epoch-99-avg-1.int8.onnx",
        "decoder": "decoder-epoch-99-avg-1.int8.onnx",
        "joiner": "joiner-epoch-99-avg-1.int8.onnx",
        "tokens": "tokens.txt",
    },
    "fp32": {
        "encoder": "encoder-epoch-99-avg-1.onnx",
        "decoder": "decoder-epoch-99-avg-1.onnx",
        "joiner": "joiner-epoch-99-avg-1.onnx",
        "tokens": "tokens.txt",
    },
}
VOICE_ASR_PRECISION_DEFAULT = "int8"
VOICE_ASR_REQUIRED_FILES_BY_PRECISION = {
    precision: tuple(files.values())
    for precision, files in VOICE_ASR_MODEL_FILES.items()
}
VOICE_ASR_REQUIRED_FILES = VOICE_ASR_REQUIRED_FILES_BY_PRECISION[
    VOICE_ASR_PRECISION_DEFAULT
]


def voice_asr_cache_dir() -> Path:
    """Return the writable cache root for the optional ASR model."""
    return Path(data_path("voice_asr"))


def normalize_voice_asr_precision(precision: object) -> str:
    """把任意配置值收敛到已知档位，未知一律按默认档。"""
    key = str(precision or "").strip().lower()
    return key if key in VOICE_ASR_MODEL_FILES else VOICE_ASR_PRECISION_DEFAULT


def find_voice_asr_model_dir(precision: object = None) -> Path | None:
    """Find a complete ASR model for one precision (new cache or legacy path)."""
    required = VOICE_ASR_REQUIRED_FILES_BY_PRECISION[
        normalize_voice_asr_precision(precision)
    ]
    roots: list[Path] = []
    for root in (
        voice_asr_cache_dir(),
        Path(project_path("models", "voice_asr")),
    ):
        if root not in roots:
            roots.append(root)

    def complete(directory: Path) -> bool:
        return directory.is_dir() and all(
            (directory / name).is_file() for name in required
        )

    for root in roots:
        expected = (
            root
            / "models"
            / VOICE_ASR_MODEL_CACHE_NAME
            / "snapshots"
            / "master"
        )
        if complete(expected):
            return expected
        if complete(root):
            return root
        if not root.is_dir():
            continue
        try:
            for marker in root.rglob("tokens.txt"):
                if complete(marker.parent):
                    return marker.parent
        except OSError:
            continue
    return None


# --- P1-3：Silero VAD（自动断句）与 KWS（唤醒词）的模型定位 ----------------------
# 与 voice_asr 同纪律：文件名是单一真值，下载侧（向导）与运行侧（vad.py/kws.py）
# 读同一张表；目录分开放（voice_vad/、voice_kws/），各管各的取件与缺席判定。

VOICE_VAD_MODEL_FILE = "silero_vad.onnx"
#: sherpa-onnx 官方 Release 资产地址（下载侧用它；运行侧只认盘上的文件）。
VOICE_VAD_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
)


def voice_vad_cache_dir() -> Path:
    """Silero VAD 的缓存根（voice_vad/），与 voice_asr/ 同级。"""
    return Path(data_path("voice_vad"))


def find_voice_vad_model() -> Path | None:
    """找盘上的 Silero VAD 模型，命中返回文件路径；只报在场/缺席，不验内容。

    两处按优先级找（新缓存 → 工程内 models/voice_vad）；半成品子目录里的同名
    文件也认——下载器可能把整包解到子目录，与 find_voice_asr_model_dir 同姿势。
    """
    for root in (
        voice_vad_cache_dir(),
        Path(project_path("models", "voice_vad")),
    ):
        if not root.is_dir():
            continue
        candidate = root / VOICE_VAD_MODEL_FILE
        if candidate.is_file():
            return candidate
        try:
            for marker in root.rglob(VOICE_VAD_MODEL_FILE):
                if marker.is_file():
                    return marker
        except OSError:
            continue
    return None


#: KWS 模型 zipformer（中英混合，~3.3M 参数）；文件名用前缀匹配——该模型的
#: 版本号刻在文件名里，钉死全名会在模型换版时造成「下了却找不到」的假缺席。
VOICE_KWS_MODEL_REPO = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile"
VOICE_KWS_PREFIXES = {
    "encoder": ("encoder-",),
    "decoder": ("decoder-",),
    "joiner": ("joiner-",),
}


def voice_kws_cache_dir() -> Path:
    """KWS 模型的缓存根（voice_kws/）。"""
    return Path(data_path("voice_kws"))


def _kws_variant_key(filename: str) -> str:
    """把 onnx 文件名归约到「同一套模型」的键：`<epoch>-<avg 段>`。

    上游一个目录里常常并排放**好几套**（实测 wenetspeech 官方包里同时有
    `epoch-99-avg-1` 与 `epoch-12-avg-2`，各有 int8/非 int8 两档）。按件挑
    会撞出「encoder 用 99、decoder 用 12」这种混搭——加载要么直接失败，
    要么识别结果整个错乱，而报错信息不会指向真正的原因。

    所以判据不是"哪个文件先被遍历到"，而是"它们是不是同一套"：
    去掉前缀（encoder-/decoder-/joiner-）、去掉精度后缀（.int8.onnx/.onnx），
    剩下的那段就是这个变体的身份。
    """
    stem = filename
    for prefix in ("encoder-", "decoder-", "joiner-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    for suffix in (".int8.onnx", ".onnx"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _kws_prefer_int8(candidates: list) -> list:
    """同一变体有 int8 与非 int8 两档时优先 int8——与 ASR 那一路的默认档一致，
    也是向导文案承诺的体积档。"""
    int8 = [p for p in candidates if p.name.endswith(".int8.onnx")]
    return int8 or candidates


def find_voice_kws_files() -> dict | None:
    """找**成套**的 KWS 模型文件，命中返回 {encoder, decoder, joiner, tokens[, keywords]}。

    keywords 是用户词表（keywords.txt）：模型目录里没有就不塞进结果——词表缺席
    由 kws.build_keyword_spotter 单独报「词表缺失」，和模型缺失分开处置。

    成套判据见 `_kws_variant_key`：encoder/decoder/joiner 必须同属一个变体，
    不接受"三件都找到了"这种按件抽查式的合格。同一变体内优先 int8。
    """
    for root in (
        voice_kws_cache_dir(),
        Path(project_path("models", "voice_kws")),
    ):
        if not root.is_dir():
            continue
        candidates = [root]
        try:
            candidates.extend(sorted(p for p in root.iterdir() if p.is_dir()))
        except OSError:
            continue
        for directory in candidates:
            try:
                entries = [p for p in directory.iterdir() if p.is_file()]
            except OSError:
                continue
            tokens = next(
                (str(e) for e in entries if e.name == "tokens.txt"), None
            )
            if tokens is None:
                continue

            by_variant: dict = {}
            for entry in entries:
                prefix = next(
                    (
                        key
                        for key, prefixes in VOICE_KWS_PREFIXES.items()
                        if entry.suffix == ".onnx" and entry.name.startswith(prefixes)
                    ),
                    None,
                )
                if prefix is None:
                    continue
                by_variant.setdefault(_kws_variant_key(entry.name), {}).setdefault(
                    prefix, []
                ).append(entry)

            for variant in sorted(by_variant):
                parts = by_variant[variant]
                if not all(k in parts for k in ("encoder", "decoder", "joiner")):
                    continue
                found = {"tokens": tokens}
                for key in ("encoder", "decoder", "joiner"):
                    chosen = _kws_prefer_int8(sorted(parts[key]))
                    found[key] = str(chosen[0])
                keywords = next(
                    (str(e) for e in entries if e.name == "keywords.txt"), None
                )
                if keywords is not None:
                    found["keywords"] = keywords
                return found
    return None


def migrate_legacy_home_data() -> list[str]:
    """Copy files from legacy ``~/.meapet`` into the portable data dir once.

    Only fills missing targets so a newer ``_internal`` copy is never overwritten.
    Returns a list of human-readable migration notes (empty if nothing moved).
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE or not is_frozen():
        return []
    _MIGRATION_DONE = True

    if not _LEGACY_HOME_DATA.is_dir():
        return []

    dest_root = Path(get_data_dir())
    notes: list[str] = []
    candidates = (
        "config.json",
        "openclaw_device_identity.json",
        "mea_memory.db",
        "mea_memory.db-wal",
        "mea_memory.db-shm",
        "chat_errors.log",
        "meapet_boot.log",
        "meapet_fault.log",
    )
    for name in candidates:
        src = _LEGACY_HOME_DATA / name
        dest = dest_root / name
        if not src.is_file() or dest.exists():
            continue
        try:
            shutil.copy2(src, dest)
            notes.append(f"migrated {name} from ~/.meapet")
        except OSError:
            continue

    src_logs = _LEGACY_HOME_DATA / "logs"
    dest_logs = dest_root / "logs"
    if src_logs.is_dir():
        try:
            dest_logs.mkdir(parents=True, exist_ok=True)
            for item in src_logs.iterdir():
                if not item.is_file():
                    continue
                target = dest_logs / item.name
                if target.exists():
                    continue
                shutil.copy2(item, target)
                notes.append(f"migrated logs/{item.name} from ~/.meapet")
        except OSError:
            pass
    return notes
