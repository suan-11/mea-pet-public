"""TTS 配置页 mixin"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from typing import Optional

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import *

from wizard.styles import (
    styled_message_box,
    styled_open_file,
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    set_status,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH
from wizard.env_utils import pip_install, check_installed

class TtsPageVitsMixin:
    def _browse_python(self, input_field):
        dir_path = styled_open_file(
            self, "选择 python.exe", "", "python.exe (python.exe)"
        )
        if dir_path:
            input_field.setText(dir_path)

    def _is_pet_exe(self, py_exe: str) -> bool:
        """判断是否为打包版 MeaPet.exe（非真正 Python 解释器）。"""
        try:
            from meapet.tts.common import is_pet_executable

            return is_pet_executable(py_exe)
        except Exception:
            if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
                return False
            try:
                return os.path.realpath(py_exe) == os.path.realpath(sys.executable)
            except Exception:
                return False

    @staticmethod
    def _path_is_pet_exe(py_exe: str) -> bool:
        """Static-safe pet-exe check (works when mixin methods are unbound)."""
        try:
            from meapet.tts.common import is_pet_executable

            return is_pet_executable(py_exe)
        except Exception:
            if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
                return False
            try:
                return os.path.realpath(py_exe) == os.path.realpath(sys.executable)
            except Exception:
                return False

    def _check_vits(self):
        """检查 VITS 模型是否就绪"""
        from meapet.paths import project_path

        model_path = project_path("vits_models", "G_latest.pth")
        config_path = project_path("vits_models", "finetune_speaker.json")
        if os.path.exists(model_path) and os.path.exists(config_path):
            model_size = os.path.getsize(model_path) / 1e6
            set_status(
                self.vits_status,
                "success",
                f"VITS 模型就绪（{model_size:.0f} MB；打包版默认进程内合成）",
            )
        else:
            set_status(
                self.vits_status,
                "error",
                "VITS 模型文件缺失（不会自动下载，请手动放置或点下方安装）",
            )

    def _setup_vits_env(self):
        """On explicit user click, detect / create the VITS Python environment.

        In frozen mode ``sys.executable`` is the pet exe, not a real Python
        interpreter — we skip subprocess calls that point to it and search
        for a real system Python instead.
        """
        ret = styled_message_box(
            self,
            title="按需安装确认",
            text=(
                "将检测/创建 VITS 环境，可能通过 pip 下载 PyTorch 等大包。\n"
                "默认不会自动下载，仅在你确认后进行。\n继续？"
            ),
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            self.log("已取消 VITS 环境安装")
            return
        import sys as _sys, subprocess, threading, os as _os
        base = os.path.dirname(CONFIG_PATH)
        log = lambda msg: QTimer.singleShot(0, lambda: self.log(msg)) if hasattr(self, 'log') else None

        # ── 构建干净的环境（去掉 PYTHONPATH 避免污染其他 Python 的子进程） ──
        _clean_env = _os.environ.copy()
        _clean_env.pop("PYTHONPATH", None)
        _clean_env.pop("PYTHONHOME", None)

        def _check_torch(py_exe: str) -> tuple[bool, str]:
            """检查指定 Python 能否 import torch，返回 (成功, 版本或错误信息)"""
            # 打包版中 sys.executable 是 MeaPet.exe，不能当 Python 用
            if TtsPageVitsMixin._path_is_pet_exe(py_exe):
                return False, "frozen"
            try:
                r = subprocess.run(
                    [py_exe, "-c", "import torch; print(torch.__version__)"],
                    capture_output=True, text=True, timeout=15,
                    env=_clean_env  # 关键修复：用干净环境防止 PYTHONPATH 污染
                )
                if r.returncode == 0 and r.stdout.strip():
                    return True, r.stdout.strip()
                return False, r.stderr[:100]
            except Exception as e:
                return False, str(e)

        def _pip_install_deps(py_exe: str, desc_prefix: str = "") -> bool:
            """给指定 Python 装 VITS 依赖（不含 torch，装完再装 torch）"""
            ok = True
            # VITS requirements（不含 torch/torchaudio）
            req_path = _os.path.join(base, "vits_requirements.txt")
            with open(req_path, "r", encoding="utf-8") as f:
                raw_lines = []
                for _l in f:
                    _stripped = _l.strip()
                    if not _stripped or _stripped.startswith("#"):
                        continue
                    if _stripped.lower().startswith("torch"):
                        continue
                    raw_lines.append(_stripped)
                req_lines = raw_lines
            if req_lines:
                log(f"{desc_prefix}安装 VITS 依赖包（{len(req_lines)} 个）…")
                rc = _pip_run(py_exe, req_lines + ["-i",
                    "https://pypi.tuna.tsinghua.edu.cn/simple"], timeout_sec=600)
                if rc != 0:
                    log(f"{desc_prefix}⚠ pip 部分失败，继续…")
                    ok = False
            # PyTorch
            wheels_dir = _os.path.join(base, "wheels")
            torch_whl = None
            torchaudio_whl = None
            if _os.path.isdir(wheels_dir):
                for f in _os.listdir(wheels_dir):
                    if f.endswith(".whl"):
                        if "torch-" in f and "torchaudio" not in f:
                            torch_whl = _os.path.join(wheels_dir, f)
                        elif "torchaudio" in f:
                            torchaudio_whl = _os.path.join(wheels_dir, f)
            if torch_whl and torchaudio_whl:
                log(f"{desc_prefix}本地 .whl 安装 PyTorch…")
                _pip_run(py_exe, [torch_whl, torchaudio_whl], timeout_sec=300)
            else:
                tsinghua_torch = "https://mirrors.tuna.tsinghua.edu.cn/pytorch/whl/cpu"
                log(f"{desc_prefix}清华镜像下载 PyTorch（约 200MB）…")
                _pip_run(py_exe, ["torch", "torchaudio",
                         "--index-url", tsinghua_torch,
                         "--extra-index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                        timeout_sec=900)
            return ok

        def _pip_run(py_exe: str, args: list, timeout_sec: int = 600) -> int:
            """通用 pip install（实时输出+超时保护，干净环境）

            冻结模式且 py_exe 是 pet exe 时直接返回失败，避免启动重复进程。
            """
            if TtsPageVitsMixin._path_is_pet_exe(py_exe):
                return 1
            _pip_env = _clean_env.copy()
            _pip_env["PYTHONUNBUFFERED"] = "1"  # 关掉子进程缓冲，每行实时可见
            proc = subprocess.Popen(
                [py_exe, "-m", "pip", "install", "--timeout", "120"] + args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, encoding="utf-8", errors="replace",
                env=_pip_env
            )

            # ── 后台 reader 线程：逐行读取，实时输出到日志 ──
            _last_pct = -1  # 限频：进度百分比只输出变化时

            def _reader():
                nonlocal _last_pct
                for _raw in proc.stdout:
                    _line = _raw.strip()
                    if not _line:
                        continue
                    # 下载进度条限频（每变化 >=2% 才输出，省得刷屏）
                    _m = __import__('re').search(r'(\d+)%', _line)
                    if _m:
                        _pct = int(_m.group(1))
                        if _pct - _last_pct >= 2:
                            _last_pct = _pct
                            QTimer.singleShot(0, lambda l=_line: log(f"    {l}"))
                        continue
                    # 非进度行直接输出
                    QTimer.singleShot(0, lambda l=_line: log(f"    {l}"))

            _reader_thread = threading.Thread(target=_reader, daemon=True)
            _reader_thread.start()

            # ── 主线程：超时控制 ──
            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                QTimer.singleShot(0, lambda: log("  ❌ 超时（>{}s），pip 安装中断".format(timeout_sec)))
                return 1

            _reader_thread.join(timeout=5)  # 等 reader 读完残存输出
            return proc.returncode

        # ═══════════════════════════════════════════════════
        # 按速度排序，逐级尝试可用的 Python 环境
        # ═══════════════════════════════════════════════════

        # 0️⃣ 当前 Python（Hermes venv，最快）
        ver_ok, ver_info = _check_torch(_sys.executable)
        if ver_ok:
            self.vits_python_input.setText(_sys.executable)
            set_status(
                self.vits_status,
                "success",
                f"使用当前 Python（torch {ver_info}）",
            )
            log(f"✓ 当前 Python 已有 torch {ver_info}")
            self._ensure_vits_deps(_sys.executable, log)
            return

        # 0️⃣.5️⃣ 项目自带的 _python（embedable，已存在则省去 venv 创建）
        _embedded = _os.path.join(base, "_python", "python.exe")
        if _os.path.isfile(_embedded):
            ver_ok, ver_info = _check_torch(_embedded)
            if ver_ok:
                self.vits_python_input.setText(_embedded)
                set_status(
                    self.vits_status,
                    "success",
                    f"使用 _python（torch {ver_info}）",
                )
                log(f"✓ 项目 _python 已有 torch {ver_info}")
                self._ensure_vits_deps(_embedded, log)
                return
            # _python 存在但没有 torch → 直接在上面装（秒杀创建 venv）
            log("📦 项目 _python 已存在，直接安装 PyTorch（省去 venv 创建）…")
            self.setup_vits_btn.setEnabled(False)
            self.setup_vits_btn.setText("正在安装 PyTorch 到 _python…")
            def _task_embedded():
                _pip_install_deps(_embedded, "[_python] ")
                QTimer.singleShot(0, lambda: self._on_vits_env_done(True, _embedded))
            threading.Thread(target=_task_embedded, daemon=True).start()
            return

        # 1️⃣ vits_ft conda 环境
        candidates = [
            _os.path.join(_os.path.expanduser("~"), ".conda", "envs", "vits_ft", "python.exe"),
            _os.path.join(_os.path.expanduser("~"), "miniconda3", "envs", "vits_ft", "python.exe"),
            _os.path.join(_os.path.expanduser("~"), "anaconda3", "envs", "vits_ft", "python.exe"),
        ]
        found = None
        for c in candidates:
            if _os.path.isfile(c):
                ok, _ = _check_torch(c)
                if ok:
                    found = c
                    break
        if found:
            self.vits_python_input.setText(found)
            set_status(
                self.vits_status,
                "success",
                "找到 VITS 环境: "
                f"{_os.path.basename(_os.path.dirname(_os.path.dirname(found)))}",
            )
            log(f"✓ 使用 {found}")
            self._ensure_vits_deps(found, log)
            return

        # 2️⃣ 已有 vits_env venv
        venv_path = _os.path.join(base, "vits_env")
        if _os.path.isdir(venv_path):
            py_path = _os.path.join(venv_path, "Scripts", "python.exe")
            if _os.path.isfile(py_path):
                ok, _ = _check_torch(py_path)
                if ok:
                    self.vits_python_input.setText(py_path)
                    set_status(self.vits_status, "success", "使用已有 vits_env")
                    self._ensure_vits_deps(py_path, log)
                    return

        # 3️⃣ 需要创建新环境（后台线程）
        self.setup_vits_btn.setEnabled(False)
        self.setup_vits_btn.setText("正在配置 VITS 环境…")
        log("开始创建 VITS Python 环境…")

        def task():
            try:
                # 创建 venv（冻结模式用 PATH 上的系统 Python 代替 pet exe）
                _master_py = _sys.executable
                if TtsPageVitsMixin._path_is_pet_exe(_master_py):
                    import shutil as _shutil
                    _master_py = (
                        _shutil.which("python")
                        or _shutil.which("python3")
                        or _master_py
                    )
                subprocess.run([_master_py, "-m", "venv", venv_path],
                             capture_output=True, timeout=60)
                py_path = _os.path.join(venv_path, "Scripts", "python.exe")
                if not _os.path.isfile(py_path):
                    raise Exception("venv 创建失败")

                _pip_install_deps(py_path, "[venv] ")

                # 复制 pyopenjtalk 词典
                import shutil
                src_dict = _os.path.join(_os.path.expanduser("~"), ".conda", "envs", "vits_ft",
                                        "lib", "site-packages", "pyopenjtalk")
                if _os.path.isdir(src_dict):
                    dst_pkg = _os.path.join(venv_path, "Lib", "site-packages")
                    if _os.path.isdir(dst_pkg):
                        shutil.copytree(src_dict, _os.path.join(dst_pkg, "pyopenjtalk"),
                                       dirs_exist_ok=True)
                        log("已复制 pyopenjtalk 词典")

                QTimer.singleShot(0, lambda: self._on_vits_env_done(True, py_path))
            except Exception as e:
                error = str(e)
                QTimer.singleShot(
                    0,
                    lambda error=error: self._on_vits_env_done(False, error),
                )

        threading.Thread(target=task, daemon=True).start()

    def _ensure_vits_deps(self, py_exe: str, log):
        """确保 VITS 所需的基础依赖已安装（soundfile, numpy, scipy 等），非阻塞

        打包版中 pet exe 不是真正 Python，跳过子进程检查。
        """
        if TtsPageVitsMixin._path_is_pet_exe(py_exe):
            log("  ⚠ 打包版中无法检查 VITS 依赖（pet exe 不是 Python 解释器）")
            return
        import subprocess, threading
        needed = []
        _checks = {
            "soundfile": "import soundfile; print('ok')",
            "scipy": "import scipy; print('ok')",
            "librosa": "import librosa; print('ok')",
        }
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for mod, test_code in _checks.items():
            try:
                r = subprocess.run([py_exe, "-c", test_code],
                                   capture_output=True, text=True, timeout=10, env=env)
                if r.returncode != 0:
                    needed.append(mod)
            except Exception:
                needed.append(mod)

        if not needed:
            log("✓ VITS 基础依赖已就绪")
            return

        # 后台异步安装（不阻塞主线程、不阻塞向导流程）
        log(f"  ⚠ 缺少 {len(needed)} 个 VITS 依赖: {', '.join(needed)}，后台安装中…")
        def _task():
            try:
                r = subprocess.run(
                    [py_exe, "-m", "pip", "install", "--timeout", "120",
                     "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + needed,
                    capture_output=True, text=True, timeout=300, env=env
                )
                if r.returncode == 0:
                    QTimer.singleShot(0, lambda: log("✓ VITS 依赖安装完成"))
                else:
                    QTimer.singleShot(0, lambda: log(f"  ⚠ pip 安装失败: {r.stderr[-150:]}"))
            except subprocess.TimeoutExpired:
                QTimer.singleShot(0, lambda: log("  ⚠ pip 安装超时，VITS 可能无法正常工作"))
        threading.Thread(target=_task, daemon=True).start()

    def _on_vits_env_done(self, ok, result):
        self.setup_vits_btn.setEnabled(True)
        if ok:
            self.vits_python_input.setText(result)
            set_status(self.vits_status, "success", "VITS 环境已就绪")
            self.setup_vits_btn.setText("VITS 环境已配置")
        else:
            set_status(self.vits_status, "error", f"配置失败: {result[:50]}")
            self.setup_vits_btn.setText("自动配置 VITS 环境（重试）")



# ═══════════════════════════════════════
# 页面：识图 / 屏幕观察
# ═══════════════════════════════════════
