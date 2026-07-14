"""配置向导各页面"""
from __future__ import annotations

import os

from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from wizard.styles import (
    MIN_TARGET_SIZE,
    STYLE_INPUT,
    STYLE_PAGE_CARD,
)

# 兼容页面内可能使用的短名
from wizard.page_tts_gsv import TtsPageGsvMixin
from wizard.page_tts_mimo import TtsPageMimoMixin
from wizard.page_tts_vits import TtsPageVitsMixin
from meapet.config.normalizers import normalize_gsv_ref_language
from wizard.widgets import WheelSafeComboBox


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

        test_row = QHBoxLayout()
        self.test_connection_btn = QPushButton("测试当前语音引擎")
        self.test_connection_btn.setAccessibleName("测试当前语音引擎连接")
        self.test_connection_btn.setProperty("doesNotModifyConfig", True)
        test_row.addWidget(self.test_connection_btn)
        self.connection_status = QLabel("尚未测试；测试会合成一句短音频")
        self.connection_status.setProperty("status", "muted")
        self.connection_status.setAccessibleName("语音引擎连接测试状态")
        self.connection_status.setWordWrap(True)
        test_row.addWidget(self.connection_status, 1)
        layout.addLayout(test_row)

        self.engine_details_toggle = QCheckBox("显示引擎详细设置")
        self.engine_details_toggle.setChecked(False)
        self.engine_details_toggle.setAccessibleName("显示引擎详细设置")
        self.engine_details_toggle.setProperty("doesNotModifyConfig", True)
        self.engine_details_toggle.setToolTip("关闭后只保留总开关，适合先开玩再细调")
        self.engine_details_toggle.toggled.connect(self._sync_engine_details_visibility)
        layout.addWidget(self.engine_details_toggle)

        # ═══ 语音后端选择 ═══
        self.backend_label = QLabel("选择语音引擎：")
        self.backend_label.setObjectName("FieldLabel")
        layout.addWidget(self.backend_label)

        self.backend_combo = WheelSafeComboBox()
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

        # 默认/翻译兜底语言（正常回复仍优先遵循模型输出语言）
        lang_row = QHBoxLayout()
        language_label = QLabel("默认合成语言：")
        language_label.setObjectName("FieldLabel")
        lang_row.addWidget(language_label)
        self.mimo_voice_lang_combo = WheelSafeComboBox()
        self.mimo_voice_lang_combo.setObjectName("MimoVoiceLanguage")
        self.mimo_voice_lang_combo.setAccessibleName("语音合成语言")
        # 默认日语（梅尔日语人设）；中文/英文可选
        self.mimo_voice_lang_combo.addItem("日语（默认，克隆用 jp_* 参考）", "jp")
        self.mimo_voice_lang_combo.addItem("中文（克隆用 zh_* 参考）", "zh")
        self.mimo_voice_lang_combo.addItem("英文", "en")
        self.mimo_voice_lang_combo.setToolTip(
            "旧格式回复或需要翻译兜底时使用；新回复会优先遵循 voice_language。\n"
            "voice-clone 只会使用与最终合成语言一致的参考音频。"
        )
        self.mimo_voice_lang_combo.currentIndexChanged.connect(self._on_mimo_voice_lang_changed)
        lang_row.addWidget(self.mimo_voice_lang_combo, 1)
        mimo_layout.addLayout(lang_row)

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
            "按回复语言各指定一条固定参考音频（可选；留空时继续自动选择）。"
            "若旁边有同名 .txt，会自动作为参考文本。"
        )
        ref_hint.setObjectName("HelperText")
        ref_hint.setWordWrap(True)
        gsv_layout.addWidget(ref_hint)

        self.gsv_reference_inputs = {}
        self.gsv_reference_buttons = {}
        self._gsv_reference_texts = {}
        self._gsv_reference_loaded_paths = {}
        reference_labels = (("jp", "日语"), ("zh", "中文"), ("en", "英语"))
        reference_examples = {
            "jp": "例如 ./GPT-Sovits/normal/jp_normal.wav",
            "zh": "例如 ./GPT-Sovits/normal/zh_normal.wav",
            "en": "例如 ./voice_cache/en_sample.wav",
        }
        for language, label_text in reference_labels:
            ref_row = QHBoxLayout()
            ref_label = QLabel(f"{label_text}：")
            ref_label.setObjectName("FieldLabel")
            ref_label.setMinimumWidth(52)
            ref_row.addWidget(ref_label)

            ref_input = QLineEdit()
            ref_input.setObjectName(
                f"GsvReferenceAudio{language.title()}"
            )
            ref_input.setPlaceholderText(reference_examples[language])
            ref_input.setStyleSheet(STYLE_INPUT)
            ref_input.setAccessibleName(
                f"GPT-SoVITS {label_text}固定参考音频路径"
            )
            ref_input.setAccessibleDescription(
                f"{label_text}回复优先使用这条参考音频"
            )
            ref_row.addWidget(ref_input, 1)

            ref_browse_btn = QPushButton("选择音频…")
            ref_browse_btn.setMinimumSize(104, MIN_TARGET_SIZE)
            ref_browse_btn.setAccessibleName(
                f"选择 GPT-SoVITS {label_text}参考音频"
            )
            ref_browse_btn.clicked.connect(
                lambda _checked=False, lang=language: self._browse_gsv_ref_wav(lang)
            )
            ref_row.addWidget(ref_browse_btn)
            gsv_layout.addLayout(ref_row)
            self.gsv_reference_inputs[language] = ref_input
            self.gsv_reference_buttons[language] = ref_browse_btn

        layout.addWidget(self.gsv_container)

        packaged_hint = QLabel("语音模型已打包，开箱即用。")
        packaged_hint.setObjectName("HelperText")
        layout.addWidget(packaged_hint)

        # 翻译只是“输出语言不受 TTS 支持”时的显式兜底。
        self.translate_frame = QFrame()
        tf = QVBoxLayout(self.translate_frame)
        tf.setContentsMargins(0, 5, 0, 0)
        self.translation_enabled_cb = QCheckBox(
            "输出语言不受支持时，使用翻译 API 后再合成"
        )
        self.translation_enabled_cb.setChecked(False)
        self.translation_enabled_cb.setAccessibleName("TTS 语言翻译兜底")
        self.translation_enabled_cb.setToolTip(
            "仅在 voice_language 没有可用 TTS/参考音频时调用。\n"
            "模型请求失败、TTS 请求失败都不会触发翻译。"
        )
        tf.addWidget(self.translation_enabled_cb)
        # 保留旧属性名，避免第三方页面扩展失效。
        self.mimo_translate_jp_cb = self.translation_enabled_cb

        target_row = QHBoxLayout()
        target_label = QLabel("翻译目标语言：")
        target_label.setObjectName("FieldLabel")
        target_row.addWidget(target_label)
        self.translate_target_combo = WheelSafeComboBox()
        self.translate_target_combo.setObjectName("TtsTranslationTargetLanguage")
        self.translate_target_combo.setAccessibleName("TTS 翻译目标语言")
        self.translate_target_combo.addItem("日语", "jp")
        self.translate_target_combo.addItem("中文", "zh")
        self.translate_target_combo.addItem("英语", "en")
        target_row.addWidget(self.translate_target_combo, 1)
        tf.addLayout(target_row)

        translate_hint = QLabel(
            "只会翻译到已有固定参考音频、且当前 TTS 可合成的语言。"
        )
        translate_hint.setObjectName("HelperText")
        translate_hint.setWordWrap(True)
        tf.addWidget(translate_hint)
        self.translate_key = QLineEdit()
        self.translate_key.setObjectName("TranslationApiKey")
        self.translate_key.setPlaceholderText("启用语言兜底时必填：翻译 API Key")
        self.translate_key.setStyleSheet(STYLE_INPUT)
        self.translate_key.setEchoMode(QLineEdit.Password)
        self.translate_key.setAccessibleName("翻译 API Key")
        tf.addWidget(self.translate_key)
        layout.addWidget(self.translate_frame)
        self._sync_engine_details_visibility()

        layout.addStretch()
        self._tl_widgets = []
        # 首帧只同步控件显隐；GSV 子进程探测改由用户点击连接测试触发。
        self._toggle_backend()

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
        self.test_connection_btn.setEnabled(bool(on))
        self.backend_combo.setEnabled(on)
        self.vits_python_input.setEnabled(on)
        self.gsv_dir_input.setEnabled(on)
        for attr in (
            "mimo_voice_input",
            "mimo_api_key_input",
            "mimo_api_base_input",
            "mimo_fill_from_chat_btn",
            "mimo_voice_lang_combo",
            "mimo_voiceclone_cb",
            "mimo_clone_ref_input",
            "translation_enabled_cb",
            "translate_target_combo",
            "translate_key",
        ):
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(on)
        for widget in getattr(self, "gsv_reference_inputs", {}).values():
            widget.setEnabled(on)
        for widget in getattr(self, "gsv_reference_buttons", {}).values():
            widget.setEnabled(on)
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

        self.translate_frame.setVisible(self.enable_cb.isChecked())

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
        """根据默认合成语言更新 clone 提示。"""
        lang = "jp"
        if hasattr(self, "mimo_voice_lang_combo"):
            lang = self.mimo_voice_lang_combo.currentData() or "jp"
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

        if hasattr(self, "translation_enabled_cb"):
            self.translation_enabled_cb.setChecked(
                bool(tts_cfg.get("translate_to_jp", False))
            )
        if hasattr(self, "translate_target_combo"):
            target_language = normalize_gsv_ref_language(
                tts_cfg.get("translate_target_language")
                or tts_cfg.get("voice_lang")
                or "jp"
            )
            for index in range(self.translate_target_combo.count()):
                if self.translate_target_combo.itemData(index) == target_language:
                    self.translate_target_combo.setCurrentIndex(index)
                    break

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

        if hasattr(self, "gsv_reference_inputs"):
            references = {}
            raw_references = tts_cfg.get("reference_audios")
            if isinstance(raw_references, dict):
                for raw_language, raw_entry in raw_references.items():
                    language = normalize_gsv_ref_language(raw_language)
                    if isinstance(raw_entry, dict):
                        path = str(raw_entry.get("path") or "").strip()
                        text = str(raw_entry.get("text") or "").strip()
                    else:
                        path = str(raw_entry or "").strip()
                        text = ""
                    references[language] = {"path": path, "text": text}
            legacy_path = str(tts_cfg.get("gsv_ref_wav") or "").strip()
            legacy_language = normalize_gsv_ref_language(
                tts_cfg.get("gsv_ref_lang")
            )
            if legacy_path and legacy_language not in references:
                references[legacy_language] = {
                    "path": legacy_path,
                    "text": "",
                }
            self._gsv_reference_texts = {}
            self._gsv_reference_loaded_paths = {}
            for language, widget in self.gsv_reference_inputs.items():
                entry = references.get(language) or {}
                loaded_path = str(entry.get("path") or "")
                widget.setText(loaded_path)
                self._gsv_reference_loaded_paths[language] = loaded_path
                self._gsv_reference_texts[language] = str(
                    entry.get("text") or ""
                )

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
