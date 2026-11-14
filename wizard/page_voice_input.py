"""语音输入配置页面 — 向导第 5 标签页。

默认关闭（隐私优先）。启用后支持：
- 本地 sherpa-onnx 离线转文字（中英双语，档位可选：int8 / fp32）
- 选择录音设备（默认自动选路）
- 一键安装依赖 + 按档下载模型（后台线程，不卡 UI）
"""
from __future__ import annotations

import copy
import subprocess
import sys

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from meapet.config.normalizers import normalize_input_device_index
from meapet.dependencies import (
    VOICE_INPUT_INSTALL_REQUIREMENTS,
    resolve_pip_index_url,
)
from meapet.paths import (
    VOICE_ASR_MODEL_FILES,
    VOICE_ASR_MODEL_REPO,
    VOICE_ASR_PRECISION_DEFAULT,
    VOICE_ASR_REQUIRED_FILES_BY_PRECISION,
    find_voice_asr_model_dir,
    is_frozen,
    normalize_voice_asr_precision,
    voice_asr_cache_dir,
)
from meapet.voice.engine import VOICE_INPUT_SAMPLE_RATE, list_input_devices
from wizard.styles import (
    STYLE_PAGE_CARD,
    set_status,
)
from meapet.ui_theme import apply_named_style
from wizard.widgets import WheelSafeComboBox


# 设备下拉的不可用标注：产品录音固定按 (VOICE_INPUT_SAMPLE_RATE, 单声道) 开流，
# 实测同一批设备会分别栽在采样率（Errno -9997）与通道数（-9998）上，所以按整组格式写。
# 提示语引用的那串字与列表里显示的标签必须同源。
_UNSUPPORTED_TAG = f"不支持 {VOICE_INPUT_SAMPLE_RATE // 1000}kHz 单声道"

# 档位文案：体积与加载差是本机实测（2026-10-05，两档各真下真载过一次），
# 落在磁盘上的模型文件本身仍以 meapet.paths.VOICE_ASR_MODEL_FILES 为唯一真值。
# fp32 的**精度优势没有证据**（三条样本读得反而更短更糊），所以文案只报代价。
_TIER_HINTS = {
    "int8": ("int8（默认）", "约 198 MB"),
    "fp32": ("fp32（加载多 2.6 秒，精度优势未实测）", "约 357 MB"),
}


def _tier_label(precision: str) -> str:
    return _TIER_HINTS.get(precision, (precision, "所选档位"))[0]


def _tier_size(precision: str) -> str:
    return _TIER_HINTS.get(precision, (precision, "所选档位"))[1]


def _known_tiers() -> list[str]:
    """下拉可选项 = 单一真值表里的档位，默认档排最前。"""
    return [VOICE_ASR_PRECISION_DEFAULT] + [
        tier for tier in VOICE_ASR_MODEL_FILES if tier != VOICE_ASR_PRECISION_DEFAULT
    ]


class _InstallWorker(QThread):
    """后台线程：pip install + ModelScope 按档模型下载。"""
    progress = pyqtSignal(str)       # 阶段文字更新
    finished = pyqtSignal(bool, str)  # (success, detail)

    def __init__(self, precision: str = VOICE_ASR_PRECISION_DEFAULT, parent=None):
        super().__init__(parent)
        self._precision = normalize_voice_asr_precision(precision)

    def run(self):
        cmd = _voice_input_install_command()
        if cmd is None:
            self.finished.emit(
                False,
                "当前是冻结版程序，不支持运行时安装语音依赖或下载模型。"
                "请在构建环境准备依赖和模型后重新打包。",
            )
            return

        # 步骤 1: pip install
        self.progress.emit("正在安装语音识别依赖…")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                self.finished.emit(False, err[:500] if err else "pip 安装失败")
                return
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "安装超时，请检查网络")
            return
        except Exception as exc:
            self.finished.emit(False, str(exc))
            return

        # 步骤 2: 只下所选档位那几个文件——整仓 592MB，另一档运行时永远读不到
        allow_patterns = list(VOICE_ASR_REQUIRED_FILES_BY_PRECISION[self._precision])
        self.progress.emit(
            f"正在下载 {self._precision} 档语音识别模型"
            f"（{_tier_size(self._precision)}）…"
        )
        try:
            from modelscope import snapshot_download

            snapshot_download(
                VOICE_ASR_MODEL_REPO,
                cache_dir=str(voice_asr_cache_dir()),
                allow_patterns=allow_patterns,
            )
            self.finished.emit(True, "安装完成！依赖 + 模型已就绪")
        except ImportError:
            self.finished.emit(
                False,
                "模型下载失败：modelscope 安装后仍无法导入，请检查安装日志。",
            )
        except Exception as exc:
            self.finished.emit(False, f"模型下载失败: {exc}")


def _voice_input_install_command() -> list[str] | None:
    """Return the source-runtime pip command; frozen EXEs cannot run pip."""
    if is_frozen():
        return None
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--index-url",
        resolve_pip_index_url(),
        *VOICE_INPUT_INSTALL_REQUIREMENTS,
    ]


class _VadModelWorker(QThread):
    """后台线程：只下 Silero VAD 那一个小文件（~2 MB）。

    与 ASR 的 _InstallWorker 分开，是因为**可达成性不同**：ASR 那一路要 pip 装
    sherpa+pyaudio，冻结版走不通；VAD 只是一个 HTTPS 文件，用 requests 直下即可，
    冻结版也能下——这正是「冻结版语音输入不可用」这条封锁里能先解开的一格。
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def run(self):
        from meapet.paths import VOICE_VAD_MODEL_URL, voice_vad_cache_dir

        target_dir = voice_vad_cache_dir()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.finished.emit(False, f"建目录失败: {exc}")
            return
        target = target_dir / "silero_vad.onnx"
        staging = target.with_suffix(".onnx.download")
        self.progress.emit("正在下载 Silero VAD 模型（约 2 MB）…")
        try:
            import requests

            with requests.get(VOICE_VAD_MODEL_URL, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(staging, "wb") as handle:
                    for chunk in resp.iter_content(1 << 16):
                        if chunk:
                            handle.write(chunk)
            size = staging.stat().st_size
            # 太小说明拿到的是错误页/重定向壳，不是模型——宁可不换，也不写一个假模型进去
            if size < 100_000:
                staging.unlink(missing_ok=True)
                self.finished.emit(
                    False,
                    f"下载到的文件只有 {size} 字节，不是模型（可能被网络拦截）",
                )
                return
            staging.replace(target)
            self.finished.emit(True, f"VAD 模型已就绪（{size // 1024} KB）")
        except Exception as exc:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit(False, f"下载失败: {exc}")


class VoiceInputPage(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        apply_named_style(self, "STYLE_PAGE_CARD")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        title = QLabel("语音输入")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel(
            "开启语音输入后，打开输入框会多出一个麦克风按钮。\n"
            "点击开始录音，再点停止 → 自动转文字填入输入框。\n"
            "识别引擎为 sherpa-onnx，中英双语，完全离线。"
        )
        desc.setObjectName("PageDescription")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.enable_cb = QCheckBox("启用语音输入（默认关闭）")
        self.enable_cb.setChecked(False)
        self.enable_cb.toggled.connect(self._on_enable_changed)
        layout.addWidget(self.enable_cb)

        progressive = QLabel("可先不启用。需要时再打开，并在下方安装依赖。")
        progressive.setObjectName("HelperText")
        progressive.setWordWrap(True)
        layout.addWidget(progressive)

        self.settings_frame = QFrame()
        self.settings_frame.setObjectName("SectionCard")
        self.settings_layout = QVBoxLayout(self.settings_frame)
        self.settings_layout.setContentsMargins(16, 14, 16, 14)
        self.settings_layout.setSpacing(10)
        self.settings_frame.setVisible(False)
        layout.addWidget(self.settings_frame)

        lang_label = QLabel("识别语言：")
        lang_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(lang_label)
        self.lang_combo = WheelSafeComboBox()
        self.lang_combo.setObjectName("VoiceLanguage")
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("自动检测", "auto")
        self.lang_combo.addItem("英文", "en")
        self.lang_combo.addItem("日文", "ja")
        self.settings_layout.addWidget(self.lang_combo)

        mic_label = QLabel("录音设备：")
        mic_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(mic_label)
        self.device_combo = WheelSafeComboBox()
        self.device_combo.setObjectName("VoiceInputDevice")
        self.device_combo.addItem("自动（推荐）", None)
        self.settings_layout.addWidget(self.device_combo)
        mic_note = QLabel(
            "自动按系统默认选路。录到的声音不对（虚拟麦克风、静音设备）时改成你那台。\n"
            f"标了「{_UNSUPPORTED_TAG}」的设备选了也不会生效，运行时会回落自动选路。"
        )
        mic_note.setObjectName("HelperText")
        mic_note.setWordWrap(True)
        self.settings_layout.addWidget(mic_note)

        self.auto_send_cb = QCheckBox("识别完成后自动发送（关闭则只填入输入框）")
        self.auto_send_cb.setChecked(False)
        self.auto_send_cb.setToolTip(
            "开启后语音识别结果会直接发送；关闭则先填入输入框等你确认。"
        )
        self.settings_layout.addWidget(self.auto_send_cb)

        prec_label = QLabel("模型精度：")
        prec_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(prec_label)
        self.precision_combo = WheelSafeComboBox()
        self.precision_combo.setObjectName("VoiceAsrPrecision")
        for tier in _known_tiers():
            self.precision_combo.addItem(_tier_label(tier), tier)
        self.settings_layout.addWidget(self.precision_combo)

        deps_label = QLabel("一键安装（依赖 + 模型）")
        deps_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(deps_label)
        deps_desc = QLabel(
            "安装 sherpa-onnx + pyaudio，并下载中英双语语音识别模型。\n"
            "只下载上面选中的档位，仅需下载一次。"
        )
        deps_desc.setObjectName("HelperText")
        deps_desc.setWordWrap(True)
        self.settings_layout.addWidget(deps_desc)

        self.install_btn = QPushButton("安装语音识别依赖与模型")
        self.install_btn.setProperty("doesNotModifyConfig", True)
        self.install_btn.clicked.connect(self._install_deps)
        self.settings_layout.addWidget(self.install_btn)

        self.deps_status = QLabel("")
        self.deps_status.setWordWrap(True)
        self.settings_layout.addWidget(self.deps_status)

        self.deps_status_detail = QLabel("")
        self.deps_status_detail.setWordWrap(True)
        self.deps_status_detail.setProperty("status", "muted")
        self.settings_layout.addWidget(self.deps_status_detail)

        # P1-3：VAD 自动断句。与上面那条分开，因为它**冻结版也能装**——
        # 只是一个 2 MB 的 HTTPS 文件，不需要 pip。
        vad_label = QLabel("自动断句（VAD）")
        vad_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(vad_label)
        vad_desc = QLabel(
            "常驻收音时自动判断一句话说完了没有，说完就转写并发送。\n"
            "Silero VAD 模型约 2 MB，冻结版也可直接下载。"
        )
        vad_desc.setObjectName("HelperText")
        vad_desc.setWordWrap(True)
        self.settings_layout.addWidget(vad_desc)

        self.vad_btn = QPushButton("下载 VAD 模型")
        self.vad_btn.setProperty("doesNotModifyConfig", True)
        self.vad_btn.clicked.connect(self._install_vad)
        self.settings_layout.addWidget(self.vad_btn)

        self.vad_status = QLabel("")
        self.vad_status.setWordWrap(True)
        self.settings_layout.addWidget(self.vad_status)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("status", "muted")
        self.settings_layout.addWidget(self.hint)

        layout.addStretch()
        self.precision_combo.currentIndexChanged.connect(self._check_deps)
        self._check_deps()
        self._check_vad()
        self._install_worker = None
        self._vad_worker = None

    def _on_enable_changed(self, enabled: bool):
        self.settings_frame.setVisible(enabled)
        if enabled:
            self._check_deps()

    def _sync_hint(self):
        set_status(
            self.hint, "muted",
            "sherpa-onnx 完全离线运行，无需 API Key。"
        )

    def _precision(self) -> str:
        return normalize_voice_asr_precision(self.precision_combo.currentData())

    def _model_ok(self) -> bool:
        """检查所选档位的 ASR 模型文件是否齐全。"""
        return find_voice_asr_model_dir(self._precision()) is not None

    def _apply_devices(self, selected=None) -> None:
        """重建录音设备下拉并选中 ``selected``。

        ``selected`` 是存过的设备号但当前枚举不到时，仍然列出来并标「当前不可用」——
        否则一次保存就会把用户挑过的设备静默抹成「自动」。
        """
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("自动（推荐）", None)
        known: list[int] = []
        for index, name, channels, supported in list_input_devices():
            known.append(index)
            unsupported = "" if supported else f" · {_UNSUPPORTED_TAG}"
            self.device_combo.addItem(
                f"{name}（{channels} 声道 · index {index}{unsupported}）", index
            )
        if selected is not None and selected not in known:
            self.device_combo.addItem(f"index {selected}（当前不可用）", selected)
        target = 0
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == selected:
                target = i
                break
        self.device_combo.setCurrentIndex(target)
        self.device_combo.blockSignals(False)

    def _check_deps(self):
        missing = []
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            missing.append("pyaudio")
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            missing.append("sherpa-onnx")

        if "pyaudio" not in missing:
            self._apply_devices(self.device_combo.currentData())

        precision = self._precision()
        size = _tier_size(precision)
        model_ok = self._model_ok()
        if not missing and model_ok:
            set_status(self.deps_status, "success", f"依赖与 {precision} 档模型已就绪")
            self.deps_status_detail.setText("可以正常使用语音输入功能")
            self.install_btn.setEnabled(False)
            self.install_btn.setText("已安装就绪")
        elif is_frozen():
            set_status(
                self.deps_status,
                "warning",
                f"冻结版缺少语音识别依赖或 {precision} 档模型",
            )
            self.deps_status_detail.setText(
                "当前冻结版不支持运行时安装或下载。"
                "请在构建环境准备依赖和模型后重新打包。"
            )
            self.install_btn.setEnabled(False)
            self.install_btn.setText("需重新打包")
        elif not missing:
            set_status(self.deps_status, "warning", f"依赖已安装，但 {precision} 档模型缺失")
            self.deps_status_detail.setText(f"点击按钮下载语音识别模型（{size}）")
            self.install_btn.setEnabled(True)
            self.install_btn.setText("下载语音识别模型")
        else:
            set_status(self.deps_status, "warning",
                       f"缺少: {', '.join(missing)}")
            self.deps_status_detail.setText(
                "点击按钮一键安装依赖库并下载模型\n"
                f"（sherpa-onnx + pyaudio + {precision} 档模型 {size}）"
            )
            self.install_btn.setEnabled(True)
            self.install_btn.setText("安装语音识别依赖与模型")

    def _vad_model_ok(self) -> bool:
        from meapet.paths import find_voice_vad_model

        return find_voice_vad_model() is not None

    def _check_vad(self) -> None:
        if self._vad_model_ok():
            set_status(self.vad_status, "success", "VAD 模型已就绪")
            self.vad_btn.setEnabled(False)
            self.vad_btn.setText("已就绪")
        else:
            set_status(self.vad_status, "muted", "VAD 模型未下载（不使用自动断句可不装）")
            self.vad_btn.setEnabled(True)
            self.vad_btn.setText("下载 VAD 模型")

    def _install_vad(self) -> None:
        self.vad_btn.setEnabled(False)
        self.vad_btn.setText("下载中…")
        set_status(self.vad_status, "info", "正在下载…")
        self._vad_worker = _VadModelWorker(self)
        self._vad_worker.progress.connect(lambda text: set_status(self.vad_status, "info", text))
        self._vad_worker.finished.connect(self._on_vad_finished)
        self._vad_worker.start()

    def _on_vad_finished(self, success: bool, detail: str) -> None:
        if success:
            set_status(self.vad_status, "success", detail)
            self._check_vad()
        else:
            set_status(self.vad_status, "error", detail)
            self.vad_btn.setEnabled(True)
            self.vad_btn.setText("重试下载")

    def _install_deps(self):
        if is_frozen():
            self._on_install_finished(
                False,
                "当前是冻结版程序，不支持运行时安装语音依赖或下载模型。"
                "请在构建环境准备依赖和模型后重新打包。",
            )
            return
        self.install_btn.setEnabled(False)
        self.install_btn.setText("安装中…")
        set_status(self.deps_status, "info", "第一步：安装 Python 依赖…")
        self.deps_status_detail.setText("正在后台执行，请稍候")

        self._install_worker = _InstallWorker(self._precision())
        self._install_worker.progress.connect(self.deps_status_detail.setText)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_finished(self, success: bool, detail: str):
        if success:
            self._check_deps()
        else:
            set_status(self.deps_status, "error", detail)
            frozen = is_frozen()
            self.install_btn.setEnabled(not frozen)
            self.install_btn.setText("需重新打包" if frozen else "重试安装")

    def apply_config(self, voice_cfg: dict):
        voice_cfg = voice_cfg or {}
        # P1-3 这几件本页不编辑，但 collect() 会把它们原样带回去——
        # 不在这里接住，向导保存一次就把用户的 mode/ptt/vad/wake_word 抹成默认。
        self._passthrough = {
            key: copy.deepcopy(voice_cfg.get(key))
            for key in (
                "mode",
                "ptt_hotkey",
                "vad",
                "wake_word",
                "echo_gate_tail_ms",
            )
            if key in voice_cfg
        }
        self.enable_cb.setChecked(bool(voice_cfg.get("enabled", False)))

        lang = str(voice_cfg.get("language", "zh")).strip().lower()
        lidx = self.lang_combo.findData(lang)
        self.lang_combo.setCurrentIndex(lidx if lidx >= 0 else 0)

        pidx = self.precision_combo.findData(
            normalize_voice_asr_precision(voice_cfg.get("precision"))
        )
        self.precision_combo.setCurrentIndex(pidx if pidx >= 0 else 0)

        self._apply_devices(normalize_input_device_index(
            voice_cfg.get("input_device_index")
        ))

        self.auto_send_cb.setChecked(bool(voice_cfg.get("auto_send", False)))
        self._sync_hint()

    def collect(self) -> dict:
        # 本页不编辑的 P1-3 键原样带回（见 apply_config）：向导只该改它认识的那几格。
        payload = copy.deepcopy(getattr(self, "_passthrough", {}))
        payload.update(
            {
                "enabled": self.enable_cb.isChecked(),
                "language": self.lang_combo.currentData() or "zh",
                "auto_send": self.auto_send_cb.isChecked(),
                "precision": self._precision(),
                "input_device_index": normalize_input_device_index(
                    self.device_combo.currentData()
                ),
            }
        )
        return {"voice_input": payload}
