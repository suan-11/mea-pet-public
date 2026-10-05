"""VITS「就绪」这句结论只能来自交付脚本自己的 import 面。

出处：Athena 件 meapet-vits-posix-env-path-bug 的 Linux 端到端复跑。空
``vits_env``（有解释器、无包）从前 ``health_check()`` 返回 True——那条分支
把 ``python=True`` 硬写在 checks 里，合取项里也没有它——2.2 s 后合成撞
``ModuleNotFoundError``，用户看到的只是"回退预制语音"。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFER_SCRIPT = ROOT / "meapet" / "tools" / "vits_infer.py"


def _write_stub(tmp_path: Path, body: str) -> str:
    path = tmp_path / "stub_infer.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_probe_says_ok_only_on_the_scripts_own_sentinel(tmp_path):
    from meapet.tts.common import probe_vits_deps

    script = _write_stub(tmp_path, 'print("OK:deps_loaded")\n')
    verdict, detail = probe_vits_deps(sys.executable, script)
    assert verdict == "ok"
    assert "importable" in detail


def test_probe_maps_missing_module_to_missing_with_the_name(tmp_path):
    from meapet.tts.common import probe_vits_deps

    script = _write_stub(tmp_path, "import unidecode  # noqa: F401\n")
    verdict, detail = probe_vits_deps(sys.executable, script)
    assert verdict == "missing"
    # detail 直接进日志/向导文案：得说清缺哪个，不能只回一个布尔
    assert "unidecode" in detail


def test_probe_reports_unknown_for_problems_that_are_not_the_users(tmp_path, monkeypatch):
    """探测自己坏了（超时/脚本不在/没解释器）不许判成"你的环境缺包"。"""
    from meapet.tts import common

    script = _write_stub(tmp_path, "import time; time.sleep(30)\n")
    monkeypatch.setattr(common, "VITS_DEPS_PROBE_TIMEOUT", 1)
    assert common.probe_vits_deps(sys.executable, script)[0] == "unknown"

    # rc 非 0 但不是 ImportError：无法据此判依赖，只能 unknown
    crashy = _write_stub(tmp_path, "import sys; sys.exit(3)\n")
    assert common.probe_vits_deps(sys.executable, crashy)[0] == "unknown"

    assert common.probe_vits_deps("", script)[0] == "unknown"
    assert common.probe_vits_deps(sys.executable, str(tmp_path / "nope.py"))[0] == "unknown"


def test_infer_script_accepts_the_check_deps_flag(tmp_path):
    """--check-deps 必须真被 argparse 接受（rc=2 就是探针与脚本契约断了）。

    用一个只会 ImportError 的假 torch 把 import 掐在第一行：无论本机装没装
    torch，这条测试都是秒级，也不会真的去加载权重。
    """
    fake = tmp_path / "fakepkgs"
    fake.mkdir()
    (fake / "torch.py").write_text("raise ImportError('stub: no torch here')\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, str(INFER_SCRIPT), "--check-deps",
         "--text", "probe", "--output", os.devnull],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode != 2, proc.stderr
    assert "unrecognized arguments" not in proc.stderr
    assert "ImportError" in proc.stderr


def test_transient_status_text_has_a_settler():
    """向导写"检测依赖中"这类过渡文案时，必须把状态条交给能结掉它的那条路。

    实测过的坏形状：`_on_vits_env_done` 落了 warning "检测依赖中…"，而探针只写日志
    不碰状态条 ⇒ 状态条永远停在那句过渡文案上（Qt 事件循环里没人会替它改口）。
    """
    src = (ROOT / "wizard" / "page_tts_vits.py").read_text(encoding="utf-8")

    assert "检测依赖中" in src
    assert "status_widget=self.vits_status" in src, (
        "过渡文案没有交割对象：_ensure_vits_deps 需要 status_widget 才会落定结论"
    )


def test_probe_and_script_share_one_sentinel_contract():
    """两边各改一半就静默失效：flag 名与哨兵串必须在两处源码里同时出现。"""
    script_src = INFER_SCRIPT.read_text(encoding="utf-8")
    probe_src = (ROOT / "meapet" / "tts" / "common.py").read_text(encoding="utf-8")

    assert '"--check-deps"' in probe_src
    assert "--check-deps" in script_src
    assert "OK:deps_loaded" in script_src
    assert "OK:deps_loaded" in probe_src


def _vits_health_env(tmp_path, monkeypatch):
    """铺一套磁盘事实全齐的 VITS 现场，返回日志行列表（调用方收集读数）。

    磁盘侧准备与健康检查那两条测试同形，共用一份，免得一边改了另一边还绿。
    """
    from meapet.tts import service

    def fake_project_path(*parts):
        return str(tmp_path.joinpath(*parts))

    monkeypatch.setattr(service, "project_path", fake_project_path)
    (tmp_path / "vits_models").mkdir()
    # 真身判据是"不是 LFS 指针"，这里给一段非指针字节即可
    (tmp_path / "vits_models" / "G_latest.pth").write_bytes(b"\x80\x02not-a-pointer")
    (tmp_path / "vits_models" / "finetune_speaker.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vits_core").mkdir()
    (tmp_path / "meapet" / "tools").mkdir(parents=True)
    (tmp_path / "meapet" / "tools" / "vits_infer.py").write_text("", encoding="utf-8")

    lines: list[str] = []
    collector = type("Log", (), {
        "info": staticmethod(lambda msg, *a: lines.append(str(msg))),
        "warning": staticmethod(lambda msg, *a: lines.append(str(msg))),
        "error": staticmethod(lambda msg, *a: lines.append(str(msg))),
        "debug": staticmethod(lambda msg, *a: lines.append(str(msg))),
    })
    monkeypatch.setattr(service, "log", collector)
    return lines


def test_health_check_vits_stops_claiming_an_unmeasured_python(tmp_path, monkeypatch):
    """子进程分支的日志只报量过的东西，解释器以文件名出现而不是布尔断言。"""
    from meapet.tts import service

    lines = _vits_health_env(tmp_path, monkeypatch)

    tts = service.MeaTTS({
        "tts": {
            "enabled": True,
            "engine": "vits",
            "vits_python": sys.executable,
            # 旋钮走显式配置，不靠 patch service.project_path：模型/配置的默认
            # 取值现在收在 common.vits_model_path() 里，函数内 import 的是
            # meapet.paths，patch service 那一层已经够不着它了（会验到仓库里
            # 那份未水化的 LFS 指针）。
            "vits_model": str(tmp_path / "vits_models" / "G_latest.pth"),
            "vits_config": str(tmp_path / "vits_models" / "finetune_speaker.json"),
        }
    })
    assert tts.health_check() is True
    joined = "\n".join(lines)
    assert "mode=subprocess" in joined
    assert "python=True" not in joined
    assert os.path.basename(sys.executable) in joined


def _inprocess_tts(tmp_path, **extra):
    """显式 ``vits_inprocess: true``、外部解释器留空的 MeaTTS。

    这一格是老代码唯一没验的：进程内那条路的"解释器"就是本进程，而
    health_check 只查 core/model/config 三个磁盘事实，于是宿主 venv 里没有
    torch 也照样报绿，speak() 才撞 ModuleNotFoundError。
    """
    from meapet.tts import service

    cfg = {
        "enabled": True,
        "engine": "vits",
        "vits_inprocess": True,
        "vits_model": str(tmp_path / "vits_models" / "G_latest.pth"),
        "vits_config": str(tmp_path / "vits_models" / "finetune_speaker.json"),
    }
    cfg.update(extra)
    return service.MeaTTS({"tts": cfg})


def test_inprocess_health_check_is_red_when_this_process_cannot_find_torch(
    tmp_path, monkeypatch
):
    from meapet.tts import service

    lines = _vits_health_env(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "module_present", lambda name: False)

    assert _inprocess_tts(tmp_path).health_check() is False
    joined = "\n".join(lines)
    assert "mode=inprocess" in joined
    assert "torch=False" in joined
    assert "寻不到 torch" in joined


def test_inprocess_health_check_does_not_newly_block_when_torch_is_present(
    tmp_path, monkeypatch
):
    """新判据只往"缺失"方向拦：torch 在，磁盘事实齐就不该因它变红。"""
    from meapet.tts import service

    lines = _vits_health_env(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "module_present", lambda name: True)

    assert _inprocess_tts(tmp_path).health_check() is True
    assert "torch=True" in "\n".join(lines)


def test_module_present_addresses_a_module_without_running_it(tmp_path, monkeypatch):
    """``module_present`` 测的是"寻不寻得到"，不是"跑不跑得起来"。

    这条边界是它敢落在 speak() 路径上的全部理由：find_spec 实测 0.1–0.4 ms，
    真 import torch 3914 ms。第二半断言钉的是"它确实没执行"——否则这个便宜
    判据会偷偷背上加载成本。
    """
    import importlib

    from meapet.tts.common import module_present

    pkg = tmp_path / "addressed_not_executed"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "raise ImportError('executed: __init__ ran')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "addressed_not_executed", raising=False)

    assert module_present("addressed_not_executed") is True
    with pytest.raises(ImportError):
        importlib.import_module("addressed_not_executed")

    assert module_present("definitely_not_installed_here_xyz") is False


def test_speak_path_is_not_taxed_by_the_full_stack_probe():
    """全栈 import 探针 ≈20 s，不许出现在 speak() 的 _ensure_deps 路径上。

    只数调用行、不数散文——上一轮我就把注释里的这个名字扫成了"仍在调用"。
    """

    def call_lines(rel: str) -> list[str]:
        out = []
        for no, line in enumerate(
            (ROOT / rel).read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if "probe_vits_deps(" in stripped and not stripped.startswith("#"):
                out.append(f"{rel}:{no}: {stripped}")
        return out

    assert call_lines("meapet/tts/service.py") == []
    assert call_lines("wizard/page_tts_vits.py"), "就绪判据得有人真的去问"
