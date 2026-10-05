"""GSV 根的发现必须按标记认，不按目录名认。

出处：Athena 件 gsv-linux-runtime-support。旧判据要求「解释器的父目录名字面等于
runtime」，Windows 整合包恰好命中，而 POSIX 的 ``runtime/bin/python`` 与 conda 的
``envs/<n>/bin/python`` 中间都多一层 ``bin`` ⇒ 根=None ⇒ 子进程立刻回
``GSV root not found``，Linux 上无论怎么装都进不去。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INFER_SCRIPT = ROOT / "meapet" / "tools" / "gsv_infer.py"

_STUB_TTS = '''
import os


class TTS_Config:
    def __init__(self, path):
        print(
            "STUB-REACHED "
            f"cwd={os.getcwd()} config={path} "
            f"abs={os.path.abspath(path)} exists={os.path.exists(path)}"
        )
        raise SystemExit(0)


class TTS:
    def __init__(self, cfg):
        pass
'''


def _make_tree(base: Path) -> Path:
    """造一份带标记的 GPT-SoVITS 树，返回根目录。"""
    pack = base / "GPT_SoVITS" / "TTS_infer_pack"
    pack.mkdir(parents=True, exist_ok=True)
    (base / "GPT_SoVITS" / "__init__.py").write_text("", encoding="utf-8")
    (pack / "__init__.py").write_text("", encoding="utf-8")
    (pack / "TTS.py").write_text(_STUB_TTS, encoding="utf-8")
    configs = base / "GPT_SoVITS" / "configs"
    configs.mkdir(exist_ok=True)
    (configs / "tts_infer.yaml").write_text("custom:\n  device: cpu\n", encoding="utf-8")
    return base


def _interpreter_at(target: Path) -> Path:
    """在指定位置放一个能跑的真解释器（POSIX 用符号链接，Windows 用副本）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shutil.copy2(sys.executable, target)
    else:
        target.symlink_to(Path(sys.executable).resolve())
    return target


def _payload(root: Path, gsv_root: str = "") -> str:
    payload = {
        "output_wav": str(root / "out.wav"),
        "ref_wav": str(root / "ref.wav"),
        "prompt_text": "x",
        "prompt_language": "中文",
        "text": "测试",
        "text_language": "中文",
        "gpt_path": str(root / "gpt.ckpt"),
        "sovits_path": str(root / "sovits.pth"),
        "top_k": 15,
        "top_p": 0.8,
        "temperature": 0.6,
        "speed": 1.0,
        "sample_steps": 8,
    }
    if gsv_root:
        payload["gsv_root"] = gsv_root
    return json.dumps(payload, ensure_ascii=False)


def _run(script: Path, interpreter: Path, payload: str) -> str:
    proc = subprocess.run(
        [str(interpreter), str(script)],
        input=payload.encode("utf-8"),
        capture_output=True,
        timeout=60,
        cwd=str(ROOT),
    )
    return proc.stdout.decode("utf-8", errors="replace")


def _copied_script(tmp_path: Path) -> Path:
    # 脚本按 __file__ 上溯三级找 audio_cache 落点，复制一层保持测试不出仓库写文件
    dest = tmp_path / "meapet" / "tools" / "gsv_infer.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(INFER_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_find_gsv_root_accepts_every_native_layout(tmp_path):
    from meapet.tools.gsv_infer import find_gsv_root

    tree = _make_tree(tmp_path / "GPT-SoVITS")

    win = _interpreter_at(tree / "runtime" / "python.exe")
    posix_venv = _interpreter_at(tree / "runtime" / "bin" / "python")
    assert find_gsv_root("", str(win)) == str(tree)
    assert find_gsv_root("", str(posix_venv)) == str(tree)

    # conda env 在树外：解释器上溯找不到，只能由显式根给
    outside = _interpreter_at(tmp_path / "miniconda3" / "envs" / "GPTSoVITS" / "bin" / "python")
    assert find_gsv_root("", str(outside)) == ""
    assert find_gsv_root(str(tree), str(outside)) == str(tree)


def test_find_gsv_root_rejects_a_tree_without_the_marker(tmp_path):
    from meapet.tools.gsv_infer import find_gsv_root

    bare = tmp_path / "not_gsv" / "runtime" / "bin"
    py = _interpreter_at(bare / "python")
    assert find_gsv_root("", str(py)) == ""
    # 显式给了一个没有标记的目录也不能认
    assert find_gsv_root(str(tmp_path / "not_gsv"), str(py)) == ""


def test_gsv_infer_runs_against_a_posix_runtime(tmp_path):
    script = _copied_script(tmp_path)
    tree = _make_tree(tmp_path / "pkg")
    py = _interpreter_at(tree / "runtime" / "bin" / "python")

    out = _run(script, py, _payload(tree))

    assert "GSV root not found" not in out
    assert "STUB-REACHED" in out
    # chdir 到根之后，默认的相对 config 路径才解得开
    assert f"cwd={tree}" in out and "exists=True" in out


def test_gsv_infer_runs_with_a_conda_interpreter_outside_the_tree(tmp_path):
    script = _copied_script(tmp_path)
    tree = _make_tree(tmp_path / "GPT-SoVITS-src")
    py = _interpreter_at(tmp_path / "miniconda3" / "envs" / "GPTSoVITS" / "bin" / "python")

    out = _run(script, py, _payload(tree, gsv_root=str(tree)))

    assert "GSV root not found" not in out
    assert "STUB-REACHED" in out
    assert f"cwd={tree}" in out


def test_gsv_infer_still_reports_when_nothing_is_found(tmp_path):
    script = _copied_script(tmp_path)
    py = _interpreter_at(tmp_path / "env" / "bin" / "python")

    out = _run(script, py, _payload(tmp_path))

    assert "GSV root not found" in out
