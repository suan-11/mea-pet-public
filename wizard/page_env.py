"""配置向导各页面"""
from __future__ import annotations

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
    styled_message_box,
)
from wizard.platform_info import (
    PLATFORM,
    PYTHON_CHECK_NAME,
    platform_checklist,
    python_runtime_compatibility,
)
from wizard.env_utils import (
    pip_install, check_installed,
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

            if name == PYTHON_CHECK_NAME:
                ok, version_status = python_runtime_compatibility()
                self._set_item_status(
                    name, ok,
                    f"{'✅' if ok else '⚠️'} {version_status}",
                )
                self.log(
                    f"Python: {sys.version.split()[0]} "
                    f"({'OK' if ok else '需要 3.10+'})"
                )
            elif name == "Ollama":
                # Ollama check removed — model selection is now provider-agnostic
                pass
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

    def _set_installing(self, busy: bool):
        """Disable/enable all install buttons while installing."""
        self._installing = busy
        for name in self.items:
            _, _, btn, _ = self.items[name]
            btn.setEnabled(not busy)
        self.total_bar.setVisible(busy)
        if busy:
            self.total_bar.setRange(0, 100)
            self.total_bar.setValue(0)

    def install_package(self, name: str):
        """用户显式点击后才 pip 安装（后台线程，默认不自动装）"""
        from PyQt5.QtWidgets import QMessageBox
        ret = styled_message_box(
            self,
            title="按需安装确认",
            text=(
                f"将通过 pip 安装 {name}。\n"
                "默认不自动下载组件，仅在你确认后安装。\n继续？"
            ),
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            self.log(f"已取消安装 {name}")
            return
        self._set_installing(True)
        if name == "pywin32" and not PLATFORM["is_windows"]:
            self.log("⏭ pywin32 仅适用于 Windows，已跳过")
            self._set_installing(False)
            return
        if name == PYTHON_CHECK_NAME:
            self.log("请从 https://www.python.org/downloads/ 手动安装 Python 3.10+")
            styled_message_box(
                self,
                title="手动安装 Python",
                text=(
                    f"当前平台：{PLATFORM['display']}\n\n"
                    "请手动安装 Python 3.10+ 并加入 PATH。\n"
                    "如需本地 VITS，推荐使用 Python 3.10~3.12。\n"
                    "本向导默认不自动下载 Python。"
                ),
                icon=QMessageBox.Information,
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

    def _install_done(self, name: str, ok: bool):
        """Restore UI after an install completes."""
        self._set_installing(False)
        if ok:
            self._set_item_status(name, True)
            set_status(self.total_status, "success", f"{name} 安装成功")
        else:
            set_status(self.total_status, "error", f"{name} 安装失败")
        self.total_bar.setVisible(False)


# ═══════════════════════════════════════
# 页面：LLM 选择
# ═══════════════════════════════════════
