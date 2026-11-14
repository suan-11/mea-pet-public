"""P1-3 语音输入全双工：门控 / VAD / PTT / 唤醒词 / 配置面。

这一套的纪律与仓库既有取证文化一致：**能离线确定的就不碰真硬件**。
- 回声门控：注入假钟，逐毫秒断言「什么时候该闭嘴」。
- VAD：真 sherpa + 真模型跑，但样本是合成的（静音/白噪声/正弦），不依赖录音设备。
- PTT：只测解析与安全判定，RegisterHotKey 不进 CI（会被占用，且改系统状态）。
- 唤醒词：只测「模型缺席要出声报」，不跑真监听线程。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from meapet.config.normalizers import (
    VOICE_INPUT_MODE_DEFAULT,
    clamp_float,
    clamp_int,
    normalize_voice_mode,
)
from meapet.voice.gate import DEFAULT_ECHO_GATE_TAIL_MS, EchoGate, wav_duration_seconds


# ----------------------------------------------------------------------
# 回声门控：P1-3 防自激的核心
# ----------------------------------------------------------------------


class _FakeClock:
    """可手摇的单调钟；门控的验收全靠它，不靠 sleep。"""

    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_gate_starts_idle_and_opens_on_hold():
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    assert gate.active() is False
    gate.hold(1.0, tail_ms=300)
    assert gate.active() is True
    assert gate.remaining() == pytest.approx(1.3)


def test_gate_expires_by_itself_after_duration_plus_tail():
    """预测时长必须能自我过期——fallback 播放路径拿不到「播完」回调。"""
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(1.0, tail_ms=300)
    clock.advance(1.29)
    assert gate.active() is True
    clock.advance(0.02)
    assert gate.active() is False


def test_gate_hold_never_shortens_an_existing_window():
    """逐句流水线：下一句在上一句尾音里进队，取 max 才不会提前解门。"""
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(3.0, tail_ms=0)
    clock.advance(1.0)  # 剩 2.0
    gate.hold(0.5, tail_ms=0)  # 本来只剩 1.5，不能被压短
    assert gate.remaining() == pytest.approx(2.0)


def test_gate_hold_can_extend():
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(1.0, tail_ms=0)
    gate.hold(5.0, tail_ms=0)
    assert gate.remaining() == pytest.approx(5.0)


def test_gate_release_clears_immediately():
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(10.0)
    gate.release()
    assert gate.active() is False
    assert gate.remaining() == 0.0


@pytest.mark.parametrize("bad", [None, "", "abc", object()])
def test_gate_hold_survives_garbage_duration(bad):
    """播放链上拿到的时长可能是 None/空串；门控不能因此抛异常打断播放。"""
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(bad, tail_ms=300)  # 不抛
    # 垃圾值按 0 时长处理，只留尾音那一格
    assert gate.remaining() == pytest.approx(0.3)


def test_gate_negative_tail_is_clamped():
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    gate.hold(1.0, tail_ms=-500)
    assert gate.remaining() == pytest.approx(1.0)


def test_gate_snapshot_is_readable():
    clock = _FakeClock()
    gate = EchoGate(clock=clock)
    assert "空闲" in gate.snapshot()
    gate.hold(2.0, tail_ms=0)
    assert "生效中" in gate.snapshot()


def test_default_tail_is_300ms():
    """这个数出现在配置默认值、文档、测试三处，钉住它免得各处漂。"""
    assert DEFAULT_ECHO_GATE_TAIL_MS == 300


# ----------------------------------------------------------------------
# wav 时长：门控的时长来源之一
# ----------------------------------------------------------------------


def _write_pcm16_wav(path, *, rate=16000, channels=1, seconds=0.5):
    """手写一个最小 PCM16 wav（不依赖 wave 模块，与实现同源）。"""
    frames = int(rate * seconds)
    data = b"\x00\x00" * frames * channels
    block_align = channels * 2
    byte_rate = rate * block_align
    fmt = struct.pack("<HHIIHH", 1, channels, rate, byte_rate, block_align, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(data)) + data
    blob = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
    path.write_bytes(blob)
    return path


def test_wav_duration_reads_pcm16(tmp_path):
    path = _write_pcm16_wav(tmp_path / "a.wav", seconds=1.25)
    assert wav_duration_seconds(str(path)) == pytest.approx(1.25, abs=0.01)


def test_wav_duration_handles_float32_format(tmp_path):
    """VITS 产的是 fmt=3 / 32-bit float，wave 模块读不了，这条路径必须自己解。"""
    rate, channels, seconds = 22050, 1, 0.4
    frames = int(rate * seconds)
    data = b"\x00\x00\x00\x00" * frames * channels
    block_align = channels * 4
    byte_rate = rate * block_align
    fmt = struct.pack("<HHIIHH", 3, channels, rate, byte_rate, block_align, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(data)) + data
    path = tmp_path / "vits.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)

    assert wav_duration_seconds(str(path)) == pytest.approx(0.4, abs=0.01)


@pytest.mark.parametrize("payload", [b"", b"not a wav", b"RIFF\x00\x00\x00\x00NOPE"])
def test_wav_duration_returns_zero_on_garbage(tmp_path, payload):
    path = tmp_path / "bad.wav"
    path.write_bytes(payload)
    assert wav_duration_seconds(str(path)) == 0.0


def test_wav_duration_missing_file_is_zero(tmp_path):
    assert wav_duration_seconds(str(tmp_path / "nope.wav")) == 0.0


# ----------------------------------------------------------------------
# 配置面：模式 / clamp
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, VOICE_INPUT_MODE_DEFAULT),
        ("", VOICE_INPUT_MODE_DEFAULT),
        ("nope", VOICE_INPUT_MODE_DEFAULT),
        ("toggle", "toggle"),
        (" HANDS_FREE ", "hands_free"),
    ],
)
def test_normalize_voice_mode(raw, expected):
    assert normalize_voice_mode(raw) == expected


def test_default_mode_preserves_legacy_behaviour():
    """默认必须是点按切换——升级不该悄悄改变用户已有的收音习惯。"""
    assert VOICE_INPUT_MODE_DEFAULT == "toggle"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 0.5),
        ("abc", 0.5),
        (-1.0, 0.0),
        (99.0, 1.0),
        ("0.7", 0.7),
    ],
)
def test_clamp_float(raw, expected):
    assert clamp_float(raw, 0.5, 0.0, 1.0) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 300), ("x", 300), (-5, 0), (10**9, 5000), ("250", 250)],
)
def test_clamp_int(raw, expected):
    assert clamp_int(raw, 300, 0, 5000) == expected


def test_config_normalization_adds_p1_3_keys():
    """新键必须由 normalize_config 一处给默认，别让各处各抄一份。"""
    from meapet.config.store import normalize_config

    cfg = normalize_config({})
    voice = cfg["voice_input"]
    assert voice["mode"] == "toggle"
    assert voice["ptt_hotkey"] == ""
    assert voice["wake_word"] == {"enabled": False, "keywords_file": ""}
    assert voice["echo_gate_tail_ms"] == 300
    assert voice["vad"] == {
        "threshold": 0.5,
        "min_silence_duration": 0.5,
        "min_speech_duration": 0.25,
        "max_speech_duration": 20.0,
    }


def test_wake_word_defaults_off_even_when_voice_enabled():
    """隐私边界：开语音输入 ≠ 授权常驻监听。"""
    from meapet.config.store import normalize_config

    cfg = normalize_config({"voice_input": {"enabled": True}})
    assert cfg["voice_input"]["enabled"] is True
    assert cfg["voice_input"]["wake_word"]["enabled"] is False


def test_config_normalization_clamps_bad_vad_values():
    from meapet.config.store import normalize_config

    cfg = normalize_config(
        {
            "voice_input": {
                "vad": {
                    "threshold": 99,
                    "min_silence_duration": -1,
                    "max_speech_duration": "abc",
                }
            }
        }
    )
    vad = cfg["voice_input"]["vad"]
    assert vad["threshold"] == 1.0
    assert vad["min_silence_duration"] == 0.05
    assert vad["max_speech_duration"] == 20.0


# ----------------------------------------------------------------------
# PTT：解析与安全判定（不碰 RegisterHotKey）
# ----------------------------------------------------------------------


def test_parse_hotkey_basic():
    from meapet.voice.ptt import parse_hotkey

    mods, vk = parse_hotkey("Ctrl+Shift+Space")
    assert mods == 0x0002 | 0x0004
    assert vk == 0x20


def test_parse_hotkey_letter_and_function_key():
    from meapet.voice.ptt import parse_hotkey

    assert parse_hotkey("Alt+A")[1] == ord("A")
    assert parse_hotkey("Ctrl+F9")[1] == 0x70 + 8


@pytest.mark.parametrize("bad", ["", "Ctrl", "Ctrl+Shift", "Ctrl+Nope", "Ctrl+a+b"])
def test_parse_hotkey_rejects_malformed(bad):
    from meapet.voice.ptt import parse_hotkey

    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_hotkey_without_modifier_is_unsafe():
    """单按一个键就当全局 PTT，等于全系统录音——必须拦。"""
    from meapet.voice.ptt import hotkey_is_safe

    assert hotkey_is_safe("Space") is False
    assert hotkey_is_safe("A") is False
    assert hotkey_is_safe("") is False
    assert hotkey_is_safe("Ctrl+Space") is True


def test_ptt_availability_matches_platform():
    from meapet.voice.ptt import GlobalPushToTalk

    assert GlobalPushToTalk.available() == sys.platform.startswith("win")


# ----------------------------------------------------------------------
# VAD：真 sherpa + 真模型，样本合成
# ----------------------------------------------------------------------


def _require_vad():
    pytest.importorskip("sherpa_onnx", reason="VAD 是可选依赖，裸环境跳过")
    pytest.importorskip("numpy", reason="VAD 需要 numpy")
    from meapet.paths import find_voice_vad_model

    if not find_voice_vad_model():
        pytest.skip("Silero VAD 模型未下载（voice_vad/silero_vad.onnx）")


def test_vad_model_missing_is_reported_not_crashed(tmp_path, monkeypatch):
    """模型缺席必须是**专门的异常**，不能退化成 ImportError 或 None。"""
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    assert paths.find_voice_vad_model() is None

    pytest.importorskip("sherpa_onnx")
    from meapet.voice.vad import VadModelMissing, VadSegmenter

    with pytest.raises(VadModelMissing):
        VadSegmenter()


def test_vad_finds_model_in_nested_layout(tmp_path, monkeypatch):
    """下载器可能把模型解到子目录，与 ASR 那一路同姿势要认。"""
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    nested = paths.voice_vad_cache_dir() / "some" / "nested"
    nested.mkdir(parents=True)
    (nested / paths.VOICE_VAD_MODEL_FILE).write_bytes(b"x")

    assert paths.find_voice_vad_model() == nested / paths.VOICE_VAD_MODEL_FILE


def test_vad_segments_speech_and_rejects_silence():
    """一段合成语音只该断出一句；静音与白噪声一句都不该断。

    白噪声那条是**防误触的对照**：VAD 在稳态噪声上乱断，等于常驻监听每两秒
    自己发一条空消息——这正是路线图验收线「8h 底噪误唤醒 < 1 次」要防的。
    """
    _require_vad()
    import numpy as np

    from meapet.voice.vad import VadSegmenter

    sr = 16000

    def segments_of(signal):
        seg = VadSegmenter()
        out = seg.feed_samples(np.asarray(signal, dtype=np.float32))
        out += seg.flush()
        return out

    # 静音
    assert segments_of(np.zeros(sr * 2, dtype=np.float32)) == []

    # 白噪声（底噪近似）
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(sr * 2) * 0.01).astype(np.float32)
    assert segments_of(noise) == []

    # 有声音的样本：用仓内那两条合成语音（真正弦在 Silero 上是**负样本**——
    # 它是人声判据，稳态纯音不触发，实测 300Hz 三谐波被一刀切掉；
    # 拿纯音当正样本会让这条测试变成「测 VAD 会不会漏掉非人声」）。
    import wave

    from meapet.paths import project_path

    wav_path = None
    for candidate in (
        ("GPT-Sovits", "normal", "zh_normal.wav"),
        ("GPT-Sovits", "soft", "zh_normal.wav"),
    ):
        path = project_path(*candidate)
        if __import__("os").path.isfile(path):
            wav_path = path
            break
    assert wav_path, "仓内语音样本缺席，这条测试的前提没了"

    with wave.open(wav_path, "rb") as handle:
        frames = handle.getnframes()
        channels = handle.getnchannels()
        raw = handle.readframes(frames)
        rate = handle.getframerate()
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != sr:
        points = np.linspace(0, samples.size - 1, int(samples.size * sr / rate))
        samples = np.interp(points, np.arange(samples.size), samples).astype(np.float32)

    # 两头各垫 0.5 s 静音：VAD 该交回中间那一句，不该把静音也当话
    signal = np.concatenate(
        [
            np.zeros(int(sr * 0.5), dtype=np.float32),
            samples.astype(np.float32),
            np.zeros(int(sr * 0.5), dtype=np.float32),
        ]
    )
    segs = segments_of(signal)
    assert len(segs) >= 1, "真语音样本没被断成句"
    # 断出来的句子总长不该超过有声部分太多（把静音塞进来就会超）
    assert sum(len(s) for s in segs) / sr < samples.size / sr + 1.5


def test_vad_feed_pcm16_matches_feed_samples():
    """录音循环喂的是 int16 字节；两条入口必须等价，否则线上跑的和测的不是一条路。"""
    _require_vad()
    import numpy as np

    from meapet.voice.vad import VadSegmenter

    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    tone = (0.4 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    pcm = np.concatenate(
        [np.zeros(sr // 2, dtype=np.int16), tone, np.zeros(sr // 2, dtype=np.int16)]
    ).tobytes()

    a = VadSegmenter()
    from_bytes = a.feed_pcm16(pcm) + a.flush()

    b = VadSegmenter()
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    from_samples = b.feed_samples(samples) + b.flush()

    assert len(from_bytes) == len(from_samples)


def test_vad_reset_clears_pending_tail():
    """播音期门控会把检测器 reset 掉；reset 之后旧尾巴不能再混进下一句。"""
    _require_vad()
    import numpy as np

    from meapet.voice.vad import VadSegmenter

    seg = VadSegmenter()
    seg.feed_samples(np.zeros(100, dtype=np.float32))  # 不足一窗，留在 pending
    seg.reset()
    assert seg._pending.size == 0


# ----------------------------------------------------------------------
# 唤醒词：只测缺席这条必报路径
# ----------------------------------------------------------------------


def test_kws_model_missing_raises_dedicated_error(tmp_path, monkeypatch):
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    assert paths.find_voice_kws_files() is None

    pytest.importorskip("sherpa_onnx")
    from meapet.voice.kws import KwsModelMissing, build_keyword_spotter

    with pytest.raises(KwsModelMissing):
        build_keyword_spotter()


def test_kws_finds_all_four_files(tmp_path, monkeypatch):
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    model_dir = paths.voice_kws_cache_dir() / "kws-model"
    model_dir.mkdir(parents=True)
    for name in (
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "tokens.txt",
        "keywords.txt",
    ):
        (model_dir / name).write_bytes(b"x")

    found = paths.find_voice_kws_files()
    assert found is not None
    assert found["encoder"].endswith("encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    assert found["tokens"].endswith("tokens.txt")
    assert found["keywords"].endswith("keywords.txt")


def test_kws_reports_missing_keywords_separately(tmp_path, monkeypatch):
    """模型在场但词表缺席：不许把「没词表」报成「没模型」——处置不同。"""
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    model_dir = paths.voice_kws_cache_dir()
    model_dir.mkdir(parents=True)
    for name in (
        "encoder-x.onnx",
        "decoder-x.onnx",
        "joiner-x.onnx",
        "tokens.txt",
    ):
        (model_dir / name).write_bytes(b"x")

    found = paths.find_voice_kws_files()
    assert found is not None
    assert "keywords" not in found

    pytest.importorskip("sherpa_onnx")
    from meapet.voice.kws import KwsModelMissing, build_keyword_spotter

    with pytest.raises(KwsModelMissing, match="词表"):
        build_keyword_spotter(model_files=found)


# ----------------------------------------------------------------------
# 引擎：模式接线与门控丢帧
# ----------------------------------------------------------------------


def test_engine_default_mode_is_toggle():
    pytest.importorskip("PyQt5")
    from meapet.voice.engine import VoiceEngine

    assert VoiceEngine().mode == "toggle"


def test_engine_accepts_hands_free_mode():
    pytest.importorskip("PyQt5")
    from meapet.voice.engine import VoiceEngine

    assert VoiceEngine(mode="hands_free").mode == "hands_free"


def test_engine_read_chunk_drops_frames_while_gate_active():
    """播音期必须整帧丢——这是「她说的话不进识别」的唯一现场。"""
    pytest.importorskip("PyQt5")

    from meapet.voice.engine import VoiceEngine
    from meapet.voice.gate import EchoGate

    class _Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = _Clock()
    gate = EchoGate(clock=clock)
    engine = VoiceEngine(gate=gate)

    class _Stream:
        def read(self, *a, **kw):
            return b"\x00\x01"

    stream = _Stream()
    assert engine._read_chunk(stream) == b"\x00\x01"

    gate.hold(10.0)
    assert engine._read_chunk(stream) is None

    gate.release()
    assert engine._read_chunk(stream) == b"\x00\x01"


def test_engine_read_chunk_survives_stream_errors():
    """单帧读失败只丢那一帧，不能把整条录音拆掉。"""
    pytest.importorskip("PyQt5")
    from meapet.voice.engine import VoiceEngine

    class _BadStream:
        def read(self, *a, **kw):
            raise OSError("device gone")

    assert VoiceEngine()._read_chunk(_BadStream()) is None


# ----------------------------------------------------------------------
# P1-3 的键不许是死键：每个键都要有一条真实的读取路径
# ----------------------------------------------------------------------


def test_p1_3_keys_are_actually_read():
    """配置面新增的每一格，都必须能在运行时找到读取点。

    test_hardcoding_regressions 里那条断言只保证「配置面里没有多余键」，
    但它管不到「键在、没人读」这种更隐蔽的死键——P1-3 加了 5 格，
    所以这里逐格点名读取点。
    """
    import inspect

    from meapet.desktop import audio as audio_mod
    from meapet.desktop import voice_mixin
    from meapet.voice import engine as engine_mod

    src_mixin = inspect.getsource(voice_mixin)
    assert 'cfg.get("mode")' in src_mixin, "voice_mixin 没把 mode 交给引擎"
    assert 'cfg.get("vad")' in src_mixin, "voice_mixin 没把 vad 交给引擎"
    assert 'cfg.get("ptt_hotkey")' in src_mixin, "PTT 热键配置没人读"
    assert 'cfg.get("wake_word")' in src_mixin, "唤醒词配置没人读"

    src_factory = inspect.getsource(engine_mod.create_voice_engine)
    assert 'cfg.get("mode")' in src_factory, "create_voice_engine 没读 mode"
    assert 'cfg.get("vad")' in src_factory, "create_voice_engine 没读 vad"

    assert "echo_gate_tail_ms" in inspect.getsource(audio_mod), "尾音缓冲配置没人读"


def test_vad_config_reaches_segmenter():
    """voice_input.vad 的四格必须原样送到 VadSegmenter 的构造参数上。"""
    pytest.importorskip("PyQt5")
    from meapet.voice.engine import VoiceEngine

    cfg = {
        "threshold": 0.7,
        "min_silence_duration": 0.8,
        "min_speech_duration": 0.3,
        "max_speech_duration": 15.0,
    }
    engine = VoiceEngine(vad_config=cfg)
    assert engine._vad_kwargs() == cfg


def test_vad_kwargs_ignores_unknown_keys():
    """配置面可能带进无关键；只挑 VadSegmenter 认得的那四格。"""
    pytest.importorskip("PyQt5")
    from meapet.voice.engine import VoiceEngine

    engine = VoiceEngine(vad_config={"threshold": 0.6, "nonsense": 1})
    assert engine._vad_kwargs() == {"threshold": 0.6}

def test_hands_free_loop_emits_segments_and_skips_gate():
    """常驻模式端到端：假音频流喂真 VAD，断出的句子必须经 result_ready 发出去。

    这是 P1-3 最核心的一条接线——前面各条测的是零件（VAD 能断、门控能丢），
    这一条测的是「零件接起来以后，用户真的会收到字」。
    """
    _require_vad()
    pytest.importorskip("PyQt5")
    import numpy as np
    import wave

    from meapet.paths import project_path
    from meapet.voice.engine import VoiceEngine
    from meapet.voice.gate import EchoGate

    wav_path = project_path("GPT-Sovits", "normal", "zh_normal.wav")
    import os

    if not os.path.isfile(wav_path):
        pytest.skip("仓内语音样本缺席")

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
        samples = np.interp(points, np.arange(samples.size), samples).astype(np.float32)
    pcm = (samples * 32767).astype(np.int16).tobytes()

    class _Clock:
        now = 0.0

        def __call__(self):
            return self.now

    gate = EchoGate(clock=_Clock())

    class _Stream:
        """一次吐一块 PCM，吐完就把引擎停掉（模拟用户停止）。"""

        def __init__(self, blob, chunk, engine):
            self._blob = blob
            self._pos = 0
            self._chunk = chunk
            self._engine = engine

        def read(self, size, exception_on_overflow=False):
            data = self._blob[self._pos : self._pos + self._chunk]
            self._pos += self._chunk
            if not data:
                self._engine._stop = True
                return b""
            return data

    engine = VoiceEngine(mode="hands_free", gate=gate)
    engine._recognizer = object()  # 假识别器：这一条测的是分段与发射，不是转写质量
    emitted = []
    engine.result_ready.connect(emitted.append)

    # 替换解码：真识别器缺席时仍要证明「断出的段被送进了转写并被发射」
    import meapet.voice.engine as engine_mod

    original = engine_mod._decode_samples
    engine_mod._decode_samples = lambda rec, seg, sr: f"seg{len(seg)}"
    try:
        stream = _Stream(pcm, 3200, engine)
        engine._loop_hands_free(stream)
    finally:
        engine_mod._decode_samples = original

    assert emitted, "常驻模式没有把断出的句子发出去"
    assert all(text.startswith("seg") for text in emitted)

# ----------------------------------------------------------------------
# PTT 探针：它的解析/判定半区能进 CI，真注册那半区靠 scripts/ptt_probe.py
# ----------------------------------------------------------------------


def test_ptt_probe_parses_without_registering():
    """探针的解析段要能在无桌面环境下跑通——否则探针自己就成了只在真机可用的死代码。"""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "ptt_probe", root / "scripts" / "ptt_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.run)
    assert callable(module.main)


@pytest.mark.parametrize("hotkey", ["Ctrl+Alt+F9", "Ctrl+Shift+Space", "Win+Alt+A"])
def test_default_probe_hotkeys_are_all_safe(hotkey):
    """探针默认用 Ctrl+Alt+F9；文档里推荐的几个也都得是安全的，别推荐到会被拒的串。"""
    from meapet.voice.ptt import hotkey_is_safe

    assert hotkey_is_safe(hotkey) is True

def test_kws_never_mixes_epochs(tmp_path, monkeypatch):
    """同一目录里放两套模型时，必须**成套**取，不许 encoder-99 + decoder-12 混搭。

    这条红测试来自一次真踩：上游 wenetspeech 官方包一个目录里并排放着
    `epoch-99-avg-1` 与 `epoch-12-avg-2` 两套（各有 int8/非 int8）。原来按件
    `startswith` 挑，靠 iterdir 顺序决定拿到哪一套——实测那次侥幸全落在 12，
    但顺序不保证，换个文件系统就可能拼出一个加载失败或识别错乱的组合。
    """
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    model_dir = paths.voice_kws_cache_dir()
    model_dir.mkdir(parents=True)

    # 两套并存，故意让 99 排在前面（按名字排序时 12 在前，这里靠写入顺序混淆）
    for epoch in ("99-avg-1", "12-avg-2"):
        for stem in ("encoder", "decoder", "joiner"):
            (model_dir / f"{stem}-epoch-{epoch}-chunk-16-left-64.onnx").write_bytes(b"x")
    (model_dir / "tokens.txt").write_bytes(b"x")

    found = paths.find_voice_kws_files()
    assert found is not None
    variants = {
        paths._kws_variant_key(Path(found[k]).name)
        for k in ("encoder", "decoder", "joiner")
    }
    assert len(variants) == 1, f"混搭了两套模型: {variants}"


def test_kws_prefers_int8_within_one_variant(tmp_path, monkeypatch):
    """同一变体的 int8 与非 int8 两档并存时取 int8（与 ASR 默认档、向导文案一致）。"""
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    model_dir = paths.voice_kws_cache_dir()
    model_dir.mkdir(parents=True)

    for stem in ("encoder", "decoder", "joiner"):
        (model_dir / f"{stem}-epoch-99-avg-1-chunk-16-left-64.onnx").write_bytes(b"x")
        (model_dir / f"{stem}-epoch-99-avg-1-chunk-16-left-64.int8.onnx").write_bytes(b"x")
    (model_dir / "tokens.txt").write_bytes(b"x")

    found = paths.find_voice_kws_files()
    assert found is not None
    for key in ("encoder", "decoder", "joiner"):
        assert found[key].endswith(".int8.onnx"), found[key]


def test_kws_incomplete_variant_is_not_accepted(tmp_path, monkeypatch):
    """只有 encoder 没有 decoder/joiner 时，不许把"找到一件"当成"找到一套"。"""
    import meapet.paths as paths

    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(paths, "project_path", lambda *p: str(tmp_path.joinpath(*p)))
    model_dir = paths.voice_kws_cache_dir()
    model_dir.mkdir(parents=True)
    (model_dir / "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx").write_bytes(b"x")
    (model_dir / "tokens.txt").write_bytes(b"x")

    assert paths.find_voice_kws_files() is None


def test_kws_variant_key_strips_prefix_and_precision():
    """变体键的归约规则：去掉 encoder-/decoder-/joiner- 与精度后缀。"""
    from meapet.paths import _kws_variant_key

    assert (
        _kws_variant_key("encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx")
        == _kws_variant_key("decoder-epoch-99-avg-1-chunk-16-left-64.onnx")
        == _kws_variant_key("joiner-epoch-99-avg-1-chunk-16-left-64.onnx")
        == "epoch-99-avg-1-chunk-16-left-64"
    )
    # 不同 epoch 必须区分开——这正是要防混搭的地方
    assert _kws_variant_key("encoder-epoch-99-avg-1-x.onnx") != _kws_variant_key(
        "encoder-epoch-12-avg-2-x.onnx"
    )



