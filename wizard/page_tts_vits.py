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

from meapet.dependencies import (
    resolve_pip_index_url,
    resolve_torch_index_url,
)
from meapet.tts.common import env_site_packages, prefix_python, venv_python
from wizard.styles import (
    styled_message_box,
    styled_open_file,
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    set_status,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH
from wizard.env_utils import pip_install, check_installed


class _MainThreadInvoker(QObject):
    """经队列信号把回调投递回主线程执行。

    普通 threading.Thread 里调用 QTimer.singleShot 不会触发——工作线程
    没有 Qt 事件循环，定时器根本不启动，安装完成回调因此永远丢失。
    """

    trigger = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        # 发射方在工作线程、本对象归属主线程 → Qt 自动走 QueuedConnection
        self.trigger.connect(self._run)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except Exception:
            pass


_invoker: Optional[_MainThreadInvoker] = None


def _ensure_main_invoker() -> None:
    """必须在主线程先调用一次，确保投递器的线程亲和是主线程。"""
    global _invoker
    if _invoker is None:
        _invoker = _MainThreadInvoker()


def _post_to_main(fn) -> None:
    """线程安全地把回调排到主线程。

    - 已在主线程：直接 QTimer.singleShot(0, …)（兼容现有测试对 singleShot 的 mock）。
    - 在工作线程：经 _MainThreadInvoker 的 QueuedConnection 投递，
      因为工作线程没有 Qt 事件循环，单靠 singleShot 永远不会触发。
    """
    app = QApplication.instance()
    if app is not None and QThread.currentThread() is app.thread():
        QTimer.singleShot(0, fn)
        return
    if _invoker is not None:
        _invoker.trigger.emit(fn)
        return
    # 无 QApplication / 投递器未初始化：同步执行（单元测试的 ImmediateThread 路径）
    try:
        fn()
    except Exception:
        pass


class TtsPageVitsMixin:
    def _browse_python(self, input_field):
        wanted = "python.exe" if os.name == "nt" else "python*"
        dir_path = styled_open_file(
            self, f"选择 {wanted}", "", f"{wanted} ({wanted})"
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
        """检查 VITS 模型是否就绪，并预告实际会走哪条路"""
        from meapet.paths import project_path
        from meapet.tts.common import (
            DEFAULT_VITS_CONFIG_NAME,
            DEFAULT_VITS_MODEL_NAME,
            is_git_lfs_pointer,
        )

        model_path = project_path("vits_models", DEFAULT_VITS_MODEL_NAME)
        config_path = project_path("vits_models", DEFAULT_VITS_CONFIG_NAME)
        # exists() 对 LFS 指针为真——从前它会显示"模型就绪（0 MB）"，
        # 而 speak() 那边同一份文件被判不可用（service.py 的 pointer 分支）。
        if is_git_lfs_pointer(model_path):
            set_status(
                self.vits_status,
                "error",
                "VITS 权重仍是 Git LFS pointer（不会自动拉取）"
                "——运行 git lfs pull，或手动放置真身",
            )
            return
        if os.path.isfile(model_path) and os.path.isfile(config_path):
            model_size = os.path.getsize(model_path) / 1e6
            route = self._vits_route_summary()
            suffix = f"；{route}" if route else ""
            set_status(
                self.vits_status,
                "success",
                f"VITS 模型就绪（{model_size:.0f} MB）{suffix}",
            )
        else:
            set_status(
                self.vits_status,
                "error",
                "VITS 模型文件缺失（不会自动下载，请手动放置或点下方安装）",
            )

    def _vits_route_summary(self) -> str:
        """据当前输入框内容预告实际走哪条路 —— 与交付码同一判据。

        打包版在没有外部解释器时才默认进程内；填了解释器就一定走子进程，
        哪怕手改 config.json 写了 vits_inprocess=true（那条优先级是有意的，
        交付码会为此落一行 warning）。
        """
        try:
            from meapet.tts.common import (
                _is_frozen,
                module_present,
                resolve_external_python,
                resolve_vits_route,
            )

            raw = ""
            if hasattr(self, "vits_python_input"):
                raw = self.vits_python_input.text().strip()
            route = resolve_vits_route(
                external_python=resolve_external_python(raw),
                inprocess_pref=None,
                frozen=_is_frozen(),
            )
            if route.inprocess:
                # 进程内那条路的解释器就是本进程，torch 在不在是本路的第一个
                # 会撞的事实。判据与 service.health_check 同一个 module_present
                # （find_spec，0.1–0.4 ms），别在这里 import torch。
                if not module_present("torch"):
                    return (
                        "实际走进程内 torch——但本进程寻不到 torch，"
                        "这条路会失败：填下方解释器走子进程，或给本程序装上 torch"
                    )
                return "实际走进程内 torch"
            if route.external_python:
                return f"实际走子进程（{os.path.basename(route.external_python)}）"
            return "当前无可用解释器"
        except Exception:
            return ""

    def _validate_vits_python_for_save(self) -> str:
        """保存前的 D2 校验：返回 warning 文本（空串=没问题）。

        ``resolve_external_python`` 对空串 / pet exe / 不在盘上三种输入都返回
        ""，而向导以前是无条件 ``.text().strip()`` 落盘。用户填了一个路径、
        看见它被保存，产品却走了另一条路 —— 这一格必须当场说出来，而不是等
        合成失败才发现。取值放在这里，是为了和交付码共用同一个解析函数。
        """
        try:
            raw = self.vits_python_input.text().strip()
        except Exception:
            return ""
        if not raw:
            return ""
        from meapet.tts.common import is_pet_executable, resolve_external_python

        if is_pet_executable(raw):
            return (
                "填的是 MeaPet 自己，不是 Python 解释器："
                "这条配置不会生效（会按未配置处理）。"
            )
        if not os.path.isfile(raw):
            return f"这个路径上找不到 Python：{raw}"
        if resolve_external_python(raw):
            return ""
        return "这个路径不是可用的 Python 解释器，配置不会生效。"

    def _report_vits_python_for_save(self) -> str:
        """保存时把校验结果写到 VITS 状态条上（不改变落盘内容）。"""
        warning = self._validate_vits_python_for_save()
        if not warning:
            return ""
        set_status(self.vits_status, "warning", f"⚠ {warning}")
        try:
            self.log(f"  ⚠ VITS Python 路径：{warning}")
        except Exception:
            pass
        return warning

    def _setup_vits_env(self):
        """On explicit user click, detect / create the VITS Python environment.

        In frozen mode ``sys.executable`` is the pet exe, not a real Python
        interpreter — we skip subprocess calls that point to it and search
        for a real system Python instead.
        """
        _ensure_main_invoker()  # 按钮点击在主线程：先建好跨线程投递器
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
        log = lambda msg: _post_to_main(lambda: self.log(msg)) if hasattr(self, 'log') else None

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
                    resolve_pip_index_url()], timeout_sec=600)
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
                torch_index = resolve_torch_index_url()
                log(f"{desc_prefix}下载 PyTorch（约 200MB）…")
                _pip_run(py_exe, ["torch", "torchaudio",
                         "--index-url", torch_index,
                         "--extra-index-url", resolve_pip_index_url()],
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
                            _post_to_main(lambda l=_line: log(f"    {l}"))
                        continue
                    # 非进度行直接输出
                    _post_to_main(lambda l=_line: log(f"    {l}"))

            _reader_thread = threading.Thread(target=_reader, daemon=True)
            _reader_thread.start()

            # ── 主线程：超时控制 ──
            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                _post_to_main(lambda: log("  ❌ 超时（>{}s），pip 安装中断".format(timeout_sec)))
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
        # embeddable 是 Windows 便携包的形态，POSIX 上没有这种目录，isfile 自然为假。
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
                _post_to_main(lambda: self._on_vits_env_done(True, _embedded))
            threading.Thread(target=_task_embedded, daemon=True).start()
            return

        # 1️⃣ vits_ft conda 环境
        _home = _os.path.expanduser("~")
        candidates = [
            prefix_python(_os.path.join(_home, root, "envs", "vits_ft"))
            for root in (".conda", "miniconda3", "anaconda3")
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
            py_path = venv_python(venv_path)
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
                py_path = venv_python(venv_path)
                if not _os.path.isfile(py_path):
                    raise Exception("venv 创建失败")

                _pip_install_deps(py_path, "[venv] ")

                # 复制 pyopenjtalk 词典
                import shutil
                src_pkg = env_site_packages(_os.path.join(
                    _os.path.expanduser("~"), ".conda", "envs", "vits_ft"))
                src_dict = _os.path.join(src_pkg, "pyopenjtalk")
                if _os.path.isdir(src_dict):
                    dst_pkg = env_site_packages(venv_path)
                    if _os.path.isdir(dst_pkg):
                        shutil.copytree(src_dict, _os.path.join(dst_pkg, "pyopenjtalk"),
                                       dirs_exist_ok=True)
                        log("已复制 pyopenjtalk 词典")

                _post_to_main(lambda: self._on_vits_env_done(True, py_path))
            except Exception as e:
                error = str(e)
                _post_to_main(
                    lambda error=error: self._on_vits_env_done(False, error),
                )

        threading.Thread(target=task, daemon=True).start()

    def _ensure_vits_deps(self, py_exe: str, log, status_widget=None):
        """检测 VITS 推理依赖，缺包时装完再复测；探测全程在后台线程。

        判据是 vits_infer._load_torch_stack 那份 import 面（--check-deps），不是
        这里另列的包名——本机实测该 env 有 torch 却缺 unidecode/eng_to_ipa/num_thai，
        从前逐个 import soundfile/scipy/librosa 的写法照样报"就绪"，到合成才炸。
        探测要 12–20 s（torch+text 前端全量 import，冷/热页缓存各一头），所以不进主线程。
        *status_widget* 给了就把结论也落到状态条——调用方若要写"检测中"这类
        过渡文案，只有这里能把它结掉。
        """
        _ensure_main_invoker()  # 主线程入口：先建好跨线程投递器
        from meapet.paths import project_path
        from meapet.tts.common import probe_vits_deps

        if TtsPageVitsMixin._path_is_pet_exe(py_exe):
            log("  ⚠ 打包版中无法检查 VITS 依赖（pet exe 不是 Python 解释器）")
            if status_widget is not None:
                set_status(status_widget, "warning", "打包版无法检测 VITS 依赖")
            return
        import subprocess, threading
        infer_script = project_path("meapet", "tools", "vits_infer.py")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        def _report(verdict, detail):
            if verdict == "ok":
                log("✓ VITS 推理依赖就绪")
                if status_widget is not None:
                    set_status(status_widget, "success", "VITS 环境已就绪（依赖探针通过）")
            elif verdict == "missing":
                log(f"  ✗ VITS 推理依赖不完整：{detail}——合成会失败并回退预制语音")
                if status_widget is not None:
                    set_status(status_widget, "error", f"VITS 依赖不完整：{detail}")
            else:
                log("  ⚠ 未能判定 VITS 推理依赖状态（探测超时或脚本缺失）")
                if status_widget is not None:
                    set_status(status_widget, "warning", f"未能判定 VITS 依赖：{detail}")

        def _task():
            verdict, detail = probe_vits_deps(py_exe, infer_script)
            if verdict == "missing":
                _post_to_main(
                    lambda d=detail: log(f"  ⚠ 缺依赖（{d}），安装基础包后复测…")
                )
                try:
                    r = subprocess.run(
                        [py_exe, "-m", "pip", "install", "--timeout", "120",
                         "-i", resolve_pip_index_url(),
                         "soundfile", "scipy", "librosa"],
                        capture_output=True, text=True, timeout=600, env=env
                    )
                    if r.returncode != 0:
                        _post_to_main(
                            lambda e=r.stderr[-150:]: log(f"  ⚠ pip 安装失败: {e}")
                        )
                except subprocess.TimeoutExpired:
                    _post_to_main(lambda: log("  ⚠ pip 安装超时"))
                verdict, detail = probe_vits_deps(py_exe, infer_script)
            _post_to_main(lambda v=verdict, d=detail: _report(v, d))

        threading.Thread(target=_task, daemon=True).start()

    def _on_vits_env_done(self, ok, result):
        self.setup_vits_btn.setEnabled(True)
        if ok:
            self.vits_python_input.setText(result)
            # 这里只说明"解释器已就位"；依赖齐不齐由 _ensure_vits_deps 的探针
            # 另报——从前这句无条件写"环境已就绪"，空 env 也会被它安抚过去。
            set_status(self.vits_status, "warning", "VITS 环境已配置，检测依赖中…")
            self.setup_vits_btn.setText("VITS 环境已配置")
            self._ensure_vits_deps(
                result,
                lambda msg: self.log(msg) if hasattr(self, "log") else None,
                status_widget=self.vits_status,
            )
        else:
            set_status(self.vits_status, "error", f"配置失败: {result[:50]}")
            self.setup_vits_btn.setText("自动配置 VITS 环境（重试）")



# ═══════════════════════════════════════
# 页面：识图 / 屏幕观察
# ═══════════════════════════════════════
