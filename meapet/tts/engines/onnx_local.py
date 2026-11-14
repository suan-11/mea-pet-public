"""轻量 ONNX TTS 引擎（P1-4）：用 sherpa-onnx 跑 VITS/Piper，不依赖 torch。

为什么它值得单独做一个引擎
--------------------------
core 包现在 321 MB，但**本地离线发声**仍要外置 VITS 扩展包（便携 Python 3.11 +
torch + 权重，1 GB 级）。用户只是想让她用自己的声音说句话，却要下 1 GB——
这是 P0-2 瘦身的收尾缺口。

sherpa-onnx 的 `OfflineTts` 跑 ONNX 模型时**不需要 torch**：语音输入那一路
（P1-3）已经把 sherpa-onnx 装进 core 了，所以这个引擎的**增量只有模型体积**。
实测 `vits-piper-zh_CN-chaowen-medium-int8` 是 13 MB。

与既有引擎的关系
----------------
不替代 GSV（音色克隆、音质更好但重），也不替代 MiMo（云端、更自然但要网）。
它是**第四档**：轻、离线、能出声。用户不装任何扩展包也能听到中文。

模型布局（与 sherpa-onnx 官方包一致）
------------------------------------
    <model_dir>/model.onnx          如 chaowen-medium-int8.onnx（名字随包）
    <model_dir>/tokens.txt
    <model_dir>/espeak-ng-data/     Piper 系模型需要；VITS-icefall 用 lexicon 代替
    <model_dir>/lexicon.txt         可选（icefall 系）
"""
from __future__ import annotations

import os
import time

from meapet.log import get_color_logger
from meapet.tts.engines.protocol import (
    CAPABILITY_OFFLINE,
    EngineCapabilities,
    LATENCY_FAST,
    SIZE_TIER_TINY,
)

log = get_color_logger("tts")

#: Piper/VITS 系模型的采样率通常是 22050；具体以模型自带的为准，
#: 这个常量只在拿不到时兜底。
DEFAULT_SAMPLE_RATE = 22050


class OnnxModelMissing(RuntimeError):
    """模型不在盘上——与「加载失败」分开报：去下载 vs 去看报错，处置不同。"""


class OnnxTtsEngine:
    """sherpa-onnx OfflineTts 封装。实现了 `TtsEngine` 协议。

    懒加载：构造它不碰磁盘，第一次 `speak`/`health_check` 才真载模型。
    理由与注册表存工厂一致——配置加载时把所有引擎都载一遍，启动会慢十几秒，
    而大多数进程只会用其中一个。
    """

    name = "onnx"

    capabilities = EngineCapabilities(
        languages=("zh", "en"),
        capabilities=frozenset({CAPABILITY_OFFLINE}),
        size_tier=SIZE_TIER_TINY,
        latency=LATENCY_FAST,
        config_keys=(
            "onnx_model_dir",
            "onnx_speaker",
            "onnx_speed",
            "onnx_num_threads",
        ),
    )

    def __init__(
        self,
        model_dir: str = "",
        *,
        speaker_id: object = 0,
        speed: float = 1.0,
        num_threads: int = 2,
    ):
        self._model_dir = str(model_dir or "").strip()
        self._speaker_id = _as_int(speaker_id, 0)
        self._speed = _as_float(speed, 1.0, low=0.3, high=3.0)
        self._num_threads = _as_int(num_threads, 2)
        self._tts = None
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._load_ms = 0

    # ------------------------------------------------------------------
    # 模型定位
    # ------------------------------------------------------------------

    def _resolve_model_files(self) -> dict:
        """在模型目录里认出 model.onnx / tokens.txt / 可选词典。

        文件名不能写死：官方包里 onnx 叫 `chaowen-medium-int8.onnx` 之类，
        Piper 与 icefall 也不一样。认「目录里唯一那个 .onnx」比认名字稳，
        但**多份并存时要报错而不是随便挑一个**——挑错的症状是音色莫名变了。
        """
        root = self._model_dir
        if not root or not os.path.isdir(root):
            raise OnnxModelMissing(f"ONNX 语音模型目录不存在: {root or '(未配置)'}")

        onnx_files = [f for f in os.listdir(root) if f.endswith(".onnx")]
        if not onnx_files:
            raise OnnxModelMissing(f"模型目录里没有 .onnx 文件: {root}")
        if len(onnx_files) > 1:
            raise OnnxModelMissing(
                f"模型目录里有 {len(onnx_files)} 个 .onnx（{', '.join(sorted(onnx_files)[:3])}…），"
                "不知道该用哪个；请只保留要用的那一个，或用 tts.onnx_model_file 指定"
            )

        tokens = os.path.join(root, "tokens.txt")
        if not os.path.isfile(tokens):
            raise OnnxModelMissing(f"模型目录缺 tokens.txt: {root}")

        files = {
            "model": os.path.join(root, onnx_files[0]),
            "tokens": tokens,
        }
        # 词典：espeak-ng-data 目录（Piper）或 lexicon.txt（icefall）。
        data_dir = os.path.join(root, "espeak-ng-data")
        if os.path.isdir(data_dir):
            files["data_dir"] = data_dir
        lexicon = os.path.join(root, "lexicon.txt")
        if os.path.isfile(lexicon):
            files["lexicon"] = lexicon
        return files

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """载入模型。已载入返回 True；失败返回 False 并把原文打出来。

        **不在失败后重试**：模型缺失/损坏是稳定失败，每次 speak 都重试只会
        让日志刷屏，而用户看到的现象是"卡一下又不说话"。
        """
        if self._tts is not None:
            return True
        try:
            import sherpa_onnx
        except ImportError:
            log.warning("[onnx] sherpa-onnx 未安装（pip install sherpa-onnx）")
            return False

        try:
            files = self._resolve_model_files()
        except OnnxModelMissing as exc:
            log.warning(f"[onnx] {exc}")
            return False

        started = time.monotonic()
        try:
            cfg = sherpa_onnx.OfflineTtsConfig()
            cfg.model.num_threads = self._num_threads
            cfg.model.provider = "cpu"
            cfg.model.vits.model = files["model"]
            cfg.model.vits.tokens = files["tokens"]
            if "data_dir" in files:
                cfg.model.vits.data_dir = files["data_dir"]
            if "lexicon" in files:
                cfg.model.vits.lexicon = files["lexicon"]
            cfg.max_num_sentences = 1
            cfg.validate()

            self._tts = sherpa_onnx.OfflineTts(cfg)
            self._sample_rate = int(getattr(self._tts, "sample_rate", 0) or DEFAULT_SAMPLE_RATE)
            self._load_ms = int((time.monotonic() - started) * 1000)
            log.info(
                f"[onnx] 模型已载入 {os.path.basename(files['model'])} "
                f"sr={self._sample_rate} 耗时={self._load_ms}ms"
            )
            return True
        except Exception as exc:  # noqa: BLE001 - 加载失败原文要给用户
            log.warning(f"[onnx] 模型加载失败: {type(exc).__name__}: {exc}")
            return False

    # ------------------------------------------------------------------
    # 协议实现
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        return self._ensure_loaded()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def load_ms(self) -> int:
        return self._load_ms

    @property
    def num_speakers(self) -> int:
        if not self._ensure_loaded():
            return 0
        return int(getattr(self._tts, "num_speakers", 1) or 1)

    def speak(self, text: str, output_wav: str, **kwargs):
        """合成到 `output_wav`，返回 `(wav_path, lang)` 或 `(None, '')`。

        `**kwargs` 只为协议兼容（调度主干会传 mood/style/language 等）：
        这个引擎不做情绪与风格，收到就忽略——**不假装支持**。
        """
        if not text or not text.strip():
            return None, ""
        if not self._ensure_loaded():
            return None, ""

        try:
            audio = self._tts.generate(
                text, sid=self._speaker_id, speed=self._speed
            )
        except Exception as exc:  # noqa: BLE001 - 单句合成失败要能继续下一句
            log.warning(f"[onnx] 合成失败: {type(exc).__name__}: {exc}")
            return None, ""

        try:
            import numpy as np
            import wave

            samples = np.asarray(getattr(audio, "samples", audio), dtype=np.float32)
            if samples.size == 0:
                log.warning("[onnx] 合成结果为空")
                return None, ""
            pcm = np.clip(samples, -1.0, 1.0)
            pcm16 = (pcm * 32767.0).astype("<i2")
            os.makedirs(os.path.dirname(os.path.abspath(output_wav)) or ".", exist_ok=True)
            with wave.open(output_wav, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(int(getattr(audio, "sample_rate", 0) or self._sample_rate))
                handle.writeframes(pcm16.tobytes())
        except Exception as exc:  # noqa: BLE001 - 写盘失败要说清是哪一步
            log.warning(f"[onnx] 写 wav 失败: {type(exc).__name__}: {exc}")
            return None, ""

        lang = str(kwargs.get("language") or "zh")
        log.info(
            f"[onnx] 合成完成 chars={len(text)} "
            f"dur={samples.size / max(1, self._sample_rate):.2f}s"
        )
        return output_wav, lang


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: object, default: float, *, low: float, high: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    return max(low, min(parsed, high))


def create_onnx_engine(config: dict):
    """从整份配置造一个 ONNX 引擎（注册表工厂）。"""
    tts_cfg = (config or {}).get("tts") or {}
    return OnnxTtsEngine(
        model_dir=tts_cfg.get("onnx_model_dir", ""),
        speaker_id=tts_cfg.get("onnx_speaker", 0),
        speed=tts_cfg.get("onnx_speed", 1.0),
        num_threads=tts_cfg.get("onnx_num_threads", 2),
    )


#: 能力矩阵挂在**工厂函数**上：注册表问「你支持什么」时不该为了这一句去构造实例，
#: 更不该去载模型（ONNX 载入实测 4 秒）。定义之后再赋值是为了让属性与函数同一个名字，
#: 读代码的人一眼能对上。
create_onnx_engine.capabilities = OnnxTtsEngine.capabilities
