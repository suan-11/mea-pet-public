"""LFS 指针闸门：pre-commit 脚本必须拦住未水化的指针文件。

本文件能证明什么
----------------
* 头行口径正确：只有头字节以 LFS spec 头行开头的文件才判为指针
  （与 scripts/build_windows.ps1 的 Test-LfsPointer 同规则）。
* 端到端（临时 git 仓库，全程无网络）：暂存指针文件 => 脚本退出码 1 且点名；
  水化（换真内容重新暂存）=> 退出码 0。
* index 语义：暂存了指针、工作区后来改成真内容但没重新 add，照样拦——
  闸门读的是「将要提交的内容」，不是工作区现状。

本文件**不能**证明什么
----------------------
* 不证 pre-commit 真的会调用这个 hook（需要 pre-commit install，本机未装）；
* 不证 LFS 对象能从远端拉下来（测试不使用任何网络）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.precommit_lfs_check import (  # noqa: E402
    LFS_SPEC_HEADER,
    looks_like_lfs_pointer,
)

SCRIPT = ROOT / "scripts" / "precommit_lfs_check.py"
POINTER = LFS_SPEC_HEADER + b"\noid sha256:" + b"0" * 64 + b"\nsize 12345\n"
REAL_BYTES = bytes(range(256)) * 4  # 含水化后应有的二进制内容


def test_header_rule():
    """头行必须在文件开头；正文中间出现头行不算指针。"""
    assert looks_like_lfs_pointer(POINTER) is True
    assert looks_like_lfs_pointer(REAL_BYTES) is False
    assert looks_like_lfs_pointer(b"") is False
    assert looks_like_lfs_pointer(b"# comment\n" + POINTER) is False


@pytest.fixture()
def git_repo(tmp_path):
    """临时本地 git 仓库；没有 git 就明说判不了，不假装通过。"""
    if shutil.which("git") is None:
        pytest.skip("git 不可用，无法构造临时仓库")
    subprocess.run(
        ["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


def _run_script(cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
        check=False,
    )


def _stage(repo: Path, name: str, data: bytes) -> None:
    (repo / name).write_bytes(data)
    subprocess.run(
        ["git", "add", name], cwd=repo, check=True, capture_output=True
    )


def test_staged_pointer_rejected(git_repo):
    """构造的指针文件一旦暂存，脚本退出码 1 且点名该文件。"""
    _stage(git_repo, "model.pth", POINTER)
    proc = _run_script(git_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "model.pth" in proc.stderr


def test_hydrated_file_passes(git_repo):
    """真内容（非指针）暂存后放行。"""
    _stage(git_repo, "model.pth", REAL_BYTES)
    proc = _run_script(git_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_staged_pointer_with_clean_worktree_still_rejected(git_repo):
    """暂存的是指针、工作区已换真内容但没重新 add：仍按 index 内容拦。"""
    _stage(git_repo, "model.pth", POINTER)
    (git_repo / "model.pth").write_bytes(REAL_BYTES)  # 不重新 git add
    proc = _run_script(git_repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_no_staged_files_passes(git_repo):
    """空暂存区放行（0 个文件）。"""
    proc = _run_script(git_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_untracked_pointer_not_checked(git_repo):
    """闸门只管暂存内容：未暂存的指针文件不在本闸门职责内。"""
    (git_repo / "model.pth").write_bytes(POINTER)
    proc = _run_script(git_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
