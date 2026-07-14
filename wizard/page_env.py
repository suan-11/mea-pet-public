"""配置向导各页面"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal, pyqtSlot

from wizard.styles import (
    MIN_TARGET_SIZE,
    STYLE_PAGE_CARD,
    set_status,
)
from wizard.platform_info import PLATFORM, platform_checklist, ollama_install_hint
from wizard.env_utils import (
    pip_install, check_installed, download_file,
    check_ollama_running, check_ollama_installed, pull_ollama_model,
)

# 兼容页面内可能使用的短名
class EnvCheckPage(QFrame):
    ui_call = pyqtSignal(object)
    requirements_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui_call.connect(self._dispatch_ui_call, Qt.QueuedConnection)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 24, 28, 28)
        self.layout.setSpacing(12)

        title = QLabel("环境检测")
        title.setObjectName("PageTitle")
        title.setAccessibleName("环境检测")
        self.layout.addWidget(title)

        desc = QLabel("仅检测环境；组件默认不自动下载，需要时点「安装」按需获取")
        desc.setObjectName("PageDescription")
        desc.setWordWrap(True)
        self.layout.addWidget(desc)

        # 自动检测并展示当前平台
        self.platform_label = QLabel(f"当前平台 · {PLATFORM['display']}")
        self.platform_label.setObjectName("PageEyebrow")
        self.platform_label.setAccessibleName("当前运行平台")
        self.layout.addWidget(self.platform_label)
        self.log_platform_once = True

        # 检测结果列表（按平台动态生成）
        self.items = {}  # name -> (label, status_label, btn, hint)
        self._checklist = platform_checklist()
        self._required_names = tuple(
            name for name, _hint, required in self._checklist if required
        )
        self._check_results = {}
        for name, hint, _required in self._checklist:
            row = QHBoxLayout()
            name_label = QLabel(name)
            name_label.setObjectName("FieldLabel")
            name_label.setMinimumWidth(132)
            row.addWidget(name_label)

            hint_label = QLabel(hint)
            hint_label.setObjectName("HelperText")
            row.addWidget(hint_label)

            row.addStretch()

            status = QLabel("检测中…")
            status.setProperty("status", "muted")
            status.setAccessibleName(f"{name} 检测状态")
            row.addWidget(status)

            btn = QPushButton("安装")
            btn.setMinimumSize(76, MIN_TARGET_SIZE)
            btn.setAccessibleName(f"安装 {name}")
            btn.hide()
            # 连接安装按钮
            if name == "Ollama":
                btn.clicked.connect(self.install_ollama)
            else:
                btn.clicked.connect(lambda checked, n=name: self.install_package(n))
            row.addWidget(btn)

            self.items[name] = (name_label, status, btn, hint)
            self.layout.addLayout(row)

        # 总体进度
        self.layout.addSpacing(10)
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 100)
        self.total_bar.setValue(0)
        self.total_bar.setFixedHeight(8)
        self.total_bar.setTextVisible(False)
        self.total_bar.setAccessibleName("环境检测总进度")
        self.layout.addWidget(self.total_bar)

        self.total_status = QLabel("正在检测…")
        self.total_status.setProperty("status", "muted")
        self.total_status.setAccessibleName("环境检测汇总")
        self.layout.addWidget(self.total_status)

        self.layout.addSpacing(8)

        # 日志输出
        log_title = QLabel("安装日志")
        log_title.setObjectName("FieldLabel")
        self.layout.addWidget(log_title)

        self.log_area = QTextEdit()
        self.log_area.setObjectName("LogOutput")
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(200)
        self.log_area.setMinimumHeight(80)
        self.log_area.setAccessibleName("安装与检测日志")
        self.layout.addWidget(self.log_area)

        self._installing = False
        self._model_items = {}
        self._checking = False
        self._check_thread = None
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.timeout.connect(self._run_checks)
        self._check_timer.start(200)

    def log(self, msg):
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(lambda value=str(msg): self.log(value))
            return
        self.log_area.show()
        self.log_area.append(msg)

    def _run_on_ui(self, callback):
        """把后台线程产生的界面更新投递到 Qt 主线程。"""
        try:
            self.ui_call.emit(callback)
        except RuntimeError:
            return

    @pyqtSlot(object)
    def _dispatch_ui_call(self, callback):
        try:
            callback()
        except RuntimeError as exc:
            if "has been deleted" not in str(exc):
                raise

    def _set_item_status(self, name, ok: bool, text: str = None):
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(
                lambda: self._set_item_status(name, ok, text)
            )
            return
        _, status, btn, _ = self.items[name]
        if ok:
            set_status(status, "success", text or "就绪")
            btn.hide()
        else:
            set_status(status, "error", text or "缺失")
            btn.show()
        self._check_results[name] = bool(ok)
        self.requirements_changed.emit()

    def required_missing(self) -> list[str]:
        """返回已经完成检测且确认缺失的必需环境项。"""
        return [
            f"{name} 环境"
            for name in self._required_names
            if self._check_results.get(name) is False
        ]

    def _run_checks(self):
        """在后台执行检测，避免配置页首次显示时冻结。"""
        if self._checking:
            return
        self._checking = True
        self.total_status.setText("正在后台检测…")
        thread = threading.Thread(
            target=self._run_checks_worker,
            name="meapet-wizard-env-check",
            daemon=True,
        )
        self._check_thread = thread
        thread.start()

    def _run_checks_worker(self) -> None:
        try:
            self._run_checks_impl()
        except RuntimeError as exc:
            if "has been deleted" in str(exc):
                return
            raise
        except Exception as exc:
            self.log(f"环境检测失败：{type(exc).__name__}")
            self._run_on_ui(
                lambda: set_status(
                    self.total_status,
                    "error",
                    "环境检测失败，可稍后重新打开配置页或查看日志。",
                )
            )
        finally:
            self._run_on_ui(self._finish_check_run)

    def _finish_check_run(self) -> None:
        self._checking = False
        self._check_thread = None

    def _set_progress(self, value: int) -> None:
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(lambda: self._set_progress(value))
            return
        self.total_bar.setValue(value)

    def _set_skipped_status(self, name: str, text: str) -> None:
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(lambda: self._set_skipped_status(name, text))
            return
        if name in self.items:
            _, status, btn, _ = self.items[name]
            set_status(status, "muted", text)
            btn.hide()

    def _mark_optional_missing(self, name: str) -> None:
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(lambda: self._mark_optional_missing(name))
            return
        _, status, btn, _ = self.items[name]
        set_status(status, "warning")
        btn.show()

    def _finish_check_summary(self) -> None:
        if QThread.currentThread() is not self.thread():
            self._run_on_ui(self._finish_check_summary)
            return
        self.total_bar.setValue(100)
        set_status(
            self.total_status,
            "success",
            f"检测完成（{PLATFORM['os_label']}）— 缺失项可点“安装”按需获取",
        )

    def _run_checks_impl(self):
        self.log(f"开始检测环境… 平台={PLATFORM['display']}")
        self.log(f"  system={PLATFORM['system']} arch={PLATFORM['arch']} wsl={PLATFORM['is_wsl']}")

        checklist = getattr(self, "_checklist", platform_checklist())
        total_steps = max(len(checklist), 1)
        step = 0

        for name, _hint, _required in checklist:
            step += 1
            pct = int(step / total_steps * 90)

            if name == "Python 3.10–3.12":
                ver = sys.version_info
                ok = ver.major == 3 and 10 <= ver.minor <= 12
                self._set_item_status(
                    name, ok,
                    f"{'✅' if ok else '⚠️'} {ver.major}.{ver.minor}.{ver.micro}"
                )
                self.log(
                    f"Python: {sys.version.split()[0]} "
                    f"({'OK' if ok else '需要 3.10–3.12'})"
                )
            elif name == "Ollama":
                ollama_ok = check_ollama_installed()
                running, models = check_ollama_running()
                if running:
                    model_list = ", ".join(models[:3]) or "无模型"
                    self._set_item_status(name, True, f"✅ 运行中 ({model_list})")
                    self.log(f"Ollama: 运行中，模型 {model_list}")
                elif ollama_ok:
                    self._set_item_status(name, True, "✅ 已安装，未运行")
                    self.log("Ollama: 已安装但未运行（可手动 ollama serve）")
                else:
                    self._set_item_status(name, False, "❌ 未安装")
                    self.log("Ollama: 未安装")
                    self.log(ollama_install_hint().replace("\n", " | "))
                # 模型检测仅在运行时
                if running:
                    self._check_ollama_models(models)
            elif name == "pywin32":
                if not PLATFORM["is_windows"]:
                    # 理论上 checklist 已排除；兜底隐藏
                    self._set_skipped_status(name, "非 Windows，已跳过")
                    self.log("pywin32: 非 Windows 平台，跳过")
                else:
                    ok = check_installed("pywin32")
                    self._set_item_status(name, ok)
                    self.log(f"pywin32: {'就绪' if ok else '缺失（Windows 推荐安装）'}")
            elif name == "live2d-py":
                ok = check_installed("live2d-py")
                self._set_item_status(name, ok, "✅ 就绪" if ok else "○ 可选未装（将用 PNG）")
                if not ok:
                    # 可选：不强制标红按钮也可装
                    self._mark_optional_missing(name)
                self.log(f"live2d-py: {'就绪' if ok else '未装（可选）'}")
            elif name == "PyOpenGL":
                ok = check_installed("PyOpenGL")
                self._set_item_status(name, ok, "✅ 就绪" if ok else "○ 可选未装")
                if not ok:
                    self._mark_optional_missing(name)
                self.log(f"PyOpenGL: {'就绪' if ok else '未装（可选）'}")
            elif name == "requests":
                ok = check_installed("requests")
                self._set_item_status(name, ok)
                self.log(f"requests: {'就绪' if ok else '缺失'}")
            elif name == "httpx":
                ok = check_installed("httpx")
                self._set_item_status(name, ok)
                self.log(f"httpx: {'就绪' if ok else '缺失'}")
            else:
                # pip / PyQt5 等
                pkg = name
                ok = check_installed(pkg)
                self._set_item_status(name, ok)
                self.log(f"{name}: {'就绪' if ok else '缺失'}")

            self._set_progress(pct)

        # 平台提示
        if PLATFORM["is_linux"]:
            self.log("Linux 提示: 启动可用 QT_QPA_PLATFORM=xcb python pet.py")
            if PLATFORM["is_wsl"]:
                self.log("WSL 提示: 需可用的 GUI（WSLg / X11）；音频与截屏能力视子系统而定")
        elif PLATFORM["is_macos"]:
            self.log("macOS 提示: Live2D/OpenGL 依赖本机图形栈；首次运行可能需授权辅助功能")
        elif PLATFORM["is_windows"]:
            self.log("Windows 提示: 也可用「启动桌宠.bat」；默认不自动下载组件")

        self._finish_check_summary()
        self.log("环境检测完成")

    def _add_row(self, name: str, hint: str) -> tuple:
        """动态添加一行检测项"""
        row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setObjectName("FieldLabel")
        name_label.setMinimumWidth(132)
        row.addWidget(name_label)
        hint_label = QLabel(hint)
        hint_label.setObjectName("HelperText")
        row.addWidget(hint_label)
        row.addStretch()
        status = QLabel("检测中…")
        status.setProperty("status", "muted")
        status.setAccessibleName(f"{name} 检测状态")
        row.addWidget(status)
        btn = QPushButton("拉取")
        btn.setMinimumSize(76, MIN_TARGET_SIZE)
        btn.setAccessibleName(f"拉取 {name}")
        btn.hide()
        row.addWidget(btn)
        # 追加到布局末尾（spacer 和进度条之前）
        insert_pos = self.layout.count() - 3  # 在 stretch 之前
        self.layout.insertLayout(insert_pos, row)
        self.items[name] = (name_label, status, btn, hint)
        return name_label, status, btn

    def _check_ollama_models(self, existing_models: list):
        """检测 Ollama 模型是否就绪"""
        if QThread.currentThread() is not self.thread():
            models = list(existing_models or [])
            self._run_on_ui(lambda: self._check_ollama_models(models))
            return
        self._model_items = {}
        needed = [
            ("qwen3.5:4b", "多模态模型（约 3GB）", "对话+识图用"),
        ]
        has_model = any("qwen3.5" in m for m in existing_models)

        for model_name, hint, purpose in needed:
            _, status, btn = self._add_row(f"  {model_name}", hint)
            if has_model:
                set_status(status, "success", "就绪")
                btn.hide()
            else:
                set_status(status, "error", f"未拉取（{purpose}）")
                btn.show()
                btn.clicked.connect(lambda checked, m=model_name, s=status: self._pull_model(m, s))
            self._model_items[model_name] = (status, btn)

    def _set_installing(self, busy: bool):
        """安装中禁用/启用所有按钮"""
        self._installing = busy
        for name in self.items:
            _, _, btn, _ = self.items[name]
            btn.setEnabled(not busy)
        # 也禁用模型拉取按钮
        for mn in self._model_items:
            _, btn = self._model_items[mn]
            btn.setEnabled(not busy)
        self.total_bar.setVisible(busy)
        if busy:
            self.total_bar.setRange(0, 100)  # 重置可能被脉冲模式改过的范围
            self.total_bar.setValue(0)

    def install_package(self, name: str):
        """用户显式点击后才 pip 安装（后台线程，默认不自动装）"""
        from PyQt5.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, "按需安装确认",
            f"将通过 pip 安装 {name}。\n默认不自动下载组件，仅在你确认后安装。\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            self.log(f"已取消安装 {name}")
            return
        self._set_installing(True)
        if name == "pywin32" and not PLATFORM["is_windows"]:
            self.log("⏭ pywin32 仅适用于 Windows，已跳过")
            self._set_installing(False)
            return
        if name == "Python 3.10–3.12":
            self.log("请从 https://www.python.org/downloads/ 手动安装 Python 3.10–3.12")
            QMessageBox.information(
                self, "手动安装 Python",
                f"当前平台：{PLATFORM['display']}\n\n"
                "请手动安装 Python 3.10~3.12 并加入 PATH。\n"
                "本向导默认不自动下载 Python。"
            )
            self._set_installing(False)
            return
        pkg_map = {
            "PyQt5": ["PyQt5"],
            "pywin32": ["pywin32"],
            "live2d-py": ["live2d-py"],
            "PyOpenGL": ["PyOpenGL", "PyOpenGL-accelerate"],
            "requests": ["requests"],
            "httpx": ["httpx"],
            "pip": ["pip"],
        }
        packages = pkg_map.get(name, [name])
        self.log(f"📦 安装 {name} 中…（平台 {PLATFORM['os_label']}）")

        def task():
            ok = pip_install(packages)
            self._run_on_ui(
                lambda package_name=name, succeeded=ok:
                self._install_done(package_name, succeeded)
            )

        threading.Thread(target=task, daemon=True).start()

    def install_ollama(self):
        """按平台引导安装 Ollama；仅 Windows 提供可选 exe 下载，其它平台给手动命令。"""
        from PyQt5.QtWidgets import QMessageBox

        if not PLATFORM["is_windows"]:
            QMessageBox.information(
                self, f"在 {PLATFORM['os_label']} 上安装 Ollama",
                ollama_install_hint() + "\n\n本向导不会在非 Windows 平台自动下载安装包。"
            )
            self.log(ollama_install_hint().replace("\n", " | "))
            return

        ret = QMessageBox.question(
            self, "按需下载确认",
            "检测到 Windows。将下载 OllamaSetup.exe（约 300MB）并静默安装。\n"
            "默认不会自动下载，仅在你确认后进行。\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            self.log("已取消 Ollama 下载")
            return
        self._set_installing(True)
        self.total_bar.setValue(0)
        self.total_bar.setVisible(True)
        self.log("📦 正在下载 Ollama（Windows，约 300MB）…")

        def dl_progress(pct):
            self._run_on_ui(
                lambda value=pct: self._set_dl_progress(value)
            )

        def task():
            dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OllamaSetup.exe")
            ok = download_file(
                "https://ollama.com/download/OllamaSetup.exe",
                dest, dl_progress
            )
            if ok:
                self._run_on_ui(lambda: self.log("下载完成，正在安装…"))
                try:
                    result = subprocess.run([dest, "/S"], timeout=120)
                    if result.returncode != 0:
                        raise RuntimeError(f"安装程序退出码 {result.returncode}")
                    self._run_on_ui(
                        lambda: self._finish_ollama_install(
                            True, "✅ Ollama 安装成功！重启后生效"
                        )
                    )
                    return
                except Exception as e:
                    message = f"❌ 安装失败：{e}"
                    self._run_on_ui(
                        lambda text=message: self._finish_ollama_install(False, text)
                    )
                    return
            else:
                self._run_on_ui(
                    lambda: self._finish_ollama_install(
                        False, "❌ 下载失败，请手动从 ollama.com 下载安装"
                    )
                )

        threading.Thread(target=task, daemon=True).start()

    def _set_dl_progress(self, pct: int):
        """下载进度更新（主线程回调），支持无 Content-Length 的脉冲模式"""
        if pct < 0:
            # 无 Content-Length → 脉冲样式
            self.total_bar.setRange(0, 0)  # 不确定范围 → 自动脉冲动画
        else:
            self.total_bar.setRange(0, 100)
            self.total_bar.setValue(pct)

    def _install_done(self, name: str, ok: bool):
        """安装完成后恢复界面"""
        self._set_installing(False)
        if ok:
            self._set_item_status(name, True)
            set_status(self.total_status, "success", f"{name} 安装成功")
        else:
            set_status(self.total_status, "error", f"{name} 安装失败")
        self.total_bar.setVisible(False)

    def _finish_ollama_install(self, ok: bool, message: str):
        """在主线程完成 Ollama 安装状态更新。"""
        self.log(message)
        self._install_done("Ollama", ok)

    def _pull_model(self, model: str, status_label):
        """用户显式点击后才拉取 Ollama 模型（后台线程，带进度）"""
        from PyQt5.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, "按需下载确认",
            f"将通过 ollama pull 下载模型 {model}（可能数 GB）。\n"
            "默认不会自动拉取，仅在你确认后进行。\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            self.log(f"已取消拉取 {model}")
            return
        self._set_installing(True)
        self.total_bar.setValue(0)
        self.total_bar.setVisible(True)
        self.log(f"📦 正在拉取 {model}（这可能需要很久，取决于你的网速）…")

        def task():
            def on_log(line: str):
                self._run_on_ui(
                    lambda text=line: self.log(f"  {text}")
                )
                # 尝试解析进度百分比
                import re
                m = re.search(r'(\d+)%', line)
                if m:
                    pct = int(m.group(1))
                    self._run_on_ui(
                        lambda value=pct: self.total_bar.setValue(value)
                    )

            ok = pull_ollama_model(model, log_callback=on_log)
            self._run_on_ui(
                lambda model_name=model, succeeded=ok, label=status_label:
                self._pull_done(model_name, succeeded, label)
            )

        threading.Thread(target=task, daemon=True).start()

    def _pull_done(self, model: str, ok: bool, status_label):
        """模型拉取完成"""
        self._set_installing(False)
        self.total_bar.setVisible(False)
        if ok:
            set_status(status_label, "success", "就绪")
            self.log(f"✅ {model} 拉取完成")
            set_status(self.total_status, "success", f"{model} 就绪")
        else:
            self.log(f"❌ {model} 拉取失败，稍后可以手动运行: ollama pull {model}")
            set_status(self.total_status, "error", f"{model} 拉取失败")


# ═══════════════════════════════════════
# 页面：LLM 选择
# ═══════════════════════════════════════
