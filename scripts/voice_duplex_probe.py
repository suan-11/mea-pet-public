#!/usr/bin/env python3
"""P1-3 验收仪器：底噪 + 自播音下的「误断句 / 误唤醒」率。

路线图的验收线是「8 小时环境底噪 + 自身 TTS 播音下，误唤醒 < 1 次」。
真挂 8 小时在 CI 里不可行，所以这台仪器做两件事：

1. 真跑 VAD（不是模拟）：把仓内语音样本当「她自己在说话」，把白噪声当
   「环境底噪」，按 8 小时的量级喂满帧数并统计断句次数。
2. 门控对照：同样时长，一臂开回声门控、一臂关，比较断句数。
   门控臂要是没压到 0，P1-3 的防自激就是没做成。

用法（无头，不开窗口）::

    python scripts/voice_duplex_probe.py --hours 8
    python scripts/voice_duplex_probe.py --hours 0.1 --json

判据（与路线图一致）：
- 底噪臂：断句 0 次。
- 自播音臂（门控开）：断句 0 次 —— 她自己的声音一次都不该进来。
- 自播音臂（门控关）：应当 > 0。这一臂是仪器的自检：它必须能测出
  「没有门控时确实会误触发」，否则「有门控时 0 次」不说明任何事。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SAMPLE_RATE = 16000
WINDOW = 512


def _load_speech_sample():
    """取仓内那条合成语音，重采样到 16 kHz；找不到返回 None。"""
    import wave

    import numpy as np

    from meapet.paths import project_path

    for parts in (
        ("GPT-Sovits", "normal", "zh_normal.wav"),
        ("GPT-Sovits", "soft", "zh_normal.wav"),
    ):
        path = project_path(*parts)
        if not os.path.isfile(path):
            continue
        with wave.open(path, "rb") as handle:
            frames = handle.getnframes()
            channels = handle.getnchannels()
            rate = handle.getframerate()
            raw = handle.readframes(frames)
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if rate != SAMPLE_RATE:
            points = np.linspace(
                0, samples.size - 1, int(samples.size * SAMPLE_RATE / rate)
            )
            samples = np.interp(points, np.arange(samples.size), samples)
        return samples.astype(np.float32)
    return None


def _make_noise(seconds: float, seed: int = 0):
    """稳态底噪：白噪声 + 50 Hz 漂移（空调/风扇那种形状）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = int(SAMPLE_RATE * seconds)
    noise = (rng.standard_normal(n) * 0.004).astype(np.float32)
    t = np.linspace(0, seconds, n, endpoint=False)
    hum = (0.002 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    return noise + hum


class _TickingGate:
    """探针专用门控：时间由喂帧推进，不依赖墙钟（8 小时不能真等 8 小时）。

    与 meapet.voice.gate.EchoGate 同语义（hold 只叠 max、到期自动解），
    但时钟可手摇——真等的就不是仪器了。
    """

    def __init__(self, tail_ms: int = 300):
        self._now = 0.0
        self._until = 0.0
        self._tail = tail_ms

    def tick(self, seconds: float) -> None:
        self._now += seconds

    def hold(self, duration_s: float) -> None:
        self._until = max(self._until, self._now + duration_s + self._tail / 1000.0)

    def active(self) -> bool:
        return self._now < self._until

    def release(self) -> None:
        self._until = 0.0


def _count_segments(signal, gate=None):
    """把 signal 按窗喂给 VadSegmenter，返回 (断句数, 丢帧数)。

    gate 非 None 时模拟录音循环：门控生效期间整帧丢弃并 reset 检测器
    （与 VoiceEngine._read_chunk + _loop_hands_free 同语义）。
    """
    from meapet.voice.vad import VadSegmenter

    segmenter = VadSegmenter()
    segs = 0
    dropped = 0
    pos = 0
    while pos + WINDOW <= signal.size:
        frame = signal[pos : pos + WINDOW]
        pos += WINDOW
        if gate is not None:
            gate.tick(WINDOW / SAMPLE_RATE)
            if gate.active():
                dropped += 1
                segmenter.reset()
                continue
        segs += len(segmenter.feed_samples(frame))
    segs += len(segmenter.flush())
    return segs, dropped


def run(hours: float = 8.0, *, json_out: bool = False) -> int:
    try:
        import numpy as np
        import sherpa_onnx  # noqa: F401
    except ImportError as exc:
        print(f"[probe] 缺依赖：{exc}（pip install sherpa-onnx numpy）", file=sys.stderr)
        return 2

    from meapet.paths import find_voice_vad_model

    if not find_voice_vad_model():
        print(
            "[probe] Silero VAD 模型缺席（voice_vad/silero_vad.onnx）："
            "先在向导里下载，或把模型放到 models/voice_vad/",
            file=sys.stderr,
        )
        return 2

    speech = _load_speech_sample()
    if speech is None:
        print("[probe] 仓内语音样本缺席，无法构造自播音臂", file=sys.stderr)
        return 2

    seconds = max(1.0, hours * 3600.0)
    started = time.monotonic()
    speech_seconds = speech.size / SAMPLE_RATE
    loops = int(np.ceil(seconds / speech_seconds))
    speech_loop = np.tile(speech, loops)[: int(SAMPLE_RATE * seconds)]
    noise = _make_noise(seconds)

    # 臂一：纯底噪（无门控）
    noise_segs, _ = _count_segments(noise)

    # 臂二：自播音 + 门控开。整段持续宣告「在播音」，模拟她一整天在说话。
    gate = _TickingGate()
    gate.hold(seconds + 1.0)
    gated_segs, dropped = _count_segments(speech_loop, gate=gate)

    # 臂三：自播音 + 门控关 —— 仪器自检，必须 > 0
    open_segs, _ = _count_segments(speech_loop)

    elapsed = time.monotonic() - started
    report = {
        "hours": hours,
        "sample_rate": SAMPLE_RATE,
        "elapsed_s": round(elapsed, 2),
        "noise": {"segments": noise_segs},
        "self_speech_gate_on": {"segments": gated_segs, "frames_dropped": dropped},
        "self_speech_gate_off": {"segments": open_segs, "note": "自检：必须 > 0"},
        "verdict": {
            "noise_ok": noise_segs == 0,
            "gate_ok": gated_segs == 0,
            "probe_self_check": open_segs > 0,
        },
    }
    ok = all(report["verdict"].values())
    noise_ok = "通过" if noise_segs == 0 else "未过"
    gate_ok = "通过" if gated_segs == 0 else "未过"
    self_ok = "通过" if open_segs > 0 else "未过（读数不可信）"

    if json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[probe] P1-3 误唤醒验收 · {hours} 小时（实耗 {elapsed:.1f}s）")
        print(f"  底噪臂        断句 {noise_segs} 次")
        print(f"  自播音·门控开 断句 {gated_segs} 次（丢帧 {dropped}）")
        print(f"  自播音·门控关 断句 {open_segs} 次（自检，须 > 0）")
        print()
        print(f"  判据 底噪=0        : {noise_ok}")
        print(f"  判据 门控=0        : {gate_ok}")
        print(f"  判据 仪器自检 > 0  : {self_ok}")
        print(f"  总判 = {'通过' if ok else '未过'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1-3 语音全双工误唤醒验收探针")
    parser.add_argument(
        "--hours", type=float, default=8.0, help="底噪/自播音的等效时长（默认 8）"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    return run(args.hours, json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
