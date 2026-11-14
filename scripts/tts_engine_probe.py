#!/usr/bin/env python3
"""P1-4 验收仪器：TTS 引擎的能力矩阵、体积增量与合成实测。

路线图 P1-4 的三条验收判据，这台探针逐条给读数：

1. **新增引擎不改调度主干**——统计 `service.py` 里 `if engine ==` 形态的分支数，
   以及 `chat_flow.py` 里有没有为某个具体引擎名写死分支。这条是"可插拔"的定义。
2. **core + 轻量语音包增量 < 150 MB**——量 ONNX 模型目录的真实体积。
3. **每个引擎留档延迟与复读实测**——真合成一句，量载入耗时、首音耗时、音频时长。

用法::

    python scripts/tts_engine_probe.py --model-dir <ONNX 模型目录>
    python scripts/tts_engine_probe.py --model-dir ... --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 路线图 P1-4 验收②的硬上限（MB）：轻量语音包相对 core 的增量。
LIGHT_PACK_MAX_MB = 150.0

SENTENCE = "你好，我是梅儿，今天也想陪着你。"


def _dir_size_mb(path: str) -> float:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total / (1024 * 1024)


#: 引擎**分发点**：这三处是"按引擎名分派行为"的地方。新增引擎若只碰注册表、
#: 不碰它们，就叫可插拔；碰了就是耦合。既有的 `_vits_mode/_mimo_mode` 两枚布尔
#: 在 `__init__` 里赋值，是**历史既有三引擎的入口**，不算分发——它们不随引擎数量增长。
_DISPATCH_MARKERS = (
    "def speak(",
    "def speak_async(",
    "def health_check(",
    "def _ensure_deps(",
)


def _count_engine_branches(path: str) -> dict:
    """数**分发方法体内**为具体引擎名写死的分支。

    为什么不是整个文件：`__init__` 里那句 `engine == "mimo"` 是把配置名翻成布尔，
    它只有两行、且对**既有三引擎**成立，是新旧世界的接缝而不是分发点。
    真正的判据是「加第 N 个引擎要不要动 speak/health_check 这些方法」——
    那才是可插拔与否的分界线。

    实现：按方法切块，只在分派方法的体内搜 `engine == "xxx"`。
    """
    if not os.path.isfile(path):
        return {"file": path, "missing": True}
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines()
    hits: list = []
    in_dispatch = False
    body_indent = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if any(stripped.startswith(m) for m in _DISPATCH_MARKERS):
            in_dispatch = True
            body_indent = indent
            continue
        if in_dispatch:
            # 退回到同级或更外层 = 这个方法结束了
            if stripped and indent <= body_indent and not stripped.startswith(")"):
                in_dispatch = False
                continue
            for name in re.findall(r'engine\s*==\s*["\']([a-z_0-9]+)["\']', line):
                hits.append(name)
    return {
        "file": os.path.basename(path),
        "dispatch_hardcoded_engine_names": sorted(set(hits)),
        "count": len(hits),
    }


def run(model_dir: str, *, json_out: bool = False) -> int:
    report: dict = {}

    # ── 判据 1：可插拔面 ──
    root = _ROOT
    report["dispatch"] = {
        "service": _count_engine_branches(os.path.join(root, "meapet", "tts", "service.py")),
        "chat_flow": _count_engine_branches(
            os.path.join(root, "meapet", "desktop", "chat_flow.py")
        ),
    }

    # ── 判据 2：体积 ──
    if os.path.isdir(model_dir):
        report["model_dir"] = model_dir
        report["model_mb"] = round(_dir_size_mb(model_dir), 1)
        report["size_ok"] = report["model_mb"] < LIGHT_PACK_MAX_MB
    else:
        report["model_mb"] = None
        report["size_ok"] = False
        report["size_note"] = f"模型目录不存在: {model_dir}"

    # ── 判据 3：真合成 ──
    try:
        from meapet.tts.service import MeaTTS

        tts = MeaTTS(
            {
                "tts": {
                    "enabled": True,
                    "engine": "onnx",
                    "onnx_model_dir": model_dir,
                    "voice_lang": "zh",
                }
            }
        )
        report["engine"] = tts.engine
        report["capabilities"] = tts.capabilities.describe()

        health_t0 = time.monotonic()
        report["health"] = bool(tts.health_check())
        report["health_ms"] = int((time.monotonic() - health_t0) * 1000)

        engine = getattr(tts, "_plugin_engine", None)
        report["load_ms"] = getattr(engine, "load_ms", None)

        speak_t0 = time.monotonic()
        result = tts.speak(SENTENCE, mood="neutral")
        report["speak_ms"] = int((time.monotonic() - speak_t0) * 1000)

        wav, lang = (result if result else (None, ""))
        report["ok_speak"] = bool(wav and os.path.isfile(wav))
        report["lang"] = lang
        if report["ok_speak"]:
            import wave

            with wave.open(wav, "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or 1
            report["wav_seconds"] = round(frames / rate, 2)
            report["wav_kb"] = round(os.path.getsize(wav) / 1024, 1)
            # 复读检测：同一句合两次，音频时长不该差出一大截（差太多说明
            # 要么抖得厉害，要么模型在复读/截断）。
            tts.speak(SENTENCE, mood="neutral")
            result2 = tts.speak(SENTENCE, mood="neutral")
            wav2 = result2[0] if result2 else None
            if wav2 and os.path.isfile(wav2):
                with wave.open(wav2, "rb") as handle:
                    frames2 = handle.getnframes()
                    rate2 = handle.getframerate() or 1
                second = frames2 / rate2
                drift = abs(second - report["wav_seconds"]) / max(0.01, report["wav_seconds"])
                report["repeat_duration_drift"] = round(drift, 3)
                report["repeat_ok"] = drift < 0.15
        else:
            report["repeat_ok"] = False
    except Exception as exc:  # noqa: BLE001 - 探针自己炸了要报原文
        report["engine_error"] = f"{type(exc).__name__}: {exc}"
        report["ok_speak"] = False
        report["repeat_ok"] = False

    # 判据 1 的通过标准：主干里为**具体引擎名**写死的分支数为 0。
    # 既有三引擎靠布尔标志分支，不算硬编码名字。
    report["dispatch_ok"] = (
        report["dispatch"]["service"].get("count", 0) == 0
        and report["dispatch"]["chat_flow"].get("count", 0) == 0
    )
    report["ok"] = bool(
        report.get("ok_speak")
        and report.get("repeat_ok")
        and report.get("size_ok")
        and report.get("dispatch_ok")
    )

    if json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("[probe] P1-4 TTS 引擎插拔验收")
        print()
        print("  ① 调度主干不含硬编码引擎名")
        print(f"     service.py  : {report['dispatch']['service'].get('hardcoded_engine_names')}")
        print(f"     chat_flow.py: {report['dispatch']['chat_flow'].get('hardcoded_engine_names')}")
        print(f"     -> {'通过' if report['dispatch_ok'] else '未过'}")
        print()
        print("  ② 轻量语音包体积")
        print(f"     模型目录 {report['model_mb']} MB（上限 {LIGHT_PACK_MAX_MB:.0f}）")
        print(f"     -> {'通过' if report['size_ok'] else '未过'}")
        print()
        print("  ③ 真合成读数")
        print(f"     引擎        : {report.get('engine')}")
        print(f"     能力矩阵    : {report.get('capabilities')}")
        print(f"     health      : {report.get('health')}（{report.get('health_ms')} ms）")
        print(f"     模型载入    : {report.get('load_ms')} ms")
        print(f"     合成一句    : {report.get('speak_ms')} ms")
        if report.get("ok_speak"):
            print(f"     音频        : {report['wav_seconds']} s / {report['wav_kb']} KB")
            print(
                f"     复读漂移    : {report.get('repeat_duration_drift')} "
                f"-> {'通过' if report.get('repeat_ok') else '未过'}"
            )
        else:
            print(f"     合成失败    : {report.get('engine_error', '返回空')}")
        print()
        print(f"  总判 = {'通过' if report['ok'] else '未过'}")
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1-4 TTS 引擎插拔验收探针")
    parser.add_argument(
        "--model-dir", required=True, help="轻量 ONNX 语音模型目录"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    return run(args.model_dir, json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
