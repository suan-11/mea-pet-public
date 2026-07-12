"""配置向导主窗口"""
from __future__ import annotations

import json
import os
import sys
import traceback

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QMessageBox, QFrame, QProgressBar, QScrollArea, QSizeGrip,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QKeySequence, QPalette
from PyQt5.QtWidgets import QShortcut

from wizard.platform_info import PLATFORM, CONFIG_PATH, detect_platform
from wizard.styles import (
    COLOR_BG,
    COLOR_ELEVATED,
    COLOR_TEXT,
    WIZARD_STYLESHEET,
    prepare_accessible_page,
)
from meapet.ui_theme import MIN_TARGET_SIZE, PALETTE
from wizard.pages import (
    EnvCheckPage, LLMPage, ApiKeyPage, TTSPage, VisionPage, SummaryPage,
)

class SetupWizard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MeaPet 配置向导 — {PLATFORM['os_label']}")
        self.setObjectName("WizardRoot")
        self.setMinimumSize(680, 640)
        self.resize(760, 780)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(WIZARD_STYLESHEET)
        self.setAccessibleName("MeaPet 配置向导")
        self.setAccessibleDescription("分步骤配置对话、语音和屏幕识图功能")

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

        brand_name = QLabel("MeaPet Setup")
        brand_name.setObjectName("BrandName")
        top.addWidget(brand_name)
        top.addStretch()

        self.step_label = QLabel("环境检测")
        self.step_label.setObjectName("StepLabel")
        self.step_label.setAccessibleName("当前步骤")
        top.addWidget(self.step_label)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseButton")
        self.close_btn.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.close_btn.setToolTip("关闭配置向导（Esc）")
        self.close_btn.setAccessibleName("关闭配置向导")
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn)
        main.addWidget(header)

        # 进度条
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(24, 0, 24, 14)
        self.progress = QProgressBar()
        self.progress.setRange(1, 7)
        self.progress.setValue(1)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("配置进度")
        progress_row.addWidget(self.progress)
        main.addLayout(progress_row)

        divider = QFrame()
        divider.setObjectName("WizardDivider")
        main.addWidget(divider)

        # 页面
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setAccessibleName("配置页面")
        main.addWidget(self.scroll, 1)

        self.stack = QStackedWidget()
        self.stack.setObjectName("WizardPages")
        self.scroll.setWidget(self.stack)

        self.env_page = EnvCheckPage()
        self.llm_page = LLMPage()
        self.key_page_ds = ApiKeyPage(self, backend="deepseek")
        self.key_page_mimo = ApiKeyPage(self, backend="mimo")
        self.tts_page = TTSPage()
        self.vision_page = VisionPage()
        self.summary_page = SummaryPage(self)

        self.stack.addWidget(self.env_page)      # 0
        self.stack.addWidget(self.llm_page)       # 1
        self.stack.addWidget(self.key_page_ds)    # 2
        self.stack.addWidget(self.key_page_mimo)  # 3
        self.stack.addWidget(self.tts_page)       # 4
        self.stack.addWidget(self.vision_page)    # 5
        self.stack.addWidget(self.summary_page)   # 6

        for page in (
            self.env_page,
            self.llm_page,
            self.key_page_ds,
            self.key_page_mimo,
            self.tts_page,
            self.vision_page,
            self.summary_page,
        ):
            prepare_accessible_page(page)

        # 当前显示的 key_page 引用（指向 key_page_ds 或 key_page_mimo）
        self.key_page = self.key_page_ds
        self._existing_config = {}

        # 底部按钮
        footer = QFrame()
        footer.setObjectName("WizardFooter")
        btns = QHBoxLayout(footer)
        btns.setContentsMargins(24, 12, 18, 18)
        btns.setSpacing(12)

        footer_hint = QLabel("设置仅保存在本机，可随时再次打开向导修改")
        footer_hint.setObjectName("HelperText")
        footer_hint.setWordWrap(True)
        btns.addWidget(footer_hint, 1)

        self.back_btn = QPushButton("上一步")
        self.back_btn.setObjectName("SecondaryButton")
        self.back_btn.setMinimumSize(104, MIN_TARGET_SIZE)
        self.back_btn.setAccessibleName("返回上一步")
        self.back_btn.setToolTip("返回上一步（Alt+左方向键）")
        self.back_btn.clicked.connect(self._back)
        self.back_btn.setEnabled(False)
        btns.addWidget(self.back_btn)

        self.next_btn = QPushButton("继续")
        self.next_btn.setObjectName("PrimaryButton")
        self.next_btn.setMinimumSize(116, MIN_TARGET_SIZE)
        self.next_btn.setAccessibleName("继续到下一步")
        self.next_btn.clicked.connect(self._next)
        btns.addWidget(self.next_btn)

        size_grip = QSizeGrip(self.container)
        size_grip.setFixedSize(18, 18)
        size_grip.setToolTip("拖动调整窗口大小")
        btns.addWidget(size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        main.addWidget(footer)

        self._close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._close_shortcut.activated.connect(self.close)
        self._back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self._back_shortcut.activated.connect(self._back)
        self.setTabOrder(self.back_btn, self.next_btn)

        # 窗口拖拽
        self._drag = None
        for w in [header, brand_mark, brand_name]:
            w.mousePressEvent = lambda e: self._drag_start(e)
            w.mouseMoveEvent = lambda e: self._drag_move(e)
            w.mouseReleaseEvent = lambda e: setattr(self, '_drag', None)

        self._page = 0
        self._update()
        # 再次配置：读取并回填上次 config.json
        QTimer.singleShot(0, self._load_existing_config)

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

    def _update(self):
        p = self._page
        self.progress.setValue(p + 1)
        self.back_btn.setEnabled(p > 0)
        names = ["环境检测", "AI 大脑", "API Key", "API Key", "语音设置", "识图设置", "确认"]
        step_name = names[p] if p < len(names) else "完成"
        self.step_label.setText(f"{p + 1} / 7 · {step_name}")
        self.progress.setAccessibleDescription(f"第 {p + 1} 步，共 7 步：{step_name}")
        if p == 0:
            self.next_btn.setText("跳过检测")
            self.next_btn.setAccessibleName("跳过环境检测")
        elif p == 6:
            self.next_btn.setText("保存配置")
            self.next_btn.setAccessibleName("保存配置并完成")
        else:
            self.next_btn.setText("继续")
            self.next_btn.setAccessibleName("继续到下一步")

    def _back(self):
        p = self._page
        if p == 0:
            return
        if p == 1:
            self._page = 0
        elif p in (2, 3):
            self._page = 1
        elif p == 4:
            b = self.llm_page.get_backend()
            self._page = 2 if b == "deepseek" else (3 if b == "mimo" else 1)
        elif p == 5:
            self._page = 4
        elif p == 6:
            self._page = 5
        self.stack.setCurrentIndex(self._page)
        self._update()


    def _next(self):
        p = self._page

        # 环境页 → AI
        if p == 0:
            self._page = 1
            self.stack.setCurrentIndex(1)
            self._update()
            return

        # LLM 页 → Key 或 语音
        if p == 1:
            b = self.llm_page.get_backend()
            if b == "deepseek":
                self.key_page = self.key_page_ds
                self._page = 2
            elif b == "mimo":
                self.key_page = self.key_page_mimo
                self._page = 3
            else:
                self._page = 4  # Ollama 跳过 API Key 页
            self.stack.setCurrentIndex(self._page)
            self._update()
            return

        # API Key 页 → 语音
        if p in (2, 3):
            self._page = 4
            self.stack.setCurrentIndex(4)
            self._update()
            return

        # 语音页 → 识图
        if p == 4:
            self._page = 5
            self.stack.setCurrentIndex(5)
            self._update()
            return

        # 识图页 → 确认
        if p == 5:
            self.summary_page.refresh()
            self._page = 6
            self.stack.setCurrentIndex(6)
            self._update()
            return

        # 确认 → 保存
        if p == 6:
            self._save()


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
            "display": {"scale": 0.5, "fps": 30},
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
                "提示：再次打开向导会自动加载本次选择。"
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
