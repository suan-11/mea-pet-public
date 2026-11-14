"""TTS 设置页 · 轻量 ONNX 离线引擎段（P1-4 收尾）。

与另外三个引擎 mixin 同形：控件构建、显隐、状态读数、下载线程。
取件走 meapet.onnx_voice_pack（钉住 sha256 + 多路由 + 原子换入），
本文件只管 UI 与线程，不碰网络逻辑。
"""
from __future__ import annotations

import os
import threading

from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from meapet.ui_theme import apply_named_style
from wizard.page_tts_vits import _post_to_main
from wizard.styles import MIN_TARGET_SIZE, set_status, styled_open_directory
from wizard.widgets import WheelSafeComboBox


class TtsPageOnnxMixin:
    def _build_onnx_section(self, layout):
        """构建 ONNX 引擎段；由 TTSPage.__init__ 在 VITS 离线包段之后调用。"""
        self.onnx_frame = QFrame()
        self.onnx_frame.setObjectName("SectionCard")
        onnx_layout = QVBoxLayout(self.onnx_frame)
        onnx_layout.setContentsMargins(18, 16, 18, 18)
        onnx_layout.setSpacing(10)

        description = QLabel(
            "轻量离线语音（ONNX）：不需要 torch 与整合包，与语音输入共用 sherpa-onnx 底座。\n"
            "模型约 20 MB，中文可读可用；不支持音色克隆与情绪（那两项用 GPT-SoVITS）。"
        )
        description.setObjectName("HelperText")
        description.setWordWrap(True)
        onnx_layout.addWidget(description)

        dir_label = QLabel("模型目录：")
        dir_label.setObjectName("FieldLabel")
        onnx_layout.addWidget(dir_label)
        dir_row = QHBoxLayout()
        self.onnx_model_dir_input = QLineEdit()
        self.onnx_model_dir_input.setObjectName("OnnxModelDir")
        self.onnx_model_dir_input.setPlaceholderText(
            "留空用默认目录 models/voice_onnx（冻结版在 _internal 内）"
        )
        apply_named_style(self.onnx_model_dir_input, "STYLE_INPUT")
        self.onnx_model_dir_input.setAccessibleName("ONNX 语音模型目录")
        self.onnx_model_dir_input.editingFinished.connect(self._check_onnx_model)
        dir_row.addWidget(self.onnx_model_dir_input)
        browse_btn = QPushButton("浏览…")
        browse_btn.setMinimumSize(88, MIN_TARGET_SIZE)
        browse_btn.setAccessibleName("选择 ONNX 语音模型目录")
        browse_btn.clicked.connect(self._browse_onnx_dir)
        dir_row.addWidget(browse_btn)
        onnx_layout.addLayout(dir_row)

        self.onnx_model_status = QLabel("")
        self.onnx_model_status.setProperty("status", "muted")
        self.onnx_model_status.setWordWrap(True)
        self.onnx_model_status.setAccessibleName("ONNX 语音模型状态")
        onnx_layout.addWidget(self.onnx_model_status)

        url_row = QHBoxLayout()
        self.onnx_url_input = QLineEdit()
        self.onnx_url_input.setObjectName("OnnxVoiceUrl")
        self.onnx_url_input.setPlaceholderText(
            "留空用官方 Release 地址（GitHub 不通时自动换镜像）；也可粘贴镜像直链"
        )
        apply_named_style(self.onnx_url_input, "STYLE_INPUT")
        self.onnx_url_input.setAccessibleName("ONNX 语音模型下载地址")
        url_row.addWidget(self.onnx_url_input)
        self.onnx_download_btn = QPushButton("下载语音模型（约 14 MB）")
        self.onnx_download_btn.setMinimumSize(180, MIN_TARGET_SIZE)
        self.onnx_download_btn.setAccessibleName("下载 ONNX 语音模型")
        self.onnx_download_btn.clicked.connect(self._download_onnx_model)
        url_row.addWidget(self.onnx_download_btn)
        onnx_layout.addLayout(url_row)

        self.onnx_download_status = QLabel("")
        self.onnx_download_status.setProperty("status", "muted")
        self.onnx_download_status.setWordWrap(True)
        self.onnx_download_status.setAccessibleName("ONNX 语音模型下载状态")
        onnx_layout.addWidget(self.onnx_download_status)

        speed_row = QHBoxLayout()
        speed_label = QLabel("语速：")
        speed_label.setObjectName("FieldLabel")
        speed_row.addWidget(speed_label)
        self.onnx_speed_combo = WheelSafeComboBox()
        self.onnx_speed_combo.setObjectName("OnnxSpeed")
        self.onnx_speed_combo.setAccessibleName("ONNX 语速")
        for value, label in (
            (0.8, "0.8×（较慢）"),
            (1.0, "1.0×（默认）"),
            (1.2, "1.2×（较快）"),
            (1.5, "1.5×（更快）"),
        ):
            self.onnx_speed_combo.addItem(label, value)
        self.onnx_speed_combo.setCurrentIndex(1)
        speed_row.addWidget(self.onnx_speed_combo, 1)
        onnx_layout.addLayout(speed_row)

        layout.addWidget(self.onnx_frame)

    # ------------------------------------------------------------------
    # 状态读数
    # ------------------------------------------------------------------

    def _onnx_effective_dir(self) -> str:
        """输入框有值用输入框；留空回落默认目录（与交付码同一处）。"""
        from meapet.paths import voice_onnx_model_dir

        text = self.onnx_model_dir_input.text().strip()
        return text or str(voice_onnx_model_dir())

    def _check_onnx_model(self):
        """与引擎同一判据：恰好一个 .onnx + tokens.txt。在场/缺席如实报。"""
        directory = self._onnx_effective_dir()
        if not os.path.isdir(directory):
            set_status(
                self.onnx_model_status, "muted",
                f"模型尚未下载（默认目录 {directory}）——点「下载语音模型」",
            )
            return
        try:
            entries = os.listdir(directory)
        except OSError as exc:
            set_status(self.onnx_model_status, "error", f"模型目录读不了：{exc}")
            return
        onnx_files = [f for f in entries if f.endswith(".onnx")]
        if not onnx_files:
            set_status(
                self.onnx_model_status, "warning",
                f"目录在场但没有 .onnx 模型：{directory}",
            )
            return
        if len(onnx_files) > 1:
            set_status(
                self.onnx_model_status, "error",
                f"目录里有 {len(onnx_files)} 个 .onnx，引擎会拒绝挑——请只保留一个：{directory}",
            )
            return
        if "tokens.txt" not in entries:
            set_status(
                self.onnx_model_status, "error",
                f"模型目录缺 tokens.txt：{directory}",
            )
            return
        try:
            size_mb = sum(
                os.path.getsize(os.path.join(directory, f)) for f in entries
            ) / (1024 * 1024)
        except OSError:
            size_mb = 0.0
        set_status(
            self.onnx_model_status, "success",
            f"模型就绪：{onnx_files[0]}（{size_mb:.1f} MB）—— {directory}",
        )

    def _browse_onnx_dir(self):
        folder = styled_open_directory(self, "选择 ONNX 语音模型目录")
        if folder:
            self.onnx_model_dir_input.setText(folder)
            self._check_onnx_model()

    def _onnx_speed_value(self) -> float:
        try:
            return float(self.onnx_speed_combo.currentData())
        except (TypeError, ValueError):
            return 1.0

    def _apply_onnx_config(self, tts_cfg: dict):
        """从已有 config.tts 恢复 ONNX 段。配置里的语速不在档位表时如实补一档，
        不悄悄改用户的值。"""
        directory = str(tts_cfg.get("onnx_model_dir") or "").strip()
        self.onnx_model_dir_input.setText(directory)
        try:
            speed = float(tts_cfg.get("onnx_speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        speed = max(0.3, min(speed, 3.0))
        index = next(
            (
                i for i in range(self.onnx_speed_combo.count())
                if abs(float(self.onnx_speed_combo.itemData(i)) - speed) < 1e-6
            ),
            None,
        )
        if index is None:
            self.onnx_speed_combo.addItem(f"{speed:g}×（当前配置）", speed)
            index = self.onnx_speed_combo.count() - 1
        self.onnx_speed_combo.setCurrentIndex(index)
        self._check_onnx_model()

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    def _download_onnx_model(self):
        """一键下载 ONNX 语音模型（冻结版也能下——只是一个 HTTPS 档案）。

        工作线程跑 meapet.onnx_voice_pack.install_onnx_voice（钉住 sha256 +
        多路由 + 原子换入），阶段进度经 _post_to_main 回主线程。成功后把
        落盘目录填进输入框；失败响亮报错，不装成"装上了"。
        """
        from meapet import onnx_voice_pack

        url = (
            self.onnx_url_input.text().strip()
            or onnx_voice_pack.DEFAULT_ONNX_VOICE_URL
        )
        target = self._onnx_effective_dir()
        self.onnx_download_btn.setEnabled(False)
        set_status(self.onnx_download_status, "muted", "准备下载 ONNX 语音模型…")

        def on_stage(stage: str):
            _post_to_main(
                lambda: set_status(
                    self.onnx_download_status, "muted",
                    f"ONNX 语音模型：{stage}…",
                )
            )

        def worker():
            try:
                info = onnx_voice_pack.install_onnx_voice(
                    url, target, progress=on_stage
                )
            except Exception as exc:
                # 闭包捕获消息值而不是异常变量（主线程回调执行时它已不存在）。
                message = str(exc)

                def fail():
                    set_status(
                        self.onnx_download_status, "error",
                        f"ONNX 语音模型下载失败：{message}",
                    )
                    self.onnx_download_btn.setEnabled(True)
                _post_to_main(fail)
                return

            def done():
                self.onnx_model_dir_input.setText(info["model_dir"])
                self._check_onnx_model()
                set_status(
                    self.onnx_download_status, "success",
                    f"语音模型就绪（sha256 {info['sha256'][:12]}…，"
                    f"经 {info['route']}）—— 请保存配置生效",
                )
                self.onnx_download_btn.setEnabled(True)
            _post_to_main(done)

        threading.Thread(target=worker, daemon=True).start()
