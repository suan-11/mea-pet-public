"""Silero VAD 自动断句（P1-3，全双工语音输入的端点检测）。

为什么走 sherpa-onnx 自带的 Silero VAD：语音输入那一路已经依赖 sherpa-onnx（ASR），
VAD 是它内置的端点检测——2 MB 的 silero_vad.onnx，不用引第二个推理运行时。
参数（阈值 / 最小静音 / 最短说话）在 SileroVadModelConfig 上有默认值，这里只把
产品里真正暴露的那几格搬出来，其余保持 sherpa 默认，一处都不抄第二份。

本模块不 import Qt，也不 import pyaudio——录音循环在 VoiceEngine 里，
这里只管「样本进、句子出」，无头测试与 doctor 取证都能直接驱动。
"""
from __future__ import annotations

from typing import Optional

from meapet.log import get_color_logger
from meapet.paths import find_voice_vad_model

log = get_color_logger("voice")

#: Silero 的固定窗（16 kHz × 32 ms）；与 SileroVadModelConfig.window_size 的
#: 默认值同数。硬编码它只为没装 sherpa 时也能把「每帧多少样本」讲清楚。
VOICE_VAD_WINDOW = 512
VOICE_VAD_SAMPLE_RATE = 16000


class VadModelMissing(RuntimeError):
    """Silero VAD 模型不在盘上——与「加载失败」分开报（去下载 / 去重装是两种处置）。"""


def build_vad_config(
    model_path: str,
    *,
    threshold: float = 0.5,
    min_silence_duration: float = 0.5,
    min_speech_duration: float = 0.25,
    max_speech_duration: float = 20.0,
    sample_rate: int = VOICE_VAD_SAMPLE_RATE,
):
    """按这一份参数造校验过的 VadModelConfig。

    越界参数在 validate() 这里就炸，不留到 feed 中途才报错——录音线程里
    晚一格炸等于录了半句白录。
    """
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(model_path)
    cfg.silero_vad.threshold = float(threshold)
    cfg.silero_vad.min_silence_duration = float(min_silence_duration)
    cfg.silero_vad.min_speech_duration = float(min_speech_duration)
    cfg.silero_vad.max_speech_duration = float(max_speech_duration)
    cfg.sample_rate = int(sample_rate)
    cfg.num_threads = 1
    cfg.provider = "cpu"
    cfg.validate()
    return cfg


class VadSegmenter:
    """逐帧喂样本，吐「一句」的 float32 样本数组。

    窗口语义：sherpa 只吃 window_size 的整数倍（默认 512 样本）。feed_* 内部
    缓冲不足一窗的尾巴、跨帧拼满才喂；flush() 把最后不足一窗的尾巴正式收进去
    ——不 flush 就丢，等于静默吃掉用户的最后半句。
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        threshold: float = 0.5,
        min_silence_duration: float = 0.5,
        min_speech_duration: float = 0.25,
        max_speech_duration: float = 20.0,
        sample_rate: int = VOICE_VAD_SAMPLE_RATE,
        buffer_seconds: float = 60.0,
    ):
        import sherpa_onnx  # noqa: PLC0415 - 没装 sherpa 时这里才炸，模块导入不炸

        import numpy as np

        path = model_path or find_voice_vad_model()
        if not path:
            raise VadModelMissing("Silero VAD 模型缺失（voice_vad/，配置页可下载）")
        self._cfg = build_vad_config(
            str(path),
            threshold=threshold,
            min_silence_duration=min_silence_duration,
            min_speech_duration=min_speech_duration,
            max_speech_duration=max_speech_duration,
            sample_rate=sample_rate,
        )
        self._detector = sherpa_onnx.VoiceActivityDetector(
            self._cfg, buffer_size_in_seconds=float(buffer_seconds)
        )
        self._window = int(self._cfg.silero_vad.window_size or VOICE_VAD_WINDOW)
        self.sample_rate = int(self._cfg.sample_rate)
        self._np = np
        self._pending = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------------
    # 喂样本
    # ------------------------------------------------------------------

    def feed_pcm16(self, pcm: bytes) -> list:
        """喂一段 16-bit 单声道 PCM，返回本段里完整结束的句子（float32 列表）。"""
        if not pcm:
            return []
        np = self._np
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return self.feed_samples(samples)

    def feed_samples(self, samples) -> list:
        """喂一段 float32 单声道样本（[-1,1]），返回完整结束的句子列表。"""
        np = self._np
        if samples is None or len(samples) == 0:
            return []
        self._pending = np.concatenate(
            [self._pending, np.asarray(samples, dtype=np.float32)]
        )
        out: list = []
        while self._pending.size >= self._window:
            window = self._pending[: self._window]
            self._pending = self._pending[self._window:]
            self._detector.accept_waveform(window)
            out.extend(self._drain())
        return out

    # ------------------------------------------------------------------
    # 收口
    # ------------------------------------------------------------------

    def flush(self) -> list:
        """强制收尾：把 buffer 里还没凑够「一句」的尾巴也判出来。"""
        np = self._np
        out = self._drain()
        self._detector.flush()
        out.extend(self._drain())
        self._pending = np.zeros(0, dtype=np.float32)
        return out

    def reset(self) -> None:
        """换一轮：检测器与待喂尾巴一起清零（新一轮对话 / 切配置时用）。"""
        np = self._np
        self._detector.reset()
        self._pending = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------------

    def _drain(self) -> list:
        np = self._np
        out: list = []
        while not self._detector.empty():
            seg = self._detector.front
            samples = getattr(seg, "samples", None)
            if samples is not None and len(samples):
                out.append(np.array(samples, dtype=np.float32))
            self._detector.pop()
        return out
