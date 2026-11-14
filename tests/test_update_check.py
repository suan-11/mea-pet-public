"""检查更新（meapet/update_check.py）与菜单入口的护栏。

本文件能证明什么
----------------
* parse_version / is_newer_version 的容错与比较语义（v 前缀、非数字段、
  短版本补 0、判不了返回 None）。
* mock HTTP 下三种结果（有更新 / 已是最新 / 检查失败）的形状与降级行为；
  请求确实打到 NekoHome-Studio 的真实上游 API、带 meapet.USER_AGENT、
  超时 10s；sha256 从 digest 字段或 SHA256SUMS.txt 文本资产读出。
* **只提示不升级**：整个检查只 GET release JSON 与 SHA256SUMS.txt，
  绝不触碰 zip 本体的下载 URL。
* 「检查更新…」菜单项存在于 _build_context_menu 产出的菜单树（设置与数据
  子菜单下），触发动作确实接到 _check_for_updates；offscreen Qt 下
  后台线程→信号→信息框的完整链路可走通（HTTP 与 exec_ 均 mock）。

本文件不能证明什么
------------------
* 不证真实 GitHub API 的可达性与真实 release 的资产形状（HTTP 全 mock，
  测试禁真实网络）；首次真实检查需人工复核。
* 不证真实 GUI 的显示效果（offscreen，无真窗口）；不证 PyInstaller 冻结
  态下 QThread/httpx 的行为（那是构建机实测的活）。
* 不证 release.yml 真能产出 Release（CI 静态审查级，见该文件头注）。
"""
from __future__ import annotations

import time
import types
from unittest import mock

import httpx
import pytest

import meapet
import meapet.update_check as uc


# ---------------------------------------------------------------- 版本解析

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2.3", (1, 2, 3)),
        ("V1.0", (1, 0)),
        ("  v2.0.0  ", (2, 0, 0)),
        ("1.2.3-beta", (1, 2, 3)),
        ("1.2.3rc1", (1, 2, 3)),
        ("1.2.3.4", (1, 2, 3, 4)),
        ("1..2", (1,)),
        ("", None),
        ("abc", None),
        ("v", None),
        (None, None),
        (123, None),
    ],
)
def test_parse_version(text, expected):
    assert uc.parse_version(text) == expected


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("v1.10.0", "1.9.0", True),  # 按数字段比，不是字符串比
        ("1.0.1", "1.0.0", True),
        ("v2.0", "1.9.9", True),
        ("1.0.0", "1.0", False),  # 补 0 后相等
        ("1.0", "1.0.0", False),
        ("v1.0.0", "1.0.0", False),
        ("0.9.0", "1.0.0", False),
        ("garbage", "1.0.0", None),  # 判不了就说判不了
        ("1.0.0", "garbage", None),
    ],
)
def test_is_newer_version(candidate, current, expected):
    assert uc.is_newer_version(candidate, current) is expected


def test_release_api_points_to_real_upstream():
    """上游必须是 NekoHome-Studio；pyproject 里的 suan-11 是旧地址。"""
    assert uc.RELEASE_API == (
        "https://api.github.com/repos/NekoHome-Studio/mea-pet-public/releases/latest"
    )
    assert "suan-11" not in uc.RELEASE_API


# ---------------------------------------------------------------- mock HTTP

ZIP_NAME = "MeaPet-win64-core-v9.9.9.zip"
ZIP_URL = "https://github.com/NekoHome-Studio/mea-pet-public/releases/download/v9.9.9/" + ZIP_NAME
SUMS_URL = "https://github.com/NekoHome-Studio/mea-pet-public/releases/download/v9.9.9/SHA256SUMS.txt"
SHA = "ab" * 32


def _release_payload(tag="v9.9.9", assets=None, body="修复了若干问题。"):
    return {
        "tag_name": tag,
        "body": body,
        "html_url": "https://github.com/NekoHome-Studio/mea-pet-public/releases/tag/" + tag,
        "assets": [] if assets is None else assets,
    }


def _zip_asset(digest=None):
    asset = {"name": ZIP_NAME, "browser_download_url": ZIP_URL}
    if digest is not None:
        asset["digest"] = digest
    return asset


def _sums_asset():
    return {"name": "SHA256SUMS.txt", "browser_download_url": SUMS_URL}


def _response(status_code=200, payload=None, text=""):
    def _json():
        if isinstance(payload, Exception):
            raise payload
        return payload

    return types.SimpleNamespace(status_code=status_code, json=_json, text=text)


def _patch_client(responses):
    """替换 httpx.Client：get 按序返回/抛出 responses 里的元素。"""
    client = mock.MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = list(responses)
    patcher = mock.patch.object(uc.httpx, "Client", return_value=client)
    return patcher, client


def test_update_available_reads_sha_from_sums_asset():
    payload = _release_payload(assets=[_zip_asset(), _sums_asset()])
    sums_text = SHA + "  " + ZIP_NAME + "\n"
    patcher, client = _patch_client([_response(payload=payload), _response(text=sums_text)])
    with patcher:
        result = uc.check_for_updates("1.0.0")
        # 请求口径要在 patch 生效期间读：真实上游 + UA + 10s 超时
        kwargs = uc.httpx.Client.call_args.kwargs
    assert result.status == "update_available"
    assert result.has_update is True
    assert result.current_version == "1.0.0"
    assert result.latest_version == "v9.9.9"
    assert result.asset_name == ZIP_NAME
    assert result.asset_download_url == ZIP_URL
    assert result.asset_sha256 == SHA
    assert result.release_notes == "修复了若干问题。"
    assert kwargs["headers"]["User-Agent"] == meapet.USER_AGENT
    assert kwargs["timeout"] == uc.CHECK_TIMEOUT_SECONDS == 10.0
    urls = [call.args[0] for call in client.get.call_args_list]
    assert urls == [uc.RELEASE_API, SUMS_URL]
    # 只提示不升级：绝不触碰 zip 本体
    assert not any(u.endswith(".zip") for u in urls)


def test_update_available_reads_sha_from_digest_field():
    payload = _release_payload(assets=[_zip_asset(digest="sha256:" + SHA)])
    patcher, client = _patch_client([_response(payload=payload)])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "update_available"
    assert result.asset_sha256 == SHA
    # digest 在场时不必再拉 SHA256SUMS.txt
    assert client.get.call_count == 1


def test_update_available_without_assets_degrades_gracefully():
    patcher, _client = _patch_client([_response(payload=_release_payload(assets=[]))])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "update_available"
    assert result.asset_name == ""
    assert result.asset_sha256 == ""


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.9.0", "1.0"])
def test_up_to_date(tag):
    patcher, client = _patch_client([_response(payload=_release_payload(tag=tag))])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "up_to_date"
    assert result.has_update is False
    assert result.latest_version == tag
    assert client.get.call_count == 1  # 没有更新就不必读资产


def test_error_on_timeout():
    patcher, _client = _patch_client([httpx.TimeoutException("timed out")])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "error"
    assert "超时" in result.error


def test_error_on_http_status():
    patcher, _client = _patch_client([_response(status_code=403)])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "error"
    assert "403" in result.error


def test_error_on_bad_json():
    patcher, _client = _patch_client([_response(payload=ValueError("bad json"))])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "error"
    assert "解析失败" in result.error


def test_error_on_missing_tag():
    patcher, _client = _patch_client([_response(payload={"body": "no tag"})])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "error"
    assert "tag_name" in result.error


def test_error_on_unparseable_version():
    patcher, _client = _patch_client([_response(payload=_release_payload(tag="nightly"))])
    with patcher:
        result = uc.check_for_updates("1.0.0")
    assert result.status == "error"
    assert "判不了" in result.error


def test_default_current_version_comes_from_package():
    patcher, _client = _patch_client([_response(payload=_release_payload(tag="v0.0.1"))])
    with patcher:
        result = uc.check_for_updates()
    assert result.current_version == meapet.__version__
    assert result.status == "up_to_date"


# ---------------------------------------------------------------- 文案渲染

def test_format_result_text_three_states():
    update = uc.UpdateCheckResult(
        status="update_available",
        current_version="1.0.0",
        latest_version="v9.9.9",
        release_url="https://example.invalid/release",
        release_notes="更新说明正文",
        asset_name=ZIP_NAME,
        asset_download_url=ZIP_URL,
        asset_sha256=SHA,
    )
    title, text = uc.format_result_text(update)
    assert title and text
    assert "v9.9.9" in text and SHA in text and ZIP_NAME in text
    assert "不会自动下载" in text

    title, text = uc.format_result_text(
        uc.UpdateCheckResult(status="up_to_date", current_version="1.0.0")
    )
    assert "已是最新" in text

    title, text = uc.format_result_text(
        uc.UpdateCheckResult(status="error", current_version="1.0.0", error="网络超时")
    )
    assert "网络超时" in text
    assert uc.RELEASE_PAGE_URL in text


# ---------------------------------------------------------------- Qt 链路（offscreen）

def test_worker_run_emits_result(monkeypatch, _qt_application):
    """run() 把 check_for_updates 的结果经 checked 信号发出（不真起线程）。"""
    canned = uc.UpdateCheckResult(status="up_to_date", current_version="1.0.0")
    monkeypatch.setattr(uc, "check_for_updates", lambda *a, **k: canned)
    worker = uc.UpdateCheckWorker()
    received = []
    worker.checked.connect(received.append)
    worker.run()
    assert received == [canned]
    worker.deleteLater()


def _make_chrome_host():
    """最小宿主：只带菜单构建要读的字段；其余 mixin 方法给无操作替身。

    _build_context_menu 在构建期只 connect 那些方法、不调用它们（除
    _is_auto_start / _build_click_feedback_menu，前者显式给 False，后者
    是 mixin 里的真函数、吃空 config 没问题）。
    """
    from PyQt5.QtWidgets import QWidget

    from meapet.desktop.window_chrome import PetWindowChromeMixin

    class _Host(QWidget, PetWindowChromeMixin):
        _update_check_worker = None

        def __init__(self):
            super().__init__()
            self.config = {}
            self._standby = False
            self._use_live2d = False
            self._dev_unlocked = False

        def _is_auto_start(self):
            return False

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return lambda *a, **k: None

    return _Host()


def _find_action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
        submenu = action.menu()
        if submenu is not None:
            found = _find_action(submenu, text)
            if found is not None:
                return found
    return None


def test_context_menu_has_update_entry(monkeypatch, _qt_application):
    """「检查更新…」在设置与数据子菜单里，且触发接到 _check_for_updates。"""
    host = _make_chrome_host()
    calls = []
    monkeypatch.setattr(host, "_check_for_updates", lambda: calls.append(1))
    menu = host._build_context_menu()
    try:
        settings = _find_action(menu, "设置与数据")
        assert settings is not None and settings.menu() is not None
        action = _find_action(settings.menu(), "检查更新…")
        assert action is not None, "菜单树里找不到「检查更新…」"
        action.trigger()
        assert calls == [1]
    finally:
        menu.deleteLater()
        host.close()
        host.deleteLater()


def test_chrome_check_flow_offscreen(monkeypatch, _qt_application):
    """后台线程→信号→信息框全链路（HTTP 与 exec_ 均 mock，无真 GUI/网络）。"""
    from PyQt5.QtWidgets import QMessageBox

    canned = uc.UpdateCheckResult(
        status="up_to_date", current_version="1.0.0", latest_version="v1.0.0"
    )
    monkeypatch.setattr(uc, "check_for_updates", lambda *a, **k: canned)
    shown = []

    def _fake_exec(self):
        shown.append(self.text())
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec_", _fake_exec)
    host = _make_chrome_host()
    try:
        host._check_for_updates()
        deadline = time.monotonic() + 5.0
        while getattr(host, "_update_check_worker", None) is not None:
            if time.monotonic() > deadline:
                pytest.fail("后台检查 5s 内没有回到 GUI 线程")
            _qt_application.processEvents()
            time.sleep(0.01)
        assert shown and "已是最新" in shown[0]
        # 让 deleteLater 落地，避免线程对象泄漏到下一项测试
        for _ in range(5):
            _qt_application.processEvents()
    finally:
        host.close()
        host.deleteLater()


def test_chrome_does_not_stack_concurrent_checks(monkeypatch, _qt_application):
    """已有一次检查在途时，再点菜单不发第二次。"""
    host = _make_chrome_host()
    created = []

    class _BusyWorker:
        def isRunning(self):
            return True

    monkeypatch.setattr(
        uc, "UpdateCheckWorker", lambda *a, **k: created.append(1)
    )
    host._update_check_worker = _BusyWorker()
    try:
        host._check_for_updates()
        assert created == []
    finally:
        host.close()
        host.deleteLater()
