"""配置向导主窗口"""
from __future__ import annotations

import copy
import json
import os
import sys

from PyQt5.QtWidgets import (
    QApplication,
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PyQt5.QtWidgets import QShortcut

from wizard.platform_info import CONFIG_PATH, PLATFORM
from wizard.styles import (
    WIZARD_STYLESHEET,
    prepare_accessible_page,
    set_status,
)
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    PALETTE,
    UI_FONT_SCALE_DEFAULT,
    apply_ui_font_scale,
    ensure_application_fonts,
    normalize_ui_font_scale,
    set_ui_font_scale,
)
from wizard.pages import (
    BackendPage,
    EnvCheckPage,
    LLMPage,
    TTSPage,
    VisionPage,
)


class SetupWizard(QWidget):
    config_saved = pyqtSignal(dict)

    TAB_ENV = 0
    TAB_CHAT = 1
    TAB_VOICE = 2
    TAB_VISION = 3

    @staticmethod
    def _read_initial_font_scale(
        config_path: str,
        initial_config: dict | None = None,
    ) -> float:
        """只读取显示字号，避免配置页首次绘制后再发生字体跳变。"""
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                config = json.load(file)
            if isinstance(config, dict) and isinstance(
                config.get("display"),
                dict,
            ):
                return normalize_ui_font_scale(
                    config["display"].get("font_scale")
                )
        except (OSError, ValueError, TypeError):
            pass
        if isinstance(initial_config, dict) and isinstance(
            initial_config.get("display"),
            dict,
        ):
            return normalize_ui_font_scale(
                initial_config["display"].get("font_scale")
            )
        return UI_FONT_SCALE_DEFAULT

    def __init__(
        self,
        config_path: str | os.PathLike[str] | None = None,
        initial_config: dict | None = None,
    ):
        from meapet.config.store import resolve_writable_config_path

        source_path = os.fspath(config_path) if config_path else CONFIG_PATH
        self._source_config_path = source_path
        self.config_path = resolve_writable_config_path(source_path)
        self._initial_config = copy.deepcopy(initial_config or {})
        font_config_path = (
            self.config_path
            if os.path.isfile(self.config_path)
            else source_path
        )
        initial_font_scale = self._read_initial_font_scale(
            font_config_path,
            self._initial_config,
        )
        set_ui_font_scale(initial_font_scale)
        super().__init__()
        self._dirty = False
        self._suppress_dirty = False
        self._closing_after_save = False
        self._connection_test_jobs = {}
        ensure_application_fonts()
        self.setWindowTitle(f"MeaPet 配置 — {PLATFORM['os_label']}")
        self.setObjectName("WizardRoot")
        self.setMinimumSize(760, 620)
        self.resize(880, 780)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(WIZARD_STYLESHEET)
        self.setAccessibleName("MeaPet 配置")
        self.setAccessibleDescription("使用标签页配置环境、对话、语音和屏幕识图功能")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        self.container = QFrame()
        self.container.setObjectName("WizardShell")
        main = QVBoxLayout(self.container)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        outer.addWidget(self.container)

        # 顶栏
        header = QFrame()
        header.setObjectName("WizardHeader")
        top = QHBoxLayout(header)
        top.setContentsMargins(24, 16, 16, 10)
        top.setSpacing(10)

        brand_mark = QLabel("M")
        brand_mark.setObjectName("BrandMark")
        brand_mark.setFixedSize(28, 28)
        brand_mark.setAlignment(Qt.AlignCenter)
        brand_mark.setAccessibleName("MeaPet")
        top.addWidget(brand_mark)

        brand_name = QLabel("MeaPet 设置")
        brand_name.setObjectName("BrandName")
        top.addWidget(brand_name)
        top.addStretch()

        section_label = QLabel("配置中心")
        section_label.setObjectName("StepLabel")
        section_label.setAccessibleName("当前页面")
        top.addWidget(section_label)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseButton")
        self.close_btn.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.close_btn.setToolTip("关闭配置页（Esc）")
        self.close_btn.setAccessibleName("关闭配置页")
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn)
        main.addWidget(header)

        divider = QFrame()
        divider.setObjectName("WizardDivider")
        main.addWidget(divider)

        # 页面内容保留原有控件和配置收集逻辑，只把导航改为普通标签页。
        self.env_page = EnvCheckPage()
        self.display_page = self._build_display_settings(initial_font_scale)
        self.backend_page = BackendPage()
        self.llm_page = LLMPage()
        self.tts_page = TTSPage()
        self.vision_page = VisionPage()

        for page in (
            self.env_page,
            self.display_page,
            self.backend_page,
            self.llm_page,
            self.tts_page,
            self.vision_page,
        ):
            prepare_accessible_page(page)

        self._existing_config = {}
        self._missing_icon = self._build_missing_icon()

        status_row = QHBoxLayout()
        status_row.setContentsMargins(24, 12, 24, 4)
        self.config_status = QLabel("正在检查必要配置…")
        self.config_status.setObjectName("ConfigStatus")
        self.config_status.setWordWrap(True)
        self.config_status.setAccessibleName("必要配置状态")
        status_row.addWidget(self.config_status, 1)
        main.addLayout(status_row)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("ConfigurationTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setIconSize(QSize(12, 12))
        self.tabs.setAccessibleName("配置分类")
        self.tabs.setAccessibleDescription("带红点的标签缺少必要配置，并有文字提示")
        self.tabs.tabBar().setAccessibleName("环境、对话、语音和屏幕识图标签")
        self.tabs.tabBar().setAccessibleDescription(
            "红点表示该标签仍缺少必要配置；具体原因显示在标签提示和顶部状态中"
        )
        self.tabs.addTab(
            self._make_scroll_tab(self.display_page, self.env_page),
            "环境",
        )
        self.tabs.addTab(
            self._make_scroll_tab(
                self.backend_page,
                self.llm_page,
            ),
            "对话",
        )
        self.tabs.addTab(self._make_scroll_tab(self.tts_page), "语音")
        self.tabs.addTab(self._make_scroll_tab(self.vision_page), "屏幕识图")
        main.addWidget(self.tabs, 1)

        # 底部按钮
        footer = QFrame()
        footer.setObjectName("WizardFooter")
        btns = QHBoxLayout(footer)
        btns.setContentsMargins(24, 12, 18, 18)
        btns.setSpacing(12)

        footer_hint = QLabel("先完成「环境」和「对话」即可开玩；语音与屏幕识图可稍后设置。设置仅保存在本机。")
        footer_hint.setObjectName("HelperText")
        footer_hint.setWordWrap(True)
        btns.addWidget(footer_hint, 1)

        self.save_btn = QPushButton("保存配置")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.setMinimumSize(124, MIN_TARGET_SIZE)
        self.save_btn.setAccessibleName("保存全部配置")
        self.save_btn.setToolTip("保存当前所有标签页中的配置")
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.save_btn)

        size_grip = QSizeGrip(self.container)
        size_grip.setFixedSize(18, 18)
        size_grip.setToolTip("拖动调整窗口大小")
        btns.addWidget(size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        main.addWidget(footer)

        self._close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._close_shortcut.activated.connect(self.close)
        self.setTabOrder(self.tabs, self.save_btn)
        self.setTabOrder(self.save_btn, self.close_btn)

        # 窗口拖拽
        self._drag = None
        for w in [header, brand_mark, brand_name]:
            w.mousePressEvent = lambda e: self._drag_start(e)
            w.mouseMoveEvent = lambda e: self._drag_move(e)
            w.mouseReleaseEvent = lambda e: setattr(self, '_drag', None)

        self._connect_required_field_updates()
        self._connect_connection_tests()
        self._sync_llm_key_panel()
        self._connect_dirty_tracking()
        self._refresh_required_tabs()
        # 再次配置：读取并回填上次 config.json
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._load_existing_config)
        # 配置文件是本地小 JSON；首帧前同步回填，避免 100% 控件随后跳回保存值。
        self._load_existing_config()
        apply_ui_font_scale(
            self,
            self.font_scale_slider.value() / 100.0,
        )

    def _build_display_settings(self, initial_scale: float) -> QFrame:
        """创建独立的界面字号设置卡，并提供即时预览。"""
        card = QFrame()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel("界面显示")
        title.setObjectName("PageTitle")
        title.setAccessibleName("界面显示")
        layout.addWidget(title)

        description = QLabel(
            "调整配置页、聊天气泡、菜单和对话框的字体大小。"
            "配置页会即时预览，桌宠重启后应用。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel("字体缩放")
        label.setObjectName("FieldLabel")
        label.setMinimumWidth(112)
        row.addWidget(label)

        self.font_scale_slider = QSlider(Qt.Horizontal)
        self.font_scale_slider.setRange(80, 150)
        self.font_scale_slider.setSingleStep(5)
        self.font_scale_slider.setPageStep(10)
        self.font_scale_slider.setTracking(True)
        self.font_scale_slider.setValue(round(initial_scale * 100))
        self.font_scale_slider.setAccessibleName("界面字体缩放")
        self.font_scale_slider.setAccessibleDescription(
            "可在百分之八十到百分之一百五十之间调整，步长百分之五"
        )
        row.addWidget(self.font_scale_slider, 1)

        self.font_scale_value = QLabel(
            f"{self.font_scale_slider.value()}%"
        )
        self.font_scale_value.setObjectName("FontScaleValue")
        self.font_scale_value.setMinimumWidth(52)
        self.font_scale_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.font_scale_value.setAccessibleName("当前字体缩放")
        row.addWidget(self.font_scale_value)
        layout.addLayout(row)

        hint = QLabel("建议 100%；高分屏或阅读困难时可调至 120%–150%。")
        hint.setObjectName("HelperText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.reduced_motion_cb = QCheckBox("减少动画（气泡与输入框淡入淡出）")
        self.reduced_motion_cb.setObjectName("ReducedMotionToggle")
        self.reduced_motion_cb.setAccessibleName("减少动画")
        self.reduced_motion_cb.setAccessibleDescription(
            "开启后桌宠界面动画会尽量瞬切，适合晕动或低性能设备"
        )
        self.reduced_motion_cb.setChecked(False)
        layout.addWidget(self.reduced_motion_cb)

        motion_hint = QLabel(
            "减少动画保存后立即应用；字体缩放需要桌宠重启后完整应用。"
        )
        motion_hint.setObjectName("HelperText")
        motion_hint.setWordWrap(True)
        layout.addWidget(motion_hint)

        self.font_scale_slider.valueChanged.connect(
            self._on_font_scale_changed
        )
        return card

    def _on_font_scale_changed(self, value: int) -> None:
        value = int(value)
        self.font_scale_value.setText(f"{value}%")
        apply_ui_font_scale(self, value / 100.0)

    @property
    def is_dirty(self) -> bool:
        """配置页是否含尚未保存的用户编辑。"""
        return bool(self._dirty)

    def _mark_dirty(self, *_args) -> None:
        if not self._suppress_dirty:
            self._dirty = True

    def _connect_dirty_tracking(self) -> None:
        """统一跟踪表单控件，避免新增字段时忘记接入关闭确认。"""
        for widget in self.findChildren(QLineEdit):
            widget.textChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QAbstractButton):
            if widget.property("doesNotModifyConfig"):
                continue
            widget.toggled.connect(self._mark_dirty)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QSlider):
            widget.valueChanged.connect(self._mark_dirty)

    def closeEvent(self, event) -> None:
        if (
            self.is_dirty
            and self.isVisible()
            and not self._closing_after_save
        ):
            reply = QMessageBox.question(
                self,
                "放弃未保存的更改？",
                "当前配置尚未保存。关闭后这些更改会丢失。",
                QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply != QMessageBox.Discard:
                event.ignore()
                return
        self._dirty = False
        self._cancel_connection_tests()
        event.accept()

    def _connect_connection_tests(self) -> None:
        """把四个请求入口统一接到后台探测，不在 GUI 线程等待网络。"""
        bindings = (
            (
                "direct",
                self.llm_page.test_connection_btn,
                self.llm_page.connection_status,
            ),
            (
                "agent",
                self.backend_page.test_agent_connection_btn,
                self.backend_page.agent_connection_status,
            ),
            (
                "tts",
                self.tts_page.test_connection_btn,
                self.tts_page.connection_status,
            ),
            (
                "vision",
                self.vision_page.test_connection_btn,
                self.vision_page.connection_status,
            ),
        )
        for target, button, status in bindings:
            button.clicked.connect(
                lambda _checked=False, kind=target, action=button, label=status: (
                    self._start_connection_test(kind, action, label)
                )
            )

    def _start_connection_test(
        self,
        target: str,
        button: QPushButton,
        status: QLabel,
    ) -> None:
        active = self._connection_test_jobs.get(target)
        if active is not None and not active[0].done():
            return
        try:
            from meapet.async_runtime import submit
            from wizard.connection_test import probe_connection

            config = self.collect_config()
            future = submit(probe_connection(target, config))
        except Exception as exc:
            set_status(status, "error", f"无法开始测试：{exc}")
            return

        button.setEnabled(False)
        set_status(status, "warning", "正在测试，请稍候…")
        timer = QTimer(self)
        timer.setInterval(100)
        timer.timeout.connect(
            lambda kind=target: self._poll_connection_test(kind)
        )
        self._connection_test_jobs[target] = (future, timer, button, status)
        timer.start()

    def _poll_connection_test(self, target: str) -> None:
        job = self._connection_test_jobs.get(target)
        if job is None:
            return
        future, timer, button, status = job
        if not future.done():
            return
        timer.stop()
        timer.deleteLater()
        self._connection_test_jobs.pop(target, None)
        try:
            result = future.result()
        except Exception as exc:
            set_status(status, "error", f"测试失败：{exc}")
        else:
            set_status(
                status,
                "success" if result.ok else "error",
                result.message,
            )
        enabled = True
        if target == "tts":
            enabled = self.tts_page.enable_cb.isChecked()
        elif target == "vision":
            enabled = self.vision_page.mode_combo.currentData() != "disabled"
        button.setEnabled(enabled)

    def _cancel_connection_tests(self) -> None:
        for future, timer, button, _status in tuple(
            self._connection_test_jobs.values()
        ):
            timer.stop()
            future.cancel()
            try:
                button.setEnabled(True)
            except RuntimeError:
                pass
        self._connection_test_jobs.clear()

    def _make_scroll_tab(self, *pages: QWidget) -> QScrollArea:
        """给每个标签提供独立滚动区域，避免高 DPI 或小窗口裁切表单。"""
        content = QWidget()
        content.setObjectName("ConfigurationTabContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        for page in pages:
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            layout.addWidget(page)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("ConfigurationTabScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        scroll.setAccessibleName("配置表单")
        return scroll

    @staticmethod
    def _build_missing_icon() -> QIcon:
        """绘制独立红点图标，避免用文字字符模拟状态。"""
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(PALETTE["danger"]))
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _connect_required_field_updates(self) -> None:
        self.env_page.requirements_changed.connect(
            self._refresh_required_tabs
        )
        for radio in (
            self.llm_page.radio_ollama,
            self.llm_page.radio_ds,
            self.llm_page.radio_mimo,
            self.llm_page.radio_custom,
        ):
            radio.toggled.connect(self._on_llm_backend_changed)
        self.backend_page.direct_radio.toggled.connect(
            self._on_conversation_mode_changed
        )
        self.backend_page.agent_radio.toggled.connect(
            self._on_conversation_mode_changed
        )
        self.backend_page.agent_base_url.textChanged.connect(
            self._refresh_required_tabs
        )
        self.llm_page.endpoint_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.llm_page.model_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.llm_page.direct_api_key_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.backend_page.control_enabled.toggled.connect(
            self._refresh_required_tabs
        )
        self.backend_page.control_allow_http.toggled.connect(
            self._refresh_required_tabs
        )
        self.backend_page.control_listen_host.textChanged.connect(
            self._refresh_required_tabs
        )
        self.backend_page.control_cert_file.textChanged.connect(
            self._refresh_required_tabs
        )
        self.backend_page.control_key_file.textChanged.connect(
            self._refresh_required_tabs
        )
        self.tts_page.enable_cb.toggled.connect(self._refresh_required_tabs)
        self.tts_page.backend_combo.currentIndexChanged.connect(
            self._refresh_required_tabs
        )
        self.tts_page.mimo_api_key_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.vision_page.enable_cb.toggled.connect(self._refresh_required_tabs)
        self.vision_page.mode_combo.currentIndexChanged.connect(
            self._refresh_required_tabs
        )
        self.vision_page.main_model_vision_cb.toggled.connect(
            self._refresh_required_tabs
        )
        self.vision_page.allow_cloud_cb.toggled.connect(
            self._refresh_required_tabs
        )
        self.vision_page.backend_combo.currentIndexChanged.connect(
            self._refresh_required_tabs
        )
        self.vision_page.api_key_input.textChanged.connect(
            self._refresh_required_tabs
        )

    def _on_llm_backend_changed(self, checked: bool = True) -> None:
        if not checked:
            return
        self._sync_llm_key_panel()
        self._refresh_required_tabs()

    def _on_conversation_mode_changed(self, checked: bool = True) -> None:
        if not checked:
            return
        self._sync_llm_key_panel()
        self._refresh_required_tabs()

    def _sync_llm_key_panel(self) -> None:
        direct_mode = self.backend_page.direct_radio.isChecked()
        self.llm_page.setVisible(direct_mode)

    def _configuration_issues(self) -> dict[int, list[str]]:
        issues = {
            self.TAB_ENV: [],
            self.TAB_CHAT: [],
            self.TAB_VOICE: [],
            self.TAB_VISION: [],
        }
        issues[self.TAB_ENV].extend(self.env_page.required_missing())

        conversation_mode = self.backend_page.mode()
        llm_backend = self.llm_page.get_backend()
        llm_key = self.llm_page.direct_api_key_input.text().strip()
        if conversation_mode == "agent":
            if not self.backend_page.agent_base_url.text().strip():
                issues[self.TAB_CHAT].append("Agent 地址")
            if self.backend_page.control_enabled.isChecked():
                listen = self.backend_page.control_listen_host.text().strip()
                loopback = listen in {"127.0.0.1", "::1"}
                cert = self.backend_page.control_cert_file.text().strip()
                key = self.backend_page.control_key_file.text().strip()
                if not loopback and not self.backend_page.control_allow_http.isChecked():
                    if not cert or not key:
                        issues[self.TAB_CHAT].append(
                            "内网监听需 HTTPS 证书，或明确允许 HTTP"
                        )
        else:
            if not self.llm_page.endpoint_input.text().strip():
                issues[self.TAB_CHAT].append("API 地址")
            if not self.llm_page.model_input.text().strip():
                issues[self.TAB_CHAT].append("模型 ID")
            if llm_backend == "deepseek" and not llm_key:
                issues[self.TAB_CHAT].append("DeepSeek API Key")
            elif llm_backend == "mimo" and not llm_key:
                issues[self.TAB_CHAT].append("MiMo API Key")

        if (
            self.tts_page.enable_cb.isChecked()
            and self.tts_page.backend_combo.currentData() == "mimo"
        ):
            tts_key = self.tts_page.mimo_api_key_input.text().strip()
            if (
                not tts_key
                and conversation_mode == "direct"
                and llm_backend == "mimo"
            ):
                tts_key = llm_key
            if not tts_key:
                issues[self.TAB_VOICE].append("MiMo TTS API Key")

        if self.vision_page.enable_cb.isChecked():
            vision_mode = self.vision_page.mode_combo.currentData() or "disabled"
            if vision_mode == "disabled":
                issues[self.TAB_VISION].append("视觉链路模式")
            elif vision_mode == "inherit":
                if not self.vision_page.main_model_vision_cb.isChecked():
                    issues[self.TAB_VISION].append("主回复后端图片能力确认")
                if conversation_mode == "agent":
                    endpoint = self.backend_page.agent_base_url.text().strip()
                else:
                    endpoint = self.llm_page.endpoint_input.text().strip()
                from meapet.utils import is_loopback_url
                if (
                    endpoint
                    and not is_loopback_url(endpoint)
                    and not self.vision_page.allow_cloud_cb.isChecked()
                ):
                    issues[self.TAB_VISION].append("云端识图授权")
            elif conversation_mode == "agent":
                issues[self.TAB_VISION].append("Agent 模式须由 Agent 直接读图")
            else:
                selected_backend = (
                    self.vision_page.backend_combo.currentData() or "auto"
                )
                actual_backend = selected_backend
                if selected_backend == "auto":
                    actual_backend = (
                        llm_backend
                        if llm_backend in {"ollama", "mimo"}
                        else "ollama"
                    )
                if actual_backend == "mimo":
                    if not self.vision_page.allow_cloud_cb.isChecked():
                        issues[self.TAB_VISION].append("云端识图授权")
                    vision_key = self.vision_page.api_key_input.text().strip()
                    if not vision_key and llm_backend == "mimo":
                        vision_key = llm_key
                    if not vision_key:
                        issues[self.TAB_VISION].append("云端识图 API Key")
        return issues

    def _refresh_required_tabs(self, *_args) -> None:
        issues = self._configuration_issues()
        labels = {
            self.TAB_ENV: "环境",
            self.TAB_CHAT: "对话",
            self.TAB_VOICE: "语音",
            self.TAB_VISION: "屏幕识图",
        }
        missing_sections = []
        for index, label in labels.items():
            missing = issues[index]
            self.tabs.setTabIcon(index, self._missing_icon if missing else QIcon())
            if missing:
                detail = "、".join(missing)
                self.tabs.setTabToolTip(index, f"{label}：缺少必要配置：{detail}")
                missing_sections.append(f"{label}（{detail}）")
            else:
                self.tabs.setTabToolTip(index, f"{label}：必要配置已就绪")

        if missing_sections:
            message = "需要补充：" + "；".join(missing_sections)
            set_status(self.config_status, "error", message)
            self.config_status.setAccessibleDescription(message)
        else:
            message = "必要配置已就绪，可以直接保存"
            set_status(self.config_status, "success", message)
            self.config_status.setAccessibleDescription(message)

    @staticmethod
    def _read_config_file(path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as file:
                config = json.load(file)
        except (OSError, ValueError, TypeError):
            return None
        return config if isinstance(config, dict) else None

    @classmethod
    def _template_config(cls) -> dict:
        template_path = os.path.join(
            os.path.dirname(CONFIG_PATH),
            "config.example.json",
        )
        return cls._read_config_file(template_path) or {}

    def _load_existing_config(self):
        """从桌宠实际使用的路径恢复配置，首次运行则使用唯一模板。"""
        self._suppress_dirty = True
        try:
            cfg = None
            for candidate in dict.fromkeys(
                (self.config_path, self._source_config_path)
            ):
                if candidate and os.path.isfile(candidate):
                    cfg = self._read_config_file(candidate)
                    if cfg is not None:
                        break
                    self.env_page.log(f"读取已有配置失败: {candidate}")
            if cfg is None and self._initial_config:
                cfg = copy.deepcopy(self._initial_config)
            if cfg is None:
                cfg = self._template_config()
            self._existing_config = copy.deepcopy(cfg)

            tts = cfg.get("tts", {}) or {}
            display = (
                cfg.get("display", {})
                if isinstance(cfg.get("display"), dict)
                else {}
            )

            self.font_scale_slider.setValue(
                round(
                    normalize_ui_font_scale(
                        display.get("font_scale", 1.0)
                    )
                    * 100
                )
            )
            self.reduced_motion_cb.setChecked(
                bool(display.get("reduced_motion", False))
            )

            self.apply_conversation_config(cfg)
            backend = self.llm_page.get_backend()

            try:
                self.tts_page.apply_config(tts)
                self.env_page.log("已恢复上次语音配置（引擎/语言/克隆等）")
            except Exception as exc:
                self.env_page.log(f"恢复语音配置失败: {exc}")

            try:
                vision = cfg.get("vision", {}) or {}
                watcher = cfg.get("watcher", {}) or {}
                if "interval" not in watcher and isinstance(
                    watcher.get("min_ms"),
                    (int, float),
                ):
                    watcher = dict(watcher)
                    watcher["interval"] = {
                        "min_ms": int(watcher.get("min_ms", 180000)),
                        "max_ms": int(watcher.get("max_ms", 360000)),
                    }
                self.vision_page.apply_config(vision, watcher)
            except Exception as exc:
                self.env_page.log(f"恢复识图配置失败: {exc}")

            eng = (tts.get("engine") or "?").lower()
            tts_on = "开" if tts.get("enabled", True) else "关"
            w = cfg.get("watcher") or {}
            w_on = "开" if w.get("enabled") else "关"
            v_back = (cfg.get("vision") or {}).get("backend") or "跟随对话"
            self.env_page.log(
                f"📂 已加载上次配置：AI={backend}，语音={eng}（{tts_on}），识图={v_back or '跟随'}（观察{w_on}）"
            )
        finally:
            self._sync_llm_key_panel()
            self._refresh_required_tabs()
            self._dirty = False
            self._suppress_dirty = False

    def apply_conversation_config(self, config: dict) -> None:
        """恢复 direct/Agent 两侧配置；切换模式时不清空非活动侧。"""
        from meapet.config.store import normalize_config

        if isinstance(config, dict):
            self._existing_config = self._deep_merge(
                getattr(self, "_existing_config", {}) or {},
                config,
            )
        normalized = normalize_config(config or {})
        llm = normalized.get("llm") or {}
        direct = llm.get("direct") or {}
        self.backend_page.apply_config(
            llm,
            normalized.get("agent_control") or {},
            normalized.get("ui") or {},
        )
        self.llm_page.apply_direct_profile(direct)
        self._sync_llm_key_panel()

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """递归合并：override 覆盖 base，未涉及的旧字段保留。"""
        out = copy.deepcopy(base or {})
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = self._deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out

    def _drag_start(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos()

    def _drag_move(self, e):
        if self._drag:
            self.move(self.pos() + e.globalPos() - self._drag)
            self._drag = e.globalPos()

    def _config_base(self, base_config: dict | None = None) -> dict:
        """用模板补缺，以现有配置为准；绝不反向覆盖已有字段。"""
        existing = (
            base_config
            if isinstance(base_config, dict)
            else getattr(self, "_existing_config", {}) or {}
        )
        return self._deep_merge(self._template_config(), existing)

    def _collect_display_fields(self, config: dict) -> None:
        """显示页只负责它实际展示的两个选项。"""
        display = config.setdefault("display", {})
        display["font_scale"] = self.font_scale_slider.value() / 100.0
        display["reduced_motion"] = self.reduced_motion_cb.isChecked()

    def _collect_reference_audios(
        self,
        tts_config: dict,
    ) -> tuple[dict, str, dict]:
        from meapet.config.normalizers import normalize_gsv_ref_language

        references = {}
        reference_inputs = getattr(self.tts_page, "gsv_reference_inputs", {})
        reference_texts = getattr(self.tts_page, "_gsv_reference_texts", {})
        loaded_paths = getattr(
            self.tts_page,
            "_gsv_reference_loaded_paths",
            {},
        )
        for language, widget in reference_inputs.items():
            path = widget.text().strip()
            if not path:
                continue
            text_value = (
                str(reference_texts.get(language) or "").strip()
                if path == loaded_paths.get(language)
                else ""
            )
            references[language] = {"path": path, "text": text_value}

        legacy_language = normalize_gsv_ref_language(
            tts_config.get("gsv_ref_lang") or "jp"
        )
        if legacy_language not in references and references:
            legacy_language = next(iter(references))
        legacy_reference = references.get(legacy_language) or {}
        return references, legacy_language, legacy_reference

    def _collect_tts_fields(self, config: dict) -> None:
        """将语音页控件写回 tts 节；模型权重等非 UI 字段原样保留。"""
        tts = config.setdefault("tts", {})
        references, legacy_language, legacy_reference = (
            self._collect_reference_audios(tts)
        )
        engine = self.tts_page.backend_combo.currentData() or "gpt_sovits"
        translation_target = (
            self.tts_page.translate_target_combo.currentData() or "jp"
        )
        use_clone = self.tts_page.mimo_voiceclone_cb.isChecked()

        llm = config.get("llm") or {}
        direct = llm.get("direct") or {}
        tts_key = self.tts_page.mimo_api_key_input.text().strip()
        tts_base = self.tts_page.mimo_api_base_input.text().strip()
        if (
            not tts_key
            and llm.get("mode") == "direct"
            and direct.get("provider") == "mimo"
        ):
            tts_key = str(direct.get("api_key") or "")
        if not tts_base:
            if (
                llm.get("mode") == "direct"
                and direct.get("provider") == "mimo"
            ):
                tts_base = str(direct.get("api_base") or "")
            tts_base = tts_base or "https://api.xiaomimimo.com/v1"

        gsv_python = self.tts_page.gsv_dir_input.text().strip()
        if gsv_python and os.path.isdir(gsv_python):
            detected = self.tts_page._find_python_exe(gsv_python)
            if detected:
                gsv_python = detected

        patch = {
            "engine": engine,
            "enabled": self.tts_page.enable_cb.isChecked(),
            "gsv_ref_wav": str(legacy_reference.get("path") or ""),
            "gsv_ref_lang": legacy_language,
            "reference_audios": references,
            "translate_to_jp": (
                self.tts_page.translation_enabled_cb.isChecked()
            ),
            "translate_target_language": translation_target,
            "translate_api_key": self.tts_page.translate_key.text().strip(),
            "python_exe": gsv_python,
            "vits_python": self.tts_page.vits_python_input.text().strip(),
            "api_key": tts_key,
            "api_base": tts_base,
            "voice": self.tts_page.mimo_voice_input.text().strip() or "冰糖",
            "voice_lang": (
                self.tts_page.mimo_voice_lang_combo.currentData() or "jp"
            ),
            "voice_clone": use_clone,
            "clone_ref": self.tts_page.mimo_clone_ref_input.text().strip(),
        }
        model = str(tts.get("model") or "mimo-v2.5-tts")
        if use_clone:
            patch["model"] = "mimo-v2.5-tts-voiceclone"
        elif "voiceclone" in model.lower():
            patch["model"] = "mimo-v2.5-tts"
        tts.update(patch)

    def _collect_conversation_fields(self, config: dict) -> None:
        """覆盖当前模式的表单字段，同时保留非活动侧和扩展字段。"""
        mode = self.backend_page.mode()
        llm = config.setdefault("llm", {})
        existing_direct = (
            llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
        )
        existing_agent = (
            llm.get("agent") if isinstance(llm.get("agent"), dict) else {}
        )

        if mode == "direct":
            direct = self._deep_merge(
                existing_direct,
                self.llm_page.collect_direct_profile(),
            )
            agent = copy.deepcopy(existing_agent)
        else:
            direct = copy.deepcopy(existing_direct)
            agent = self._deep_merge(
                existing_agent,
                self.backend_page.collect_agent(),
            )

        llm["mode"] = mode
        llm["direct"] = direct
        llm["agent"] = agent
        llm["backend"] = (
            str(agent.get("kind") or "hermes")
            if mode == "agent"
            else str(direct.get("provider") or "ollama")
        )
        # 兼容当前 ChatEngine；协议适配层完成后可逐步淡出这些镜像字段。
        llm["host"] = str(direct.get("host") or "")
        llm["api_base"] = str(direct.get("api_base") or "")
        llm["model"] = str(direct.get("model") or "")
        llm["api_key"] = str(direct.get("api_key") or "")
        llm["temperature"] = direct.get("temperature", 0.7)
        llm["max_tokens"] = direct.get("max_tokens", 4096)

        config["agent_control"] = self._deep_merge(
            config.get("agent_control") or {},
            self.backend_page.collect_control(),
        )
        ui = config.setdefault("ui", {})
        ui["timeline_turns"] = self.backend_page.timeline_turns.value()

    def _collect_vision_fields(self, config: dict) -> None:
        """视觉路由只收集一次，并保留页面未识别的扩展字段。"""
        llm = config.get("llm") or {}
        try:
            fragments = self.vision_page.collect(
                str(llm.get("backend") or "ollama"),
                llm,
            )
        except Exception as exc:
            self.env_page.log(f"收集视觉路由失败: {type(exc).__name__}")
            return
        config["vision"] = self._deep_merge(
            config.get("vision") or {},
            fragments.get("vision") or {},
        )
        config["watcher"] = self._deep_merge(
            config.get("watcher") or {},
            fragments.get("watcher") or {},
        )

    def collect_config(self, base_config: dict | None = None) -> dict:
        """把 UI 作为字段补丁应用到现有配置，而不是重建整份配置。"""
        config = self._config_base(base_config)
        self._collect_display_fields(config)
        self._collect_conversation_fields(config)
        self._collect_tts_fields(config)
        self._collect_vision_fields(config)
        return config

    def _save(self):
        try:
            issues = self._configuration_issues()
            missing = [
                item
                for section in issues.values()
                for item in section
            ]
            if missing:
                missing_text = "\n• ".join(missing)
                reply = QMessageBox.question(
                    self,
                    "仍有必要配置未完成",
                    "以下配置尚未就绪：\n"
                    f"• {missing_text}\n\n"
                    "仍要保存吗？运行时可能无法使用对应功能。",
                    QMessageBox.Save | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if reply != QMessageBox.Save:
                    return

            # 以最新磁盘内容为底应用 UI 补丁，外部新增的非 UI 字段不会被旧内存覆盖。
            disk_config = self._read_config_file(self.config_path)
            if disk_config is None:
                disk_config = copy.deepcopy(self._existing_config)
            final_cfg = self.collect_config(base_config=disk_config)

            from meapet.config.store import normalize_config, save_config

            final_cfg = normalize_config(final_cfg)
            save_config(final_cfg, self.config_path)
            self._existing_config = copy.deepcopy(final_cfg)

            self.config_saved.emit(final_cfg)
            if PLATFORM["is_windows"]:
                launch_hint = "现在双击「启动桌宠.bat」或运行 python pet.py 就能开玩啦 🐱"
            elif PLATFORM["is_linux"]:
                launch_hint = "启动：QT_QPA_PLATFORM=xcb python pet.py 🐱"
            else:
                launch_hint = "启动：python pet.py 🐱"
            QMessageBox.information(
                self, "✅ 完成",
                "配置已保存！\n\n"
                f"{launch_hint}\n"
                f"当前平台：{PLATFORM['display']}\n\n"
                "从桌宠菜单打开时：对话、语音、识图和减少动画会立即重新初始化，"
                "无需重启；若新后端启动失败，桌宠会直接报错。\n"
                "字体缩放需要重启桌宠后完整生效；独立打开配置页时，"
                "其它改动会在下次启动时生效。"
            )
            self._dirty = False
            self._closing_after_save = True
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "❌ 保存失败", str(e))


# ═══════════════════════════════════════
# 入口
# ═══════════════════════════════════════

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(PALETTE["canvas"]))
    p.setColor(QPalette.WindowText, QColor(PALETTE["text_primary"]))
    p.setColor(QPalette.Base, QColor(PALETTE["surface_input"]))
    p.setColor(QPalette.AlternateBase, QColor(PALETTE["surface_elevated"]))
    p.setColor(QPalette.Text, QColor(PALETTE["text_primary"]))
    p.setColor(QPalette.Button, QColor(PALETTE["surface_elevated"]))
    p.setColor(QPalette.ButtonText, QColor(PALETTE["text_primary"]))
    p.setColor(QPalette.Highlight, QColor(PALETTE["primary"]))
    p.setColor(QPalette.HighlightedText, QColor(PALETTE["on_primary"]))
    app.setPalette(p)

    w = SetupWizard()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
