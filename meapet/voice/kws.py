"""KWS 唤醒词（P1-3）：常驻监听，喊出唤醒词才进入收音。

隐私边界（与 vision/watcher 同档）：默认关闭，是独立的显式开关
voice_input.wake_word.enabled，不与 voice_input.enabled 捆绑——用户开语音输入
不等于授权常驻监听。

防自激：麦克风帧先过 EchoGate（TTS 播音期整帧丢弃），再往 KeywordSpotter 喂；
唤醒成功后由调用方（voice_mixin）触发录音，录音循环自己再做一遍门控——双闸，
KWS 这一闸只管「不听她自己说话」，不管「录进来的干不干净」。

模型：sherpa-onnx 的 zipformer KWS（中英混合，~3.3M 参数），取件与 ASR 模型分目录
（voice_kws/），盘上没有就按「缺席」报，不静默退化。
"""
from __future__ import annotations

import traceback
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from meapet.log import get_color_logger
from meapet.paths import find_voice_kws_files
from meapet.voice.gate import GLOBAL_ECHO_GATE

log = get_color_logger("voice")

KWS_SAMPLE_RATE = 16000


class KwsModelMissing(RuntimeError):
    """KWS 模型/词表不在盘上——与「加载失败」分开报。"""


def build_keyword_spotter(
    *,
    model_files: Optional[dict] = None,
    keywords_file: Optional[str] = None,
    sample_rate: int = KWS_SAMPLE_RATE,
):
    """造 KeywordSpotter；模型缺席抛 KwsModelMissing，sherpa 没装抛 ImportError。"""
    import sherpa_onnx

    files = model_files or find_voice_kws_files()
    if not files:
        raise KwsModelMissing("KWS 唤醒模型缺失（voice_kws/，配置页可下载）")
    kw = keywords_file or files.get("keywords")
    if not kw:
        raise KwsModelMissing("KWS 唤醒词表缺失（keywords_file）")
    log.info("[voice] loading KWS zipformer model...")
    spotter = sherpa_onnx.KeywordSpotter(
        tokens=files["tokens"],
        encoder=files["encoder"],
        decoder=files["decoder"],
        joiner=files["joiner"],
        keywords_file=kw,
        num_threads=1,
        sample_rate=sample_rate,
        provider="cpu",
    )
    log.info("[voice] KWS model loaded")
    return spotter


class WakeWordListener(QThread):
    """后台常驻监听线程：唤醒词命中发 detected(keyword)，然后继续听。"""

    detected = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        keywords_file: Optional[str] = None,
        input_device_index: object = None,
        gate=GLOBAL_ECHO_GATE,
        sample_rate: int = KWS_SAMPLE_RATE,
        chunk_ms: int = 30,
        parent=None,
    ):
        super().__init__(parent)
        self._keywords_file = keywords_file
        self._gate = gate
        self._sample_rate = int(sample_rate or KWS_SAMPLE_RATE)
        self._chunk_size = max(256, self._sample_rate * int(chunk_ms or 30) // 1000)
        self._input_device_index = input_device_index
        self._stop = False
        self._spotter = None

    def stop(self, timeout_ms: int = 0) -> bool:
        self._stop = True
        if not self.isRunning():
            return True
        try:
            to = max(0, int(timeout_ms or 0))
        except (TypeError, ValueError):
            to = 0
        if to <= 0:
            return False
        return bool(self.wait(to))

    def run(self):
        try:
            if self._spotter is None:
                self._spotter = build_keyword_spotter(
                    keywords_file=self._keywords_file,
                    sample_rate=self._sample_rate,
                )
        except ImportError:
            self.error.emit("sherpa-onnx 未安装，请在配置页下载依赖")
            return
        except KwsModelMissing as exc:
            self.error.emit(f"{exc}")
            return
        except Exception as exc:  # noqa: BLE001 - 模型加载失败的原文要给用户
            self.error.emit(f"KWS 模型加载失败: {exc}")
            return

        try:
            import numpy as np
            import pyaudio

            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._sample_rate,
                input=True,
                frames_per_buffer=self._chunk_size,
            )
            kws_stream = self._spotter.create_stream()
            log.info("[voice] KWS 监听开始")
            while not self._stop:
                try:
                    data = stream.read(self._chunk_size, exception_on_overflow=False)
                except Exception:  # noqa: BLE001 - 单帧读失败丢一帧，不拆整条监听
                    continue
                if self._gate is not None and self._gate.active():
                    # 播音期整帧丢——她说的话不许进唤醒判定
                    continue
                samples = (
                    np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                )
                kws_stream.accept_waveform(self._sample_rate, samples)
                while self._spotter.is_ready(kws_stream):
                    self._spotter.decode_stream(kws_stream)
                result = self._spotter.get_result(kws_stream).strip()
                if result:
                    log.info(f"[voice] 唤醒词命中：{result}")
                    self.detected.emit(result)
                    # 命中后清 stream 状态重新听——不清会带着旧特征立刻二次命中
                    self._spotter.reset_stream(kws_stream)
            stream.stop_stream()
            stream.close()
            p.terminate()
            log.info("[voice] KWS 监听结束")
        except ImportError:
            self.error.emit("pyaudio 未安装，请在配置页下载依赖")
        except Exception as exc:  # noqa: BLE001 - 监听线程的异常必须出声
            log.error(f"[voice] KWS error: {type(exc).__name__}: {exc}")
            log.trace(lambda: traceback.format_exc())
            self.error.emit(f"KWS 监听异常: {exc}")
