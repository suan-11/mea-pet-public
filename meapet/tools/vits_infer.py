"""
VITS Fast Fine-Tuning 推理脚本（独立版，可被外部 Python 子进程直接运行）

打包版里 meapet 主体在 PYZ 中，外部解释器（如 vits_ft）只能看到 datas
落盘的本脚本 + vits_core / vits_models / dic。因此本文件不得 import meapet.*。

进程内推理走 meapet.tts.engines.vits_runtime（MeaPet 进程内）。
用法: python vits_infer.py --text "[JA]こんにちわ[JA]" --output output.wav
"""
from __future__ import annotations

import argparse
import os
import sys


def _bootstrap() -> str:
    """Locate project / _MEIPASS root and put *only* vits_core on sys.path.

    Critical: never insert the whole ``_internal`` tree onto ``sys.path``.
    PyInstaller onedir ships many ``*.pyd`` / ``python3xx.dll`` there; an
    external conda Python (e.g. 3.8) would then load the wrong native modules
    and crash with ``Module use of python313.dll conflicts with this version``.
    """
    # meapet/tools/vits_infer.py -> parents[2] = project root or _internal
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Drop accidental MeaPet/_internal entries that parent may have injected
    # via PYTHONPATH when spawning the subprocess.
    cleaned: list[str] = []
    for entry in list(sys.path):
        if not entry:
            cleaned.append(entry)
            continue
        norm = os.path.normcase(os.path.abspath(entry))
        base_norm = os.path.normcase(os.path.abspath(base))
        # Keep vits_core; strip bare _internal / project root if it looks like
        # a frozen tree (has python3*.dll next to many extension modules).
        if norm == base_norm and (
            os.path.isfile(os.path.join(entry, "python313.dll"))
            or os.path.isfile(os.path.join(entry, "python312.dll"))
            or os.path.isfile(os.path.join(entry, "python311.dll"))
            or os.path.isfile(os.path.join(entry, "base_library.zip"))
        ):
            continue
        cleaned.append(entry)
    sys.path[:] = cleaned

    vits_core = os.path.join(base, "vits_core")
    if os.path.isdir(vits_core):
        # Prefer our bundled core first, but only that directory.
        while vits_core in sys.path:
            sys.path.remove(vits_core)
        sys.path.insert(0, vits_core)
    else:
        print(f"  ❌ vits_core 不存在: {vits_core}", file=sys.stderr, flush=True)

    # embeddable / conda: ensure site-packages is visible
    py_home = os.path.dirname(sys.executable)
    for sp in (
        os.path.join(py_home, "Lib", "site-packages"),
        os.path.join(py_home, "lib", "site-packages"),
    ):
        if os.path.isdir(sp) and sp not in sys.path:
            sys.path.append(sp)

    # Windows console often GBK; IPA phoneme prints need UTF-8
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    builtin_dic = os.path.join(base, "dic", "open_jtalk_dic_utf_8-1.11")
    if os.path.isdir(builtin_dic):
        os.environ["MEA_PET_OPEN_JTALK_DICT_DIR"] = builtin_dic
        print("  ✓ 使用项目内置日语词典", file=sys.stderr, flush=True)
    else:
        pyjt_dir = os.path.join(
            os.environ.get("MEA_PET_APPDATA", py_home), ".pyopenjtalk"
        )
        dic_dir = os.path.join(pyjt_dir, "open_jtalk_dic_utf_8-1.11")
        if os.path.isdir(dic_dir):
            os.environ["MEA_PET_OPEN_JTALK_DICT_DIR"] = dic_dir
            print(f"  ✓ 使用已缓存日语词典: {dic_dir}", file=sys.stderr, flush=True)
        else:
            print("  ❌ 未找到日语词典 open_jtalk_dic_utf_8-1.11", file=sys.stderr, flush=True)

    # Optional: only probe pkg_resources after path is clean.
    try:
        import pkg_resources  # noqa: F401
    except Exception as exc:
        print(
            f"  ⚠ pkg_resources 不可用 ({type(exc).__name__})；"
            "若合成失败请: pip install 'setuptools==69.5.1'",
            file=sys.stderr,
            flush=True,
        )
    return base


def _load_torch_stack():
    import torch
    import scipy.io.wavfile as wavf
    from torch import LongTensor, no_grad
    import commons
    from text import text_to_sequence
    from models import SynthesizerTrn
    import utils

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    return {
        "torch": torch,
        "wavf": wavf,
        "LongTensor": LongTensor,
        "no_grad": no_grad,
        "commons": commons,
        "text_to_sequence": text_to_sequence,
        "SynthesizerTrn": SynthesizerTrn,
        "utils": utils,
        "device": device,
    }


def _get_text(rt, text, hps, is_symbol=False):
    text_norm = rt["text_to_sequence"](
        text,
        hps.symbols,
        [] if is_symbol else hps.data.text_cleaners,
    )
    if hps.data.add_blank:
        text_norm = rt["commons"].intersperse(text_norm, 0)
    return rt["LongTensor"](text_norm)


def _load_model(rt, model_path, config_path):
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


def _is_speaker_table(speakers) -> bool:
    """说话人表判定走鸭子类型，不能 isinstance(x, dict)。

    get_hparams_from_file 读出来的 hps.speakers 是 utils.HParams：它有
    __contains__ / __getitem__ / keys，但不继承 dict，所以 isinstance 恒为假。
    """
    return speakers is not None and hasattr(
        speakers, "__contains__"
    ) and hasattr(speakers, "__getitem__")


def resolve_speaker(speaker_ids, requested: str | None) -> tuple[int, str | None]:
    """把说话人名解析成 id，并如实报告回落。

    本文件不得 import meapet.*（外部解释器只能看到落盘的本脚本），所以这里
    与 meapet.tts.common.resolve_vits_speaker 是同一条口径的两份实现，由
    tests/test_vits_route_and_knobs.py 钉住一致性。
    """
    if not _is_speaker_table(speaker_ids):
        if requested:
            return 0, (
                f"VITS 配置没有说话人表，已忽略请求的说话人 {requested!r}，使用 0 号"
            )
        return 0, None
    if not requested:
        return 0, None
    if requested not in speaker_ids:
        try:
            available = ", ".join(repr(name) for name in speaker_ids.keys())
        except Exception:
            available = "?"
        return 0, (
            f"VITS 说话人 {requested!r} 不在模型里（可用: {available or '(空)'}），"
            "已静默换到 0 号音色"
        )
    try:
        return int(speaker_ids[requested]), None
    except (TypeError, ValueError):
        return 0, f"VITS 说话人 {requested!r} 的 id 不是整数，使用 0 号"


def synthesize(
    text: str,
    output_wav: str,
    model_path: str,
    config_path: str,
    speaker: str = "Mea",
    noise_scale: float = 0.667,
    noise_scale_w: float = 0.6,
    length_scale: float = 1.0,
) -> str:
    rt = _load_torch_stack()
    hps, net_g = _load_model(rt, model_path, config_path)

    speaker_id, speaker_warning = resolve_speaker(hps.speakers, speaker)
    if speaker_warning:
        print(f"WARN:speaker:{speaker_warning}", file=sys.stderr, flush=True)

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


if __name__ == "__main__":
    base = _bootstrap()
    parser = argparse.ArgumentParser(description="VITS 语音合成（独立脚本）")
    parser.add_argument("-t", "--text", required=True, help="合成文本（含语言标记）")
    parser.add_argument("-o", "--output", default="output.wav", help="输出音频路径")
    parser.add_argument("-s", "--speaker", default="Mea", help="说话人名称")
    parser.add_argument("--noise_scale", type=float, default=0.667)
    parser.add_argument("--noise_scale_w", type=float, default=0.6)
    parser.add_argument("--length_scale", type=float, default=1.0)
    parser.add_argument("--warmup", action="store_true", help="预热加载模型")
    parser.add_argument(
        "--check-deps",
        dest="check_deps",
        action="store_true",
        help="只验证推理依赖能否 import，不读权重",
    )
    parser.add_argument("--model", default="", help="模型权重路径")
    parser.add_argument("--config", default="", help="模型配置路径")
    args = parser.parse_args()

    # 就绪探针走这条路：判据就是 _load_torch_stack 那份 import 面本身。
    # 另写一份"该装哪些包"的清单会与 vits_requirements.txt、与本函数的导入集合
    # 三处各说各话——实测本机 env 有 torch 却缺 unidecode/eng_to_ipa/num_thai/
    # indic_transliteration，只探 torch 的探针照样放行。
    if args.check_deps:
        _load_torch_stack()
        print("OK:deps_loaded")
        sys.exit(0)

    model_path = args.model or os.path.join(base, "vits_models", "G_latest.pth")
    config_path = args.config or os.path.join(
        base, "vits_models", "finetune_speaker.json"
    )

    if not os.path.isfile(model_path) or not os.path.isfile(config_path):
        print(f"ERR:model_missing model={model_path} config={config_path}", file=sys.stderr)
        sys.exit(1)

    if args.warmup:
        rt = _load_torch_stack()
        _load_model(rt, model_path, config_path)
        print("OK:model_loaded")
        sys.exit(0)

    result = synthesize(
        args.text,
        args.output,
        model_path=model_path,
        config_path=config_path,
        speaker=args.speaker,
        noise_scale=args.noise_scale,
        noise_scale_w=args.noise_scale_w,
        length_scale=args.length_scale,
    )
    print(f"OK:{result}")
