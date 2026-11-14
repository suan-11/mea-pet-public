"""向导 ONNX 引擎段（P1-4 收尾）的契约。

盯三件事：
1. 引擎下拉真有 ONNX 这一档，选中后段落显隐与既有三引擎同形；
2. 状态读数与引擎同一判据（恰好一个 .onnx + tokens.txt），缺席/多份如实报；
3. 下载按钮把落盘目录填回输入框、保存流把 onnx 字段写进 config——
   「选引擎 + 保存」开箱即用，不留手改配置这一步。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meapet import onnx_voice_pack  # noqa: E402
from meapet.paths import voice_onnx_model_dir  # noqa: E402
from wizard.page_tts import TTSPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    widget = TTSPage()
    yield widget
    widget.close()
    QApplication.processEvents()


def make_model_dir(tmp_path, *, onnx_count=1, tokens=True):
    directory = tmp_path / "voice_onnx_model"
    directory.mkdir()
    for i in range(onnx_count):
        (directory / f"model{i}.onnx").write_bytes(b"fake")
    if tokens:
        (directory / "tokens.txt").write_text("_ 0\n")
    return directory


class ImmediateThread:
    """start() 即同步跑完——被测的是接线，不是线程本身。"""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


# ─── 下拉与显隐 ─────────────────────────────────────────────────────────


def test_combo_has_onnx_item(page):
    items = [page.backend_combo.itemData(i) for i in range(page.backend_combo.count())]
    assert "onnx" in items


def test_onnx_section_visible_only_for_onnx(page):
    page.engine_details_toggle.setChecked(True)
    page.show()
    QApplication.processEvents()
    page.set_engine("onnx")
    assert page.onnx_frame.isVisible()
    page.set_engine("mimo")
    assert not page.onnx_frame.isVisible()
    page.hide()


def test_details_collapsed_hides_onnx(page):
    page.set_engine("onnx")
    page.engine_details_toggle.setChecked(False)
    assert not page.onnx_frame.isVisible()


# ─── 配置回填 ───────────────────────────────────────────────────────────


def test_apply_config_restores_onnx(page, tmp_path):
    directory = make_model_dir(tmp_path)
    page.apply_config({
        "engine": "onnx",
        "enabled": True,
        "onnx_model_dir": str(directory),
        "onnx_speed": 1.2,
    })
    assert page.backend_combo.currentData() == "onnx"
    assert page.onnx_model_dir_input.text() == str(directory)
    assert page.onnx_speed_combo.currentData() == 1.2
    assert "模型就绪" in page.onnx_model_status.text()


def test_apply_config_keeps_odd_speed(page, tmp_path):
    """配置里的语速不在档位表时如实补一档，不悄悄改用户的值。"""
    page.apply_config({"engine": "onnx", "onnx_speed": 1.1})
    assert page.onnx_speed_combo.currentData() == 1.1


# ─── 状态读数：与引擎同判据 ─────────────────────────────────────────────


def test_check_reports_missing_dir(page, tmp_path):
    page.onnx_model_dir_input.setText(str(tmp_path / "not_there"))
    page._check_onnx_model()
    assert "尚未下载" in page.onnx_model_status.text()


def test_check_reports_empty_dir(page, tmp_path):
    directory = make_model_dir(tmp_path, onnx_count=0, tokens=False)
    page.onnx_model_dir_input.setText(str(directory))
    page._check_onnx_model()
    assert "没有 .onnx" in page.onnx_model_status.text()


def test_check_reports_multiple_onnx(page, tmp_path):
    directory = make_model_dir(tmp_path, onnx_count=2)
    page.onnx_model_dir_input.setText(str(directory))
    page._check_onnx_model()
    assert "拒绝挑" in page.onnx_model_status.text()


def test_check_reports_missing_tokens(page, tmp_path):
    directory = make_model_dir(tmp_path, tokens=False)
    page.onnx_model_dir_input.setText(str(directory))
    page._check_onnx_model()
    assert "tokens.txt" in page.onnx_model_status.text()


def test_default_dir_fallback(page):
    page.onnx_model_dir_input.setText("")
    assert page._onnx_effective_dir() == str(voice_onnx_model_dir())


# ─── 下载接线 ───────────────────────────────────────────────────────────


def test_download_success_fills_dir(page, tmp_path):
    target = str(tmp_path / "installed_model")

    def fake_install(url, dest, **kwargs):
        assert url == onnx_voice_pack.DEFAULT_ONNX_VOICE_URL
        assert str(dest) == target or str(dest) == str(voice_onnx_model_dir())
        progress = kwargs.get("progress")
        if progress:
            progress("下载")
        return {
            "model_dir": str(dest), "sha256": "ab" * 32,
            "route": "api 资产路由", "url": url,
        }

    page.onnx_model_dir_input.setText(target)
    with patch("meapet.onnx_voice_pack.install_onnx_voice", fake_install), \
         patch("wizard.page_tts_onnx.threading.Thread", ImmediateThread), \
         patch("wizard.page_tts_onnx._post_to_main", lambda fn: fn()):
        page._download_onnx_model()
    assert page.onnx_model_dir_input.text() == target
    assert "就绪" in page.onnx_download_status.text()
    assert "请保存配置生效" in page.onnx_download_status.text()
    assert page.onnx_download_btn.isEnabled()


def test_download_failure_reports_loudly(page, tmp_path):
    def fake_install(url, dest, **kwargs):
        raise onnx_voice_pack.OnnxVoicePackError("网络不通")

    page.onnx_model_dir_input.setText(str(tmp_path))
    with patch("meapet.onnx_voice_pack.install_onnx_voice", fake_install), \
         patch("wizard.page_tts_onnx.threading.Thread", ImmediateThread), \
         patch("wizard.page_tts_onnx._post_to_main", lambda fn: fn()):
        page._download_onnx_model()
    assert "下载失败" in page.onnx_download_status.text()
    assert "网络不通" in page.onnx_download_status.text()
    assert page.onnx_download_btn.isEnabled()


def test_download_uses_url_override(page, tmp_path):
    seen = {}

    def fake_install(url, dest, **kwargs):
        seen["url"] = url
        return {"model_dir": str(dest), "sha256": "cd" * 32, "route": "自定义直链", "url": url}

    custom = "https://mirror.example.com/x/vits-piper-zh_CN-chaowen-medium-int8.tar.bz2"
    page.onnx_url_input.setText(custom)
    with patch("meapet.onnx_voice_pack.install_onnx_voice", fake_install), \
         patch("wizard.page_tts_onnx.threading.Thread", ImmediateThread), \
         patch("wizard.page_tts_onnx._post_to_main", lambda fn: fn()):
        page._download_onnx_model()
    assert seen["url"] == custom


# ─── 探测句语言跟引擎能力走 ─────────────────────────────────────────────


def test_probe_language_follows_engine_capabilities():
    from wizard.connection_test import _probe_language

    # ONNX 只声明 zh/en：voice_lang=jp 时探测句回落中文，不让它念日语怪音
    assert _probe_language({"engine": "onnx", "voice_lang": "jp"}) == "zh"
    assert _probe_language({"engine": "onnx", "voice_lang": "zh"}) == "zh"
    # 既有引擎不在注册表里，行为一字不变
    assert _probe_language({"engine": "mimo", "voice_lang": "jp"}) == "jp"
    assert _probe_language({"engine": "gpt_sovits", "voice_lang": "jp"}) == "jp"


# ─── 保存流：config 真拿到 onnx 字段 ────────────────────────────────────


@pytest.fixture
def wizard(qapp):
    from wizard.app import SetupWizard

    w = SetupWizard()
    w.env_page._check_timer.stop()
    w._load_timer.stop()
    for timer in w.tts_page._startup_timers:
        timer.stop()
    yield w
    w.close()
    QApplication.processEvents()


def test_collect_writes_onnx_fields(wizard, tmp_path):
    wizard.tts_page.set_engine("onnx")
    wizard.tts_page.onnx_model_dir_input.setText(str(tmp_path))
    wizard.tts_page.onnx_speed_combo.setCurrentIndex(2)  # 1.2×
    config = {"tts": {"engine": "mimo"}}
    wizard._collect_tts_fields(config)
    assert config["tts"]["engine"] == "onnx"
    assert config["tts"]["onnx_model_dir"] == str(tmp_path)
    assert config["tts"]["onnx_speed"] == 1.2


def test_collect_empty_dir_falls_back_to_default(wizard):
    """选了 ONNX 但目录留空 → 落默认目录（冻结版随包模型就在那里）。"""
    wizard.tts_page.set_engine("onnx")
    wizard.tts_page.onnx_model_dir_input.setText("")
    config = {"tts": {}}
    wizard._collect_tts_fields(config)
    assert config["tts"]["onnx_model_dir"] == str(voice_onnx_model_dir())


def test_collect_other_engine_keeps_existing_onnx_dir(wizard):
    wizard.tts_page.set_engine("mimo")
    wizard.tts_page.onnx_model_dir_input.setText("")
    config = {"tts": {"onnx_model_dir": "/keep/me"}}
    wizard._collect_tts_fields(config)
    assert config["tts"]["onnx_model_dir"] == "/keep/me"
