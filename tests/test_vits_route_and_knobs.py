"""VITS 选路与旋钮口径的回归测试。

对应 working/community 件 ``vits-windows-knobs-divergence`` 的 D1–D4。

D1 的病根是一个恒真式：``prefer_subprocess = bool(external_py) and not (force
and not external_py)`` 后面的 ``if external_py: prefer_subprocess = True``
把它整个盖掉，于是 ``vits_inprocess`` 参与计算却影响不了任何结论。
``test_force_inprocess_is_not_inert`` 与
``test_route_decision_table_is_total`` 就是钉死这一点的：前者一旦失败，
说明又回到了"设置了却没用"的状态。
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest

from meapet.tts import common as common_mod
from meapet.tts.common import (
    DEFAULT_VITS_SPEAKER,
    resolve_vits_route,
    resolve_vits_speaker,
)
from meapet.tts.engines.vits import TtsVitsMixin
from meapet.tts import service as service_mod


# ══════════════════════════════════════════════════════════════════
# D1 · 选路判据
# ══════════════════════════════════════════════════════════════════

def _decision(external: bool, pref: bool | None, *, frozen: bool) -> bool:
    """把 4 格自由变量收敛成"是否走进程内"。"""
    route = resolve_vits_route(
        external_python="C:/py/python.exe" if external else "",
        inprocess_pref=pref,
        frozen=frozen,
    )
    return route.inprocess


def test_force_inprocess_is_not_inert_when_no_external_python():
    """D1 的反向钉子：没有外部解释器时，显式声明必须改变结论。"""
    assert _decision(False, None, frozen=False) is False
    assert _decision(False, True, frozen=False) is True
    assert _decision(False, True, frozen=False) != _decision(
        False, None, frozen=False
    )


def test_route_decision_table_is_total():
    """穷举 (external_py, vits_inprocess, frozen) 九格，逐格钉住结论。"""
    table = {
        # (external, pref, frozen): inprocess?
        (False, None, False): False,   # 源码运行且没配解释器 → 回落本进程
        (False, None, True): True,     # 打包版默认进程内（原有行为）
        (False, True, False): True,
        (False, True, True): True,
        (False, False, False): False,
        (False, False, True): False,   # 显式关掉 → 两路都没有，由 speak 报错
        (True, None, False): False,    # 有外部解释器 → 子进程
        (True, None, True): False,
        (True, True, True): False,     # 声明被覆盖（但必须出声，见下一条）
        (True, True, False): False,
        (True, False, False): False,
        (True, False, True): False,
    }
    for (external, pref, frozen), expected in table.items():
        assert _decision(external, pref, frozen=frozen) is expected, (
            f"external={external} pref={pref} frozen={frozen}"
        )


def test_route_reports_ignored_inprocess_declaration():
    """D1 的核心症状：声明被吞。现在必须能被上层读出来并出声。"""
    override = resolve_vits_route(
        external_python="C:/py/python.exe", inprocess_pref=True, frozen=True
    )
    assert override.inprocess is False
    assert override.ignored_inprocess_pref is True
    assert override.mode == "subprocess"
    assert "overridden" in override.reason

    # 没有声明时不算"被覆盖"，不该刷警告。
    quiet = resolve_vits_route(
        external_python="C:/py/python.exe", inprocess_pref=None, frozen=True
    )
    assert quiet.ignored_inprocess_pref is False
    assert quiet.reason == "external_configured"


def test_explicit_subprocess_without_external_python_leaves_no_route():
    """vits_inprocess=false 且无外部解释器：两路都没有，不能报绿。"""
    route = resolve_vits_route(
        external_python="",
        inprocess_pref=False,
        frozen=True,
        fallback_python="C:/MeaPet/MeaPet.exe",
    )
    assert route.inprocess is False
    assert route.external_python == ""
    assert route.reason == "no_external_python_fallback"


def test_engine_route_reads_host_decision():
    """引擎与健康检查必须读同一份判据（老代码里是两份恒真式）。"""
    class Host:
        python_exe = ""
        timeout = 5

        def _vits_route_decision(self):
            return resolve_vits_route(
                external_python="", inprocess_pref=True, frozen=True
            )

    assert TtsVitsMixin._vits_route(Host()).inprocess is True


def test_engine_falls_back_to_own_attrs_for_duck_hosts():
    """鸭子类型宿主（仅有配置属性、不继承 mixin）也要能选路。"""
    class Duck:
        _vits_python = ""
        _vits_inprocess = True
        python_exe = ""
        timeout = 5

    with mock.patch.object(common_mod, "_is_frozen", return_value=True):
        assert TtsVitsMixin._vits_route(Duck()).inprocess is True

    class DuckForced:
        _vits_python = ""
        _vits_inprocess = False
        python_exe = ""
        timeout = 5

    with mock.patch.object(common_mod, "_is_frozen", return_value=True):
        assert TtsVitsMixin._vits_route(DuckForced()).inprocess is False


def test_speak_vits_warns_when_inprocess_declaration_is_overridden(
    tmp_path, caplog
):
    """被覆盖时必须有一行日志 —— 这是 C1 选定"保留优先级但要出声"。"""

    class Host:
        _vits_python = "C:/py/python.exe"
        _vits_inprocess = True
        python_exe = ""
        timeout = 5
        _vits_model = ""
        _vits_config = ""
        _vits_speaker = ""

    calls: list[str] = []

    def fake_sub(self, text, out, py, knobs=None):
        calls.append("subprocess")
        return out, "jp"

    def fake_in(self, text, out):
        calls.append("inprocess")
        return out, "jp"

    with mock.patch.object(
        TtsVitsMixin, "_vits_external_python", lambda self: "C:/py/python.exe"
    ), mock.patch.object(
        TtsVitsMixin, "_speak_vits_subprocess", fake_sub
    ), mock.patch.object(
        TtsVitsMixin, "_speak_vits_inprocess", fake_in
    ), caplog.at_level("WARNING"):
        TtsVitsMixin._speak_vits(Host(), "hi", str(tmp_path / "o.wav"))

    assert calls == ["subprocess"]
    assert any(
        "vits_inprocess" in record.message and "覆盖" in record.message
        for record in caplog.records
    ), caplog.text


# ══════════════════════════════════════════════════════════════════
# D3 · 三条旋钮：生产者 + 两路口径一致
# ══════════════════════════════════════════════════════════════════

class _RobustHost:
    python_exe = ""
    timeout = 5

    def __init__(self, **cfg):
        self._vits_python = cfg.get("vits_python", "")
        self._vits_inprocess = cfg.get("vits_inprocess")
        self._vits_model = cfg.get("vits_model")
        self._vits_config = cfg.get("vits_config")
        self._vits_speaker = cfg.get("vits_speaker")
        self._vits_route_decision = lambda: resolve_vits_route(
            external_python=self._vits_python or "",
            inprocess_pref=self._vits_inprocess,
            frozen=False,
        )


def test_subprocess_argv_carries_model_config_and_speaker(tmp_path):
    """D3：argv 里以前没有 --model/--config/--speaker，脚本只能回落硬编码。"""
    host = _RobustHost(
        vits_python="C:/py/python.exe",
        vits_model=str(tmp_path / "custom.pth"),
        vits_config=str(tmp_path / "custom.json"),
        vits_speaker="Mea",
    )
    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):
        captured.append(list(argv))
        out = argv[argv.index("--output") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("x")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(
        TtsVitsMixin, "_vits_external_python", lambda self: "C:/py/python.exe"
    ), mock.patch("meapet.tts.engines.vits.subprocess.run", fake_run):
        TtsVitsMixin._speak_vits_subprocess(
            host, "hi", str(tmp_path / "o.wav"), "C:/py/python.exe"
        )

    assert len(captured) == 1
    argv = captured[0]
    assert argv[argv.index("--model") + 1] == str(tmp_path / "custom.pth")
    assert argv[argv.index("--config") + 1] == str(tmp_path / "custom.json")
    assert argv[argv.index("--speaker") + 1] == "Mea"


def test_subprocess_and_inprocess_agree_on_knobs(tmp_path, monkeypatch):
    """两路取值口径必须逐字相同，否则健康检查验 A、合成做 B。"""
    host = _RobustHost(
        vits_python="C:/py/python.exe",
        vits_model=str(tmp_path / "m.pth"),
        vits_config=str(tmp_path / "c.json"),
        vits_speaker="Voice2",
    )
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["sub"] = (
            argv[argv.index("--model") + 1],
            argv[argv.index("--config") + 1],
            argv[argv.index("--speaker") + 1],
        )
        out = argv[argv.index("--output") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("x")
        return mock.Mock(returncode=0, stdout="", stderr="")

    import meapet.tts.engines.vits_runtime as runtime_mod

    def fake_synth(text, out, **kwargs):
        seen["in"] = (
            kwargs["model_path"],
            kwargs["config_path"],
            kwargs["speaker"],
        )
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("x")
        return out

    monkeypatch.setattr(runtime_mod, "synthesize_vits", fake_synth)

    with mock.patch.object(
        TtsVitsMixin, "_vits_external_python", lambda self: "C:/py/python.exe"
    ), mock.patch("meapet.tts.engines.vits.subprocess.run", fake_run):
        TtsVitsMixin._speak_vits_subprocess(
            host, "hi", str(tmp_path / "s.wav"), "C:/py/python.exe"
        )
        TtsVitsMixin._speak_vits_inprocess(host, "hi", str(tmp_path / "i.wav"))

    assert seen["sub"] == seen["in"]


def test_knobs_fall_back_to_package_defaults(tmp_path):
    """没有生产者时两路都落回随包默认值（不能各自 hardcode 一份）。"""
    host = _RobustHost(vits_python="C:/py/python.exe")
    model, config, speaker = TtsVitsMixin._vits_knobs(host)
    assert os.path.basename(model) == common_mod.DEFAULT_VITS_MODEL_NAME
    assert os.path.basename(config) == common_mod.DEFAULT_VITS_CONFIG_NAME
    assert speaker == DEFAULT_VITS_SPEAKER


def test_service_is_a_knob_producer(tmp_path):
    """D3 的"没有生产者"：MeaTTS 现在必须把三键落成属性。"""
    tts = service_mod.MeaTTS(
        {
            "tts": {
                "enabled": True,
                "engine": "vits",
                "vits_model": str(tmp_path / "x.pth"),
                "vits_config": str(tmp_path / "x.json"),
                "vits_speaker": "Other",
                "output_dir": str(tmp_path / "audio_cache"),
            }
        }
    )
    assert tts._vits_model == str(tmp_path / "x.pth")
    assert tts._vits_config == str(tmp_path / "x.json")
    assert tts._vits_speaker == "Other"
    assert TtsVitsMixin._vits_knobs(tts) == (
        str(tmp_path / "x.pth"),
        str(tmp_path / "x.json"),
        "Other",
    )


def test_health_check_uses_the_engine_knobs(tmp_path):
    """健康检查必须验引擎真正会用的那两个文件。"""
    missing_model = tmp_path / "nope.pth"
    tts = service_mod.MeaTTS(
        {
            "tts": {
                "enabled": True,
                "engine": "vits",
                "vits_model": str(missing_model),
                "output_dir": str(tmp_path / "audio_cache"),
            }
        }
    )
    assert tts.health_check() is False


def test_health_check_mode_matches_route(tmp_path, caplog):
    """健康日志自称的 mode 必须就是引擎实际走的那条路。"""
    tts = service_mod.MeaTTS(
        {
            "tts": {
                "enabled": True,
                "engine": "vits",
                "output_dir": str(tmp_path / "audio_cache"),
            }
        }
    )
    with caplog.at_level("INFO"):
        tts.health_check()
    expected = tts._vits_route().mode
    assert any(
        f"mode={expected}" in record.message for record in caplog.records
    ), caplog.text


def test_engine_and_health_read_one_decision(tmp_path):
    """两处口径不可能再分叉：引擎读到的就是服务算出的那一份。"""
    tts = service_mod.MeaTTS(
        {
            "tts": {
                "enabled": True,
                "engine": "vits",
                "output_dir": str(tmp_path / "audio_cache"),
            }
        }
    )
    assert TtsVitsMixin._vits_route(tts).mode == tts._vits_route().mode
    assert (
        TtsVitsMixin._vits_route(tts).reason == tts._vits_route().reason
    )


# ══════════════════════════════════════════════════════════════════
# D2 · vits_python 解析失败要出声
# ══════════════════════════════════════════════════════════════════

def test_rejected_vits_python_is_reported_at_load(tmp_path, caplog):
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_python": str(tmp_path / "not-on-disk" / "python.exe"),
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    with caplog.at_level("WARNING"):
        tts = service_mod.MeaTTS(cfg)
    assert tts._vits_configured_python_rejected is True
    assert tts._vits_configured_python == ""
    assert any(
        "vits_python" in record.message and "解释器" in record.message
        for record in caplog.records
    ), caplog.text


def test_accepted_vits_python_is_silent(tmp_path, caplog):
    real_py = tmp_path / "python.exe"
    real_py.write_text("", encoding="utf-8")
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_python": str(real_py),
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    with caplog.at_level("WARNING"):
        tts = service_mod.MeaTTS(cfg)
    assert tts._vits_configured_python_rejected is False
    assert not any(
        "vits_python" in record.message for record in caplog.records
    ), caplog.text


# ══════════════════════════════════════════════════════════════════
# D4 · 说话人回落要出声；两份实现必须同口径
# ══════════════════════════════════════════════════════════════════

_SPEAKER_CASES = [
    ({"Mea": 0}, "Mea"),
    ({"Mea": 0}, "Nobody"),
    ({"Mea": 0, "Other": 1}, "Other"),
    ({"Mea": "0"}, "Mea"),
    ({"Mea": "abc"}, "Mea"),
    ({"Mea": 0}, ""),
    ({"Mea": 0}, None),
    ({}, "Mea"),
    ({}, ""),
    (None, "Mea"),
    (None, ""),
    ([], "Mea"),
    ({"Mea": 0, "二": 1}, "二"),
]


class _HParamsLike:
    """vits_core.utils.HParams 的形状：不是 dict，但有 __contains__/__getitem__。

    随包的 ``hps.speakers`` 就是它。老代码用 ``isinstance(x, dict)`` 判定，
    恒为假 —— 说话人查表从来没执行过。
    """

    def __init__(self, mapping):
        for key, value in mapping.items():
            setattr(self, key, value)

    def keys(self):
        return self.__dict__.keys()

    def __contains__(self, key):
        return key in self.__dict__

    def __getitem__(self, key):
        return getattr(self, key)


@pytest.mark.parametrize("speakers,requested", _SPEAKER_CASES)
def test_speaker_resolvers_agree(speakers, requested):
    """vits_infer.py 不能 import meapet.*，所以口径是两份实现，必须钉死。"""
    from meapet.tools import vits_infer

    assert vits_infer.resolve_speaker(speakers, requested) == (
        resolve_vits_speaker(speakers, requested)
    )


@pytest.mark.parametrize("speakers,requested", _SPEAKER_CASES)
def test_speaker_resolvers_treat_hparams_like_a_table(speakers, requested):
    """hps.speakers 不是 dict；两种形状必须解析出同一个结果。"""
    from meapet.tools import vits_infer

    if isinstance(speakers, dict):
        wrapped = _HParamsLike(speakers)
    else:
        wrapped = speakers

    assert resolve_vits_speaker(wrapped, requested) == resolve_vits_speaker(
        speakers, requested
    ), f"speakers={speakers!r} requested={requested!r}"
    assert vits_infer.resolve_speaker(wrapped, requested) == (
        vits_infer.resolve_speaker(speakers, requested)
    )


def test_hparams_like_speaker_table_is_actually_looked_up():
    """这条钉死那个恒假的 isinstance：HParams 形状下 'Other' 必须查到 1。"""
    table = _HParamsLike({"Mea": 0, "Other": 1})
    assert isinstance(table, dict) is False
    assert resolve_vits_speaker(table, "Other") == (1, None)
    assert resolve_vits_speaker(table, "Missing")[0] == 0
    assert resolve_vits_speaker(table, "Missing")[1]


def test_real_hps_speakers_is_not_a_dict():
    """随包模型配置经 vits_core 读出来必须走鸭子类型这条路。"""
    vits_core = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vits_core"
    )
    config_path = os.path.join(
        os.path.dirname(vits_core),
        "vits_models",
        common_mod.DEFAULT_VITS_CONFIG_NAME,
    )
    if not (os.path.isdir(vits_core) and os.path.isfile(config_path)):
        pytest.skip("vits_core / 模型配置不在（LFS 未 hydrate）")
    sys.path.insert(0, vits_core)
    try:
        import utils as vits_utils
    except Exception as exc:  # pragma: no cover - 环境缺依赖时跳过
        pytest.skip(f"vits_core.utils 不可导入: {exc}")
    finally:
        sys.path.remove(vits_core)

    hps = vits_utils.get_hparams_from_file(config_path)
    assert isinstance(hps.speakers, dict) is False, (
        "若哪天 hps.speakers 真变成了 dict，这段鸭子类型判定可以简化"
    )
    assert resolve_vits_speaker(hps.speakers, DEFAULT_VITS_SPEAKER)[1] is None


def test_speaker_fallback_is_not_silent(caplog):
    """D4：名字不在 finetune_speaker.json 里就静默换 0 号是病根。"""
    _id, warning = resolve_vits_speaker({"Mea": 0}, "NotThere")
    assert _id == 0
    assert warning and "NotThere" in warning and "0 号" in warning

    _id, ok = resolve_vits_speaker({"Mea": 0}, "Mea")
    assert (_id, ok) == (0, None)


def test_service_warns_on_unknown_speaker(tmp_path, caplog):
    cfg_path = tmp_path / "finetune_speaker.json"
    cfg_path.write_text(
        json.dumps({"speakers": {"Mea": 0}}), encoding="utf-8"
    )
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_config": str(cfg_path),
            "vits_speaker": "Missing",
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    with caplog.at_level("WARNING"):
        tts = service_mod.MeaTTS(cfg)
    assert "Missing" in tts._vits_speaker_warning
    assert any(
        "Missing" in record.message for record in caplog.records
    ), caplog.text


def test_service_is_quiet_for_a_valid_speaker(tmp_path, caplog):
    cfg_path = tmp_path / "finetune_speaker.json"
    cfg_path.write_text(
        json.dumps({"speakers": {"Mea": 0}}), encoding="utf-8"
    )
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_config": str(cfg_path),
            "vits_speaker": "Mea",
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    with caplog.at_level("WARNING"):
        tts = service_mod.MeaTTS(cfg)
    assert tts._vits_speaker_warning == ""


def test_speaker_check_tolerates_missing_config(tmp_path):
    """配置缺失不在这里报错（健康检查管这个），只返回空警告。"""
    assert service_mod.MeaTTS._check_vits_speaker(
        str(tmp_path / "absent.json"), "Mea"
    ) == ""


def test_shipped_config_declares_the_default_speaker():
    """随包 finetune_speaker.json 必须真的有默认说话人，否则 D4 静默生效。"""
    from meapet.paths import project_path

    config_path = project_path(
        "vits_models", common_mod.DEFAULT_VITS_CONFIG_NAME
    )
    if not os.path.isfile(config_path):
        pytest.skip("随包模型配置不在（LFS 未 hydrate）")
    with open(config_path, "r", encoding="utf-8") as fh:
        hps = json.load(fh)
    speakers = hps.get("speakers") or {}
    assert DEFAULT_VITS_SPEAKER in speakers, (
        f"默认说话人 {DEFAULT_VITS_SPEAKER!r} 不在模型里: {list(speakers)}"
    )


# ══════════════════════════════════════════════════════════════════
# D2 · 向导保存路径的校验
# ══════════════════════════════════════════════════════════════════

def _wizard_stub(text: str):
    """只带 vits_python_input 的最小向导替身（不起 Qt）。"""
    from wizard.page_tts_vits import TtsPageVitsMixin

    class _Input:
        def __init__(self, value):
            self._value = value

        def text(self):
            return self._value

    class _Page(TtsPageVitsMixin):
        def __init__(self):
            self.vits_python_input = _Input(text)
            self.vits_status = None

        def log(self, _msg):
            pass

    return _Page()


def test_wizard_rejects_missing_python_on_save(tmp_path):
    """D2：填了一个不存在的路径，保存时必须当场说出来。"""
    page = _wizard_stub(str(tmp_path / "ghost" / "python.exe"))
    warning = page._validate_vits_python_for_save()
    assert warning and "找不到" in warning


def test_wizard_rejects_a_directory_on_save(tmp_path):
    page = _wizard_stub(str(tmp_path))
    assert page._validate_vits_python_for_save()


def test_wizard_accepts_a_real_interpreter(tmp_path):
    real_py = tmp_path / "python.exe"
    real_py.write_text("", encoding="utf-8")
    page = _wizard_stub(str(real_py))
    assert page._validate_vits_python_for_save() == ""


def test_wizard_is_silent_for_an_empty_field():
    assert _wizard_stub("")._validate_vits_python_for_save() == ""
    assert _wizard_stub("   ")._validate_vits_python_for_save() == ""


def test_wizard_reports_pet_exe_as_the_launcher(tmp_path):
    """填 MeaPet 自己：解析返回空串，校验必须点明这是启动器而不是 Python。"""
    pet = tmp_path / "MeaPet.exe"
    pet.write_text("mz", encoding="utf-8")
    page = _wizard_stub(str(pet))

    with mock.patch.object(
        common_mod, "_is_frozen", return_value=True
    ), mock.patch.object(common_mod.sys, "executable", str(pet)):
        warning = page._validate_vits_python_for_save()

    assert warning and "MeaPet" in warning


def test_wizard_route_summary_follows_the_delivery_judgement(tmp_path):
    """状态条预告的路线必须与交付码同一判据。"""
    real_py = tmp_path / "python.exe"
    real_py.write_text("", encoding="utf-8")

    assert "子进程" in _wizard_stub(str(real_py))._vits_route_summary()

    missing = _wizard_stub(str(tmp_path / "ghost" / "python.exe"))
    with mock.patch.object(common_mod, "_is_frozen", return_value=False):
        assert "无可用解释器" in missing._vits_route_summary()

    # torch 那条判据单独测，别让它跟着本机装没装 torch 翻来翻去
    with mock.patch.object(common_mod, "_is_frozen", return_value=True), mock.patch.object(
        common_mod, "module_present", return_value=True
    ):
        assert missing._vits_route_summary() == "实际走进程内 torch"


def test_wizard_route_summary_says_so_when_inprocess_has_no_torch(tmp_path):
    """进程内那条路的解释器就是本进程：本进程寻不到 torch 得当场说出来。

    health_check 现在会因这个判据报红，状态条如果还只写"实际走进程内 torch"，
    就是同一个事实在两处口径不一（用户看到绿的/白的，合成却失败）。
    """
    stub = _wizard_stub(str(tmp_path / "ghost" / "python.exe"))
    with mock.patch.object(common_mod, "_is_frozen", return_value=True), mock.patch.object(
        common_mod, "module_present", return_value=False
    ):
        summary = stub._vits_route_summary()

    assert "进程内" in summary, "路线预告别把选路结论改掉，只准追加读数"
    assert "寻不到 torch" in summary


def test_wizard_save_report_surfaces_a_warning():
    """_report_vits_python_for_save 必须把警告写到状态条上。"""
    from wizard.page_tts_vits import TtsPageVitsMixin

    page = _wizard_stub(r"C:\definitely\not\here\python.exe")
    seen: dict = {}
    page.vits_status = object()

    with mock.patch(
        "wizard.page_tts_vits.set_status",
        lambda widget, status, text: seen.update(status=status, text=text),
    ):
        warning = page._report_vits_python_for_save()

    assert warning
    assert seen["status"] == "warning"
    assert "⚠" in seen["text"]
    assert TtsPageVitsMixin is not None


def test_wizard_save_report_is_quiet_when_valid(tmp_path):
    real_py = tmp_path / "python.exe"
    real_py.write_text("", encoding="utf-8")
    page = _wizard_stub(str(real_py))
    page.vits_status = object()
    with mock.patch("wizard.page_tts_vits.set_status") as set_status:
        assert page._report_vits_python_for_save() == ""
    set_status.assert_not_called()

