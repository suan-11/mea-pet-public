"""配置向导主窗口"""
from __future__ import annotations

import json
import os
import sys
import traceback

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QFrame, QScrollArea, QSizeGrip, QSizePolicy, QTabWidget,
    QSlider, QCheckBox,
)
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PyQt5.QtWidgets import QShortcut

from wizard.platform_info import PLATFORM, CONFIG_PATH, detect_platform
from wizard.styles import (
    COLOR_BG,
    COLOR_ELEVATED,
    COLOR_TEXT,
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
    EnvCheckPage, LLMPage, ApiKeyPage, TTSPage, VisionPage,
)

class SetupWizard(QWidget):
    TAB_ENV = 0
    TAB_CHAT = 1
    TAB_VOICE = 2
    TAB_VISION = 3

    @staticmethod
    def _read_initial_font_scale() -> float:
        """只读取显示字号，避免配置页首次绘制后再发生字体跳变。"""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as file:
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
        return UI_FONT_SCALE_DEFAULT

    def __init__(self):
        initial_font_scale = self._read_initial_font_scale()
        set_ui_font_scale(initial_font_scale)
        super().__init__()
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
        self.llm_page = LLMPage()
        self.key_page_ds = ApiKeyPage(self, backend="deepseek")
        self.key_page_mimo = ApiKeyPage(self, backend="mimo")
        self.tts_page = TTSPage()
        self.vision_page = VisionPage()

        for page in (
            self.env_page,
            self.display_page,
            self.llm_page,
            self.key_page_ds,
            self.key_page_mimo,
            self.tts_page,
            self.vision_page,
        ):
            prepare_accessible_page(page)

        # 当前显示的 key_page 引用（指向 key_page_ds 或 key_page_mimo）
        self.key_page = self.key_page_ds
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
                self.llm_page,
                self.key_page_ds,
                self.key_page_mimo,
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
        self._sync_llm_key_panel()
        self._refresh_required_tabs()
        # 再次配置：读取并回填上次 config.json
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._load_existing_config)
        self._load_timer.start(0)
        apply_ui_font_scale(self, initial_font_scale)

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

        self.font_scale_slider.valueChanged.connect(
            self._on_font_scale_changed
        )
        return card

    def _on_font_scale_changed(self, value: int) -> None:
        value = int(value)
        self.font_scale_value.setText(f"{value}%")
        apply_ui_font_scale(self, value / 100.0)

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
        for radio in (
            self.llm_page.radio_ollama,
            self.llm_page.radio_ds,
            self.llm_page.radio_mimo,
        ):
            radio.toggled.connect(self._on_llm_backend_changed)
        self.key_page_ds.key_input.textChanged.connect(self._refresh_required_tabs)
        self.key_page_mimo.key_input.textChanged.connect(self._refresh_required_tabs)
        self.tts_page.enable_cb.toggled.connect(self._refresh_required_tabs)
        self.tts_page.backend_combo.currentIndexChanged.connect(
            self._refresh_required_tabs
        )
        self.tts_page.mimo_api_key_input.textChanged.connect(
            self._refresh_required_tabs
        )
        self.vision_page.enable_cb.toggled.connect(self._refresh_required_tabs)
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

    def _sync_llm_key_panel(self) -> None:
        backend = self.llm_page.get_backend()
        self.key_page_ds.setVisible(backend == "deepseek")
        self.key_page_mimo.setVisible(backend == "mimo")
        if backend == "mimo":
            self.key_page = self.key_page_mimo
        elif backend == "deepseek":
            self.key_page = self.key_page_ds

    def _configuration_issues(self) -> dict[int, list[str]]:
        issues = {
            self.TAB_ENV: [],
            self.TAB_CHAT: [],
            self.TAB_VOICE: [],
            self.TAB_VISION: [],
        }
        llm_backend = self.llm_page.get_backend()
        llm_key = ""
        if llm_backend == "deepseek":
            llm_key = self.key_page_ds.key_input.text().strip()
            if not llm_key:
                issues[self.TAB_CHAT].append("DeepSeek API Key")
        elif llm_backend == "mimo":
            llm_key = self.key_page_mimo.key_input.text().strip()
            if not llm_key:
                issues[self.TAB_CHAT].append("MiMo API Key")

        if (
            self.tts_page.enable_cb.isChecked()
            and self.tts_page.backend_combo.currentData() == "mimo"
        ):
            tts_key = self.tts_page.mimo_api_key_input.text().strip()
            if not tts_key and llm_backend == "mimo":
                tts_key = llm_key
            if not tts_key:
                issues[self.TAB_VOICE].append("MiMo TTS API Key")

        if self.vision_page.enable_cb.isChecked():
            selected_backend = self.vision_page.backend_combo.currentData() or "auto"
            actual_backend = selected_backend
            if selected_backend == "auto":
                actual_backend = llm_backend if llm_backend in {"ollama", "mimo"} else "ollama"
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

    def _load_existing_config(self):
        """打开向导时加载 config.json，恢复上次选择（再次配置不会丢）。"""
        self._existing_config = {}
        if not os.path.isfile(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                return
            self._existing_config = cfg
        except Exception as e:
            try:
                self.env_page.log(f"读取已有配置失败: {e}")
            except Exception:
                pass
            return

        llm = cfg.get("llm", {}) or {}
        tts = cfg.get("tts", {}) or {}
        display = (
            cfg.get("display", {})
            if isinstance(cfg.get("display"), dict)
            else {}
        )

        self.font_scale_slider.setValue(
            round(normalize_ui_font_scale(display.get("font_scale", 1.0)) * 100)
        )
        if hasattr(self, "reduced_motion_cb"):
            self.reduced_motion_cb.setChecked(
                bool(display.get("reduced_motion", False))
            )

        # AI 后端
        backend = (llm.get("backend") or "ollama").lower()
        self.llm_page.set_backend(backend)
        if backend == "mimo":
            self.key_page = self.key_page_mimo
        elif backend == "deepseek":
            self.key_page = self.key_page_ds

        # API Key / Base
        api_key = (llm.get("api_key") or "").strip()
        api_base = (llm.get("api_base") or "").strip()
        if backend == "mimo":
            if api_key:
                self.key_page_mimo.key_input.setText(api_key)
            if api_base:
                self.key_page_mimo.api_base.setText(api_base)
        elif backend == "deepseek":
            if api_key:
                self.key_page_ds.key_input.setText(api_key)
            if api_base:
                self.key_page_ds.api_base.setText(api_base)

        # 语音页
        try:
            self.tts_page.apply_config(tts)
            try:
                self.env_page.log("已恢复上次语音配置（引擎/语言/克隆等）")
            except Exception:
                pass
        except Exception as e:
            try:
                self.env_page.log(f"恢复语音配置失败: {e}")
            except Exception:
                pass

        # 识图 / 屏幕观察
        try:
            vision = cfg.get("vision", {}) or {}
            watcher = cfg.get("watcher", {}) or {}
            # 单一 config：watcher 已含 interval
            if "interval" not in watcher and isinstance(watcher.get("min_ms"), (int, float)):
                watcher = dict(watcher)
                watcher["interval"] = {
                    "min_ms": int(watcher.get("min_ms", 180000)),
                    "max_ms": int(watcher.get("max_ms", 360000)),
                }
            self.vision_page.apply_config(vision, watcher)
        except Exception as e:
            try:
                self.env_page.log(f"恢复识图配置失败: {e}")
            except Exception:
                pass

        # 环境页提示
        try:
            eng = (tts.get("engine") or "?").lower()
            tts_on = "开" if tts.get("enabled", True) else "关"
            w = (cfg.get("watcher") or {})
            w_on = "开" if w.get("enabled") else "关"
            v_back = ((cfg.get("vision") or {}).get("backend") or "跟随对话")
            self.env_page.log(
                f"📂 已加载上次配置：AI={backend}，语音={eng}（{tts_on}），识图={v_back or '跟随'}（观察{w_on}）"
            )
        except Exception:
            pass
        self._sync_llm_key_panel()
        self._refresh_required_tabs()

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """递归合并：override 覆盖 base，未涉及的旧字段保留。"""
        out = dict(base or {})
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = self._deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    def _drag_start(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos()

    def _drag_move(self, e):
        if self._drag:
            self.move(self.pos() + e.globalPos() - self._drag)
            self._drag = e.globalPos()

    def collect_config(self):
        config = {
            "llm": {"backend": self.llm_page.get_backend(), "temperature": 0.7},
            "vision": {"model": "qwen3.5:4b", "backend": "", "enabled": False},
            "tts": {
                "engine": self.tts_page.backend_combo.currentData(),
                "enabled": self.tts_page.enable_cb.isChecked(),
                "gpt_weights_dir": "./models/GPT_weights",
                "sovits_weights_dir": "./models/SoVITS_weights",
                "gpt_model": "mea_pro-e50.ckpt",
                "sovits_model": "mea_pro_e24_s13704.pth",
                "ref_dir": "./GPT-Sovits",
                "gsv_ref_wav": (
                    self.tts_page.gsv_ref_wav_input.text().strip()
                    if hasattr(self.tts_page, "gsv_ref_wav_input")
                    else ""
                ),
                "gsv_ref_lang": (
                    self.tts_page.gsv_ref_lang_combo.currentData() or "jp"
                    if hasattr(self.tts_page, "gsv_ref_lang_combo")
                    else "jp"
                ),
                "top_k": 15, "top_p": 0.8,
                "temperature": 0.6, "speed": 1.0,
                "translate_to_jp": (
                    bool(self.tts_page.mimo_translate_jp_cb.isChecked())
                    if hasattr(self.tts_page, "mimo_translate_jp_cb")
                    and self.tts_page.backend_combo.currentData() == "mimo"
                    else self.tts_page.backend_combo.currentData() != "mimo"
                ),
                "voice_lang": (
                    (self.tts_page.mimo_voice_lang_combo.currentData() or "jp")
                    if hasattr(self.tts_page, "mimo_voice_lang_combo")
                    and self.tts_page.backend_combo.currentData() == "mimo"
                    else "jp"
                ),
                "translate_api_key": "",
                "translate_model": "deepseek-v4-flash",
                "api_base": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5-tts",
                "voice": (
                    self.tts_page.mimo_voice_input.text().strip()
                    if hasattr(self.tts_page, "mimo_voice_input")
                    else "冰糖"
                ) or "冰糖",
                "style": "",
                "voice_clone": bool(
                    hasattr(self.tts_page, "mimo_voiceclone_cb")
                    and self.tts_page.mimo_voiceclone_cb.isChecked()
                ),
                "clone_ref": (
                    self.tts_page.mimo_clone_ref_input.text().strip()
                    if hasattr(self.tts_page, "mimo_clone_ref_input")
                    else ""
                ),
                "clone_dir": "./voice_cache",
            },
            "display": {
                "scale": 0.5,
                "fps": 30,
                "font_scale": self.font_scale_slider.value() / 100.0,
                "reduced_motion": bool(
                    getattr(self, "reduced_motion_cb", None)
                    and self.reduced_motion_cb.isChecked()
                ),
            },
            "character": {"name": "梅尔", "default_outfit": "01", "default_direction": "A"},
            "sprite_dir": "./sprites",
            "live2d": {
                "model_dir": "./live2d/model/mea_live2d",
                "enabled": True, "scale": 0.15
            }
        }

        b = self.llm_page.get_backend()
        if b == "ollama":
            config["llm"]["host"] = "http://127.0.0.1:11434"
            config["llm"]["model"] = "qwen3.5:4b"
            config["llm"]["api_key"] = ""
            config["llm"]["api_base"] = ""
            config["llm"]["bridge_url"] = ""
        elif b == "deepseek":
            config["llm"]["api_key"] = self.key_page.key_input.text().strip()
            config["llm"]["api_base"] = self.key_page.api_base.text().strip()
            config["llm"]["model"] = "deepseek-v4-flash"
        elif b == "mimo":
            config["llm"]["api_key"] = self.key_page.key_input.text().strip()
            config["llm"]["api_base"] = self.key_page.api_base.text().strip()
            config["llm"]["model"] = "mimo-v2.5"
            config["llm"]["host"] = ""
            config["llm"]["bridge_url"] = ""
            # 默认 vision 跟随对话；若用户在识图页另选 Ollama，collect 会覆盖
            if not config.get("vision", {}).get("backend"):
                config["vision"]["model"] = "mimo"

        # 翻译备用 Key（本地引擎中文→日语时）
        if self.tts_page.enable_cb.isChecked():
            tk = self.tts_page.translate_key.text().strip()
            if tk:
                config["tts"]["translate_api_key"] = tk

        # GPT-SoVITS Python 路径
        gsv_path = self.tts_page.gsv_dir_input.text().strip()
        if gsv_path:
            config["tts"]["python_exe"] = gsv_path

        # VITS Python 路径
        vits_py = self.tts_page.vits_python_input.text().strip()
        if vits_py:
            config["tts"]["vits_python"] = vits_py

        # MiMo TTS：优先用语音页填写的 Key；为空时再回退对话页 MiMo Key
        if config["tts"].get("engine") == "mimo":
            tts_key = ""
            tts_base = ""
            if hasattr(self.tts_page, "mimo_api_key_input"):
                tts_key = self.tts_page.mimo_api_key_input.text().strip()
            if hasattr(self.tts_page, "mimo_api_base_input"):
                tts_base = self.tts_page.mimo_api_base_input.text().strip()

            if not tts_key and b == "mimo":
                tts_key = config["llm"].get("api_key", "")
            if not tts_base:
                if b == "mimo" and config["llm"].get("api_base"):
                    tts_base = config["llm"]["api_base"]
                else:
                    tts_base = "https://api.xiaomimimo.com/v1"

            config["tts"]["api_key"] = tts_key
            config["tts"]["api_base"] = tts_base
            # voice-clone：切换模型并写入参考音频
            use_clone = bool(
                hasattr(self.tts_page, "mimo_voiceclone_cb")
                and self.tts_page.mimo_voiceclone_cb.isChecked()
            )
            clone_ref = ""
            if hasattr(self.tts_page, "mimo_clone_ref_input"):
                clone_ref = self.tts_page.mimo_clone_ref_input.text().strip()
            config["tts"]["voice_clone"] = use_clone
            if clone_ref:
                config["tts"]["clone_ref"] = clone_ref
            if use_clone:
                config["tts"]["model"] = "mimo-v2.5-tts-voiceclone"
                if not (config["tts"].get("voice") or "").strip() or config["tts"].get("voice") == "冰糖":
                    config["tts"]["voice"] = "clone"
            else:
                # 保持内置音色模型
                if "voiceclone" in str(config["tts"].get("model", "")).lower():
                    config["tts"]["model"] = "mimo-v2.5-tts"

        # 识图 / 屏幕观察（独立配置）
        try:
            vw = self.vision_page.collect(b, config.get("llm", {}))
            config["vision"] = vw.get("vision", config.get("vision", {}))
            config["watcher"] = vw.get("watcher", {
                "enabled": False,
                "allow_cloud": False,
                "interval": {"min_ms": 180000, "max_ms": 360000},
            })
        except Exception:
            config.setdefault("watcher", {
                "enabled": False,
                "allow_cloud": False,
                "interval": {"min_ms": 180000, "max_ms": 360000},
            })

        # 与已有 config 合并，避免「再次配置」把未改动的字段冲掉
        if getattr(self, "_existing_config", None):
            config = self._deep_merge(self._existing_config, config)
            # 再写回本次向导明确收集的关键字段（防止 merge 后被旧值盖住）
            config["llm"]["backend"] = self.llm_page.get_backend()
            if b == "mimo":
                config["llm"]["api_key"] = self.key_page_mimo.key_input.text().strip()
                config["llm"]["api_base"] = self.key_page_mimo.api_base.text().strip()
                config["llm"]["model"] = config["llm"].get("model") or "mimo-v2.5"
            elif b == "deepseek":
                config["llm"]["api_key"] = self.key_page_ds.key_input.text().strip()
                config["llm"]["api_base"] = self.key_page_ds.api_base.text().strip()
            config["tts"]["engine"] = self.tts_page.backend_combo.currentData()
            config["tts"]["enabled"] = self.tts_page.enable_cb.isChecked()
            if config["tts"].get("engine") == "mimo":
                if hasattr(self.tts_page, "mimo_api_key_input"):
                    k = self.tts_page.mimo_api_key_input.text().strip()
                    if k:
                        config["tts"]["api_key"] = k
                    elif not config["tts"].get("api_key") and b == "mimo":
                        config["tts"]["api_key"] = config["llm"].get("api_key", "")
                if hasattr(self.tts_page, "mimo_api_base_input"):
                    bb = self.tts_page.mimo_api_base_input.text().strip()
                    if bb:
                        config["tts"]["api_base"] = bb
                if hasattr(self.tts_page, "mimo_voice_input"):
                    vv = self.tts_page.mimo_voice_input.text().strip()
                    if vv:
                        config["tts"]["voice"] = vv
                # 语言 / 翻译 / 克隆：必须用向导当前选择覆盖旧 config（否则像“每次从头”或选了不生效）
                if hasattr(self.tts_page, "mimo_voice_lang_combo"):
                    config["tts"]["voice_lang"] = (
                        self.tts_page.mimo_voice_lang_combo.currentData() or "jp"
                    )
                if hasattr(self.tts_page, "mimo_translate_jp_cb"):
                    config["tts"]["translate_to_jp"] = bool(
                        self.tts_page.mimo_translate_jp_cb.isChecked()
                    )
                if hasattr(self.tts_page, "mimo_voiceclone_cb"):
                    use_clone = bool(self.tts_page.mimo_voiceclone_cb.isChecked())
                    config["tts"]["voice_clone"] = use_clone
                    if use_clone:
                        config["tts"]["model"] = "mimo-v2.5-tts-voiceclone"
                    elif "voiceclone" in str(config["tts"].get("model", "")).lower():
                        config["tts"]["model"] = "mimo-v2.5-tts"
                if hasattr(self.tts_page, "mimo_clone_ref_input"):
                    config["tts"]["clone_ref"] = (
                        self.tts_page.mimo_clone_ref_input.text().strip()
                    )

        return config

    def _save(self):
        try:
            # 保存前再读一次磁盘，降低与外部手改冲突时整文件覆盖的损失
            if os.path.isfile(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        disk_cfg = json.load(f)
                    if isinstance(disk_cfg, dict):
                        self._existing_config = self._deep_merge(
                            disk_cfg, getattr(self, "_existing_config", {}) or {}
                        )
                except Exception:
                    pass

            final_cfg = self.collect_config()
            # 统一写入单一 config.json（含 bubble/display/watcher UI 字段）
            try:
                from meapet.config.store import normalize_config, load_config, save_config
                # 保留磁盘上已有的 UI 字段（气泡时长等），避免向导冲掉
                try:
                    existing = load_config(CONFIG_PATH)
                    for key in ("bubble_duration_ms",):
                        if key in existing and key not in final_cfg:
                            final_cfg[key] = existing[key]
                    # display.size_factor：保留用户右键调过的值
                    if isinstance(existing.get("display"), dict):
                        final_cfg.setdefault("display", {})
                        if "size_factor" in existing["display"] and "size_factor" not in (final_cfg.get("display") or {}):
                            final_cfg["display"]["size_factor"] = existing["display"]["size_factor"]
                        elif "size_factor" in existing["display"]:
                            # 向导未提供 size_factor 时保留
                            if "size_factor" not in (final_cfg.get("display") or {}):
                                final_cfg["display"]["size_factor"] = existing["display"]["size_factor"]
                    # tts.sync_with_audio
                    if isinstance(existing.get("tts"), dict) and "sync_with_audio" in existing["tts"]:
                        final_cfg.setdefault("tts", {})
                        final_cfg["tts"].setdefault("sync_with_audio", existing["tts"]["sync_with_audio"])
                except Exception:
                    pass
                final_cfg = normalize_config(final_cfg)
                save_config(final_cfg, CONFIG_PATH)
            except Exception:
                # 原子保存失败时保留旧配置，交由外层显示错误；禁止退化为截断式覆盖。
                raise

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
                "提示：再次打开配置页会自动加载本次选择。"
            )
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
