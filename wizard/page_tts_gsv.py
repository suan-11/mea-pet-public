"""TTS 配置页 mixin"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from typing import Optional

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import *

from wizard.styles import (
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    MIN_TARGET_SIZE, WIZARD_STYLESHEET, set_status,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH
from wizard.env_utils import pip_install, check_installed
from meapet.ui_theme import set_scaled_stylesheet

class TtsPageGsvMixin:
    def _browse_gsv_dir(self):
        """浏览选择整合包解压后的文件夹"""
        folder = QFileDialog.getExistingDirectory(
            self, "选整合包解压后的文件夹"
        )
        if folder:
            self.gsv_dir_input.setText(folder)
            self._check_gsv()

    def _browse_gsv_ref_wav(self, language: str = "jp"):
        """选择固定 GPT-SoVITS 参考音频。"""
        inputs = getattr(self, "gsv_reference_inputs", {})
        target = inputs.get(language)
        if target is None:
            target = getattr(self, "gsv_ref_wav_input", None)
        current = ""
        if target is not None:
            current = target.text().strip()
        if current and os.path.isfile(current):
            start_dir = os.path.dirname(current)
        else:
            start_dir = os.path.join(
                os.path.dirname(CONFIG_PATH),
                "GPT-Sovits",
                "normal",
            )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 GPT-SoVITS 参考音频",
            start_dir,
            "WAV Audio (*.wav);;All (*.*)",
        )
        if path and target is not None:
            target.setText(path)

    def _find_python_exe(self, base_dir):
        r"""在整合包目录中查找 runtime\python.exe"""
        candidate = os.path.join(base_dir, "runtime", "python.exe")
        if os.path.isfile(candidate):
            return candidate
        # 也可能在 runtime 的下级
        for root, _dirs, files in os.walk(base_dir):
            if "python.exe" in files and os.path.basename(root) == "runtime":
                return os.path.join(root, "python.exe")
        return None

    def _check_gsv(self):
        """检测 GPT-SoVITS 整合包环境状态"""
        saved = self.gsv_dir_input.text().strip()
        gsv_python = os.environ.get("GSV_PYTHON", "")

        def _has_gsv_module(py_path):
            if not py_path or not os.path.isfile(py_path):
                return False
            try:
                r = subprocess.run(
                    [py_path, "-c", "import GPT_SoVITS; print('ok')"],
                    capture_output=True, text=True, timeout=5
                )
                return r.returncode == 0 and 'ok' in r.stdout
            except Exception:
                return False

        # 如果输入的是文件夹，自动找 runtime\python.exe
        py_path = saved
        if saved and os.path.isdir(saved):
            py_path = self._find_python_exe(saved)
            if py_path:
                self.gsv_dir_input.setText(py_path)

        if py_path and os.path.isfile(py_path) and py_path.endswith("python.exe"):
            if _has_gsv_module(py_path):
                set_status(self.gsv_status, "success", "GPT-SoVITS 环境就绪，语音可用")
            else:
                set_status(
                    self.gsv_status,
                    "warning",
                    "找到 python.exe 但缺少 GPT_SoVITS 模块，请确认是否为官方整合包",
                )
        elif gsv_python and os.path.isfile(gsv_python):
            if _has_gsv_module(gsv_python):
                set_status(self.gsv_status, "success", "已配置 GSV_PYTHON，语音可用")
            else:
                set_status(
                    self.gsv_status,
                    "warning",
                    "GSV_PYTHON 已指定，但缺少 GPT_SoVITS 模块",
                )
        else:
            set_status(self.gsv_status, "muted", "尚未配置语音；关闭语音也可以正常使用")

    def _show_gsv_guide(self):
        """大白话安装指南（GPT-SoVITS 整合包版）"""
        guide = (
            "<h3>🎤 梅尔说话需要它</h3>"
            "<p>语音功能需要装 <b>GPT-SoVITS</b> 官方整合包。</p>"
            "<hr>"
            "<h4>👇 跟着这几步做：</h4>"
            "<p><b>1. 下整合包</b><br>"
            "去 <a href='https://pan.quark.cn/s/d2bb86ae6462'>GPT-SoVITS 整合包</a><br>"
            "下载最新版整合包</p>"
            "<p><b>2. 解压</b><br>"
            "解压到你喜欢的位置，比如：<br>"
            r"<code>D:\GPT-SoVITS-v2pro-20250604\</code></p>"
            "<p><b>3. 回到向导</b><br>"
            "点「浏览」，选中解压后的整合包文件夹：<br>"
            r"<code>D:\GPT-SoVITS-v2pro-20250604</code><br><br>"
            r"向导会自动找到 runtime\python.exe，之后就能用了。</p>"
            "<hr>"
            "<h4>💡 不开语音也能玩</h4>"
            "<p>不装语音完全不影响桌宠的其他功能。<br>"
            "以后想加声音了，随时回来装就行。</p>"
            "<hr>"
            "<p>"
            "模型文件已打包在项目里，不需要额外下载。</p>"
        )
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("语音功能怎么装")
        dialog.setMinimumSize(520, 520)
        set_scaled_stylesheet(dialog, WIZARD_STYLESHEET)
        dialog.setAccessibleName("GPT-SoVITS 安装说明")
        dl = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setObjectName("SummaryOutput")
        text.setOpenExternalLinks(True)
        text.setHtml(guide)
        text.setAccessibleName("GPT-SoVITS 安装步骤")
        dl.addWidget(text)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        c = QPushButton("明白了")
        c.setObjectName("PrimaryButton")
        c.setMinimumSize(104, MIN_TARGET_SIZE)
        c.setAccessibleName("关闭安装说明")
        c.clicked.connect(dialog.accept)
        btn_row.addWidget(c)
        dl.addLayout(btn_row)
        dialog.exec_()

    # ─── 跨线程信号槽（在主线程执行） ───
