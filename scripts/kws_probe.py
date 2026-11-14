#!/usr/bin/env python3
"""P1-3 尾巴：KWS 唤醒词**端到端**验证。

tests/ 里只测「模型缺席要专用报错」与「成套定位」——真解码要模型，而模型是
可选下载物，不该让 CI 去拖 31 MB。这台探针补另一半：真加载、真解码、真命中。

三段判据，缺一条唤醒词就是没做成：

1. **成套解析**：encoder/decoder/joiner 必须同属一个变体（epoch 一致）。
   上游官方包一个目录里并排放着多套模型，按件挑会撞出混搭——这是实测踩过的。
2. **能加载**：sherpa 真的把三件读进来了。
3. **能命中**：喂模型自带的 test_wavs 音频，必须真的断出关键词。
   只验「加载成功」是不够的——一个词表与 tokens 不匹配的组合也能加载成功，
   然后永远不命中。

用法::

    python scripts/kws_probe.py --model-dir <解压后的模型目录>
    python scripts/kws_probe.py --model-dir ... --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import wave

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_16k(wav_path: str):
    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        frames = handle.getnframes()
        channels = handle.getnchannels()
        rate = handle.getframerate()
        raw = handle.readframes(frames)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        points = np.linspace(0, samples.size - 1, int(samples.size * 16000 / rate))
        samples = np.interp(points, np.arange(samples.size), samples)
    return samples.astype(np.float32)


def _decode(spotter, samples):
    """喂完一段音频，回收全部命中（命中后立刻 reset，与产品侧同姿势）。"""
    import numpy as np

    stream = spotter.create_stream()
    stream.accept_waveform(16000, samples)
    # 尾部静音：流式模型要靠它把最后那几个音素吐出来，不补会漏尾字
    stream.accept_waveform(16000, np.zeros(int(0.66 * 16000), dtype=np.float32))
    stream.input_finished()
    hits = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        result = spotter.get_result(stream)
        if result:
            hits.append(result)
            spotter.reset_stream(stream)
    return hits


def run(model_dir: str, *, json_out: bool = False) -> int:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        print("[probe] 缺 sherpa-onnx（pip install sherpa-onnx）", file=sys.stderr)
        return 2

    from pathlib import Path

    from meapet.paths import _kws_variant_key, find_voice_kws_files
    from meapet.voice.kws import build_keyword_spotter

    report: dict = {"model_dir": model_dir}

    files = find_voice_kws_files()
    if not files:
        print(
            "[probe] 找不到成套 KWS 模型：把解压后的文件放到 models/voice_kws/ ",
            file=sys.stderr,
        )
        return 2

    names = {k: Path(v).name for k, v in files.items()}
    report["resolved"] = names

    # 判据 1：成套
    variants = {
        _kws_variant_key(names[k]) for k in ("encoder", "decoder", "joiner")
    }
    report["variants"] = sorted(variants)
    report["coherent"] = len(variants) == 1

    # 判据 2：能加载
    keywords_file = str(Path(model_dir) / "test_wavs" / "test_keywords.txt")
    if not os.path.isfile(keywords_file):
        keywords_file = files.get("keywords") or ""
    report["keywords_file"] = os.path.basename(keywords_file) if keywords_file else None
    try:
        spotter = build_keyword_spotter(
            model_files=files, keywords_file=keywords_file or None
        )
        report["loaded"] = True
    except Exception as exc:  # noqa: BLE001 - 加载失败的原文要原样报出来
        report["loaded"] = False
        report["load_error"] = f"{type(exc).__name__}: {exc}"
        spotter = None

    # 判据 3：能命中
    report["hits_by_wav"] = {}
    if spotter is not None:
        wav_dir = Path(model_dir) / "test_wavs"
        wavs = sorted(wav_dir.glob("*.wav")) if wav_dir.is_dir() else []
        for wav in wavs:
            try:
                report["hits_by_wav"][wav.name] = _decode(spotter, _load_16k(str(wav)))
            except Exception as exc:  # noqa: BLE001 - 单个 wav 失败不拆整条探针
                report["hits_by_wav"][wav.name] = [f"<error: {type(exc).__name__}>"]

    total_hits = sum(
        len(v) for v in report["hits_by_wav"].values() if not str(v).startswith("<")
    )
    report["total_hits"] = total_hits
    report["ok"] = bool(report["coherent"] and report["loaded"] and total_hits > 0)

    if json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("[probe] KWS 唤醒词端到端")
        print(f"  解析到的三件      : {names['encoder']}")
        print(f"                      {names['decoder']}")
        print(f"                      {names['joiner']}")
        print(f"  成套（epoch 一致）: {'是' if report['coherent'] else '否 ' + str(report['variants'])}")
        print(f"  加载              : {'成功' if report['loaded'] else '失败: ' + report.get('load_error', '')}")
        print(f"  词表              : {report['keywords_file']}")
        for name, hits in report["hits_by_wav"].items():
            print(f"  {name:<10}        命中: {hits if hits else '无'}")
        print()
        print(f"  总命中 {total_hits} 个")
        print(f"  总判 = {'通过' if report['ok'] else '未过'}")
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1-3 KWS 唤醒词端到端验证")
    parser.add_argument(
        "--model-dir",
        default="models/voice_kws",
        help="解压后的 KWS 模型目录（含 test_wavs 时用它自带的词表）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    return run(args.model_dir, json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
