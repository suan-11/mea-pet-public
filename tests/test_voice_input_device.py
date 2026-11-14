"""语音输入的录音设备选路与模型档位。

两件真问题各自有回归兜着：
- 指定设备要么被采用、要么**出声**回落自动（不闷掉，也不静默用错麦克风）。
- 档位是单一真值表：完整性校验、下载 allow_patterns、运行时读的文件名同源，
  不允许出现「下了 fp32 却仍读 int8」这种两处各说各话的第三种状态。
"""
from __future__ import annotations

import os
import sys
import types

import pytest

from meapet.config.normalizers import normalize_input_device_index
from meapet.paths import (
    VOICE_ASR_MODEL_FILES,
    VOICE_ASR_PRECISION_DEFAULT,
    VOICE_ASR_REQUIRED_FILES,
    VOICE_ASR_REQUIRED_FILES_BY_PRECISION,
    find_voice_asr_model_dir,
    normalize_voice_asr_precision,
)
from meapet.voice.engine import (
    VOICE_INPUT_SAMPLE_RATE,
    VoiceEngine,
    list_input_devices,
)


# ----------------------------------------------------------------------
# 档位词汇表：只有一处真值
# ----------------------------------------------------------------------


def test_required_files_and_allow_patterns_share_one_table():
    assert set(VOICE_ASR_REQUIRED_FILES_BY_PRECISION) == set(VOICE_ASR_MODEL_FILES)
    for tier, files in VOICE_ASR_MODEL_FILES.items():
        assert VOICE_ASR_REQUIRED_FILES_BY_PRECISION[tier] == tuple(files.values())
    # 旧导出名仍指默认档：读物与运行时不会分岔到两个档位
    assert VOICE_ASR_REQUIRED_FILES == (
        VOICE_ASR_REQUIRED_FILES_BY_PRECISION[VOICE_ASR_PRECISION_DEFAULT]
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, VOICE_ASR_PRECISION_DEFAULT),
        ("", VOICE_ASR_PRECISION_DEFAULT),
        ("   ", VOICE_ASR_PRECISION_DEFAULT),
        ("nope", VOICE_ASR_PRECISION_DEFAULT),
        ("int8", "int8"),
        (" FP32 ", "fp32"),
    ],
)
def test_normalize_precision_converges_to_known_tier(raw, expected):
    assert normalize_voice_asr_precision(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        ("", None),
        ("auto", None),
        (-1, None),
        (3, 3),
        ("5", 5),
        (0, 0),
    ],
)
def test_normalize_input_device_index(raw, expected):
    assert normalize_input_device_index(raw) == expected


def test_find_model_dir_checks_completeness_per_tier(tmp_path, monkeypatch):
    """只有 int8 四件套的目录：int8 档找到，fp32 档必须说"缺"，不能拿 int8 顶。

    目录形状按**真机干净装机落盘**那一条写（modelscope 在 cache_dir 下建
    models/<name>/snapshots/master），不是拍脑袋的扁平布局。
    """
    import meapet.paths as paths

    new_cache = tmp_path / "runtime-voice-asr"
    monkeypatch.setattr(
        paths, "data_path", lambda *parts: str(new_cache.joinpath(*parts))
    )
    monkeypatch.setattr(
        paths, "project_path", lambda *parts: str(tmp_path.joinpath(*parts))
    )

    model_dir = (
        paths.voice_asr_cache_dir()
        / "models"
        / paths.VOICE_ASR_MODEL_CACHE_NAME
        / "snapshots"
        / "master"
    )
    model_dir.mkdir(parents=True)
    for name in VOICE_ASR_REQUIRED_FILES_BY_PRECISION["int8"]:
        (model_dir / name).write_bytes(b"model")

    assert find_voice_asr_model_dir("int8") == model_dir
    assert find_voice_asr_model_dir() == model_dir
    assert find_voice_asr_model_dir("fp32") is None


# ----------------------------------------------------------------------
# 设备选路
# ----------------------------------------------------------------------


class _FakeAudio:
    """只喂 get_device_info_by_index / is_format_supported，不碰真实音频后端。"""

    def __init__(self, devices: dict[int, dict], supported: dict[int, bool] | None = None):
        self._devices = devices
        self._supported = supported or {}

    def get_device_count(self):
        return max(self._devices, default=-1) + 1

    def get_device_info_by_index(self, index):
        if index not in self._devices:
            raise IndexError(f"no device {index}")
        return self._devices[index]

    def is_format_supported(self, rate, input_device=None, input_channels=None,
                            input_format=None):
        if not self._supported.get(input_device, True):
            raise ValueError(f"device {input_device} cannot do {rate}")
        return rate


def _engine(**kwargs) -> VoiceEngine:
    return VoiceEngine(**kwargs)


class _RecordingLog:
    """把 engine.log 的三个级别都收进同一个列表：断言只关心"有没有出声、说了什么"。"""

    def __init__(self, sink: list[str]):
        self.sink = sink

    def info(self, message):
        self.sink.append(message)

    def warning(self, message):
        self.sink.append(message)

    def error(self, message):
        self.sink.append(message)


def _quiet_log(monkeypatch) -> list[str]:
    from meapet.voice import engine as engine_module

    logged: list[str] = []
    monkeypatch.setattr(engine_module, "log", _RecordingLog(logged))
    return logged


def test_pick_mic_honours_requested_device():
    audio = _FakeAudio({7: {"name": "mic", "maxInputChannels": 1}})
    engine = _engine(input_device_index=7)
    assert engine._pick_mic_index(audio) == 7


def test_pick_mic_falls_back_with_log_when_device_unusable(monkeypatch):
    logged = _quiet_log(monkeypatch)
    audio = _FakeAudio(
        {
            7: {"name": "no inputs", "maxInputChannels": 0},
            9: {"name": "real mic", "maxInputChannels": 2},
        }
    )
    engine = _engine(input_device_index=7)
    engine._find_mic_device = lambda _p: 9  # 自动选路由既有实现负责，这里只验回落发生

    assert engine._pick_mic_index(audio) == 9
    assert any("回落自动选路" in message for message in logged)


def test_pick_mic_falls_back_when_device_cannot_do_the_sample_rate(monkeypatch):
    """设备在、也有输入通道，但开不了 16k（本机 hw 内置麦 index 9 就是这样，实测）。

    理由须点名到"不支持采样率"，否则现场报告只剩一句"不可用"，查不出是哪种。
    """
    stub = types.ModuleType("pyaudio")
    stub.paInt16 = 8
    monkeypatch.setitem(sys.modules, "pyaudio", stub)
    logged = _quiet_log(monkeypatch)

    audio = _FakeAudio({9: {"name": "Built-in Audio Analog Stereo", "maxInputChannels": 4}},
                       supported={9: False})
    engine = _engine(input_device_index=9)
    engine._find_mic_device = lambda _p: 8

    assert engine._pick_mic_index(audio) == 8
    assert any("不支持 16000 Hz" in message for message in logged)


def test_pick_mic_falls_back_when_index_missing_entirely():
    audio = _FakeAudio({9: {"name": "real mic", "maxInputChannels": 2}})
    engine = _engine(input_device_index=7)
    engine._find_mic_device = lambda _p: 9
    assert engine._pick_mic_index(audio) == 9


def test_pick_mic_without_preference_goes_straight_to_autoselect():
    audio = _FakeAudio({})
    engine = _engine()
    engine._find_mic_device = lambda _p: 42
    assert engine._pick_mic_index(audio) == 42


def test_engine_keeps_illegal_device_as_auto():
    assert _engine(input_device_index="left ear")._input_device_index is None
    assert _engine(input_device_index=None)._input_device_index is None
    assert _engine(input_device_index=4)._input_device_index == 4


def test_engine_precision_defaults_to_table_tier():
    assert _engine()._precision == VOICE_ASR_PRECISION_DEFAULT
    assert _engine(precision="fp32")._precision == "fp32"
    assert _engine(precision="bf16")._precision == VOICE_ASR_PRECISION_DEFAULT


def test_list_input_devices_without_pyaudio_returns_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    assert list_input_devices() == []


def test_list_input_devices_skips_devices_without_input_channels(monkeypatch):
    class _PyAudio:
        paInt16 = 8

        def get_device_count(self):
            return 3

        def get_device_info_by_index(self, index):
            return {
                0: {"name": "speakers", "maxInputChannels": 0},
                1: {"name": "mic", "maxInputChannels": 2},
                2: {"name": "", "maxInputChannels": 1},
            }[index]

        def is_format_supported(self, rate, input_device=None, input_channels=None,
                               input_format=None):
            if input_device == 2:
                raise ValueError("48k-only hardware")
            return rate

        def terminate(self):
            pass

    module = types.ModuleType("pyaudio")
    module.PyAudio = _PyAudio
    module.paInt16 = 8
    monkeypatch.setitem(sys.modules, "pyaudio", module)

    assert list_input_devices() == [
        (1, "mic", 2, True),
        (2, "设备 2", 1, False),
    ]


# ----------------------------------------------------------------------
# 向导页：往返 + 按档下载
# ----------------------------------------------------------------------


@pytest.fixture()
def page():
    """conftest 的 session 级 QApplication 已就位（autouse），这里只造页面。"""
    from wizard.page_voice_input import VoiceInputPage

    return VoiceInputPage()


def test_wizard_device_and_tier_round_trip(page, monkeypatch):
    monkeypatch.setattr(
        "wizard.page_voice_input.list_input_devices",
        lambda: [(3, "Built-in", 2, True), (7, "USB", 1, False)],
    )
    page._apply_devices(7)
    assert page.device_combo.currentData() == 7
    # 开不了 16k 的设备在文案里就得看出来——选了也不会生效，别让人白选
    assert "不支持 16kHz" in page.device_combo.currentText()
    assert "不支持 16kHz" not in page.device_combo.itemText(
        page.device_combo.findData(3)
    )
    page.precision_combo.setCurrentIndex(
        page.precision_combo.findData("fp32")
    )
    assert page.collect() == {
        "voice_input": {
            "enabled": False,
            "language": "zh",
            "auto_send": False,
            "precision": "fp32",
            "input_device_index": 7,
        }
    }


def test_wizard_keeps_stale_device_index_instead_of_silently_resetting(page, monkeypatch):
    """存过的设备号当下枚举不到时仍列出来（标不可用），一次保存不得把它抹成「自动」。"""
    monkeypatch.setattr(
        "wizard.page_voice_input.list_input_devices",
        lambda: [(3, "a", 1, True)],
    )
    page.apply_config({"input_device_index": 99})
    assert page.device_combo.currentData() == 99
    assert page.collect()["voice_input"]["input_device_index"] == 99

    # 用户主动选回「自动」后，刷新状态不得把旧值复活
    page.device_combo.setCurrentIndex(0)
    page._check_deps()
    assert page.device_combo.currentData() is None


def test_wizard_falls_back_to_auto_for_illegal_saved_index(page, monkeypatch):
    monkeypatch.setattr("wizard.page_voice_input.list_input_devices", lambda: [(3, "a", 1, True)])
    page.apply_config({"input_device_index": "auto"})
    assert page.device_combo.currentData() is None


def test_install_worker_downloads_only_the_selected_tier(page, tmp_path, monkeypatch):
    import wizard.page_voice_input as pvi

    captured: dict = {}

    def fake_snapshot_download(repo, cache_dir=None, allow_patterns=None, **kwargs):
        captured["repo"] = repo
        captured["cache_dir"] = str(cache_dir)
        captured["allow_patterns"] = list(allow_patterns or [])

    module = types.ModuleType("modelscope")
    module.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "modelscope", module)
    monkeypatch.setattr(pvi, "is_frozen", lambda: False)
    monkeypatch.setattr(
        pvi, "_voice_input_install_command", lambda: ["python", "-m", "pip", "install", "x"]
    )
    monkeypatch.setattr(
        pvi.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(pvi, "voice_asr_cache_dir", lambda: tmp_path / "asr-cache")

    results: list = []
    worker = pvi._InstallWorker("fp32")
    worker.finished.connect(lambda ok, detail: results.append((ok, detail)))
    worker.run()  # 同步跑，不起线程

    assert results == [(True, "安装完成！依赖 + 模型已就绪")]
    assert captured["allow_patterns"] == list(
        VOICE_ASR_REQUIRED_FILES_BY_PRECISION["fp32"]
    )
    assert captured["repo"] == pvi.VOICE_ASR_MODEL_REPO


def test_engine_receives_both_new_keys_from_config(monkeypatch):
    """配置里的档位与设备号必须真传进 VoiceEngine，不能只存在配置里。"""
    from meapet.desktop import voice_mixin as mixin

    seen: dict = {}

    class _Signal:
        def connect(self, _slot):
            pass

    class _RecordingEngine:
        recording_started = _Signal()
        recording_stopped = _Signal()
        result_ready = _Signal()
        error = _Signal()

        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("meapet.voice.engine.VoiceEngine", _RecordingEngine)

    host = mixin.PetVoiceMixin.__new__(mixin.PetVoiceMixin)
    host.config = {
        "voice_input": {
            "enabled": True,
            "language": "en",
            "precision": "fp32",
            "input_device_index": 7,
        }
    }
    host._start_voice_engine()
    # mode / vad_config 是 P1-3 加的：引擎要按模式决定收音循环，按 vad 那几格
    # 造检测器。配置里没写就传 None，由引擎自己收敛到默认档——不在这里替它补默认值，
    # 否则「默认值只有一处」这条纪律就破了。
    assert seen == {
        "language": "en",
        "precision": "fp32",
        "input_device_index": 7,
        "mode": None,
        "vad_config": None,
    }


# ----------------------------------------------------------------------
# 取证用的那副耳朵：模型构造与"转写盘上一个 wav"
# ----------------------------------------------------------------------


class _FakeStream:
    def __init__(self, sink: dict):
        self._sink = sink

    def accept_waveform(self, sample_rate, samples):
        self._sink["sample_rate"] = sample_rate
        self._sink["samples"] = samples
        self._sink["peak"] = float(max(abs(float(v)) for v in samples)) if len(samples) else 0.0

    def input_finished(self):
        # 补静音会让流式模型复读；取证那一发悄悄补了尾，读到的连读长度就是假的
        self._sink["input_finished"] = True


class _FakeRecognizer:
    """只记"喂进来什么"，转写串由外部给定。

    `is_ready` 只放行一次：解码循环真跑一轮，读数才有"确实解过"的形状，而不是桩自己返回的。
    """

    def __init__(self, result: str = "你好"):
        self._result = result
        self.sink: dict = {"input_finished": False, "decodes": 0}
        self._ready = 1

    def create_stream(self):
        return _FakeStream(self.sink)

    def is_ready(self, _stream):
        return self._ready > 0

    def decode_stream(self, _stream):
        self._ready -= 1
        self.sink["decodes"] += 1

    def get_result(self, _stream):
        return self._result


def _install_fake_sherpa(monkeypatch, sink: dict):
    """把 `sherpa_onnx` 换成只记参数的桩，返回被调用的 kwargs。"""

    class _OnlineRecognizer:
        @staticmethod
        def from_transducer(**kwargs):
            sink.update(kwargs)
            return _FakeRecognizer()

    module = types.SimpleNamespace(OnlineRecognizer=_OnlineRecognizer)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    return sink


def test_build_recognizer_uses_the_product_parameters_not_a_headless_copy(tmp_path, monkeypatch):
    """无头取证与录音那一路**共用这一枚构造**：换掉任何一项参数就换一种坏法。

    这里钉的是那五项实测出来的形状（greedy_search / feature_dim=80 / provider=cpu /
    num_threads=4 / 采样率），不是"看起来像"。
    """
    from meapet.voice import engine as engine_module

    tier = VOICE_ASR_PRECISION_DEFAULT
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    sink = _install_fake_sherpa(monkeypatch, {})
    monkeypatch.setattr(engine_module, "find_voice_asr_model_dir", lambda _p: str(model_dir))

    engine_module.build_recognizer(tier)

    files = VOICE_ASR_MODEL_FILES[tier]
    for key in ("encoder", "decoder", "joiner", "tokens"):
        assert sink[key] == os.path.join(str(model_dir), files[key])
    assert sink["decoding_method"] == "greedy_search"
    assert sink["feature_dim"] == 80
    assert sink["provider"] == "cpu"
    assert sink["num_threads"] == 4
    assert sink["sample_rate"] == VOICE_INPUT_SAMPLE_RATE


def test_build_recognizer_names_the_missing_tier_instead_of_returning_none(monkeypatch):
    """`None` 与"缺哪一档"是两种处置（下模型 / 重装），取证侧要把它们分开报。

    桩掉 `sherpa_onnx` 是必须的：`build_recognizer` 先 import 再查模型目录（那两步各对应一种
    缺席，`engine.py:37` 的 docstring 写明了），CI 装的是 `.[linux]` 那一份依赖、没有 sherpa，
    不桩就会拿到 `ModuleNotFoundError` 而不是这里要验的那一支。
    """
    from meapet.voice import engine as engine_module

    _install_fake_sherpa(monkeypatch, {})
    monkeypatch.setattr(engine_module, "find_voice_asr_model_dir", lambda _p: None)
    with pytest.raises(engine_module.AsrModelMissing, match="int8"):
        engine_module.build_recognizer("int8")


def test_record_path_and_forensics_share_one_decode(monkeypatch, tmp_path):
    """红线：**不许出现第二套解码**。两处各写一份，录音与取证就各按各的坏法出串。"""
    from meapet.voice import engine as engine_module

    calls: list = []
    monkeypatch.setattr(
        engine_module,
        "_decode_samples",
        lambda recognizer, samples, sample_rate: calls.append(len(samples)) or "同一个桩",
    )
    wav = _write_wav(tmp_path / "a.wav", rate=VOICE_INPUT_SAMPLE_RATE, frames=8000)

    assert engine_module.transcribe_wav(wav, _FakeRecognizer()) == "同一个桩"

    thread = engine_module.VoiceEngine()
    thread._recognizer = _FakeRecognizer()
    thread._audio_frames = [b"\0\0" * 8000]
    assert thread._transcribe() == "同一个桩"
    assert len(calls) == 2, "两路都该走 _decode_samples，而不是各解各的"


def test_transcribe_wav_resamples_the_artifact_to_the_models_rate(tmp_path):
    """合成产物是 22.05/32/44.1 kHz，模型只吃 16 kHz。

    不重采样也能跑，但读出来的串是"模型被喂了错采样率之后吐出来的"，不是这台仪器的读数。
    """
    from meapet.voice import engine as engine_module

    wav = _write_wav(tmp_path / "32k.wav", rate=32000, frames=32000)  # 1 秒
    recognizer = _FakeRecognizer()

    assert engine_module.transcribe_wav(wav, recognizer) == "你好"
    assert recognizer.sink["sample_rate"] == VOICE_INPUT_SAMPLE_RATE
    assert abs(len(recognizer.sink["samples"]) - 16000) <= 1, "1 s @32k 该重采成 16k"
    assert recognizer.sink["input_finished"] is True
    assert recognizer.sink["decodes"] == 1


def test_transcribe_wav_scales_int16_to_float_and_averages_channels(tmp_path):
    """`-32768..32767 → -1..1` 是 sherpa 那一侧的口径；立体声取均值，不是取左声道。"""
    from meapet.voice import engine as engine_module

    mono = _write_wav(tmp_path / "mono.wav", rate=16000, frames=16000, value=16384)
    recognizer = _FakeRecognizer()
    engine_module.transcribe_wav(mono, recognizer)
    assert recognizer.sink["peak"] == pytest.approx(0.5, abs=1e-4)

    stereo = _write_wav(
        tmp_path / "stereo.wav", rate=16000, frames=16000, channels=2, value=16384
    )
    recognizer = _FakeRecognizer()
    engine_module.transcribe_wav(stereo, recognizer)
    assert len(recognizer.sink["samples"]) == 16000, "两声道要折成一路，不能喂 32000 点"
    assert recognizer.sink["peak"] == pytest.approx(0.5, abs=1e-4)


def test_transcribe_wav_strips_the_sil_token(tmp_path):
    from meapet.voice import engine as engine_module

    wav = _write_wav(tmp_path / "a.wav", rate=16000, frames=16000)
    assert engine_module.transcribe_wav(wav, _FakeRecognizer("你好 SIL 我是")) == "你好我是"


def test_transcribe_wav_returns_empty_for_audio_shorter_than_the_record_path(tmp_path):
    """不足 0.25 s 与录音那一路同判：不喂。

    这一格必须有：塌到 1 s 以内那几发读的是"一个字都没念出来"，而"太短不读"与"模型没出声"
    在报告里不能长成同一句话——所以短到不喂也要**认得出是没喂**（空串），不是异常。
    """
    from meapet.voice import engine as engine_module

    wav = _write_wav(tmp_path / "tiny.wav", rate=16000, frames=1600)  # 0.1 s
    recognizer = _FakeRecognizer()
    assert engine_module.transcribe_wav(wav, recognizer) == ""
    assert recognizer.sink["decodes"] == 0


def test_transcribe_wav_refuses_a_bit_depth_it_cannot_read(tmp_path):
    """认不出的位深读出来是"字种 0/0"那种假读数，不如当场说清格式不对。"""
    import wave as _wave

    path = str(tmp_path / "float.wav")
    with _wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(3)  # 24-bit
        handle.setframerate(16000)
        handle.writeframes(b"\0\0\0" * 16000)
    from meapet.voice import engine as engine_module

    with pytest.raises(ValueError, match="24-bit"):
        engine_module.transcribe_wav(path, _FakeRecognizer())


def test_transcribe_wav_reads_the_float32_artifact_vits_actually_writes(tmp_path):
    """这一格是**真读数踩出来的**：VITS 那一路的产物是 `fmt=3`（32-bit float），stdlib `wave`
    当场抛 `Error: unknown format: 3`，于是这台仪器在口径里唯一保证正常的那一臂上是瞎的
    （`只保证VITS的zh正常`）——只剩塌句那一臂，参照带就没法比。

    这里同时钉两件事：float 样本**不能再除 32768**（除了就成静音，读数是"零复读"的假清白），
    以及 `wav_duration_seconds` 读同一份文件要给出秒数而不是 0.00s。
    """
    from meapet.tts.diagnostics import wav_duration_seconds
    from meapet.voice import engine as engine_module

    path = _write_float_wav(tmp_path / "vits.wav", rate=16000, samples=[0.25] * 16000)
    recognizer = _FakeRecognizer("你好")
    assert engine_module.transcribe_wav(path, recognizer) == "你好"
    assert recognizer.sink["peak"] == pytest.approx(0.25), "float 样本被当整数缩放了"
    assert recognizer.sink["decodes"] == 1
    assert wav_duration_seconds(path) == pytest.approx(1.0)


def _write_float_wav(path, *, rate, samples):
    """按 VITS 那一路的形状手写一份 `fmt=3` wav（stdlib `wave` 写不出 float）。"""
    import struct

    payload = b"".join(struct.pack("<f", float(v)) for v in samples)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
        + b"data"
        + struct.pack("<I", len(payload))
    )
    path.write_bytes(header + payload)
    return str(path)


def _write_wav(path, *, rate, frames, channels=1, value=0):
    import wave as _wave

    import numpy as np

    raw = np.full(frames * channels, value, dtype=np.int16)
    with _wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(raw.tobytes())
    return str(path)
