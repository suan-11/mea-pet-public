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
    STYLE_PAGE_CARD, set_status,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH, platform_checklist, ollama_install_hint, detect_platform
from wizard.env_utils import (
    WorkerSignals, pip_install, check_installed, download_file,
    check_ollama_running, check_ollama_installed, pull_ollama_model,
)

# 兼容页面内可能使用的短名
class VisionPage(QFrame):
    """屏幕识图配置（可独立于对话后端）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        title = QLabel("屏幕识图与观察")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        description = QLabel(
            "桌宠会定时截屏，决定要不要吐槽。默认关闭（隐私）。\n"
            "识图后端可与对话不同：例如对话用 MiMo，识图用本地 Ollama。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.enable_cb = QCheckBox("启用屏幕观察（可选，默认关闭）")
        self.enable_cb.setAccessibleDescription("定时截取屏幕并交给所选视觉模型分析")
        self.enable_cb.setChecked(False)
        layout.addWidget(self.enable_cb)
        progressive = QLabel("可先不启用。需要时再打开，并配置识图后端。")
        progressive.setObjectName("HelperText")
        progressive.setWordWrap(True)
        layout.addWidget(progressive)
        self.advanced_toggle = QCheckBox("显示高级识图选项")
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setAccessibleName("显示高级识图选项")
        layout.addWidget(self.advanced_toggle)
        self.advanced_frame = QFrame()
        self.advanced_frame.setObjectName("SectionCard")
        self.advanced_frame.setAccessibleName("高级识图设置")
        self.advanced_layout = QVBoxLayout(self.advanced_frame)
        self.advanced_layout.setContentsMargins(16, 14, 16, 14)
        self.advanced_layout.setSpacing(10)
        layout.addWidget(self.advanced_frame)
        self.advanced_frame.setVisible(False)

        self.allow_cloud_cb = QCheckBox("允许云端识图（watcher.allow_cloud，截图会上传）")
        self.allow_cloud_cb.setChecked(False)
        self.allow_cloud_cb.setToolTip("使用 MiMo 等云端识图时必须勾选。本地 Ollama 可不勾。")
        self.advanced_layout.addWidget(self.allow_cloud_cb)

        self.require_confirm_label = QLabel("隐私保护：每次将截屏上传云端前都必须确认（不可关闭）")
        self.require_confirm_label.setProperty("status", "success")
        self.require_confirm_label.setWordWrap(True)
        self.advanced_layout.addWidget(self.require_confirm_label)

        self.backend_label = QLabel("识图后端：")
        self.backend_label.setObjectName("FieldLabel")
        self.advanced_layout.addWidget(self.backend_label)
        self.backend_combo = QComboBox()
        self.backend_combo.setObjectName("VisionBackend")
        self.backend_combo.setAccessibleName("识图后端")
        self.backend_combo.addItem("跟随对话后端（推荐）", "auto")
        self.backend_combo.addItem("MiMo 云端识图", "mimo")
        self.backend_combo.addItem("Ollama 本地识图", "ollama")
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        self.advanced_toggle.toggled.connect(self._sync_advanced_visibility)
        self.enable_cb.toggled.connect(self._sync_advanced_visibility)
        self.advanced_layout.addWidget(self.backend_combo)

        self.model_label = QLabel("本地视觉模型：")
        self.model_label.setObjectName("FieldLabel")
        self.advanced_layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("VisionModel")
        self.model_combo.setAccessibleName("本地视觉模型")
        self.model_combo.addItem("qwen3.5:4b（多模态，推荐）", "qwen3.5:4b")
        self.advanced_layout.addWidget(self.model_combo)

        self.host_label = QLabel("Ollama 地址（可空=用对话配置）：")
        self.host_label.setObjectName("FieldLabel")
        self.advanced_layout.addWidget(self.host_label)
        self.host_input = QLineEdit()
        self.host_input.setObjectName("VisionOllamaHost")
        self.host_input.setPlaceholderText("http://127.0.0.1:11434")
        self.host_input.setStyleSheet(STYLE_INPUT)
        self.host_input.setAccessibleName("识图 Ollama 地址")
        self.advanced_layout.addWidget(self.host_input)

        # 云端专用 Key（可空=复用对话 MiMo Key）
        self.cloud_box = QFrame()
        self.cloud_box.setObjectName("SectionCard")
        cloud_l = QVBoxLayout(self.cloud_box)
        cloud_l.setContentsMargins(16, 14, 16, 16)
        cloud_key_label = QLabel("云端识图 Key（可空=使用对话页 MiMo Key）：")
        cloud_key_label.setObjectName("FieldLabel")
        cloud_l.addWidget(cloud_key_label)
        self.api_key_input = QLineEdit()
        self.api_key_input.setObjectName("VisionCloudApiKey")
        self.api_key_input.setPlaceholderText("可留空自动沿用对话 Key")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setStyleSheet(STYLE_INPUT)
        self.api_key_input.setAccessibleName("云端识图 API Key")
        cloud_l.addWidget(self.api_key_input)
        cloud_base_label = QLabel("API Base：")
        cloud_base_label.setObjectName("FieldLabel")
        cloud_l.addWidget(cloud_base_label)
        self.api_base_input = QLineEdit("https://api.xiaomimimo.com/v1")
        self.api_base_input.setObjectName("VisionCloudApiBase")
        self.api_base_input.setStyleSheet(STYLE_INPUT)
        self.api_base_input.setAccessibleName("云端识图 API 地址")
        cloud_l.addWidget(self.api_base_input)
        self.advanced_layout.addWidget(self.cloud_box)

        interval_label = QLabel("观察间隔（分钟，随机在最小~最大之间）：")
        interval_label.setObjectName("FieldLabel")
        self.advanced_layout.addWidget(interval_label)
        row = QHBoxLayout()
        self.min_min_input = QLineEdit("3")
        self.min_min_input.setObjectName("WatchIntervalMinimum")
        self.min_min_input.setMinimumWidth(80)
        self.min_min_input.setMaximumWidth(96)
        self.min_min_input.setStyleSheet(STYLE_INPUT)
        self.min_min_input.setAccessibleName("最小观察间隔（分钟）")
        self.max_min_input = QLineEdit("6")
        self.max_min_input.setObjectName("WatchIntervalMaximum")
        self.max_min_input.setMinimumWidth(80)
        self.max_min_input.setMaximumWidth(96)
        self.max_min_input.setStyleSheet(STYLE_INPUT)
        self.max_min_input.setAccessibleName("最大观察间隔（分钟）")
        minimum_label = QLabel("最小")
        minimum_label.setObjectName("HelperText")
        row.addWidget(minimum_label)
        row.addWidget(self.min_min_input)
        maximum_label = QLabel("最大")
        maximum_label.setObjectName("HelperText")
        row.addWidget(maximum_label)
        row.addWidget(self.max_min_input)
        row.addStretch()
        self.advanced_layout.addLayout(row)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("status", "warning")
        self.hint.setAccessibleName("识图设置提示")
        self.advanced_layout.addWidget(self.hint)

        layout.addStretch()
        self._on_backend_changed()

        self._sync_advanced_visibility()

    def _sync_advanced_visibility(self, *_args) -> None:
        """未启用且未展开时隐藏高级识图细节，降低首次配置负担。"""
        show = bool(
            self.advanced_toggle.isChecked() or self.enable_cb.isChecked()
        )
        self.advanced_frame.setVisible(show)
        if show and hasattr(self, "_on_backend_changed"):
            # 恢复后端相关的二次显隐（云端框等）
            self._on_backend_changed()

    def _on_backend_changed(self, *_args):
        data = self.backend_combo.currentData()
        is_ollama = data == "ollama"
        is_mimo = data == "mimo"
        is_auto = data == "auto"
        # 本地模型控件
        self.model_label.setVisible(is_ollama or is_auto)
        self.model_combo.setVisible(is_ollama or is_auto)
        self.host_label.setVisible(is_ollama)
        self.host_input.setVisible(is_ollama)
        # 云端 key
        self.cloud_box.setVisible(is_mimo or is_auto)
        if is_mimo:
            set_status(
                self.hint,
                "warning",
                "使用 MiMo 识图时，请勾选“允许云端识图”；截图会上传到云端。",
            )
        elif is_ollama:
            set_status(
                self.hint,
                "muted",
                "本地 Ollama 需已拉取视觉模型，例如：ollama pull qwen3.5:4b",
            )
        else:
            set_status(
                self.hint,
                "muted",
                "跟随对话：对话是 MiMo 则云端识图；对话是 Ollama 则本地识图。",
            )

    def apply_config(self, vision_cfg: dict, watcher_cfg: dict):
        vision_cfg = vision_cfg or {}
        watcher_cfg = watcher_cfg or {}
        self.enable_cb.setChecked(bool(watcher_cfg.get("enabled", False)))
        self.allow_cloud_cb.setChecked(bool(watcher_cfg.get("allow_cloud", False)))

        backend = (vision_cfg.get("backend") or "").strip().lower()
        if not backend:
            idx = 0  # auto
        elif backend == "mimo":
            idx = 1
        else:
            idx = 2
        self.backend_combo.setCurrentIndex(idx)

        model = vision_cfg.get("model") or "qwen3.5:4b"
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model:
                self.model_combo.setCurrentIndex(i)
                break

        host = (vision_cfg.get("host") or "").strip()
        if host:
            self.host_input.setText(host)
        key = (vision_cfg.get("api_key") or "").strip()
        if key:
            self.api_key_input.setText(key)
        base = (vision_cfg.get("api_base") or "").strip()
        if base:
            self.api_base_input.setText(base)

        # interval: prefer watcher.interval, else top-level later
        interval = watcher_cfg.get("interval") or {}
        min_ms = interval.get("min_ms", 180000)
        max_ms = interval.get("max_ms", 360000)
        try:
            self.min_min_input.setText(str(max(1, int(min_ms) // 60000)))
            self.max_min_input.setText(str(max(1, int(max_ms) // 60000)))
        except Exception:
            pass
        self._sync_advanced_visibility()

    def collect(self, llm_backend: str, llm_cfg: dict) -> dict:
        """返回 vision + watcher 片段。"""
        mode = self.backend_combo.currentData() or "auto"
        if mode == "auto":
            inherited_backend = (llm_backend or "ollama").lower()
            backend = (
                inherited_backend
                if inherited_backend in {"ollama", "mimo"}
                else "ollama"
            )
            vision_backend_field = ""  # 空=跟随
        else:
            backend = mode
            vision_backend_field = mode

        try:
            min_m = max(1, int(float(self.min_min_input.text().strip() or "3")))
        except Exception:
            min_m = 3
        try:
            max_m = max(min_m, int(float(self.max_min_input.text().strip() or "6")))
        except Exception:
            max_m = max(min_m, 6)

        vision = {
            "enabled": self.enable_cb.isChecked(),
            "backend": vision_backend_field,
            "model": self.model_combo.currentData() or "qwen3.5:4b",
            "host": self.host_input.text().strip(),
            "api_key": self.api_key_input.text().strip(),
            "api_base": self.api_base_input.text().strip(),
        }
        if backend == "mimo":
            # 云端时 model 用占位 mimo，实际请求用 llm/vision 的多模态模型名
            if not vision["model"] or vision["model"] in ("qwen3.5:4b",):
                vision["model"] = "mimo"
            same_provider = (llm_backend or "").lower() == "mimo"
            if not vision["api_key"] and same_provider:
                vision["api_key"] = (llm_cfg or {}).get("api_key", "")
            if not vision["api_base"]:
                inherited_base = (
                    (llm_cfg or {}).get("api_base", "")
                    if same_provider
                    else ""
                )
                vision["api_base"] = (
                    inherited_base or "https://api.xiaomimimo.com/v1"
                )
        elif backend == "ollama":
            if vision["model"] in ("mimo", ""):
                vision["model"] = "qwen3.5:4b"

        watcher = {
            "enabled": self.enable_cb.isChecked(),
            "allow_cloud": self.allow_cloud_cb.isChecked(),
            "require_confirm": True,
            "confirm_once_session": False,
            "interval": {
                "min_ms": min_m * 60000,
                "max_ms": max_m * 60000,
            },
        }
        return {"vision": vision, "watcher": watcher}



# ═══════════════════════════════════════
# 页面：确认 + 保存
# ═══════════════════════════════════════
