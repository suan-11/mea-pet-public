"""In-process VITS inference (no external Python subprocess).

Used by the frozen onedir build and when ``tts.vits_inprocess`` is enabled.
Heavy deps (torch / vits_core) are imported lazily on first synthesis call.

This module is also imported by the external ``vits_infer.py`` CLI. Keep its
import graph free of MeaTTS / Qt / heavy desktop code.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Optional

from meapet.paths import project_path
from meapet.log import get_color_logger

log = get_color_logger("tts")

_LOCK = threading.RLock()
_MODEL_CACHE: dict[str, Any] = {}
_CORE_READY = False


def _ensure_openjtalk_dict() -> str:
    """Point OPEN_JTALK_DICT_DIR at the bundled dictionary when present."""
    builtin = project_path("dic", "open_jtalk_dic_utf_8-1.11")
    if os.path.isdir(builtin):
        os.environ["OPEN_JTALK_DICT_DIR"] = builtin
        return builtin
    return os.environ.get("OPEN_JTALK_DICT_DIR", "")


def _ensure_vits_core_on_path() -> str:
    core = project_path("vits_core")
    if os.path.isdir(core) and core not in sys.path:
        sys.path.insert(0, core)
    return core


def _prepare_torch_dll_search() -> None:
    """Help Windows load torch native DLLs inside a PyInstaller onedir tree.

    WinError 1114 on ``c10.dll`` is commonly caused by the loader not searching
    ``torch/lib`` (and sibling native dirs) when the app is frozen.
    """
    if os.name != "nt":
        return
    candidates: list[str] = []

    # Frozen layout: <_MEIPASS>/torch/lib — do this BEFORE importing torch.
    meipass = getattr(sys, "_MEIPASS", "") or ""
    if meipass:
        candidates.extend(
            [
                os.path.join(meipass, "torch", "lib"),
                os.path.join(meipass, "torch"),
                meipass,
            ]
        )
    # Source / site-packages style
    for entry in list(sys.path):
        if not entry:
            continue
        candidates.append(os.path.join(entry, "torch", "lib"))

    seen: set[str] = set()
    path_prefix: list[str] = []
    for raw in candidates:
        lib_dir = os.path.abspath(raw)
        if not lib_dir or lib_dir in seen or not os.path.isdir(lib_dir):
            continue
        seen.add(lib_dir)
        path_prefix.append(lib_dir)
        add_dll = getattr(os, "add_dll_directory", None)
        if callable(add_dll):
            try:
                add_dll(lib_dir)
            except OSError:
                pass
    if path_prefix:
        os.environ["PATH"] = os.pathsep.join(path_prefix + [os.environ.get("PATH", "")])


def _import_runtime():
    """Lazy-import torch and vits_core modules."""
    global _CORE_READY
    _ensure_openjtalk_dict()
    core = _ensure_vits_core_on_path()
    if not os.path.isdir(core):
        raise FileNotFoundError(f"vits_core not found: {core}")

    try:
        import pkg_resources  # noqa: F401
    except ModuleNotFoundError:
        log.warning(
            "setuptools/pkg_resources missing; VITS text frontend may fail. "
            "Install setuptools==69.5.1 in the build environment."
        )

    _prepare_torch_dll_search()
    try:
        import torch
    except OSError as exc:
        # Re-try once after forcing torch/lib onto the DLL path using a
        # filesystem probe (import may have failed before torch.__file__).
        meipass = getattr(sys, "_MEIPASS", "") or ""
        torch_lib = os.path.join(meipass, "torch", "lib") if meipass else ""
        if torch_lib and os.path.isdir(torch_lib):
            add_dll = getattr(os, "add_dll_directory", None)
            if callable(add_dll):
                try:
                    add_dll(torch_lib)
                except OSError:
                    pass
            os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
            import torch
        else:
            raise OSError(
                f"Failed to load bundled torch ({exc}). "
                "On frozen Windows builds, configure tts.vits_python to a real "
                "Python env with torch, or rebuild with a compatible torch wheel."
            ) from exc

    import scipy.io.wavfile as wavf
    from torch import LongTensor, no_grad
    import commons
    from text import text_to_sequence
    from models import SynthesizerTrn
    import utils

    _CORE_READY = True
    return {
        "torch": torch,
        "wavf": wavf,
        "LongTensor": LongTensor,
        "no_grad": no_grad,
        "commons": commons,
        "text_to_sequence": text_to_sequence,
        "SynthesizerTrn": SynthesizerTrn,
        "utils": utils,
        "device": "cuda:0" if torch.cuda.is_available() else "cpu",
    }


def _get_text(rt: dict, text: str, hps, is_symbol: bool = False):
    text_norm = rt["text_to_sequence"](
        text,
        hps.symbols,
        [] if is_symbol else hps.data.text_cleaners,
    )
    if hps.data.add_blank:
        text_norm = rt["commons"].intersperse(text_norm, 0)
    return rt["LongTensor"](text_norm)


def _load_model(rt: dict, model_path: str, config_path: str):
    hps = rt["utils"].get_hparams_from_file(config_path)
    net_g = rt["SynthesizerTrn"](
        len(hps.symbols),
        hps.data.filter_length // 2 + 1,
        hps.train.segment_size // hps.data.hop_length,
        n_speakers=hps.data.n_speakers,
        **hps.model,
    ).to(rt["device"])
    net_g.eval()
    rt["utils"].load_checkpoint(model_path, net_g, None)
    return hps, net_g


def _cache_key(model_path: str, config_path: str) -> str:
    return f"{os.path.abspath(model_path)}::{os.path.abspath(config_path)}"


def get_cached_model(
    model_path: Optional[str] = None,
    config_path: Optional[str] = None,
):
    """Load (or reuse) the VITS model. Returns ``(hps, net_g, rt)`` or ``(None, None, None)``."""
    model_path = model_path or project_path("vits_models", "G_latest.pth")
    config_path = config_path or project_path("vits_models", "finetune_speaker.json")
    if not os.path.isfile(model_path) or not os.path.isfile(config_path):
        return None, None, None

    key = _cache_key(model_path, config_path)
    with _LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        rt = _import_runtime()
        hps, net_g = _load_model(rt, model_path, config_path)
        entry = (hps, net_g, rt)
        _MODEL_CACHE[key] = entry
        return entry


def synthesize_vits(
    text: str,
    output_wav: str,
    *,
    model_path: Optional[str] = None,
    config_path: Optional[str] = None,
    speaker: str = "Mea",
    noise_scale: float = 0.667,
    noise_scale_w: float = 0.6,
    length_scale: float = 1.0,
) -> str:
    """Synthesize *text* to *output_wav* in-process. Returns the output path."""
    model_path = model_path or project_path("vits_models", "G_latest.pth")
    config_path = config_path or project_path("vits_models", "finetune_speaker.json")
    if not text or not str(text).strip():
        raise ValueError("empty VITS text")
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"VITS model missing: {model_path}")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"VITS config missing: {config_path}")

    with _LOCK:
        key = _cache_key(model_path, config_path)
        entry = _MODEL_CACHE.get(key)
        if entry is None:
            rt = _import_runtime()
            hps, net_g = _load_model(rt, model_path, config_path)
            entry = (hps, net_g, rt)
            _MODEL_CACHE[key] = entry
        hps, net_g, rt = entry

        speaker_ids = hps.speakers
        if isinstance(speaker_ids, dict):
            speaker_id = speaker_ids.get(speaker, 0)
        else:
            speaker_id = 0

        stn_tst = _get_text(rt, text, hps, False)
        device = rt["device"]
        LongTensor = rt["LongTensor"]
        with rt["no_grad"]():
            x_tst = stn_tst.unsqueeze(0).to(device)
            x_tst_lengths = LongTensor([stn_tst.size(0)]).to(device)
            sid = LongTensor([speaker_id]).to(device)
            audio = (
                net_g.infer(
                    x_tst,
                    x_tst_lengths,
                    sid=sid,
                    noise_scale=noise_scale,
                    noise_scale_w=noise_scale_w,
                    length_scale=length_scale,
                )[0][0, 0]
                .data.cpu()
                .float()
                .numpy()
            )

        parent = os.path.dirname(output_wav) or "."
        os.makedirs(parent, exist_ok=True)
        rt["wavf"].write(output_wav, hps.data.sampling_rate, audio)
        return output_wav


def clear_model_cache() -> None:
    """Drop cached nets (tests / low-memory)."""
    with _LOCK:
        _MODEL_CACHE.clear()
