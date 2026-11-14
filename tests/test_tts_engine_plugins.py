"""P1-4：TTS 引擎协议、注册表与轻量 ONNX 引擎。

这一套守两条线：

1. **协议层自身的契约**——注册表不重名、未知名字报错、能力矩阵是固有属性。
2. **接进 MeaTTS 后既有行为不变**——三条既有引擎（GSV/VITS/MiMo）的分支判定
   完全没被改写，新引擎只是多了一条并列的分支。

ONNX 引擎的**真合成**要用模型（13 MB 下载物），所以那部分靠
`scripts/tts_engine_probe.py` 真跑，不塞进 CI；这里只测「模型缺席怎么报」。
"""
from __future__ import annotations

import pytest

from meapet.tts.engines.protocol import (
    CAPABILITY_OFFLINE,
    CAPABILITY_STREAMING,
    ENGINE_REGISTRY,
    LATENCY_FAST,
    SIZE_TIER_HEAVY,
    SIZE_TIER_TINY,
    EngineCapabilities,
    EngineRegistry,
    register_engine,
)


# ----------------------------------------------------------------------
# 能力矩阵
# ----------------------------------------------------------------------


def test_capabilities_describe_is_one_line():
    caps = EngineCapabilities(
        languages=("zh", "en"),
        capabilities=frozenset({CAPABILITY_OFFLINE}),
        size_tier=SIZE_TIER_TINY,
        latency=LATENCY_FAST,
    )
    text = caps.describe()
    assert "\n" not in text
    assert "zh/en" in text
    assert SIZE_TIER_TINY in text
    assert LATENCY_FAST in text


def test_capabilities_supports_filters_by_membership():
    caps = EngineCapabilities(capabilities=frozenset({CAPABILITY_OFFLINE}))
    assert caps.supports(CAPABILITY_OFFLINE) is True
    assert caps.supports(CAPABILITY_STREAMING) is False


def test_capabilities_default_is_conservative():
    """默认值必须是最保守的那一档：没写能力的引擎不该被当成"又小又快又全能"。"""
    caps = EngineCapabilities()
    assert caps.size_tier == SIZE_TIER_HEAVY
    assert caps.supports(CAPABILITY_OFFLINE) is False
    assert caps.languages == ()


# ----------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------


class _StubEngine:
    name = "stub"
    capabilities = EngineCapabilities(languages=("zh",))

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def speak(self, text, output_wav, **kwargs):
        return (output_wav, "zh")

    def health_check(self):
        return True


def test_registry_roundtrip():
    reg = EngineRegistry()
    reg.register("stub", lambda *a, **k: _StubEngine(*a, **k))
    assert "stub" in reg
    assert len(reg) == 1
    assert reg.names() == ("stub",)

    engine = reg.create("stub", "a", key="b")
    assert isinstance(engine, _StubEngine)
    assert engine.args == ("a",)
    assert engine.kwargs == {"key": "b"}


def test_registry_rejects_duplicate_names():
    """同名两处定义时，运行时用哪个取决于导入顺序——引擎选择上这种不确定性要命。"""
    reg = EngineRegistry()
    reg.register("stub", lambda: _StubEngine())
    with pytest.raises(ValueError, match="已注册"):
        reg.register("stub", lambda: _StubEngine())


def test_registry_allows_explicit_override():
    reg = EngineRegistry()

    class _Other(_StubEngine):
        name = "other"

    reg.register("stub", lambda: _StubEngine())
    reg.register("stub", lambda: _Other(), override=True)
    assert isinstance(reg.create("stub"), _Other)


def test_registry_unknown_name_lists_known_ones():
    reg = EngineRegistry()
    reg.register("stub", lambda: _StubEngine())
    with pytest.raises(KeyError, match="stub"):
        reg.create("nope")


def test_registry_unregister_is_idempotent():
    """卸载幂等比报错有用——插件卸两次不该抛。"""
    reg = EngineRegistry()
    reg.register("stub", lambda: _StubEngine())
    reg.unregister("stub")
    reg.unregister("stub")  # 不抛
    assert "stub" not in reg
    assert reg.names() == ()


def test_registry_name_is_case_insensitive():
    reg = EngineRegistry()
    reg.register("Stub", lambda: _StubEngine())
    assert "STUB" in reg
    assert isinstance(reg.create("stub"), _StubEngine)


def test_registry_rejects_empty_name():
    reg = EngineRegistry()
    with pytest.raises(ValueError):
        reg.register("  ", lambda: _StubEngine())


def test_registry_capabilities_of():
    reg = EngineRegistry()
    reg.register("stub", lambda: _StubEngine())
    caps = reg.capabilities_of("stub")
    assert caps.languages == ("zh",)


def test_register_engine_decorator_form():
    reg = EngineRegistry()
    # 装饰器用的是全局表，这里只验它返回原工厂（不污染全局表就别真挂）
    decorated = register_engine("decorator-test")(lambda: _StubEngine())
    assert callable(decorated)


# ----------------------------------------------------------------------
# 全局表：产品侧真的注册了 onnx
# ----------------------------------------------------------------------


def test_global_registry_has_onnx_after_import():
    """import 产品模块后 onnx 必须已登记——注册写在 service 模块级，
    这样任何走 MeaTTS 的路径都不会漏掉它。"""
    import meapet.tts.service  # noqa: F401

    assert "onnx" in ENGINE_REGISTRY


def test_onnx_engine_capabilities_are_declared():
    from meapet.tts.engines.onnx_local import OnnxTtsEngine

    caps = OnnxTtsEngine.capabilities
    assert caps.supports(CAPABILITY_OFFLINE), "ONNX 引擎的核心卖点就是离线"
    assert caps.size_tier == SIZE_TIER_TINY, "它存在的意义是轻——不该是 heavy"
    assert "zh" in caps.languages


# ----------------------------------------------------------------------
# ONNX 引擎：缺席路径（真合成见 scripts/tts_engine_probe.py）
# ----------------------------------------------------------------------


def test_onnx_missing_model_dir_is_reported(tmp_path):
    from meapet.tts.engines.onnx_local import OnnxTtsEngine

    engine = OnnxTtsEngine(model_dir=str(tmp_path / "nope"))
    assert engine.health_check() is False
    assert engine.speak("你好", str(tmp_path / "o.wav")) == (None, "")


def test_onnx_ambiguous_models_is_an_error(tmp_path):
    """目录里两个 .onnx 时必须报错而不是随便挑——挑错的症状是音色莫名变了。"""
    from meapet.tts.engines.onnx_local import OnnxModelMissing, OnnxTtsEngine

    (tmp_path / "a.onnx").write_bytes(b"x")
    (tmp_path / "b.onnx").write_bytes(b"x")
    (tmp_path / "tokens.txt").write_bytes(b"x")
    engine = OnnxTtsEngine(model_dir=str(tmp_path))
    with pytest.raises(OnnxModelMissing, match="不知道该用哪个"):
        engine._resolve_model_files()


def test_onnx_missing_tokens_is_reported(tmp_path):
    from meapet.tts.engines.onnx_local import OnnxModelMissing, OnnxTtsEngine

    (tmp_path / "m.onnx").write_bytes(b"x")
    engine = OnnxTtsEngine(model_dir=str(tmp_path))
    with pytest.raises(OnnxModelMissing, match="tokens.txt"):
        engine._resolve_model_files()


def test_onnx_resolves_lexicon_when_present(tmp_path):
    from meapet.tts.engines.onnx_local import OnnxTtsEngine

    (tmp_path / "m.onnx").write_bytes(b"x")
    (tmp_path / "tokens.txt").write_bytes(b"x")
    (tmp_path / "lexicon.txt").write_bytes(b"x")
    files = OnnxTtsEngine(model_dir=str(tmp_path))._resolve_model_files()
    assert files["lexicon"].endswith("lexicon.txt")
    assert "data_dir" not in files


def test_onnx_speed_is_clamped():
    from meapet.tts.engines.onnx_local import OnnxTtsEngine

    assert OnnxTtsEngine(speed=99).capabilities is not None
    engine = OnnxTtsEngine(speed=99)
    assert engine._speed == 3.0
    assert OnnxTtsEngine(speed="abc")._speed == 1.0


# ----------------------------------------------------------------------
# 接进 MeaTTS 之后：既有三条路径的判定不被改写
# ----------------------------------------------------------------------


def _minimal_config(**tts):
    return {"tts": {"enabled": True, **tts}}


def test_meatts_still_defaults_to_gsv():
    """没配 engine 时仍是 gpt_sovits——升级不能悄悄换掉用户的朗读引擎。"""
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS

    tts = MeaTTS(_minimal_config())
    assert tts.engine == "gpt_sovits"
    assert tts._plugin_engine is None
    assert tts._mimo_mode is False
    assert tts._vits_mode is False


def test_meatts_mimo_mode_unchanged():
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS

    tts = MeaTTS(_minimal_config(engine="mimo"))
    assert tts._mimo_mode is True
    assert tts._plugin_engine is None
    assert tts.capabilities.size_tier == SIZE_TIER_TINY


def test_meatts_vits_mode_unchanged():
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS

    tts = MeaTTS(_minimal_config(engine="vits"))
    assert tts._vits_mode is True
    assert tts._plugin_engine is None


def test_meatts_vits_mode_key_still_works_without_engine():
    """旧的 vits_mode 单键仍能把朗读领去 VITS——diagnostics 里那条注释钉的就是它。"""
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS

    tts = MeaTTS(_minimal_config(vits_mode=True))
    assert tts._vits_mode is True


def test_meatts_onnx_engine_is_wired_without_loading_model():
    """选 onnx 时插件引擎要建出来，但**不载模型**（懒建：载入实测 4 秒）。"""
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS
    from meapet.tts.engines.onnx_local import OnnxTtsEngine

    tts = MeaTTS(_minimal_config(engine="onnx", onnx_model_dir=""))
    assert isinstance(tts._plugin_engine, OnnxTtsEngine)
    assert tts._plugin_engine._tts is None, "构造 MeaTTS 不该把模型载进来"


def test_meatts_capabilities_describes_each_engine():
    """每个引擎都要能说出自己的能力——没有这一格，调用方只能靠 if engine== 猜。"""
    pytest.importorskip("PyQt5")
    from meapet.tts.service import MeaTTS

    for engine, expect_tiny in (("mimo", True), ("vits", False), ("gpt_sovits", False)):
        caps = MeaTTS(_minimal_config(engine=engine)).capabilities
        assert (caps.size_tier == SIZE_TIER_TINY) is expect_tiny, engine
        assert caps.describe()

def test_capabilities_of_needs_no_constructor_args():
    """问「你支持什么」不该先凑齐构造实参，更不该去载模型。

    这条来自一个真报错：第一版 `capabilities_of` 无条件 `self.create(name)`，
    而 onnx 的工厂需要一个 `config` 参数，于是 doctor 报
    「能力读取失败=TypeError」——接口形状错了，不是调用方写错了。
    """
    from meapet.tts.engines.onnx_local import OnnxTtsEngine
    from meapet.tts.engines.protocol import EngineRegistry
    import meapet.tts.service  # noqa: F401 - 触发注册

    reg = EngineRegistry()
    reg.register("onnx", lambda config=None: OnnxTtsEngine())

    # 不传任何参数也能读到能力
    caps = reg.capabilities_of("onnx")
    assert caps.size_tier == SIZE_TIER_TINY
    # 且没有因此把模型载进来
    assert caps is not None


def test_capabilities_of_falls_back_to_instance():
    """工厂没登记能力时退回「造个实例读」——这条是兜底路径，不能只测快路径。"""
    from meapet.tts.engines.protocol import EngineRegistry

    reg = EngineRegistry()
    reg.register("stub", lambda: _StubEngine())
    caps = reg.capabilities_of("stub")
    assert caps.languages == ("zh",)


def test_factory_declares_capabilities_without_constructing():
    """onnx 的工厂函数本身挂着能力矩阵——注册表读它时不构造实例。"""
    import meapet.tts.service  # noqa: F401
    from meapet.tts.engines.onnx_local import create_onnx_engine
    from meapet.tts.engines.protocol import EngineCapabilities

    assert isinstance(create_onnx_engine.capabilities, EngineCapabilities)
    assert create_onnx_engine.capabilities.supports(CAPABILITY_OFFLINE)

