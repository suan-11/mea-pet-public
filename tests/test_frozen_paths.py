"""Frozen / portable path and TTS packaging guards."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest


def test_get_data_dir_source_is_project_root():
    from meapet import paths as paths_mod

    with mock.patch.object(paths_mod, "is_frozen", return_value=False):
        assert Path(paths_mod.get_data_dir()) == paths_mod.PROJECT_ROOT


def test_get_data_dir_frozen_uses_meipass(tmp_path):
    from meapet import paths as paths_mod

    meipass = tmp_path / "_internal"
    meipass.mkdir()
    with mock.patch.object(paths_mod, "is_frozen", return_value=True), mock.patch.object(
        paths_mod.sys, "_MEIPASS", str(meipass), create=True
    ):
        assert Path(paths_mod.get_data_dir()) == meipass
        assert paths_mod.data_path("audio_cache") == str(meipass / "audio_cache")


def test_migrate_legacy_home_data_copies_missing_only(tmp_path):
    from meapet import paths as paths_mod

    legacy = tmp_path / "home" / ".meapet"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text('{"ok": true}\n', encoding="utf-8")
    (legacy / "logs").mkdir()
    (legacy / "logs" / "app.log").write_text("log\n", encoding="utf-8")

    meipass = tmp_path / "_internal"
    meipass.mkdir()
    # existing target must not be overwritten
    (meipass / "openclaw_device_identity.json").write_text("keep\n", encoding="utf-8")
    (legacy / "openclaw_device_identity.json").write_text("legacy\n", encoding="utf-8")

    paths_mod._MIGRATION_DONE = False
    with mock.patch.object(paths_mod, "is_frozen", return_value=True), mock.patch.object(
        paths_mod.sys, "_MEIPASS", str(meipass), create=True
    ), mock.patch.object(paths_mod, "_LEGACY_HOME_DATA", legacy):
        notes = paths_mod.migrate_legacy_home_data()

    assert (meipass / "config.json").read_text(encoding="utf-8") == '{"ok": true}\n'
    assert (meipass / "openclaw_device_identity.json").read_text(encoding="utf-8") == "keep\n"
    assert (meipass / "logs" / "app.log").read_text(encoding="utf-8") == "log\n"
    assert any("config.json" in n for n in notes)


def test_resolve_external_python_rejects_pet_exe(tmp_path):
    from meapet.tts import common as common_mod

    pet = tmp_path / "MeaPet.exe"
    pet.write_bytes(b"mz")
    real_py = tmp_path / "python.exe"
    real_py.write_bytes(b"mz")

    with mock.patch.object(common_mod, "_is_frozen", return_value=True), mock.patch.object(
        common_mod.sys, "executable", str(pet)
    ):
        assert common_mod.is_pet_executable(str(pet)) is True
        assert common_mod.resolve_external_python(str(pet)) == ""
        assert common_mod.resolve_external_python(str(real_py)) == str(real_py)
        assert common_mod.resolve_external_python("") == ""


def test_meatts_frozen_does_not_use_sys_executable(tmp_path, monkeypatch):
    from meapet.tts import service as service_mod

    pet = tmp_path / "MeaPet.exe"
    pet.write_text("", encoding="utf-8")
    monkeypatch.setattr(service_mod.sys, "executable", str(pet))
    monkeypatch.setattr(service_mod, "_is_frozen", lambda: True)
    monkeypatch.setattr(
        service_mod,
        "is_pet_executable",
        lambda p: bool(p) and os.path.realpath(p) == os.path.realpath(str(pet)),
    )

    def _resolve(p):
        if not p:
            return ""
        if os.path.realpath(p) == os.path.realpath(str(pet)):
            return ""
        return p if os.path.isfile(p) else ""

    monkeypatch.setattr(service_mod, "resolve_external_python", _resolve)
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "python_exe": str(pet),
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    tts = service_mod.MeaTTS(cfg)
    # Must never treat the frozen launcher as the GSV/VITS interpreter.
    assert tts.python_exe != str(pet)
    assert not service_mod.is_pet_executable(tts.python_exe)
    assert tts._vits_inprocess is True
    assert tts._vits_python != str(pet)


def test_meatts_uses_configured_vits_python_when_frozen(tmp_path, monkeypatch):
    from meapet.tts import service as service_mod

    pet = tmp_path / "MeaPet.exe"
    pet.write_text("", encoding="utf-8")
    real_py = tmp_path / "python.exe"
    real_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(service_mod.sys, "executable", str(pet))
    monkeypatch.setattr(service_mod, "_is_frozen", lambda: True)
    monkeypatch.setattr(
        service_mod,
        "is_pet_executable",
        lambda p: bool(p) and os.path.realpath(p) == os.path.realpath(str(pet)),
    )

    def _resolve(p):
        if not p:
            return ""
        if os.path.realpath(p) == os.path.realpath(str(pet)):
            return ""
        return p if os.path.isfile(p) else ""

    monkeypatch.setattr(service_mod, "resolve_external_python", _resolve)
    cfg = {
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_python": str(real_py),
            "output_dir": str(tmp_path / "audio_cache"),
        }
    }
    tts = service_mod.MeaTTS(cfg)
    assert tts._vits_python == str(real_py)
    assert tts._vits_inprocess is False


def test_hidden_subprocess_kwargs_on_windows():
    from meapet.tts.common import hidden_subprocess_kwargs

    kwargs = hidden_subprocess_kwargs()
    if os.name == "nt":
        assert "creationflags" in kwargs or "startupinfo" in kwargs
    else:
        assert kwargs == {}


def test_config_path_frozen_uses_meipass(tmp_path):
    from meapet.config import store as store_mod

    meipass = tmp_path / "_internal"
    meipass.mkdir()
    with mock.patch.object(store_mod.sys, "frozen", True, create=True), mock.patch.object(
        store_mod.sys, "_MEIPASS", str(meipass), create=True
    ):
        assert store_mod.config_path() == str(meipass / "config.json")
        assert store_mod.resolve_writable_config_path(None) == str(meipass / "config.json")
