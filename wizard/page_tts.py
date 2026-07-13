"""配置向导各页面"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QSize, QUrl
from PyQt5.QtGui import *

from wizard.styles import (
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    MIN_TARGET_SIZE, STYLE_PAGE_CARD,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH, platform_checklist, ollama_install_hint, detect_platform
from wizard.env_utils import (
    WorkerSignals, pip_install, check_installed, download_file,
    check_ollama_running, check_ollama_installed, pull_ollama_model,
)

# 兼容页面内可能使用的短名
from wizard.page_tts_gsv import TtsPageGsvMixin
from wizard.page_tts_mimo import TtsPageMimoMixin
from wizard.page_tts_vits import TtsPageVitsMixin
from meapet.config.normalizers import normalize_gsv_ref_language


class TTSPage(TtsPageGsvMixin, TtsPageMimoMixin, TtsPageVitsMixin, QFrame):
    """语音设置页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._startup_timers = []
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        title = QLabel("语音设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.enable_cb = QCheckBox("启用语音（梅尔会说话）")
        self.enable_cb.setAccessibleDescription("关闭后不会进行语音合成")
        self.enable_cb.setChecked(True)
        self.enable_cb.toggled.connect(self._toggle)
        layout.addWidget(self.enable_cb)
        tts_hint = QLabel("想先开玩可暂时关闭；本地引擎需要额外模型，云端 TTS 只需 API Key。")
        tts_hint.setObjectName("HelperText")
        tts_hint.setWordWrap(True)
        layout.addWidget(tts_hint)

        self.engine_details_toggle = QCheckBox("显示引擎详细设置")
        self.engine_details_toggle.setChecked(False)
        self.engine_details_toggle.setAccessibleName("显示引擎详细设置")
        self.engine_details_toggle.setToolTip("关闭后只保留总开关，适合先开玩再细调")
        self.engine_details_toggle.toggled.connect(self._sync_engine_details_visibility)
        layout.addWidget(self.engine_details_toggle)

        # ═══ 语音后端选择 ═══
        self.backend_label = QLabel("选择语音引擎：")
        self.backend_label.setObjectName("FieldLabel")
        layout.addWidget(self.backend_label)

        self.backend_combo = QComboBox()
        self.backend_combo.setObjectName("TtsEngine")
        self.backend_combo.setAccessibleName("语音引擎")
        self.backend_combo.addItem("MiMo 云端 TTS（推荐：无需本地模型，用 API Key）", "mimo")
        self.backend_combo.addItem("VITS（轻量快速，无需整合包，效果勉强能用）", "vits")
        self.backend_combo.addItem("GPT-SoVITS（效果超好，需要整合包，推理慢）", "gpt_sovits")
        self.backend_combo.currentIndexChanged.connect(self._toggle_backend)
        layout.addWidget(self.backend_combo)

        # 记录是否已从 config 恢复过，避免重复覆盖用户输入
        self._loaded_from_config = False

        # MiMo TTS 说明 / Key / 音色（云端模式显示）
        self.mimo_tts_frame = QFrame()
        self.mimo_tts_frame.setObjectName("SectionCard")
        mimo_layout = QVBoxLayout(self.mimo_tts_frame)
        mimo_layout.setContentsMargins(18, 16, 18, 18)
        mimo_layout.setSpacing(10)
        mimo_description = QLabel(
            "小米 MiMo 语音合成（mimo-v2.5-tts）。\n"
            "会自动检测：对话页 Key / config.json / 环境变量 MIMO_API_KEY。\n"
            "音色示例：冰糖 / 茉莉 / 苏打 / 白桦 / Chloe / Mia"
        )
        mimo_description.setObjectName("HelperText")
        mimo_description.setWordWrap(True)
        mimo_layout.addWidget(mimo_description)

        self.mimo_key_status = QLabel("正在检测已有 MiMo Key…")
        self.mimo_key_status.setWordWrap(True)
        self.mimo_key_status.setProperty("status", "muted")
        self.mimo_key_status.setAccessibleName("MiMo Key 检测状态")
        mimo_layout.addWidget(self.mimo_key_status)

        mimo_key_label = QLabel("MiMo TTS API Key：")
        mimo_key_label.setObjectName("FieldLabel")
        mimo_layout.addWidget(mimo_key_label)
        self.mimo_api_key_input = QLineEdit()
        self.mimo_api_key_input.setObjectName("MimoTtsApiKey")
        self.mimo_api_key_input.setPlaceholderText("可自动填入；也可手动覆盖")
        self.mimo_api_key_input.setEchoMode(QLineEdit.Password)
        self.mimo_api_key_input.setStyleSheet(STYLE_INPUT)
        self.mimo_api_key_input.setAccessibleName("MiMo TTS API Key")
        self.mimo_api_key_input.textChanged.connect(lambda _t: self._refresh_mimo_key_status())
        mimo_layout.addWidget(self.mimo_api_key_input)

        mimo_base_label = QLabel("API Base：")
        mimo_base_label.setObjectName("FieldLabel")
        mimo_layout.addWidget(mimo_base_label)
        self.mimo_api_base_input = QLineEdit("https://api.xiaomimimo.com/v1")
        self.mimo_api_base_input.setObjectName("MimoTtsApiBase")
        self.mimo_api_base_input.setPlaceholderText("https://api.xiaomimimo.com/v1")
        self.mimo_api_base_input.setStyleSheet(STYLE_INPUT)
        self.mimo_api_base_input.setAccessibleName("MiMo TTS API 地址")
        mimo_layout.addWidget(self.mimo_api_base_input)

        fill_row = QHBoxLayout()
        self.mimo_fill_from_chat_btn = QPushButton("重新检测并填入已有 Key")
        self.mimo_fill_from_chat_btn.setAccessibleName("重新检测 MiMo Key")
        self.mimo_fill_from_chat_btn.clicked.connect(lambda: self._detect_and_fill_mimo_key(force=True))
        fill_row.addWidget(self.mimo_fill_from_chat_btn)
        fill_row.addStretch()
        mimo_layout.addLayout(fill_row)

        voice_row = QHBoxLayout()
        voice_label = QLabel("音色：")
        voice_label.setObjectName("FieldLabel")
        voice_row.addWidget(voice_label)
        self.mimo_voice_input = QLineEdit("冰糖")
        self.mimo_voice_input.setObjectName("MimoVoice")
        self.mimo_voice_input.setPlaceholderText("内置: 冰糖/Chloe；克隆填 clone")
        self.mimo_voice_input.setStyleSheet(STYLE_INPUT)
        self.mimo_voice_input.setAccessibleName("MiMo 音色")
        voice_row.addWidget(self.mimo_voice_input)
        mimo_layout.addLayout(voice_row)

        # 合成语言（影响文本是否译日语 + clone 参考音频语言）
        lang_row = QHBoxLayout()
        language_label = QLabel("合成语言：")
        language_label.setObjectName("FieldLabel")
        lang_row.addWidget(language_label)
        self.mimo_voice_lang_combo = QComboBox()
        self.mimo_voice_lang_combo.setObjectName("MimoVoiceLanguage")
        self.mimo_voice_lang_combo.setAccessibleName("语音合成语言")
        # 默认日语（梅尔日语人设）；中文/英文可选
        self.mimo_voice_lang_combo.addItem("日语（默认，克隆用 jp_* 参考）", "jp")
        self.mimo_voice_lang_combo.addItem("中文（克隆用 zh_* 参考）", "zh")
        self.mimo_voice_lang_combo.addItem("英文", "en")
        self.mimo_voice_lang_combo.setToolTip(
            "决定念什么语言，以及 voice-clone 优先选 zh_* / jp_* / en_* 参考音频。\n"
            "选中文时请放中文参考（如 GPT-Sovits/normal/zh_normal.wav）。"
        )
        self.mimo_voice_lang_combo.currentIndexChanged.connect(self._on_mimo_voice_lang_changed)
        lang_row.addWidget(self.mimo_voice_lang_combo, 1)
        mimo_layout.addLayout(lang_row)

        self.mimo_translate_jp_cb = QCheckBox("先翻译成日语再合成（仅当合成语言=日语时生效）")
        self.mimo_translate_jp_cb.setChecked(True)
        self.mimo_translate_jp_cb.setToolTip(
            "开启后中文回复会先译成日语，再交给 TTS。\n"
            "合成语言为中文/英文时此项无效。"
        )
        mimo_layout.addWidget(self.mimo_translate_jp_cb)

        self.mimo_voiceclone_cb = QCheckBox("使用 voice-clone（发送参考音频克隆音色）")
        self.mimo_voiceclone_cb.setToolTip(
            "启用后模型改为 mimo-v2.5-tts-voiceclone，\n"
            "并把 clone_ref / 同语言样本（zh_* 或 jp_*）以 base64 发给 API。"
        )
        mimo_layout.addWidget(self.mimo_voiceclone_cb)

        self.mimo_clone_hint = QLabel("克隆参考音频（可选；空则按合成语言自动选同语言样本）：")
        self.mimo_clone_hint.setObjectName("FieldLabel")
        mimo_layout.addWidget(self.mimo_clone_hint)
        clone_row = QHBoxLayout()
        self.mimo_clone_ref_input = QLineEdit()
        self.mimo_clone_ref_input.setObjectName("MimoCloneReference")
        self.mimo_clone_ref_input.setPlaceholderText("例如 ./GPT-Sovits/normal/jp_normal.wav")
        self.mimo_clone_ref_input.setStyleSheet(STYLE_INPUT)
        self.mimo_clone_ref_input.setAccessibleName("克隆参考音频路径")
        clone_row.addWidget(self.mimo_clone_ref_input)
        clone_browse = QPushButton("浏览…")
        clone_browse.setMinimumSize(88, MIN_TARGET_SIZE)
        clone_browse.setAccessibleName("选择克隆参考音频")
        clone_browse.clicked.connect(self._browse_clone_ref)
        clone_row.addWidget(clone_browse)
        mimo_layout.addLayout(clone_row)

        self.mimo_clone_lang_tip = QLabel(
            "提示：当前合成语言=日语 → 自动优先 jp_*.wav / voice_cache 的 jp_*。"
        )
        self.mimo_clone_lang_tip.setProperty("status", "success")
        self.mimo_clone_lang_tip.setWordWrap(True)
        mimo_layout.addWidget(self.mimo_clone_lang_tip)
        layout.addWidget(self.mimo_tts_frame)


        # VITS Python 路径（VITS 模式显示）
        self.vits_python_frame = QFrame()
        vpy_layout = QHBoxLayout(self.vits_python_frame)
        vpy_layout.setContentsMargins(0, 0, 0, 0)
        vpy_label = QLabel("VITS Python 路径：")
        vpy_label.setObjectName("FieldLabel")
        vpy_layout.addWidget(vpy_label)
        self.vits_python_input = QLineEdit()
        self.vits_python_input.setObjectName("VitsPythonPath")
        _default_vits_py = os.path.join(
            os.path.expanduser("~"),
            ".conda", "envs", "vits_ft", "python.exe"
        )
        if os.path.isfile(_default_vits_py):
            self.vits_python_input.setText(_default_vits_py)
        self.vits_python_input.setPlaceholderText("用于 VITS 推理的 Python（需含 PyTorch CUDA）")
        self.vits_python_input.setStyleSheet(STYLE_INPUT)
        self.vits_python_input.setAccessibleName("VITS Python 路径")
        vpy_layout.addWidget(self.vits_python_input)
        vits_browse_btn = QPushButton("浏览…")
        vits_browse_btn.setMinimumSize(88, MIN_TARGET_SIZE)
        vits_browse_btn.setAccessibleName("选择 VITS Python")
        vits_browse_btn.clicked.connect(lambda: self._browse_python(self.vits_python_input))
        vpy_layout.addWidget(vits_browse_btn)
        layout.addWidget(self.vits_python_frame)

        # VITS 模型状态
        self.vits_status = QLabel("")
        self.vits_status.setProperty("status", "muted")
        self.vits_status.setAccessibleName("VITS 模型状态")
        layout.addWidget(self.vits_status)

        # VITS 环境安装按钮
        self.setup_vits_btn = QPushButton("自动配置 VITS 环境")
        self.setup_vits_btn.setAccessibleDescription("首次使用 VITS 时安装运行环境")
        self.setup_vits_btn.clicked.connect(self._setup_vits_env)
        layout.addWidget(self.setup_vits_btn)

        # GSV 相关控件容器（GPT-SoVITS 模式显示）
        self.gsv_container = QFrame()
        gsv_layout = QVBoxLayout(self.gsv_container)
        gsv_layout.setContentsMargins(0, 0, 0, 0)
        self.gsv_status = QLabel("")
        self.gsv_status.setProperty("status", "muted")
        self.gsv_status.setAccessibleName("GPT-SoVITS 环境状态")
        gsv_layout.addWidget(self.gsv_status)

        guide_btn = QPushButton("查看 GPT-SoVITS 安装说明")
        guide_btn.clicked.connect(self._show_gsv_guide)
        gsv_layout.addWidget(guide_btn)

        # GPT-SoVITS 整合包目录选择
        gsv_label = QLabel("选整合包解压后的文件夹（会自动识别 python.exe）：")
        gsv_label.setObjectName("FieldLabel")
        gsv_layout.addWidget(gsv_label)

        path_row = QHBoxLayout()
        self.gsv_dir_input = QLineEdit()
        self.gsv_dir_input.setObjectName("GptSovitsDirectory")
        self.gsv_dir_input.setPlaceholderText("点「浏览」选整合包解压后的文件夹")
        self.gsv_dir_input.setStyleSheet(STYLE_INPUT)
        self.gsv_dir_input.setAccessibleName("GPT-SoVITS 整合包目录")
        path_row.addWidget(self.gsv_dir_input)

        browse_btn = QPushButton("选择文件夹…")
        browse_btn.setMinimumSize(116, MIN_TARGET_SIZE)
        browse_btn.setAccessibleName("选择 GPT-SoVITS 整合包目录")
        browse_btn.clicked.connect(self._browse_gsv_dir)
        path_row.addWidget(browse_btn)
        gsv_layout.addLayout(path_row)

        ref_hint = QLabel(
            "指定参考音频（可选；留空时继续按情绪自动选择）。"
            "若旁边有同名 .txt，会自动作为参考文本。"
        )
        ref_hint.setObjectName("HelperText")
        ref_hint.setWordWrap(True)
        gsv_layout.addWidget(ref_hint)

        ref_row = QHBoxLayout()
        self.gsv_ref_wav_input = QLineEdit()
        self.gsv_ref_wav_input.setObjectName("GsvReferenceAudio")
        self.gsv_ref_wav_input.setPlaceholderText(
            "例如 ./GPT-Sovits/normal/jp_normal.wav"
        )
        self.gsv_ref_wav_input.setStyleSheet(STYLE_INPUT)
        self.gsv_ref_wav_input.setAccessibleName("GPT-SoVITS 指定参考音频路径")
        self.gsv_ref_wav_input.setAccessibleDescription(
            "选择后将优先于按情绪自动选择的参考音频"
        )
        ref_row.addWidget(self.gsv_ref_wav_input, 1)

        ref_browse_btn = QPushButton("选择音频…")
        ref_browse_btn.setMinimumSize(104, MIN_TARGET_SIZE)
        ref_browse_btn.setAccessibleName("选择 GPT-SoVITS 参考音频")
        ref_browse_btn.clicked.connect(self._browse_gsv_ref_wav)
        ref_row.addWidget(ref_browse_btn)
        gsv_layout.addLayout(ref_row)

        ref_lang_row = QHBoxLayout()
        ref_lang_label = QLabel("参考音频语言：")
        ref_lang_label.setObjectName("FieldLabel")
        ref_lang_row.addWidget(ref_lang_label)
        self.gsv_ref_lang_combo = QComboBox()
        self.gsv_ref_lang_combo.setObjectName("GsvReferenceLanguage")
        self.gsv_ref_lang_combo.setAccessibleName("GPT-SoVITS 参考音频语言")
        self.gsv_ref_lang_combo.setAccessibleDescription(
            "用于解析参考音频对应的同名参考文本"
        )
        self.gsv_ref_lang_combo.addItem("日语", "jp")
        self.gsv_ref_lang_combo.addItem("中文", "zh")
        self.gsv_ref_lang_combo.addItem("英语", "en")
        ref_lang_row.addWidget(self.gsv_ref_lang_combo, 1)
        gsv_layout.addLayout(ref_lang_row)

        self._schedule_startup(300, self._check_gsv)
        layout.addWidget(self.gsv_container)

        # 初始调用后端切换（默认 VITS 模式）
        self._schedule_startup(100, self._toggle_backend)

        packaged_hint = QLabel("语音模型已打包，开箱即用。")
        packaged_hint.setObjectName("HelperText")
        layout.addWidget(packaged_hint)

        # 翻译提示（中文 → 日语翻译，使用阿里巴巴免费翻译 API，无需 Key）
        self.translate_frame = QFrame()
        tf = QVBoxLayout(self.translate_frame)
        tf.setContentsMargins(0, 5, 0, 0)
        translate_hint = QLabel(
            "中文会自动翻译成日语再合成（先用免费翻译 API，加 Key 可备用 DeepSeek）"
        )
        translate_hint.setObjectName("HelperText")
        translate_hint.setWordWrap(True)
        tf.addWidget(translate_hint)
        self.translate_key = QLineEdit()
        self.translate_key.setObjectName("TranslationFallbackKey")
        self.translate_key.setPlaceholderText("可选：DeepSeek API Key（免费翻译失效时备用）")
        self.translate_key.setStyleSheet(STYLE_INPUT)
        self.translate_key.setEchoMode(QLineEdit.Password)
        self.translate_key.setAccessibleName("翻译备用 DeepSeek API Key")
        tf.addWidget(self.translate_key)
        layout.addWidget(self.translate_frame)
        self._sync_engine_details_visibility()

        layout.addStretch()
        self._tl_widgets = []


        self._schedule_startup(500, self._check_gsv)

    def _schedule_startup(self, delay_ms: int, callback) -> None:
        """创建随页面销毁的单次启动计时器，避免关闭后回调已删除控件。"""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(delay_ms)
        self._startup_timers.append(timer)

    def log(self, msg):
        """日志输出（TTSPage 版本）"""
        # 输出到 stderr 让终端可见
        import sys as _sys
        print(f"[VITS] {msg}", file=_sys.stderr, flush=True)
        # 如果有父窗口的日志区也写一份
        parent = self.parent()
        while parent:
            if hasattr(parent, 'log'):
                parent.log(msg)
                return
            parent = parent.parent()

    def _toggle(self, on):
        """语音启用/禁用"""
        self.backend_combo.setEnabled(on)
        self.vits_python_input.setEnabled(on)
        self.gsv_dir_input.setEnabled(on)
        for attr in (
            "mimo_voice_input",
            "mimo_api_key_input",
            "mimo_api_base_input",
            "mimo_fill_from_chat_btn",
            "mimo_voice_lang_combo",
            "mimo_translate_jp_cb",
            "mimo_voiceclone_cb",
            "mimo_clone_ref_input",
            "gsv_ref_wav_input",
            "gsv_ref_lang_combo",
        ):
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(on)
        if on:
            self._toggle_backend()
        else:
            self.translate_frame.setVisible(False)
            if hasattr(self, "mimo_tts_frame"):
                self.mimo_tts_frame.setVisible(False)
            if hasattr(self, "vits_python_frame"):
                self.vits_python_frame.setVisible(False)
                self.vits_status.setVisible(False)
                self.setup_vits_btn.setVisible(False)
            if hasattr(self, "gsv_container"):
                self.gsv_container.setVisible(False)
        if hasattr(self, "engine_details_toggle"):
            self.engine_details_toggle.setEnabled(bool(on))
            self._sync_engine_details_visibility()

    def _toggle_backend(self):
        """切换语音后端：MiMo / VITS / GPT-SoVITS"""
        if (
            hasattr(self, "engine_details_toggle")
            and not self.engine_details_toggle.isChecked()
        ):
            # 细节折叠时只隐藏控件，避免与 _sync_engine_details_visibility 递归
            for attr in (
                "backend_label",
                "backend_combo",
                "mimo_tts_frame",
                "vits_python_frame",
                "vits_status",
                "setup_vits_btn",
                "gsv_container",
                "translate_frame",
            ):
                w = getattr(self, attr, None)
                if w is not None:
                    w.setVisible(False)
            return
        engine = self.backend_combo.currentData()
        is_mimo = engine == "mimo"
        is_vits = engine == "vits"
        is_gsv = engine == "gpt_sovits"

        if hasattr(self, "mimo_tts_frame"):
            self.mimo_tts_frame.setVisible(is_mimo and self.enable_cb.isChecked())
            if is_mimo and self.enable_cb.isChecked():
                # 进入 MiMo 语音时：检测并自动填入已有 Key
                self._detect_and_fill_mimo_key(force=False)
                if hasattr(self, "_on_mimo_voice_lang_changed"):
                    self._on_mimo_voice_lang_changed()
        if hasattr(self, "vits_python_frame"):
            self.vits_python_frame.setVisible(is_vits and self.enable_cb.isChecked())
            self.vits_status.setVisible(is_vits and self.enable_cb.isChecked())
            self.setup_vits_btn.setVisible(is_vits and self.enable_cb.isChecked())
        if hasattr(self, "gsv_container"):
            self.gsv_container.setVisible(is_gsv and self.enable_cb.isChecked())

        # 本地日语引擎才显示「译日语」备用 Key；MiMo 默认中文直出
        self.translate_frame.setVisible(
            self.enable_cb.isChecked() and not is_mimo
        )

        if is_vits:
            self._check_vits()

    def _sync_engine_details_visibility(self, *_args) -> None:
        """折叠引擎细节，只保留总开关时更适合首次上手。"""
        show = bool(
            self.enable_cb.isChecked()
            and getattr(self, "engine_details_toggle", None)
            and self.engine_details_toggle.isChecked()
        )
        if hasattr(self, "backend_label"):
            self.backend_label.setVisible(show)
        self.backend_combo.setVisible(show)
        if not show:
            for attr in (
                "mimo_tts_frame",
                "vits_python_frame",
                "vits_status",
                "setup_vits_btn",
                "gsv_container",
                "translate_frame",
            ):
                w = getattr(self, attr, None)
                if w is not None:
                    w.setVisible(False)
            return
        # 展开时交回原有后端显隐逻辑
        if self.enable_cb.isChecked():
            self._toggle_backend()

    def set_engine(self, engine: str):
        """按 data 值选中语音引擎。"""
        engine = (engine or "mimo").lower()
        for i in range(self.backend_combo.count()):
            if self.backend_combo.itemData(i) == engine:
                self.backend_combo.setCurrentIndex(i)
                break
        self._toggle_backend()


    def _on_mimo_voice_lang_changed(self, *_args):
        """根据合成语言更新 clone 提示 / 译日语开关可用性。"""
        lang = "jp"
        if hasattr(self, "mimo_voice_lang_combo"):
            lang = self.mimo_voice_lang_combo.currentData() or "jp"
        if hasattr(self, "mimo_translate_jp_cb"):
            self.mimo_translate_jp_cb.setEnabled(lang == "jp")
            if lang == "jp":
                # 切到日语时默认打开译日语（若用户已关则不强制？这里仅当未勾选时打开）
                if not self.mimo_translate_jp_cb.isChecked() and not getattr(self, "_loaded_from_config", False):
                    self.mimo_translate_jp_cb.setChecked(True)
            else:
                # 非日语时关闭译日语，避免误导
                self.mimo_translate_jp_cb.setChecked(False)
        tips = {
            "jp": "提示：当前合成语言=日语 → 自动优先 jp_*.wav（voice_cache / GPT-Sovits）。",
            "zh": "提示：当前合成语言=中文 → 自动优先 zh_*.wav（如 GPT-Sovits/normal/zh_normal.wav）。",
            "en": "提示：当前合成语言=英文 → 优先 en_* 参考；若无则回退其它样本。",
        }
        if hasattr(self, "mimo_clone_lang_tip"):
            self.mimo_clone_lang_tip.setText(tips.get(lang, tips["jp"]))
        if hasattr(self, "mimo_clone_ref_input") and not self.mimo_clone_ref_input.text().strip():
            placeholders = {
                "jp": "例如 ./GPT-Sovits/normal/jp_normal.wav",
                "zh": "例如 ./GPT-Sovits/normal/zh_normal.wav",
                "en": "例如 ./voice_cache/en_sample.wav",
            }
            self.mimo_clone_ref_input.setPlaceholderText(placeholders.get(lang, placeholders["jp"]))

    def apply_config(self, tts_cfg: dict):
        """从已有 config.tts 恢复语音页选项。"""
        if not isinstance(tts_cfg, dict):
            return
        self._loaded_from_config = True
        self.enable_cb.setChecked(bool(tts_cfg.get("enabled", True)))
        engine = tts_cfg.get("engine") or "gpt_sovits"
        # 兼容旧字段
        if tts_cfg.get("vits_mode") and engine not in ("mimo", "vits", "gpt_sovits"):
            engine = "vits"
        self.set_engine(engine)

        if hasattr(self, "mimo_voice_input"):
            voice = (tts_cfg.get("voice") or "冰糖").strip() or "冰糖"
            self.mimo_voice_input.setText(voice)
        if hasattr(self, "mimo_api_key_input"):
            key = (tts_cfg.get("api_key") or "").strip()
            if key:
                self.mimo_api_key_input.setText(key)
        if hasattr(self, "mimo_api_base_input"):
            base = (tts_cfg.get("api_base") or "").strip()
            if base:
                self.mimo_api_base_input.setText(base)
        if hasattr(self, "mimo_voice_lang_combo"):
            vlang = (tts_cfg.get("voice_lang") or "").strip().lower()
            if not vlang:
                # 缺省日语（梅尔人设）；中文需用户显式选择
                vlang = "jp"
            if vlang in ("ja", "jpn", "japanese", "日文", "日语"):
                vlang = "jp"
            elif vlang in ("cn", "zh-cn", "zh_cn", "chinese", "中文", "汉语"):
                vlang = "zh"
            elif vlang in ("eng", "english", "英文", "英语"):
                vlang = "en"
            for i in range(self.mimo_voice_lang_combo.count()):
                if self.mimo_voice_lang_combo.itemData(i) == vlang:
                    self.mimo_voice_lang_combo.setCurrentIndex(i)
                    break
            self._on_mimo_voice_lang_changed()

        if hasattr(self, "mimo_translate_jp_cb"):
            # 显式配置优先；否则：日语模式默认开翻译
            if "translate_to_jp" in tts_cfg:
                self.mimo_translate_jp_cb.setChecked(bool(tts_cfg.get("translate_to_jp")))
            else:
                cur_lang = (
                    self.mimo_voice_lang_combo.currentData()
                    if hasattr(self, "mimo_voice_lang_combo")
                    else "jp"
                )
                self.mimo_translate_jp_cb.setChecked(cur_lang == "jp")
            # 语言切完后再同步一次可用性
            cur_lang = (
                self.mimo_voice_lang_combo.currentData()
                if hasattr(self, "mimo_voice_lang_combo")
                else "jp"
            )
            self.mimo_translate_jp_cb.setEnabled(cur_lang == "jp")

        if hasattr(self, "mimo_voiceclone_cb"):
            model = str(tts_cfg.get("model") or "")
            use_clone = bool(tts_cfg.get("voice_clone")) or ("voiceclone" in model.lower())
            if (tts_cfg.get("voice") or "").lower() in ("clone", "voiceclone", "voice-clone"):
                use_clone = True
            if tts_cfg.get("clone_ref"):
                use_clone = True
            self.mimo_voiceclone_cb.setChecked(use_clone)
        if hasattr(self, "mimo_clone_ref_input"):
            cref = (tts_cfg.get("clone_ref") or tts_cfg.get("voice_ref") or "").strip()
            if cref:
                self.mimo_clone_ref_input.setText(cref)

        if hasattr(self, "gsv_ref_wav_input"):
            self.gsv_ref_wav_input.setText(
                str(tts_cfg.get("gsv_ref_wav") or "").strip()
            )
        if hasattr(self, "gsv_ref_lang_combo"):
            ref_lang = normalize_gsv_ref_language(
                tts_cfg.get("gsv_ref_lang")
            )
            for index in range(self.gsv_ref_lang_combo.count()):
                if self.gsv_ref_lang_combo.itemData(index) == ref_lang:
                    self.gsv_ref_lang_combo.setCurrentIndex(index)
                    break

        vits_py = (tts_cfg.get("vits_python") or "").strip()
        if vits_py and hasattr(self, "vits_python_input"):
            self.vits_python_input.setText(vits_py)

        # python_exe 可能是 GSV 的 python 路径
        gsv_py = (tts_cfg.get("python_exe") or "").strip()
        if gsv_py and hasattr(self, "gsv_dir_input"):
            # 若是 .../runtime/python.exe，回填到整合包根目录更友好
            gsv_dir = gsv_py
            if gsv_py.lower().endswith("python.exe"):
                parent = os.path.dirname(gsv_py)
                if os.path.basename(parent).lower() == "runtime":
                    gsv_dir = os.path.dirname(parent)
                else:
                    gsv_dir = parent
            self.gsv_dir_input.setText(gsv_dir)

        tk = (tts_cfg.get("translate_api_key") or "").strip()
        if tk and hasattr(self, "translate_key"):
            self.translate_key.setText(tk)

        self._toggle(self.enable_cb.isChecked())
        if engine == "mimo" and self.enable_cb.isChecked():
            self._detect_and_fill_mimo_key(force=False)
            self._refresh_mimo_key_status()
