"""VITS/GSV 外部环境的解释器与 site-packages 路径必须按平台拼。

出处：Athena 件 meapet-vits-posix-env-path-bug——向导里写死 ``Scripts/python.exe``
与 ``Lib/site-packages``，POSIX 上这两条路径不存在，于是 venv 建成了却被判"创建失败"。
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 只有 meapet/tts/common.py 允许拼平台形状；这几处出现字面量就说明 POSIX 分支漏了。
# 不含 meapet/tools/vits_infer.py：那两处 Lib/lib 是给 Windows embeddable 兜底用的，
# venv/conda 自带的 site-packages 由解释器自己配好，不需要这里补。
_CALLER_SOURCES = (
    "wizard/page_tts.py",
    "wizard/page_tts_vits.py",
    "wizard/page_tts_gsv.py",
    "meapet/tts/service.py",
)
_BANNED = ("Scripts", "site-packages")


def test_venv_and_prefix_python_return_the_native_interpreter_shapes(tmp_path):
    from meapet.tts.common import prefix_python, venv_python

    env_dir = tmp_path / "vits_env"
    (env_dir / "Scripts").mkdir(parents=True)
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (env_dir / "bin" / "python").write_text("", encoding="utf-8")

    assert venv_python(str(env_dir), windows=True) == str(env_dir / "Scripts" / "python.exe")
    assert venv_python(str(env_dir), windows=False) == str(env_dir / "bin" / "python")
    # conda env 与 GPT-SoVITS 整合包的 runtime/ 是扁平前缀：Windows 上 exe 就在目录本身
    assert prefix_python(str(env_dir), windows=True) == str(env_dir / "python.exe")
    assert prefix_python(str(env_dir), windows=False) == str(env_dir / "bin" / "python")
    # 默认按当前机器判定，且 venv 那条必须真的存在
    assert Path(venv_python(str(env_dir))).is_file()


def test_env_site_packages_keeps_the_python_version_segment(tmp_path):
    from meapet.tts.common import env_site_packages

    env_dir = tmp_path / "vits_env"
    (env_dir / "Lib" / "site-packages").mkdir(parents=True)
    (env_dir / "lib" / "python3.12" / "site-packages").mkdir(parents=True)

    assert env_site_packages(str(env_dir), windows=True) == str(
        env_dir / "Lib" / "site-packages"
    )
    # POSIX 的纯库目录带版本段：写死 lib/site-packages 在两平台都命中不了
    assert env_site_packages(str(env_dir), windows=False) == str(
        env_dir / "lib" / "python3.12" / "site-packages"
    )
    assert Path(env_site_packages(str(env_dir))).is_dir()


def test_env_site_packages_does_not_invent_a_missing_dir(tmp_path):
    from meapet.tts.common import env_site_packages

    empty = tmp_path / "broken_env"
    empty.mkdir()
    assert not Path(env_site_packages(str(empty), windows=False)).exists()


def test_gsv_python_candidates_cover_posix_layouts(tmp_path):
    """GSV 发现层不能只给 .exe：conda/整合包在 POSIX 上是 bin/python。"""
    from meapet.tts.service import gsv_python_candidates

    home = tmp_path / "home"
    runtime = home / "GPT-SoVITS" / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_text("", encoding="utf-8")

    candidates = gsv_python_candidates(
        home=home,
        environ={},
        executable=tmp_path / "python",
        frozen=True,
    )

    assert str(runtime / "bin" / "python") in candidates
    # 每台机器只出自己的形状：POSIX 上不必再列 .exe
    assert str(runtime / "python.exe") not in candidates


def test_windows_shapes_are_only_built_by_the_helper():
    """改一处就够：向导/服务里再出现 Windows 形状的字面量，POSIX 分支必然漏掉。"""
    offenders = []
    for rel in _CALLER_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(bad in line for bad in _BANNED):
                offenders.append(f"{rel}:{line_no}: {line.strip()}")

    assert not offenders, "写死的 venv 形状（应经 meapet.tts.common 的助手）:\n" + "\n".join(
        offenders
    )
