"""构建后置闸门（scripts/verify_dist.py）的断言测试。

本文件能证明什么
----------------
* 验证器的每条断言在**构造的假产物树**上真的会拦：旧 CRT 回潮、关键资产缺件、
  开发者 config.json / mea_memory.db* 泄露、core 体积超限、doctor 冒烟失败
  （非零退出 / 坏 JSON / 空探针 / 非冻结态 / 超时）都有独立的红测试。
* doctor 子进程调用形状（--doctor --skip-tts --doctor-format json，Linux 另加
  --skip-layer）被钉住——它是路线图 P0-1 验收①的机器面。
* 干净产物树全绿，且 --profile full 不受 core 体积上限约束。

本文件不能证明什么
------------------
* 不证真构建产物合规（那要 scripts/build_windows.ps1 真跑一遍，L3 构建机行为）；
  这里 doctor 一律用假 runner，不起真 exe。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_dist import (  # noqa: E402
    main,
    run_doctor,
    verify_dist,
)


def _doctor_payload(frozen: bool = True) -> str:
    return json.dumps(
        {
            "elapsed_s": 0.1,
            "items": [
                {"name": "runtime", "value": f"版本 1.0.0 · frozen={frozen}", "judge": "j"},
                {"name": "paths", "value": "数据目录=…（存在=True）", "judge": "j"},
            ],
        },
        ensure_ascii=False,
    )


def _ok_runner(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout=_doctor_payload(), stderr="")


def _make_dist(tmp_path: Path, platform: str = "windows") -> Path:
    dist = tmp_path / "MeaPet"
    exe = dist / ("MeaPet.exe" if platform == "windows" else "MeaPet")
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"fake-exe")
    if platform == "linux":
        exe.chmod(0o755)
    internal = dist / "_internal"
    font = internal / "meapet" / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"
    font.parent.mkdir(parents=True, exist_ok=True)
    font.write_bytes(b"fake-font")
    vits = internal / "meapet" / "tools" / "vits_infer.py"
    vits.parent.mkdir(parents=True, exist_ok=True)
    vits.write_text("# fake", encoding="utf-8")
    (internal / "config.example.json").write_text("{}", encoding="utf-8")
    if platform == "windows":
        qtbin = internal / "PyQt5" / "Qt5" / "bin"
        qtbin.mkdir(parents=True, exist_ok=True)
        (qtbin / "Qt5Core.dll").write_bytes(b"qt")
    else:
        (internal / "liblayer_shell_shim.so").write_bytes(b"so")
        (internal / "numpy").mkdir(parents=True, exist_ok=True)
    return dist


def _verify(dist: Path, platform: str = "windows", **kwargs):
    params = dict(platform=platform, runner=_ok_runner)
    params.update(kwargs)
    return verify_dist(dist, **params)


def test_clean_windows_dist_passes(tmp_path):
    report = _verify(_make_dist(tmp_path))
    assert report["ok"], report["failures"]
    assert report["checks"]["qtbin_old_crt_count"] == 0
    assert report["checks"]["doctor"]["item_count"] == 2


def test_qtbin_old_crt_relapse_fails(tmp_path):
    dist = _make_dist(tmp_path)
    qtbin = dist / "_internal" / "PyQt5" / "Qt5" / "bin"
    (qtbin / "MSVCP140.dll").write_bytes(b"old-crt")
    (qtbin / "vcruntime140_1.dll").write_bytes(b"old-crt")
    report = _verify(dist)
    assert not report["ok"]
    assert any("旧 CRT" in f for f in report["failures"])
    assert report["checks"]["qtbin_old_crt_count"] == 2


def test_missing_font_fails(tmp_path):
    dist = _make_dist(tmp_path)
    (dist / "_internal" / "meapet" / "assets" / "fonts" / "LXGWWenKai-Regular.ttf").unlink()
    report = _verify(dist)
    assert not report["ok"]
    assert any("关键资产缺失" in f and "LXGWWenKai" in f for f in report["failures"])


def test_missing_vits_infer_fails(tmp_path):
    dist = _make_dist(tmp_path)
    (dist / "_internal" / "meapet" / "tools" / "vits_infer.py").unlink()
    report = _verify(dist)
    assert not report["ok"]
    assert any("vits_infer.py" in f for f in report["failures"])


def test_developer_config_leak_fails(tmp_path):
    dist = _make_dist(tmp_path)
    (dist / "_internal" / "config.json").write_text('{"api_key": "secret"}', encoding="utf-8")
    report = _verify(dist)
    assert not report["ok"]
    assert any("泄露" in f for f in report["failures"])


def test_memory_db_wal_leak_in_subdir_fails(tmp_path):
    dist = _make_dist(tmp_path)
    nested = dist / "_internal" / "logs"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "mea_memory.db-wal").write_bytes(b"wal")
    report = _verify(dist)
    assert not report["ok"]
    assert report["checks"]["leaked_files"]


def test_config_example_is_allowed(tmp_path):
    report = _verify(_make_dist(tmp_path))
    assert report["ok"], report["failures"]
    assert report["checks"]["leaked_files"] == []


def test_core_size_cap_enforced(tmp_path):
    dist = _make_dist(tmp_path)
    report = _verify(dist, profile="core", max_core_mb=0.0)
    assert not report["ok"]
    assert any("超上限" in f for f in report["failures"])


def test_full_profile_not_size_capped(tmp_path):
    report = _verify(_make_dist(tmp_path), profile="full", max_core_mb=0.0)
    assert report["ok"], report["failures"]


def test_doctor_nonzero_exit_fails(tmp_path):
    def bad_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    report = _verify(_make_dist(tmp_path), runner=bad_runner)
    assert not report["ok"]
    assert any("doctor exit=1" in f for f in report["failures"])


def test_doctor_bad_json_fails(tmp_path):
    def bad_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="不是 JSON", stderr="")

    report = _verify(_make_dist(tmp_path), runner=bad_runner)
    assert not report["ok"]
    assert any("合法 JSON" in f for f in report["failures"])


def test_doctor_empty_items_fails(tmp_path):
    def bad_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"elapsed_s": 0.1, "items": []}), stderr=""
        )

    report = _verify(_make_dist(tmp_path), runner=bad_runner)
    assert not report["ok"]
    assert any("items 为空" in f for f in report["failures"])


def test_doctor_source_mode_fails(tmp_path):
    def bad_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=_doctor_payload(frozen=False), stderr="")

    report = _verify(_make_dist(tmp_path), runner=bad_runner)
    assert not report["ok"]
    assert any("frozen=True" in f for f in report["failures"])


def test_doctor_timeout_fails(tmp_path):
    def slow_runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    report = _verify(_make_dist(tmp_path), runner=slow_runner)
    assert not report["ok"]
    assert any("超时" in f for f in report["failures"])


def test_doctor_command_shape_windows(tmp_path):
    seen: list = []

    def spy_runner(cmd, **kwargs):
        seen.append(cmd)
        return _ok_runner(cmd, **kwargs)

    dist = _make_dist(tmp_path)
    failures, _ = run_doctor(dist, "windows", 30, spy_runner)
    assert failures == []
    assert seen[0][1:] == ["--doctor", "--skip-tts", "--doctor-format", "json"]


def test_doctor_command_shape_linux_adds_skip_layer(tmp_path):
    seen: list = []

    def spy_runner(cmd, **kwargs):
        seen.append(cmd)
        return _ok_runner(cmd, **kwargs)

    dist = _make_dist(tmp_path, platform="linux")
    failures, _ = run_doctor(dist, "linux", 30, spy_runner)
    assert failures == []
    assert "--skip-layer" in seen[0]


def test_linux_missing_shim_fails(tmp_path):
    dist = _make_dist(tmp_path, platform="linux")
    (dist / "_internal" / "liblayer_shell_shim.so").unlink()
    report = _verify(dist, platform="linux")
    assert not report["ok"]
    assert any("liblayer_shell_shim.so" in f for f in report["failures"])


def test_linux_missing_numpy_fails(tmp_path):
    dist = _make_dist(tmp_path, platform="linux")
    (dist / "_internal" / "numpy").rmdir()
    report = _verify(dist, platform="linux")
    assert not report["ok"]
    assert any("numpy" in f for f in report["failures"])


def test_linux_exe_without_exec_bit_fails(tmp_path):
    dist = _make_dist(tmp_path, platform="linux")
    (dist / "MeaPet").chmod(0o644)
    report = _verify(dist, platform="linux")
    # Windows 宿主上 os.access(X_OK) 语义不同：仅在 POSIX 上这条才有判别力
    if os.name == "posix":
        assert not report["ok"]
        assert any("可执行位" in f for f in report["failures"])


def test_missing_exe_fails(tmp_path):
    dist = _make_dist(tmp_path)
    (dist / "MeaPet.exe").unlink()
    report = _verify(dist)
    assert not report["ok"]
    assert any("缺产物可执行文件" in f for f in report["failures"])


def test_skip_doctor_warns_but_passes(tmp_path):
    report = _verify(_make_dist(tmp_path), skip_doctor=True)
    assert report["ok"]
    assert report["warnings"]
    assert report["checks"]["doctor"] == {"skipped": True}


def test_main_exit_codes(tmp_path, capsys):
    dist = _make_dist(tmp_path)
    code = main(["--dist", str(dist), "--platform", "windows", "--skip-doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert "VERIFY_JSON=" in out
    (dist / "_internal" / "config.json").write_text("{}", encoding="utf-8")
    code = main(["--dist", str(dist), "--platform", "windows", "--skip-doctor"])
    assert code == 1
