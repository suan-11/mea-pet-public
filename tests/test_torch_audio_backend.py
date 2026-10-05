"""torchaudio 的 sox 后端在 sox_ng 机器上是 SIGSEGV，交付码里不能留下裸 ``torchaudio.load``。

出处：Athena 件 gsv-linux-runtime-support 的真机验收。读数——
``gdb`` 抓到崩溃帧全在 ``torchaudio/lib/libtorchaudio_sox.so``
(``load_audio_file → apply_effects_file → SoxEffectsChain::addOutputBuffer``)，
而 ``ldd`` 把它解析到 ``/usr/lib/libsox.so`` → ``readlink -f`` = ``libsox_ng.so.3.0.0``
（Arch 的 ``sox`` 包已是 sox_ng 的分身，14.8；wheel 是按 14.4.2 的 ABI 编的）。

真撞这道崩的只有 ``meapet/tools/gsv_infer.py`` 一处（它调上游无参的 ``torchaudio.load``）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gsv_infer = _load_module(ROOT / "meapet" / "tools" / "gsv_infer.py", "gsv_infer_under_test")


def _fake_torchaudio(monkeypatch, backends, *, with_api=True):
    """把假 torchaudio 的私有后端表塞进 sys.modules，返回该模块。"""
    ta = types.ModuleType("torchaudio")
    ta.load = lambda *a, **k: "sox-load"
    ta.info = lambda *a, **k: "sox-info"
    ta.save = lambda *a, **k: "sox-save"
    package = types.ModuleType("torchaudio._backend")
    utils = types.ModuleType("torchaudio._backend.utils")
    if with_api:
        utils.get_available_backends = lambda: backends
        utils.get_load_func = lambda: (lambda *a, **k: "rebuilt-load")
        utils.get_info_func = lambda: (lambda *a, **k: "rebuilt-info")
        utils.get_save_func = lambda: (lambda *a, **k: "rebuilt-save")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "torchaudio", ta)
    monkeypatch.setitem(sys.modules, "torchaudio._backend", package)
    monkeypatch.setitem(sys.modules, "torchaudio._backend.utils", utils)
    return ta


def _call(windows, backends, monkeypatch):
    ta = _fake_torchaudio(monkeypatch, backends)
    action = gsv_infer.prefer_non_sox_backend(windows=windows)
    return ta, action


def test_sox_stays_put_on_windows(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta, action = _call(True, backends, monkeypatch)
    assert action == "windows-untouched"
    assert list(backends) == ["sox", "soundfile"]
    assert ta.load() == "sox-load"


def test_sox_is_dropped_and_the_dispatchers_are_rebuilt(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta, action = _call(False, backends, monkeypatch)
    assert action == "sox-dropped"
    assert "sox" not in backends
    # 光从表里摘掉不够：后端表在 import 期就固化进闭包，必须重建 load/info/save
    assert ta.load() == "rebuilt-load"
    assert ta.info() == "rebuilt-info"
    assert ta.save() == "rebuilt-save"


def test_sox_survives_when_soundfile_is_absent(monkeypatch):
    backends = {"sox": object()}
    ta, action = _call(False, backends, monkeypatch)
    assert action == "no-soundfile"
    assert "sox" in backends
    assert ta.load() == "sox-load"


def test_nothing_to_do_when_sox_was_never_there(monkeypatch):
    backends = {"soundfile": object()}
    _, action = _call(False, backends, monkeypatch)
    assert action == "no-sox"


def test_unrecognised_torchaudio_shape_does_not_raise(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta = _fake_torchaudio(monkeypatch, backends, with_api=False)
    action = gsv_infer.prefer_non_sox_backend(windows=False)
    assert action == "unknown-api"
    assert "sox" in backends and ta.load() == "sox-load"


def test_no_bare_torchaudio_load_left_in_our_own_code():
    """我们自己的代码里不许出现裸 ``torchaudio.load``——在 sox_ng 机器上是段错误。

    上游整合包内的无参调用改不了（那是用户持有的第三方文件），靠 ``prefer_non_sox_backend``
    摘后端表绕开；这一条只守我们仓库里的那几支。
    """
    offenders = []
    for rel in ("meapet/tools/gsv_infer.py",
                "meapet/tts/engines/vits.py", "meapet/tts/engines/vits_runtime.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "torchaudio.load(" in line:
                offenders.append(f"{rel}:{line_no}: {line.strip()}")
    assert not offenders, "裸 torchaudio.load:\n" + "\n".join(offenders)
