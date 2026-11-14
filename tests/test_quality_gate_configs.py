"""P0-5 质量门配置的形态测试：CI 矩阵 / coverage 接线 / pre-commit hooks。

本文件能证明什么
----------------
* 两份 YAML 语法可解析，且关键验收形状在场：
  CI Linux 矩阵含 3.11/3.12/3.13；pytest 带 --cov 与 term-missing 报告；
  安装步骤含 pytest-cov；pre-commit 有 ruff / 大文件 / 尾随空格 / LFS local hook。
* ruff hook 的 rev 与本机安装的 ruff 版本对齐（本机没装 ruff 则跳过该条）。

本文件**不能**证明什么
----------------------
* 不证 CI 在 GitHub 上真跑绿（那是远端行为）；
* 不证 pre-commit 在本机真的执行拦截（LFS 脚本的拦截能力由
  tests/test_precommit_lfs_check.py 用临时仓库实证）。
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

# 裸环境没装 PyYAML 时跳过而非炸收集（CI 已显式安装，门在 CI 真跑）。
yaml = pytest.importorskip("yaml", reason="PyYAML 未安装，质量门 YAML 形态测试跳过")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "python-app.yml"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_ci_linux_matrix_covers_declared_pythons():
    """Linux 矩阵至少含 3.11/3.12/3.13（对齐 pyproject classifiers 声明）。"""
    wf = _load(WORKFLOW)
    versions = wf["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    assert {"3.11", "3.12", "3.13"} <= set(versions)


def test_ci_pytest_wired_with_coverage():
    """pytest 步骤带 --cov 与 term-missing；安装步骤带 pytest-cov；无百分比红线。"""
    wf = _load(WORKFLOW)
    steps = wf["jobs"]["test"]["steps"]
    test_step = next(s for s in steps if "pytest" in str(s.get("name", "")))
    # 只看命令行本身：注释里提到 flag 名字不算接线，也不算红线。
    cmd_lines = [ln for ln in test_step["run"].splitlines() if not ln.strip().startswith("#")]
    cmd = "\n".join(cmd_lines)
    assert "--cov" in cmd
    assert "--cov-report=term-missing" in cmd
    assert "--cov-fail-under" not in cmd
    install_step = next(s for s in steps if "Install Python dependencies" in str(s.get("name", "")))
    assert "pytest-cov" in install_step["run"]


def test_precommit_hooks_shape():
    """ruff / 大文件(2048KB) / 尾随空格 / LFS local hook 都在场；禁大面积改存量。"""
    cfg = _load(PRECOMMIT)
    hooks = {h["id"]: h for repo in cfg["repos"] for h in repo["hooks"]}
    assert "ruff" in hooks
    assert "check-added-large-files" in hooks
    assert "--maxkb=2048" in hooks["check-added-large-files"]["args"]
    assert "trailing-whitespace" in hooks
    lfs = [h for h in hooks.values() if "precommit_lfs_check.py" in h.get("entry", "")]
    assert len(lfs) == 1
    # 纪律：不加会大面积改写存量文件的 hook（如 end-of-file-fixer）。
    assert "end-of-file-fixer" not in hooks


def test_precommit_ruff_rev_matches_local_ruff():
    """ruff hook 的 rev 与本机 ruff 版本对齐；没装 ruff 就明说判不了。"""
    try:
        local = importlib.metadata.version("ruff")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("本机未安装 ruff，rev 对齐判不了")
    cfg = _load(PRECOMMIT)
    ruff_repo = next(r for r in cfg["repos"] if "ruff-pre-commit" in r["repo"])
    assert ruff_repo["rev"] == f"v{local}"
