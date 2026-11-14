"""配置向导主窗口"""
from __future__ import annotations

import copy
import json
import os
import sys

from PyQt5.QtWidgets import (
    QApplication,
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QRegion,
)
from PyQt5.QtWidgets import QShortcut

from wizard.platform_info import CONFIG_PATH, PLATFORM
from meapet.config.defaults import (
    DEFAULT_MEA_PET_MIMO_API_BASE,
    DEFAULT_MIMO_TTS_CLONE_MODEL,
    DEFAULT_MIMO_TTS_MODEL,
    DEFAULT_WATCHER_INTERVAL,
)
from wizard.styles import (
    WIZARD_STYLESHEET,
    prepare_accessible_page,
    set_status,
    styled_message_box,
)
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    PALETTE,
    PET_SIZE_FACTOR_DEFAULT,
    PET_SIZE_FACTOR_MAX,
    PET_SIZE_FACTOR_MIN,
    PET_SIZE_FACTOR_STEP,
    RADIUS_LARGE,
    UI_FONT_SCALE_DEFAULT,
    UI_THEME_MODE_DEFAULT,
    apply_ui_font_scale,
    ensure_application_fonts,
    get_ui_theme_mode,
    normalize_pet_size_factor,
    normalize_theme_mode,
    normalize_ui_font_scale,
    apply_named_style,
    get_text_color,
    normalize_text_color,
    rebuild_theme_styles,
    set_text_color,
    set_ui_font_scale,
    set_ui_theme_mode,
)
from wizard.pages import (
    BackendPage,
    EnvCheckPage,
    LLMPage,
    OneBotPage,
    TTSPage,
    VisionPage,
    VoiceInputPage,
)
from wizard.side_nav import SideNavTabs
from wizard.live2d_viewport import Live2DViewportSettings
from meapet.desktop.screen_geometry import (
    available_geometry_for,
    clamp_position,
)
from meapet.window_state import (
    load_wizard_geometry,
    save_wizard_geometry,
    state_path_for_config,
)


class SetupWizard(QWidget):
    config_saved = pyqtSignal(dict)

    TAB_ENV = 0
    TAB_LIVE2D = 1
    TAB_CHAT = 2
    TAB_VOICE = 3
    TAB_VISION = 4
    TAB_VOICE_INPUT = 5
    TAB_ONEBOT = 6

    _WINDOW_RESIZE_MARGIN = 10
    _WINDOW_STATE_SAVE_DELAY_MS = 250

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
        live2d_preview=None,
        window_state_path: str | os.PathLike[str] | None = None,
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
        self._window_state_path = (
            os.fspath(window_state_path)
            if window_state_path is not None
            else state_path_for_config(
                self.config_path,
                "wizard_window_state.json",
            )
        )
        self._window_state_ready = False
        self._restored_window_position: QPoint | None = None
        self._last_normal_geometry = QRect()
        self._window_state_save_timer = QTimer(self)
        self._window_state_save_timer.setSingleShot(True)
        self._window_state_save_timer.setInterval(
            self._WINDOW_STATE_SAVE_DELAY_MS
        )
        self._window_state_save_timer.timeout.connect(
            self._save_window_state
        )
        self._dirty = False
        self._suppress_dirty = False
        self._closing_after_save = False
        self._connection_test_jobs = {}
        ensure_application_fonts()
        self.setWindowTitle(f"MeaPet 配置 — {PLATFORM['os_label']}")
        self.setObjectName("WizardRoot")
        self.setMinimumSize(820, 640)
        self.resize(1000, 800)
        # Keep the root opaque so the desktop pet cannot show through, but use
        # the shared canvas token instead of painting a second, off-palette
        # frame around the rounded shell.
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(PALETTE["canvas"]))
        self.setPalette(palette)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        # 保留系统窗口能力，同时在自定义顶栏提供更易发现的最大化入口。
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        # Start invisible to avoid showing a blank frame before the
        # dark-themed UI is fully painted.  Fade in once the event loop runs.
        self.setWindowOpacity(0.0)
        # 首次构造就使用持久化字号对应的 QSS。若先应用 100% 原始样式，
        # 再在窗口显示前替换为缩放版本，Qt 5 会保留部分子控件的旧字体缓存：
        # 滑块虽显示保存值，界面仍按 100% 绘制，直到用户再次移动滑块。
        apply_named_style(
            self,
            "WIZARD_STYLESHEET",
            scale=initial_font_scale,
        )
        self.setAccessibleName("MeaPet 配置")
        self.setAccessibleDescription(
            "使用标签页配置环境、Live2D、对话、语音、屏幕识图和语音输入功能"
        )

        outer = QVBoxLayout(self)
        # One pixel leaves room for the shell stroke without turning the
        # frameless window background into a thick square border.
        outer.setContentsMargins(1, 1, 1, 1)

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

        self.maximize_btn = QPushButton("最大化")
        self.maximize_btn.setObjectName("SecondaryButton")
        self.maximize_btn.setMinimumHeight(MIN_TARGET_SIZE)
        self.maximize_btn.setToolTip("最大化配置窗口（也可双击顶栏）")
        self.maximize_btn.setAccessibleName("最大化配置窗口")
        self.maximize_btn.setAccessibleDescription(
            "在最大化和上一次普通窗口大小之间切换"
        )
        self.maximize_btn.setProperty("doesNotModifyConfig", True)
        self.maximize_btn.clicked.connect(self._toggle_maximized)
        top.addWidget(self.maximize_btn)

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
        self.live2d_viewport_settings = Live2DViewportSettings(
            preview=live2d_preview,
        )
        self.live2d_viewport_settings.changed.connect(self._mark_dirty)
        self.backend_page = BackendPage()
        self.llm_page = LLMPage()
        self.tts_page = TTSPage()
        self.vision_page = VisionPage()
        self.voice_input_page = VoiceInputPage()
        self.onebot_page = OneBotPage()

        for page in (
            self.env_page,
            self.display_page,
            self.live2d_viewport_settings,
            self.backend_page,
            self.llm_page,
            self.tts_page,
            self.vision_page,
            self.voice_input_page,
            self.onebot_page,
        ):
            prepare_accessible_page(page)

        self._existing_config = {}
        self._missing_icon = self._build_missing_icon()

        # 右栏顶部的必要配置状态行；挂在侧边导航组件的内容列上方。
        status_bar = QFrame()
        status_bar.setObjectName("ConfigStatusBar")
        status_row = QHBoxLayout(status_bar)
        status_row.setContentsMargins(20, 12, 24, 4)
        self.config_status = QLabel("正在检查必要配置…")
        self.config_status.setObjectName("ConfigStatus")
        self.config_status.setWordWrap(True)
        self.config_status.setAccessibleName("必要配置状态")
        status_row.addWidget(self.config_status, 1)

        self.tabs = SideNavTabs()
        self.tabs.setObjectName("ConfigurationTabs")
        self.tabs.setAccessibleName("配置分类")
        self.tabs.setAccessibleDescription("带橙色圆点的分类缺少必要配置，并有文字提示")
        self.tabs.list.setAccessibleName(
            "环境、Live2D、对话、语音、屏幕识图、语音输入和 OneBot 分类"
        )
        self.tabs.list.setAccessibleDescription(
            "橙色圆点表示该分类仍缺少必要配置；具体原因显示在分类提示和顶部状态中"
        )
        self.tabs.set_top_widget(status_bar)
        self.tabs.addTab(
            self._make_scroll_tab(self.display_page, self.env_page),
            "环境",
        )
        self.tabs.addTab(
            self._make_scroll_tab(self.live2d_viewport_settings),
            "Live2D",
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
        self.tabs.addTab(self._make_scroll_tab(self.voice_input_page), "语音输入")
        self.tabs.addTab(self._make_scroll_tab(self.onebot_page), "OneBot")
        main.addWidget(self.tabs, 1)

        # 底部按钮
        footer = QFrame()
        footer.setObjectName("WizardFooter")
        btns = QHBoxLayout(footer)
        btns.setContentsMargins(24, 12, 18, 18)
        btns.setSpacing(12)

        footer_hint = QLabel(
            "先完成「环境」和「对话」即可开玩；语音、屏幕识图与语音输入"
            "可稍后设置。设置仅保存在本机。"
        )
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

        main.addWidget(footer)

        self._close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._close_shortcut.activated.connect(self.close)
        self.setTabOrder(self.tabs, self.save_btn)
        self.setTabOrder(self.save_btn, self.maximize_btn)
        self.setTabOrder(self.maximize_btn, self.close_btn)

        # 窗口拖拽
        self._drag = None
        for w in [header, brand_mark, brand_name]:
            w.mousePressEvent = lambda e: self._drag_start(e)
            w.mouseMoveEvent = lambda e: self._drag_move(e)
            w.mouseReleaseEvent = lambda e: setattr(self, '_drag', None)
            w.mouseDoubleClickEvent = lambda e: self._header_double_click(e)

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
        self._restore_window_state()
        self._install_window_resize_filters()
        self._window_state_ready = True
        if not self.isMaximized() and not self.isFullScreen():
            self._last_normal_geometry = QRect(self.pos(), self.size())
        self._apply_rounded_window_mask()
        self._sync_window_state_controls()

        # Use an owned QTimer to defer the fade-in until after the event
        # loop has painted the dark-themed UI once.
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(lambda: self._fade_in() if self.isVisible() else None)
        self._fade_timer.start(0)

    def _build_display_settings(
        self,
        initial_scale: float,
    ) -> QFrame:
        """创建字体、桌宠尺寸与动画偏好设置卡。"""
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
            "调整配置界面字体和桌宠整体尺寸。字体在配置页即时预览，"
            "并在桌宠重启后应用；尺寸与动画偏好保存后立即应用。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(14)
        theme_label = QLabel("界面主题")
        theme_label.setObjectName("FieldLabel")
        theme_label.setMinimumWidth(112)
        theme_row.addWidget(theme_label)

        self.theme_mode_group = QButtonGroup(self)
        self._theme_radios: dict[str, QRadioButton] = {}
        initial_mode = get_ui_theme_mode()
        for mode, theme_label_text, theme_desc in (
            ("light", "浅色", "始终使用浅色界面"),
            ("dark", "深色", "始终使用深色界面"),
        ):
            radio = QRadioButton(theme_label_text)
            radio.setObjectName("ThemeModeRadio")
            radio.setChecked(mode == initial_mode)
            radio.setAccessibleName(f"界面主题：{theme_label_text}")
            radio.setAccessibleDescription(theme_desc)
            self.theme_mode_group.addButton(radio)
            self._theme_radios[mode] = radio
            theme_row.addWidget(radio)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        theme_hint = QLabel(
            "界面深浅色只按这里的选项决定，不再读取系统的浅色/深色偏好；"
            "切换后配置页即时生效，桌宠的菜单、气泡与面板同步刷新，"
            "个别已打开的窗口重新打开后应用。"
            "字色默认白色；选「浅色」时白字配浅底读不出来，会自动回落到该方案自带的深色字。"
        )
        theme_hint.setObjectName("HelperText")
        theme_hint.setWordWrap(True)
        layout.addWidget(theme_hint)

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

        pet_row = QHBoxLayout()
        pet_row.setSpacing(12)
        pet_label = QLabel("桌宠窗口大小")
        pet_label.setObjectName("FieldLabel")
        pet_label.setMinimumWidth(112)
        pet_row.addWidget(pet_label)

        self.pet_size_slider = QSlider(Qt.Horizontal)
        self.pet_size_slider.setObjectName("PetSizeSlider")
        self.pet_size_slider.setRange(
            round(PET_SIZE_FACTOR_MIN * 100),
            round(PET_SIZE_FACTOR_MAX * 100),
        )
        self.pet_size_slider.setSingleStep(round(PET_SIZE_FACTOR_STEP * 100))
        self.pet_size_slider.setPageStep(10)
        self.pet_size_slider.setTracking(True)
        self.pet_size_slider.setValue(round(PET_SIZE_FACTOR_DEFAULT * 100))
        self.pet_size_slider.setAccessibleName("桌宠窗口大小")
        self.pet_size_slider.setAccessibleDescription(
            "可在百分之三十到百分之三百之间调整桌宠窗口与立绘的大小"
        )
        pet_row.addWidget(self.pet_size_slider, 1)

        self.pet_size_value = QLabel(f"{self.pet_size_slider.value()}%")
        self.pet_size_value.setObjectName("PetSizeValue")
        self.pet_size_value.setMinimumWidth(52)
        self.pet_size_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pet_size_value.setAccessibleName("当前桌宠窗口大小")
        pet_row.addWidget(self.pet_size_value)
        layout.addLayout(pet_row)

        pet_hint = QLabel(
            "100% 为立绘原始大小；保存后桌宠立即按新比例缩放，"
            "也可在桌宠右键菜单「显示与立绘 · 调整窗口大小…」里实时预览。"
        )
        pet_hint.setObjectName("HelperText")
        pet_hint.setWordWrap(True)
        layout.addWidget(pet_hint)

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
        self.pet_size_slider.valueChanged.connect(
            self._on_pet_size_changed
        )
        for mode, radio in self._theme_radios.items():
            radio.toggled.connect(
                lambda checked, m=mode: self._on_theme_mode_changed(m, checked)
            )
        return card

    def _on_font_scale_changed(self, value: int) -> None:
        value = int(value)
        self.font_scale_value.setText(f"{value}%")
        apply_ui_font_scale(self, value / 100.0)

    def _on_theme_mode_changed(self, mode: str, checked: bool) -> None:
        """切换界面主题：即时重建样式并标记配置待保存。"""
        if not checked:
            return
        set_ui_theme_mode(mode)
        self._mark_dirty()

    def _on_pet_size_changed(self, value: int) -> None:
        self.pet_size_value.setText(f"{int(value)}%")

    def _install_window_resize_filters(self) -> None:
        """让顶层窗口及全部现有子控件都能命中四边和四角。"""
        self._resize_cursor_widget: QWidget | None = None
        for widget in (self, *self.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

    def _resize_edges_at(self, point: QPoint) -> Qt.Edges:
        """返回窗口本地坐标命中的系统缩放边；中央区域返回空。"""
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        if not self.rect().contains(point):
            return Qt.Edges()

        margin = self._WINDOW_RESIZE_MARGIN
        edges = Qt.Edges()
        if point.x() < margin:
            edges |= Qt.LeftEdge
        elif point.x() >= self.width() - margin:
            edges |= Qt.RightEdge
        if point.y() < margin:
            edges |= Qt.TopEdge
        elif point.y() >= self.height() - margin:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_resize_edges(edges: Qt.Edges):
        if edges in (
            Qt.LeftEdge | Qt.TopEdge,
            Qt.RightEdge | Qt.BottomEdge,
        ):
            return Qt.SizeFDiagCursor
        if edges in (
            Qt.RightEdge | Qt.TopEdge,
            Qt.LeftEdge | Qt.BottomEdge,
        ):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges & (Qt.TopEdge | Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def _set_resize_cursor(
        self,
        widget: QWidget | None,
        edges: Qt.Edges,
    ) -> None:
        previous = getattr(self, "_resize_cursor_widget", None)
        if previous is not None and previous is not widget:
            try:
                previous.unsetCursor()
            except RuntimeError:
                pass
            self._resize_cursor_widget = None

        if widget is not None and edges:
            widget.setCursor(self._cursor_for_resize_edges(edges))
            self._resize_cursor_widget = widget
        elif previous is widget and previous is not None:
            try:
                previous.unsetCursor()
            except RuntimeError:
                pass
            self._resize_cursor_widget = None

    def _start_system_resize(self, edges: Qt.Edges) -> bool:
        """把拖动交给窗口系统，获得与普通窗口相同的缩放行为。"""
        if not edges or self.isMaximized() or self.isFullScreen():
            return False
        handle = self.windowHandle()
        if handle is None:
            try:
                self.winId()
            except RuntimeError:
                return False
            handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemResize(edges))
        except (AttributeError, RuntimeError, TypeError):
            return False

    def eventFilter(self, watched, event) -> bool:
        """优先识别窗口边缘，再把其余鼠标事件交还原控件。"""
        event_type = event.type()
        if event_type in (QEvent.MouseMove, QEvent.MouseButtonPress):
            if isinstance(watched, QWidget):
                local = QPoint(event.pos())
                if watched is not self:
                    local = watched.mapTo(self, local)
                edges = self._resize_edges_at(local)
                self._set_resize_cursor(watched, edges)
                if (
                    event_type == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and edges
                ):
                    self._drag = None
                    if self._start_system_resize(edges):
                        event.accept()
                        return True
        elif event_type == QEvent.Leave and watched is getattr(
            self,
            "_resize_cursor_widget",
            None,
        ):
            self._set_resize_cursor(watched, Qt.Edges())
        return super().eventFilter(watched, event)

    def _restore_window_state(self) -> bool:
        """恢复上一次普通窗口几何，并记住是否处于最大化状态。"""
        state = load_wizard_geometry(self._window_state_path)
        if state is None:
            return False

        saved_position = QPoint(int(state["x"]), int(state["y"]))
        self._restored_window_position = QPoint(saved_position)
        self.resize(int(state["width"]), int(state["height"]))

        area = available_geometry_for(
            QRect(saved_position, QSize(self.width(), self.height()))
        )
        position = saved_position
        if area is not None:
            position = clamp_position(
                saved_position,
                self.size(),
                area,
                margin=0,
            )
        self.move(position)
        self._last_normal_geometry = QRect(position, self.size())

        if bool(state.get("maximized", False)):
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
        return True

    def _remember_normal_geometry(self) -> None:
        if not self.isMaximized() and not self.isFullScreen():
            # QWidget.geometry() may be offset by the platform frame even
            # after move(x, y); pos() is the user-visible top-level position.
            geometry = QRect(self.pos(), self.size())
            if geometry.isValid():
                self._last_normal_geometry = geometry

    def _schedule_window_state_save(self) -> None:
        if getattr(self, "_window_state_ready", False):
            self._window_state_save_timer.start()

    def _save_window_state(self) -> bool:
        """保存普通几何；最大化尺寸不会覆盖用户调好的窗口尺寸。"""
        maximized = self.isMaximized() or self.isFullScreen()
        if not maximized:
            self._remember_normal_geometry()
        geometry = QRect(self._last_normal_geometry)
        if not geometry.isValid():
            geometry = QRect(self.normalGeometry())
        if not geometry.isValid():
            geometry = QRect(self.geometry())
        return save_wizard_geometry(
            self._window_state_path,
            {
                "x": geometry.x(),
                "y": geometry.y(),
                "width": geometry.width(),
                "height": geometry.height(),
                "maximized": maximized,
            },
        )

    def _toggle_maximized(self, _checked: bool = False) -> None:
        """在最大化与用户上一次设置的普通窗口大小之间切换。"""
        self._drag = None
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        else:
            self._remember_normal_geometry()
            self.showMaximized()
        self._sync_window_state_controls()
        self._apply_rounded_window_mask()
        self._schedule_window_state_save()

    def _header_double_click(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        event.ignore()

    def _sync_window_state_controls(self) -> None:
        maximized = self.isMaximized() or self.isFullScreen()
        if hasattr(self, "maximize_btn"):
            if maximized:
                self.maximize_btn.setText("还原窗口")
                self.maximize_btn.setToolTip("还原到上一次配置窗口大小")
                self.maximize_btn.setAccessibleName("还原配置窗口")
            else:
                self.maximize_btn.setText("最大化")
                self.maximize_btn.setToolTip("最大化配置窗口（也可双击顶栏）")
                self.maximize_btn.setAccessibleName("最大化配置窗口")
    def _apply_rounded_window_mask(self) -> None:
        """让顶层无边框窗口的真实区域与圆角 Shell 一致。"""
        if (
            self.isMaximized()
            or self.isFullScreen()
            or self.width() <= 1
            or self.height() <= 1
        ):
            self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.0, 0.0, -0.5, -0.5),
            RADIUS_LARGE,
            RADIUS_LARGE,
        )
        rounded = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(rounded.intersected(QRegion(self.rect())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_rounded_window_mask()
        self._remember_normal_geometry()
        self._schedule_window_state_save()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._remember_normal_geometry()
        self._schedule_window_state_save()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_window_state_controls()
            self._apply_rounded_window_mask()
            self._schedule_window_state_save()

    def _fade_in(self) -> None:
        """Gradually restore opacity so the window doesn't flash from transparent."""
        try:
            from PyQt5.QtCore import QPropertyAnimation
            anim = QPropertyAnimation(self, b"windowOpacity")
            anim.setDuration(180)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(1.0)
            anim.start()
            self._fade_anim = anim  # keep a reference to prevent GC
        except Exception:
            self.setWindowOpacity(1.0)

    @property
    def is_dirty(self) -> bool:
        """配置页是否含尚未保存的用户编辑。"""
        return bool(self._dirty)

    def _mark_dirty(self, *_args) -> None:
        if not self._suppress_dirty:
            self._dirty = True
            if hasattr(self, "save_btn") and self.save_btn is not None:
                self.save_btn.setText("保存配置 *")
                self.save_btn.setToolTip("有未保存更改，点击写入本机配置")

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
            reply = styled_message_box(
                self,
                title="放弃未保存的更改？",
                text="当前配置尚未保存。关闭后这些更改会丢失。",
                icon=QMessageBox.Warning,
                buttons=QMessageBox.Discard | QMessageBox.Cancel,
                default_button=QMessageBox.Cancel,
            )
            if reply != QMessageBox.Discard:
                event.ignore()
                return
        self._dirty = False
        self._cancel_connection_tests()
        self._window_state_save_timer.stop()
        self._save_window_state()
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
        """带底环的警示圆点：在侧边导航选中/未选中项上都保持清晰边界。"""
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(10, 10, 10))
        painter.drawEllipse(QRectF(0.5, 0.5, 11.0, 11.0))
        painter.setBrush(QColor(PALETTE["danger"]))
        painter.drawEllipse(QRectF(2.0, 2.0, 8.0, 8.0))
        painter.end()
        return QIcon(pixmap)

    def _connect_required_field_updates(self) -> None:
        self.env_page.requirements_changed.connect(
            self._refresh_required_tabs
        )
        self.backend_page.direct_radio.toggled.connect(
            self._on_conversation_mode_changed
        )
        self.backend_page.agent_radio.toggled.connect(
            self._on_conversation_mode_changed
        )
        self.backend_page.agent_base_url.textChanged.connect(
            self._refresh_required_tabs
        )
        self.backend_page.agent_kind.currentIndexChanged.connect(
            self._refresh_required_tabs
        )
        self.llm_page.endpoint_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.llm_page.model_combo.currentTextChanged.connect(
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
        for widget in (
            self.onebot_page.enabled_cb,
            self.onebot_page.forward_enabled,
            self.onebot_page.reverse_enabled,
            self.onebot_page.http_api_enabled,
            self.onebot_page.http_post_enabled,
            self.onebot_page.hub_enabled,
            self.onebot_page.allow_private,
            self.onebot_page.allow_groups,
            self.onebot_page.reverse_allow_insecure_remote,
            self.onebot_page.http_post_allow_insecure_remote,
            self.onebot_page.hub_allow_insecure_remote,
        ):
            widget.toggled.connect(self._refresh_required_tabs)
        for widget in (
            self.onebot_page.forward_url,
            self.onebot_page.http_api_base,
            self.onebot_page.reverse_host,
            self.onebot_page.http_post_host,
            self.onebot_page.hub_host,
        ):
            widget.textChanged.connect(self._refresh_required_tabs)

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
            self.TAB_LIVE2D: [],
            self.TAB_CHAT: [],
            self.TAB_VOICE: [],
            self.TAB_VISION: [],
            self.TAB_VOICE_INPUT: [],
            self.TAB_ONEBOT: [],
        }
        issues[self.TAB_ENV].extend(self.env_page.required_missing())

        conversation_mode = self.backend_page.mode()
        from meapet.config.store import detect_endpoint_family

        llm_endpoint = self.llm_page.endpoint_input.text().strip()
        llm_family = detect_endpoint_family(llm_endpoint)
        llm_key = self.llm_page.direct_api_key_input.text().strip()
        if conversation_mode == "agent":
            agent_endpoint = self.backend_page.agent_base_url.text().strip()
            if not agent_endpoint:
                issues[self.TAB_CHAT].append("Agent 地址")
            else:
                from urllib.parse import urlsplit

                parsed_agent = urlsplit(agent_endpoint)
                if parsed_agent.scheme.lower() not in {"ws", "wss"}:
                    issues[self.TAB_CHAT].append(
                        "Agent 地址须使用 WS 或 WSS"
                    )
                elif (
                    parsed_agent.scheme.lower() == "ws"
                    and (parsed_agent.hostname or "").lower()
                    not in {"127.0.0.1", "::1", "localhost"}
                    and not self.backend_page.agent_allow_insecure_ws.isChecked()
                ):
                    issues[self.TAB_CHAT].append(
                        "远程明文 WS 需明确允许，或改用 WSS"
                    )
            if not self.backend_page.agent_auth_token.text().strip():
                issues[self.TAB_CHAT].append("Agent WebSocket 访问令牌")
            if (
                self.backend_page.agent_kind.currentData() != "agent_link"
                and self.backend_page.control_enabled.isChecked()
            ):
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
            if not llm_endpoint:
                issues[self.TAB_CHAT].append("API 地址")
            if not self.llm_page.model_combo.currentText().strip():
                issues[self.TAB_CHAT].append("模型 ID")

        if (
            self.tts_page.enable_cb.isChecked()
            and self.tts_page.backend_combo.currentData() == "mimo"
        ):
            tts_key = self.tts_page.mimo_api_key_input.text().strip()
            if (
                not tts_key
                and conversation_mode == "direct"
                and llm_family == "mimo"
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
                    endpoint = llm_endpoint
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
                        llm_family
                        if llm_family in {"ollama", "mimo"}
                        else "ollama"
                    )
                if actual_backend == "mimo":
                    if not self.vision_page.allow_cloud_cb.isChecked():
                        issues[self.TAB_VISION].append("云端识图授权")
                    vision_key = self.vision_page.api_key_input.text().strip()
                    if not vision_key and llm_family == "mimo":
                        vision_key = llm_key
                    if not vision_key:
                        issues[self.TAB_VISION].append("云端识图 API Key")

        try:
            from meapet.onebot.config import validate_onebot_config

            issues[self.TAB_ONEBOT].extend(
                validate_onebot_config(self.onebot_page.collect())
            )
        except Exception as exc:
            self.env_page.log(f"检查 OneBot 配置失败: {type(exc).__name__}")
        return issues

    def _refresh_required_tabs(self, *_args) -> None:
        issues = self._configuration_issues()
        labels = {
            self.TAB_ENV: "环境",
            self.TAB_LIVE2D: "Live2D",
            self.TAB_CHAT: "对话",
            self.TAB_VOICE: "语音",
            self.TAB_VISION: "屏幕识图",
            self.TAB_ONEBOT: "OneBot",
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
            live2d = (
                cfg.get("live2d", {})
                if isinstance(cfg.get("live2d"), dict)
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
            self.pet_size_slider.setValue(
                round(
                    normalize_pet_size_factor(display.get("size_factor", 1.0))
                    * 100
                )
            )
            self.reduced_motion_cb.setChecked(
                bool(display.get("reduced_motion", False))
            )
            # 仅当配置显式写入这两格时才同步；缺省时保留运行中的那一份，
            # 不能被"配置里没写"冲回入口预设或默认档。
            ui_cfg = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
            raw_theme = ui_cfg.get("theme")
            raw_text_color = ui_cfg.get("text_color")
            text_pending = bool(raw_text_color) and (
                normalize_text_color(raw_text_color) != get_text_color()
            )
            if text_pending:
                set_text_color(raw_text_color, rebuild_styles=False)
            theme_changed = False
            if raw_theme:
                theme_mode = normalize_theme_mode(raw_theme)
                radio = self._theme_radios.get(theme_mode)
                if radio is not None and not radio.isChecked():
                    radio.setChecked(True)
                if get_ui_theme_mode() != theme_mode:
                    set_ui_theme_mode(theme_mode)
                    theme_changed = True
            if text_pending and not theme_changed:
                rebuild_theme_styles()
            self.live2d_viewport_settings.set_fallback_canvas_size(
                live2d.get("default_canvas_size")
            )
            self.live2d_viewport_settings.set_window_mask(
                live2d.get("window_mask")
            )
            self.live2d_viewport_settings.set_placement_anchor(
                live2d.get("placement_anchor")
            )
            self.live2d_viewport_settings.set_window_shape(
                live2d.get("window_shape")
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
                        "min_ms": int(
                            watcher.get(
                                "min_ms",
                                DEFAULT_WATCHER_INTERVAL["min_ms"],
                            )
                        ),
                        "max_ms": int(
                            watcher.get(
                                "max_ms",
                                DEFAULT_WATCHER_INTERVAL["max_ms"],
                            )
                        ),
                    }
                self.vision_page.apply_config(vision, watcher)
            except Exception as exc:
                self.env_page.log(f"恢复识图配置失败: {exc}")

            try:
                self.voice_input_page.apply_config(
                    cfg.get("voice_input") or {},
                )
            except Exception as exc:
                self.env_page.log(f"恢复语音输入配置失败: {exc}")

            try:
                self.onebot_page.apply_config(cfg.get("onebot") or {})
                self.env_page.log(
                    f"已恢复 OneBot（QQ 通道）配置：{self.onebot_page.summary()}"
                )
            except Exception as exc:
                self.env_page.log(f"恢复 OneBot 配置失败: {exc}")

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
            if hasattr(self, "save_btn") and self.save_btn is not None:
                self.save_btn.setText("保存配置")
                self.save_btn.setToolTip("保存当前所有标签页中的配置")
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
        if e.button() == Qt.LeftButton and not self.isMaximized():
            self._drag = e.globalPos()

    def _drag_move(self, e):
        if self._drag and not self.isMaximized():
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
        """显示页只负责它实际展示的几个选项，其余 display 字段原样保留。"""
        display = config.setdefault("display", {})
        display["font_scale"] = self.font_scale_slider.value() / 100.0
        display["size_factor"] = normalize_pet_size_factor(
            self.pet_size_slider.value() / 100.0
        )
        display["reduced_motion"] = self.reduced_motion_cb.isChecked()
        ui = config.setdefault("ui", {})
        ui["theme"] = next(
            (
                mode
                for mode, radio in self._theme_radios.items()
                if radio.isChecked()
            ),
            UI_THEME_MODE_DEFAULT,
        )

    def _collect_live2d_viewport_fields(self, config: dict) -> None:
        """Live2D 页补丁视口、锚点和形状，其余字段原样保留。"""
        live2d = config.get("live2d")
        if not isinstance(live2d, dict):
            live2d = {}
            config["live2d"] = live2d
        live2d["window_mask"] = self.live2d_viewport_settings.window_mask()
        live2d["placement_anchor"] = (
            self.live2d_viewport_settings.placement_anchor()
        )
        live2d["window_shape"] = (
            self.live2d_viewport_settings.window_shape()
        )

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
        from meapet.config.store import detect_endpoint_family

        tts_key = self.tts_page.mimo_api_key_input.text().strip()
        tts_base = self.tts_page.mimo_api_base_input.text().strip()
        llm_is_mimo = (
            llm.get("mode") == "direct"
            and detect_endpoint_family(
                direct.get("api_base"),
                direct.get("host"),
                llm.get("api_base"),
                llm.get("host"),
            )
            == "mimo"
        )
        if not tts_key and llm_is_mimo:
            tts_key = str(direct.get("api_key") or "")
        if not tts_base:
            if llm_is_mimo:
                tts_base = str(direct.get("api_base") or "")
            tts_base = tts_base or DEFAULT_MEA_PET_MIMO_API_BASE

        gsv_python = self.tts_page.gsv_dir_input.text().strip()
        if gsv_python and os.path.isdir(gsv_python):
            detected = self.tts_page._find_python_exe(gsv_python)
            if detected:
                gsv_python = detected

        # vits_python 落盘前先校验：resolve_external_python 对空串 / pet exe /
        # 不在盘上三种输入都判为"没配"，以前这里无条件落盘，用户填完保存却
        # 一个读数都拿不到。存还是照存（不夺走用户输入），但当场出声。
        if hasattr(self.tts_page, "_report_vits_python_for_save"):
            self.tts_page._report_vits_python_for_save()

        # P1-4：ONNX 引擎目录。留空且选了 ONNX 时回落默认目录（冻结版随包
        # 模型就在那里），让「选引擎 + 保存」开箱即用；其余引擎保留原值。
        onnx_dir = (
            self.tts_page.onnx_model_dir_input.text().strip()
            if hasattr(self.tts_page, "onnx_model_dir_input") else ""
        )
        if (
            not onnx_dir
            and engine == "onnx"
            and hasattr(self.tts_page, "_onnx_effective_dir")
        ):
            onnx_dir = self.tts_page._onnx_effective_dir()
        onnx_dir = onnx_dir or str(tts.get("onnx_model_dir") or "").strip()

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
            "prefer_model_voice_translation": bool(
                hasattr(self.tts_page, "prefer_model_voice_cb")
                and self.tts_page.prefer_model_voice_cb.isChecked()
            ),
            "python_exe": gsv_python,
            "vits_python": self.tts_page.vits_python_input.text().strip(),
            # P0-2：外置运行时包落盘事实（安装成功时由 _download_vits_pack 写入）
            "vits_pack_dir": str(
                getattr(self.tts_page, "_vits_pack_dir", "")
                or tts.get("vits_pack_dir") or ""
            ).strip(),
            "vits_pack_url": (
                self.tts_page.vits_pack_url_input.text().strip()
                if hasattr(self.tts_page, "vits_pack_url_input") else ""
            ),
            "onnx_model_dir": onnx_dir,
            "onnx_speaker": (
                self.tts_page._onnx_speaker_value()
                if hasattr(self.tts_page, "_onnx_speaker_value")
                else tts.get("onnx_speaker", 0)
            ),
            "onnx_speed": (
                self.tts_page._onnx_speed_value()
                if hasattr(self.tts_page, "_onnx_speed_value")
                else tts.get("onnx_speed", 1.0)
            ),
            "api_key": tts_key,
            "api_base": tts_base,
            "voice": self.tts_page.mimo_voice_input.text().strip() or "冰糖",
            "voice_lang": (
                self.tts_page.mimo_voice_lang_combo.currentData() or "jp"
            ),
            "voice_clone": use_clone,
            "clone_ref": self.tts_page.mimo_clone_ref_input.text().strip(),
        }
        model = str(tts.get("model") or DEFAULT_MIMO_TTS_MODEL)
        if use_clone:
            patch["model"] = DEFAULT_MIMO_TTS_CLONE_MODEL
        elif "voiceclone" in model.lower():
            patch["model"] = DEFAULT_MIMO_TTS_MODEL
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
            else "custom"
        )
        if mode == "direct":
            direct["provider"] = "custom"
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

    def _collect_voice_input_fields(self, config: dict) -> None:
        try:
            fragments = self.voice_input_page.collect()
        except Exception as exc:
            self.env_page.log(f"收集语音输入配置失败: {type(exc).__name__}")
            return
        config["voice_input"] = self._deep_merge(
            config.get("voice_input") or {},
            fragments.get("voice_input") or {},
        )

    def collect_config(self, base_config: dict | None = None) -> dict:
        """把 UI 作为字段补丁应用到现有配置，而不是重建整份配置。"""
        config = self._config_base(base_config)
        self._collect_display_fields(config)
        self._collect_live2d_viewport_fields(config)
        self._collect_conversation_fields(config)
        self._collect_tts_fields(config)
        self._collect_vision_fields(config)
        self._collect_voice_input_fields(config)
        config["onebot"] = self._deep_merge(
            config.get("onebot") or {},
            self.onebot_page.collect(),
        )
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
                reply = styled_message_box(
                    self,
                    title="仍有必要配置未完成",
                    text=(
                        "以下配置尚未就绪：\n"
                        f"• {missing_text}\n\n"
                        "仍要保存吗？运行时可能无法使用对应功能。"
                    ),
                    icon=QMessageBox.Warning,
                    buttons=QMessageBox.Save | QMessageBox.Cancel,
                    default_button=QMessageBox.Cancel,
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
            else:
                launch_hint = "启动：python pet.py 🐱"
            styled_message_box(
                self,
                title="配置已保存",
                text=(
                    "配置已保存！\n\n"
                    f"{launch_hint}\n"
                    f"当前平台：{PLATFORM['display']}\n\n"
                    "从桌宠菜单打开时：对话、语音、识图和减少动画会立即重新初始化，"
                    "无需重启；若新后端启动失败，桌宠会直接报错。\n"
                    "字体缩放需要重启桌宠后完整生效；独立打开配置页时，"
                    "其它改动会在下次启动时生效。"
                ),
                icon=QMessageBox.Information,
            )
            self._dirty = False
            if hasattr(self, "save_btn") and self.save_btn is not None:
                self.save_btn.setText("保存配置")
                self.save_btn.setToolTip("保存当前所有标签页中的配置")
            self._closing_after_save = True
            self.close()
        except Exception as e:
            styled_message_box(
                self,
                title="保存失败",
                text=str(e),
                icon=QMessageBox.Critical,
            )


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
