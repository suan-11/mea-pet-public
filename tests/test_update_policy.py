"""更新覆盖白名单契约（meapet/update_policy.py）的钉子。

本文件能证明什么
----------------
* 白名单真值表就是路线图 P0-3 锁的那几张：config.json、mea_memory.db*
  （含 -wal/-shm）、logs/、voice_cache/、voice_asr/、addons/、audio_cache/。
* 覆盖清单里只要出现受保护路径（含大小写变体、反斜杠、./ 与 _internal/
  前缀写法），assert_update_safe 必抛 UpdateProtectedError——验收判据②
  「覆盖清单含 config.json / mea_memory.db 时必被拒」在契约层成立。
* 程序资产（meapet/ 下的同名文件、config.example.json）不受保护，
  更新器仍可正常覆盖它们；绝对路径与 .. 逃逸一律 fail-closed 拒绝。

本文件不能证明什么
------------------
* 不证真实更新器的行为——更新器尚未实现；它必须自觉调用
  assert_update_safe，绕过契约直接写盘不在防护范围内。
* 不证便携契约本身（那是 tests/test_frozen_paths.py 的活，本文件不改它）。
* 不证覆盖演练后数据完好（那是 docs/update-and-release.zh-CN.md 的
  人工演练步骤，需要真实两版包）。
"""
from __future__ import annotations

import pytest

from meapet.update_policy import (
    UPDATE_PROTECTED,
    UpdateProtectedError,
    assert_update_safe,
    is_protected_relpath,
)


def test_whitelist_table_is_the_roadmap_contract():
    """真值表必须恰好是 P0-3 锁定的七张——改表必须先有意识地改这条。"""
    assert set(UPDATE_PROTECTED) == {
        "config.json",
        "mea_memory.db*",
        "logs/",
        "voice_cache/",
        "voice_asr/",
        "addons/",
        "audio_cache/",
    }


@pytest.mark.parametrize(
    "rel",
    [
        "config.json",
        "CONFIG.JSON",
        "./config.json",
        "_internal/config.json",
        "_internal\\config.json",
        "mea_memory.db",
        "mea_memory.db-wal",
        "mea_memory.db-shm",
        "mea_memory.db-journal",
        "MEA_MEMORY.DB-WAL",
        "logs",
        "logs/",
        "logs/app.log",
        "logs\\sub\\deep.log",
        "voice_cache/a.wav",
        "voice_asr/models/encoder.onnx",
        "addons/some_addon/__init__.py",
        "audio_cache/tts_001.wav",
        "_internal/mea_memory.db-shm",
        "_internal/logs/app.log",
    ],
)
def test_protected_paths_hit(rel):
    assert is_protected_relpath(rel) is True, rel


@pytest.mark.parametrize(
    "rel",
    [
        "MeaPet.exe",
        "config.example.json",
        "meapet/config.py",
        "meapet/assets/config.json",  # 程序资产里的同名文件不是用户数据
        "_internal/meapet/paths.py",
        "logs.txt",  # 根下文件，不是 logs/ 目录
        "voice_cache_new/x.wav",
        "addons2/x.py",
        "audio_cache2/x.wav",
        "mea_memory_backup.db",  # 前缀不匹配 mea_memory.db
        "README.md",
        "",
    ],
)
def test_unprotected_paths_pass(rel):
    assert is_protected_relpath(rel) is False, rel


def test_assert_update_safe_accepts_program_files():
    """纯程序文件的覆盖清单必须放行（返回 None，不抛）。"""
    assert assert_update_safe(
        ["MeaPet.exe", "_internal/meapet/paths.py", "config.example.json"]
    ) is None
    assert assert_update_safe([]) is None


@pytest.mark.parametrize(
    "bad",
    ["config.json", "mea_memory.db", "mea_memory.db-wal", "logs/app.log"],
)
def test_assert_update_safe_rejects_protected(bad):
    """验收判据②：覆盖清单含 config.json / mea_memory.db 时必被拒。"""
    with pytest.raises(UpdateProtectedError) as excinfo:
        assert_update_safe(["MeaPet.exe", bad, "_internal/meapet/paths.py"])
    assert any(bad in v for v in excinfo.value.violations)
    assert bad in str(excinfo.value)


def test_assert_update_safe_reports_all_violations():
    """一次列全所有违规项，而不是报第一个就停。"""
    with pytest.raises(UpdateProtectedError) as excinfo:
        assert_update_safe(["config.json", "mea_memory.db-shm", "addons/x.py"])
    assert len(excinfo.value.violations) == 3


@pytest.mark.parametrize(
    "bad",
    ["../evil.py", "a/../../b", "/etc/passwd", "C:/Windows/system32/x.dll", ""],
)
def test_assert_update_safe_fail_closed_on_ambiguous(bad):
    """绝对路径、.. 逃逸、空路径：口径不明同样拒绝（fail-closed）。"""
    with pytest.raises(UpdateProtectedError):
        assert_update_safe([bad])
