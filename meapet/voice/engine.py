"""
语音输入引擎 — 录音 + sherpa-onnx 离线转文字。

点击切换模式：第一次点击开始录音（后台加载模型），第二次点击停止 → 转文字 → 发射结果。
默认关闭，需在 config 中 voice_input.enabled=true 才初始化。

模型来源：ModelScope pkufool/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
"""
from __future__ import annotations

import os
import re
import traceback
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from meapet.config.normalizers import (
    VOICE_INPUT_MODES,
    normalize_input_device_index,
    normalize_voice_mode,
)
from meapet.log import get_color_logger
from meapet.paths import (
    VOICE_ASR_MODEL_FILES,
    find_voice_asr_model_dir,
    normalize_voice_asr_precision,
)
from meapet.voice.gate import GLOBAL_ECHO_GATE

log = get_color_logger("voice")

# 识别器与采集共用的采样率：设备的可用性按它探测，向导文案也按它标注，别两处各写一个数。
VOICE_INPUT_SAMPLE_RATE = 16000

# 模式词汇表的唯一真值在 meapet.config.normalizers（配置存储那一路不引 Qt），
# 这里 re-export 只为既有 import 面不破。
__all__ = [
    "AsrModelMissing",
    "VOICE_INPUT_MODES",
    "VOICE_INPUT_SAMPLE_RATE",
    "VoiceEngine",
    "build_recognizer",
    "create_voice_engine",
    "list_input_devices",
    "normalize_voice_mode",
    "read_wav_samples",
    "transcribe_wav",
]


class AsrModelMissing(RuntimeError):
    """档位对应的模型不在盘上。取证侧要把它与"加载失败"分开报——处置完全不同（下模型 / 重装）。"""


def build_recognizer(precision: object = None, sample_rate: int = VOICE_INPUT_SAMPLE_RATE):
    """按产品那一份模型表造流式识别器；模型缺席抛 `AsrModelMissing`，sherpa 没装抛 `ImportError`。

    录音那一路（`VoiceEngine._load_model`）与无头取证（`tts.diagnostics.speech_repetition`）
    共用这一枚：`from_transducer` 那几项（int8 档、greedy_search、feature_dim=80）换掉任何一项，
    转写串就换一种坏法，两处各写一份等于给同一只耳朵立两副参数。
    """
    import sherpa_onnx

    chosen = normalize_voice_asr_precision(precision)
    model_dir = find_voice_asr_model_dir(chosen)
    if model_dir is None:
        raise AsrModelMissing(f"语音识别模型缺失（{chosen} 档）")
    files = VOICE_ASR_MODEL_FILES[chosen]
    log.info(f"[voice] loading sherpa-onnx zipformer zh-en model ({chosen})...")
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=os.path.join(model_dir, files["encoder"]),
        decoder=os.path.join(model_dir, files["decoder"]),
        joiner=os.path.join(model_dir, files["joiner"]),
        tokens=os.path.join(model_dir, files["tokens"]),
        num_threads=4,
        provider="cpu",
        sample_rate=sample_rate,
        feature_dim=80,
        decoding_method="greedy_search",
    )
    log.info("[voice] model loaded successfully")
    return recognizer


def _decode_samples(recognizer, samples, sample_rate: int) -> str:
    """把 float32 单声道样本整块喂进去，取回转写串（SIL 清掉）。

    流式模型走"全量 + `input_finished`"，**不补额外静音**——补了会让它复读（这一格是实测踩出来的，
    见 `meapet/voice/engine.py` 旧版注释），取证那一发要是悄悄补了尾，读到的连读长度就是假的。
    """
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result(stream).strip()
    # 清理 SIL token（\b 对中文无效，直接用简单替换）
    return re.sub(r"\s*SIL\s*", "", result).strip()


def read_wav_samples(wav_path: str):
    """把一个 wav 读成 `(float32 单声道 [-1,1], 采样率)`——RIFF chunk 自己走一遍。

    不用 stdlib `wave`，因为它到这一格就不够了：VITS 那一路的产物是 `fmt=3`（32-bit IEEE float），
    `wave.open` 直接抛 `Error: unknown format: 3`。而这台仪器偏偏要读**两臂**——口径
    「只保证VITS的zh正常」里那一臂就是它读不了的这份。认两种就够：16-bit PCM（GSV 那一路）、
    32-bit float（VITS 那一路）；**其余按 `fmt`/位深报出去，不静默回零**。
    """
    import numpy as np

    with open(wav_path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("不是 RIFF/WAVE 文件")
    fmt = data = None
    pos = 12
    while pos + 8 <= len(blob):
        tag = blob[pos:pos + 4]
        size = int.from_bytes(blob[pos + 4:pos + 8], "little")
        body = blob[pos + 8:pos + 8 + size]
        if tag == b"fmt " and fmt is None:
            fmt = body
        elif tag == b"data" and data is None:
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None or len(fmt) < 16:
        raise ValueError("这份 wav 缺 fmt 或 data chunk")
    form = int.from_bytes(fmt[0:2], "little")
    channels = max(int.from_bytes(fmt[2:4], "little"), 1)
    rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    frame_bytes = channels * max(bits // 8, 1)
    if frame_bytes > 1:
        # 尾上那半帧不收：`frombuffer`/`reshape` 都会为它抛，而抛出来的不是"这份截断了"
        data = data[: len(data) - len(data) % frame_bytes]
    if form == 1 and bits == 16:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif form == 3 and bits == 32:
        samples = np.frombuffer(data, dtype="<f4").astype(np.float32)
    else:
        raise ValueError(f"只支持 16-bit PCM 与 32-bit float，这份是 fmt={form} {bits}-bit")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), rate


def transcribe_wav(wav_path: str, recognizer, sample_rate: int = VOICE_INPUT_SAMPLE_RATE) -> str:
    """转写**盘上的一个 wav**（取证用），与录音那一路共用同一个识别器与同一段解码。

    取样走 `read_wav_samples`（GSV 是 16-bit PCM、VITS 是 32-bit float，两种都得读）。
    采样率不同就线性插值到 `sample_rate`——合成产物是 22.05/32 kHz，模型只吃 16 kHz，
    这一步不做的话读数不是"这台仪器读出来的"，是"模型被喂了错采样率之后吐出来的"。
    太短（不足 0.25 s）与录音那一路同判，返回空串。
    """
    import numpy as np

    samples, rate = read_wav_samples(wav_path)
    if rate != sample_rate and rate > 0 and samples.size:
        points = np.linspace(0, samples.size - 1, int(samples.size * sample_rate / rate))
        samples = np.interp(points, np.arange(samples.size), samples).astype(np.float32)
    if samples.size < sample_rate // 4:
        log.warning("[voice] audio too short, skipping")
        return ""
    return _decode_samples(recognizer, samples, sample_rate)


def _device_supports_input(p_audio, index: int, sample_rate: int, channels: int) -> bool:
    """这台设备能否按 (sample_rate, channels, paInt16) 开输入流。

    pyaudio 用 ``ValueError`` 表示"不支持"，实测与真开流的失败一致（hw 设备固定 48k 时
    16k 直接 Errno -9997）。探针本身出别的错（老版本、缺方法）时**按可用处理**——
    探测失败不该把设备判死，真开流时自会出声。
    """
    try:
        import pyaudio

        p_audio.is_format_supported(
            sample_rate,
            input_device=index,
            input_channels=channels,
            input_format=pyaudio.paInt16,
        )
    except ValueError:
        return False
    except Exception:
        return True
    return True


def list_input_devices(
    sample_rate: int = VOICE_INPUT_SAMPLE_RATE, channels: int = 1
) -> list[tuple[int, str, int, bool]]:
    """枚举有输入通道的设备 ``[(index, name, channels, 该采样率可用)]``。

    pyaudio 缺失或枚举异常时返回空表，不抛。
    """
    try:
        import pyaudio
    except ImportError:
        return []

    devices: list[tuple[int, str, int, bool]] = []
    audio = None
    try:
        audio = pyaudio.PyAudio()
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            try:
                input_channels = int(info.get("maxInputChannels", 0) or 0)
            except (TypeError, ValueError):
                input_channels = 0
            if input_channels > 0:
                devices.append(
                    (
                        index,
                        str(info.get("name") or f"设备 {index}"),
                        input_channels,
                        _device_supports_input(audio, index, sample_rate, channels),
                    )
                )
    except Exception as exc:
        log.warning(f"[voice] 设备枚举失败: {type(exc).__name__}: {exc}")
    finally:
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
    return devices


class VoiceEngine(QThread):
    """后台录音 + 语音识别线程。"""

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    result_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        language: str = "zh",
        sample_rate: int = VOICE_INPUT_SAMPLE_RATE,
        channels: int = 1,
        chunk_ms: int = 30,
        precision: object = None,
        input_device_index: object = None,
        mode: object = None,
        gate=None,
        vad_config: object = None,
    ):
        super().__init__()
        self._language = str(language or "zh").strip() or "zh"
        self._sample_rate = int(sample_rate or VOICE_INPUT_SAMPLE_RATE)
        self._channels = int(channels or 1)
        self._chunk_size = max(256, self._sample_rate * int(chunk_ms or 30) // 1000)
        self._precision = normalize_voice_asr_precision(precision)
        self._input_device_index = normalize_input_device_index(input_device_index)
        self._mode = normalize_voice_mode(mode)
        # 门控默认全局那只：桌面 _play_audio 往同一只钟上 hold，这里才接得到。
        # 测试可注入假钟的独立实例——门控的验收不靠真播音。
        self._gate = gate if gate is not None else GLOBAL_ECHO_GATE
        self._vad_cfg = dict(vad_config) if isinstance(vad_config, dict) else {}

        self._stop = False
        self._toggle = False
        self._recognizer = None
        self._audio_frames: list[bytes] = []

    @property
    def mode(self) -> str:
        return self._mode

    def toggle(self) -> bool:
        if self.isRunning():
            self._toggle = True
            return True
        self._toggle = False
        self._stop = False
        self.start()
        return True

    def stop(self, timeout_ms: int = 0) -> bool:
        self._stop = True
        self._toggle = True
        if not self.isRunning():
            return True
        try:
            to = max(0, int(timeout_ms or 0))
        except (TypeError, ValueError):
            to = 0
        if to <= 0:
            return False
        return bool(self.wait(to))

    # ------------------------------------------------------------------
    # 线程主循环（录音 + 识别）
    # ------------------------------------------------------------------

    def run(self):
        try:
            if not self._load_model():
                return

            import pyaudio

            self._audio_frames = []
            p = pyaudio.PyAudio()
            mic_idx = self._pick_mic_index(p)
            log.info(f"[voice] using mic device index={mic_idx}")

            stream = p.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                input_device_index=mic_idx,
                frames_per_buffer=self._chunk_size,
            )
            self.recording_started.emit()
            log.info(f"[voice] recording started (mode={self._mode})")

            if self._mode == "hands_free":
                self._loop_hands_free(stream)
            else:
                self._loop_toggle(stream)

            stream.stop_stream()
            stream.close()
            p.terminate()
            self.recording_stopped.emit()
            log.info(f"[voice] recording stopped, frames={len(self._audio_frames)}")

            # hands_free 每句在循环里就地转写并发射；这里再收尾一次会把最后半句
            # 当整句播第二遍，所以只走 toggle 的收尾。
            if self._stop or self._mode == "hands_free":
                return

            text = self._transcribe()
            if text:
                self.result_ready.emit(text)

        except ImportError:
            self.error.emit("pyaudio 未安装，请在配置页下载依赖")
        except Exception as exc:
            log.error(f"[voice] error: {type(exc).__name__}: {exc}")
            log.trace(lambda: traceback.format_exc())
            self.error.emit(f"语音识别异常: {exc}")

    # ------------------------------------------------------------------
    # 收音循环（两种模式共用同一套帧读取与门控）
    # ------------------------------------------------------------------

    def _read_chunk(self, stream):
        """读一帧；播音期（门控生效）整帧丢，返回 None。

        「丢」而不是「暂停」，是因为帧的时间轴与真实时间对齐：丢了只是把那段时间
        当静音，攒着晚点再读会把「她说话的时刻」压扁进用户的话里。pyaudio 单帧
        读失败也只丢这一帧——录音中间断一帧，比整条录音被拆掉好。
        """
        try:
            data = stream.read(self._chunk_size, exception_on_overflow=False)
        except Exception:  # noqa: BLE001 - 单帧读失败丢一帧，不拆整条录音
            return None
        if self._gate is not None and self._gate.active():
            return None
        return data

    def _loop_toggle(self, stream) -> None:
        """点按切换：录到第二次点击（_toggle）或 stop() 为止。"""
        while not self._stop:
            data = self._read_chunk(stream)
            if data is not None:
                self._audio_frames.append(data)
            if self._toggle:
                break

    def _loop_hands_free(self, stream) -> None:
        """常驻收音 + VAD 自动断句：断出一句就转写发射一句，直到 stop()。"""
        from meapet.voice.vad import VadModelMissing, VadSegmenter

        try:
            segmenter = VadSegmenter(**self._vad_kwargs())
        except ImportError:
            self.error.emit("sherpa-onnx 未安装，请在配置页下载依赖")
            return
        except VadModelMissing as exc:
            self.error.emit(f"{exc}")
            return
        except Exception as exc:  # noqa: BLE001 - 加载失败的原文要给用户
            self.error.emit(f"VAD 加载失败: {exc}")
            return

        while not self._stop:
            data = self._read_chunk(stream)
            if data is None:
                # 播音期：检测器里攒着的多半是她自己的尾音，清掉重新听，
                # 否则下一句用户的话会和她的尾音拼成一句。
                segmenter.reset()
                continue
            for segment in segmenter.feed_pcm16(data):
                self._emit_segment(segment)
        for segment in segmenter.flush():
            self._emit_segment(segment)

    def _emit_segment(self, segment) -> None:
        """一句就绪就转写并发射；太短的碎句（<250 ms）与录音那一路同判，丢。"""
        if segment is None or len(segment) < self._sample_rate // 4:
            return
        if self._recognizer is None:
            return
        try:
            result = _decode_samples(self._recognizer, segment, self._sample_rate)
        except Exception as exc:  # noqa: BLE001 - 单句转写失败不拆整条监听
            log.error(f"[voice] segment decode error: {type(exc).__name__}: {exc}")
            return
        if result:
            log.info(f"[voice] segment: '{result[:80]}'")
            self.result_ready.emit(result)

    def _vad_kwargs(self) -> dict:
        keys = (
            "threshold",
            "min_silence_duration",
            "min_speech_duration",
            "max_speech_duration",
        )
        return {key: self._vad_cfg[key] for key in keys if key in self._vad_cfg}

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _pick_mic_index(self, p_audio) -> int:
        """用户指定的设备优先；那台不可用时回落自动选路（出声，不闷掉）。

        "不可用"有两种，实测都撞到过，日志须分开点名：设备压根枚举不到/没有输入通道，
        或设备在但开不了这个格式——真开流按 (16000 Hz, 单声道) 试过，同一批设备分别报
        Errno -9997（采样率）与 -9998（通道数），所以理由按整组格式写，别只指采样率。
        """
        if self._input_device_index is None:
            return self._find_mic_device(p_audio)

        info = None
        try:
            info = p_audio.get_device_info_by_index(self._input_device_index)
        except Exception:
            info = None
        reason = ""
        if not info or int(info.get("maxInputChannels", 0) or 0) <= 0:
            reason = "枚举不到或没有输入通道"
        elif not _device_supports_input(
            p_audio, self._input_device_index, self._sample_rate, self._channels
        ):
            reason = f"不支持 {self._sample_rate} Hz/{self._channels} 声道输入"

        if not reason:
            return self._input_device_index

        log.info(
            f"[voice] 指定设备 index={self._input_device_index} {reason}，回落自动选路"
        )
        return self._find_mic_device(p_audio)

    def _find_mic_device(self, p_audio) -> int:
        """找真正的麦克风，跳过立体声混音/扬声器回录等设备。"""
        exclude = {"立体声混音", "stereo mix", "扬声器", "speaker",
                    "声音映射器", "mapper", "主声音捕获", "主声音"}
        default = p_audio.get_default_input_device_info()
        default_name = (default.get("name") or "").lower()
        excluded = any(kw in default_name for kw in exclude)
        if not excluded:
            return default.get("index")

        # 默认设备是立体声混音等，遍历找真正的麦克风
        for i in range(p_audio.get_device_count()):
            info = p_audio.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) <= 0:
                continue
            name = (info.get("name") or "").lower()
            if not any(kw in name for kw in exclude):
                return i

        return default.get("index")  # fallback

    def _load_model(self) -> bool:
        if self._recognizer is not None:
            return True

        try:
            self._recognizer = build_recognizer(self._precision, self._sample_rate)
            return True

        except ImportError:
            self.error.emit("sherpa-onnx 未安装，请在配置页下载依赖")
            return False
        except AsrModelMissing as exc:
            self.error.emit(f"{exc}，请在配置页下载")
            return False
        except Exception as exc:
            self.error.emit(f"模型加载失败: {exc}")
            log.error(f"[voice] failed to load model: {exc}")
            return False

    # ------------------------------------------------------------------
    # 转文字
    # ------------------------------------------------------------------

    def _transcribe(self) -> str:
        if not self._audio_frames or self._recognizer is None:
            return ""

        audio_data = b"".join(self._audio_frames)
        total_samples = len(audio_data) // 2
        if total_samples < self._sample_rate // 4:
            log.warning("[voice] audio too short, skipping")
            return ""

        try:
            import numpy as np

            samples = (
                np.frombuffer(audio_data, dtype=np.int16)
                .astype(np.float32)
                / 32768.0
            )
            result = _decode_samples(self._recognizer, samples, self._sample_rate)

            log.info(
                f"[voice] transcribed: "
                f"'{result[:80]}{'...' if len(result) > 80 else ''}'"
            )
            return result

        except Exception as exc:
            log.error(f"[voice] transcription error: {exc}")
            log.trace(lambda: traceback.format_exc())
            self.error.emit(f"转文字失败: {exc}")
            return ""


def create_voice_engine(config: Optional[dict] = None) -> Optional[VoiceEngine]:
    cfg = config or {}
    if not cfg.get("enabled", False):
        return None
    return VoiceEngine(
        language=cfg.get("language", "zh"),
        precision=cfg.get("precision"),
        input_device_index=cfg.get("input_device_index"),
        mode=cfg.get("mode"),
        vad_config=cfg.get("vad"),
    )
