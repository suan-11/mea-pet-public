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
class LLMPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        title = QLabel("选择 AI 大脑")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel("桌宠要靠一个 AI 来对话，选一个你有的：")
        desc.setObjectName("PageDescription")
        layout.addWidget(desc)

        # Ollama
        self.radio_ollama = QRadioButton("Ollama（推荐 · 免费、本地运行）")
        self.radio_ollama.setAccessibleDescription("本地运行，不需要 API Key")
        self.radio_ollama.setChecked(True)
        layout.addWidget(self.radio_ollama)
        ollama_detail = QLabel(
            "    • 完全免费，不需要 API Key\n"
            "    • 需要先装 Ollama 并下载模型\n"
            "    • 推荐模型：qwen3.5:4b（多模态，对话+识图一体）"
        )
        ollama_detail.setObjectName("HelperText")
        ollama_detail.setWordWrap(True)
        layout.addWidget(ollama_detail)

        # DeepSeek
        self.radio_ds = QRadioButton("DeepSeek API（在线、速度快）")
        self.radio_ds.setAccessibleDescription("在线服务，按量付费，需要 API Key")
        layout.addWidget(self.radio_ds)
        deepseek_detail = QLabel(
            "    • 需要注册 DeepSeek 获取 API Key\n"
            "    • 按量付费，不需要本地显卡\n"
            "    • 注：屏幕识图仍需要 Ollama（装 qwen3.5:4b 即可，多模态对话+识图一体）"
        )
        deepseek_detail.setObjectName("HelperText")
        deepseek_detail.setWordWrap(True)
        layout.addWidget(deepseek_detail)

        # MiMo V2.5
        self.radio_mimo = QRadioButton("MiMo V2.5（小米多模态 API，在线、可识图）")
        self.radio_mimo.setAccessibleDescription("在线多模态服务，支持对话和识图，需要 API Key")
        layout.addWidget(self.radio_mimo)
        mimo_detail = QLabel(
            "    • 需要注册 xiaomimimo 平台获取 API Key\n"
            "    • 按量付费，不需要本地显卡\n"
            "    • 支持识图（不需要额外装 Ollama）"
        )
        mimo_detail.setObjectName("HelperText")
        mimo_detail.setWordWrap(True)
        layout.addWidget(mimo_detail)

        # 自定义模型服务商仍属于 direct，不把 OpenAI-compatible 端点伪装成 Agent。
        self.radio_custom = QRadioButton("自定义模型接口（直连）")
        self.radio_custom.setAccessibleDescription(
            "填写实际协议、地址、模型和鉴权信息"
        )
        layout.addWidget(self.radio_custom)

        self.direct_settings = QFrame()
        self.direct_settings.setObjectName("SectionCard")
        direct_layout = QVBoxLayout(self.direct_settings)
        direct_layout.setContentsMargins(16, 14, 16, 16)
        direct_layout.setSpacing(10)

        protocol_label = QLabel("接口协议：")
        protocol_label.setObjectName("FieldLabel")
        direct_layout.addWidget(protocol_label)
        self.protocol_combo = QComboBox()
        self.protocol_combo.setObjectName("DirectProtocol")
        self.protocol_combo.setAccessibleName("直连接口协议")
        self.protocol_combo.addItem("Ollama Chat", "ollama_chat")
        self.protocol_combo.addItem("OpenAI Chat Completions", "openai_chat")
        self.protocol_combo.addItem("OpenAI Responses", "openai_responses")
        self.protocol_combo.addItem("Anthropic Messages", "anthropic_messages")
        direct_layout.addWidget(self.protocol_combo)

        endpoint_label = QLabel("实际 API 地址：")
        endpoint_label.setObjectName("FieldLabel")
        direct_layout.addWidget(endpoint_label)
        self.endpoint_input = QLineEdit("http://127.0.0.1:11434")
        self.endpoint_input.setObjectName("DirectApiEndpoint")
        self.endpoint_input.setStyleSheet(STYLE_INPUT)
        self.endpoint_input.setAccessibleName("直连 API 地址")
        direct_layout.addWidget(self.endpoint_input)

        model_label = QLabel("实际模型 ID：")
        model_label.setObjectName("FieldLabel")
        direct_layout.addWidget(model_label)
        self.model_input = QLineEdit("qwen3.5:4b")
        self.model_input.setObjectName("DirectModel")
        self.model_input.setStyleSheet(STYLE_INPUT)
        self.model_input.setAccessibleName("直连模型 ID")
        direct_layout.addWidget(self.model_input)

        key_label = QLabel("API Key / 环境变量占位符（可空）：")
        key_label.setObjectName("FieldLabel")
        direct_layout.addWidget(key_label)
        self.direct_api_key_input = QLineEdit()
        self.direct_api_key_input.setObjectName("DirectApiKey")
        self.direct_api_key_input.setStyleSheet(STYLE_INPUT)
        self.direct_api_key_input.setEchoMode(QLineEdit.Password)
        self.direct_api_key_input.setAccessibleName("直连 API Key")
        self.direct_api_key_input.setPlaceholderText("例如 $MEAPET_API_KEY")
        direct_layout.addWidget(self.direct_api_key_input)

        tuning = QHBoxLayout()
        tuning.addWidget(QLabel("temperature"))
        self.temperature_input = QDoubleSpinBox()
        self.temperature_input.setObjectName("DirectTemperature")
        self.temperature_input.setAccessibleName("直连 temperature")
        self.temperature_input.setRange(0.0, 2.0)
        self.temperature_input.setDecimals(2)
        self.temperature_input.setSingleStep(0.05)
        self.temperature_input.setValue(0.7)
        tuning.addWidget(self.temperature_input)
        tuning.addWidget(QLabel("max tokens"))
        self.max_tokens_input = QSpinBox()
        self.max_tokens_input.setObjectName("DirectMaxTokens")
        self.max_tokens_input.setAccessibleName("直连最大输出 token")
        self.max_tokens_input.setRange(1, 1_000_000)
        self.max_tokens_input.setValue(512)
        tuning.addWidget(self.max_tokens_input)
        tuning.addStretch()
        direct_layout.addLayout(tuning)
        layout.addWidget(self.direct_settings)

        # Ollama 状态
        self.ollama_status = QLabel("")
        self.ollama_status.setProperty("status", "muted")
        self.ollama_status.setAccessibleName("Ollama 运行状态")
        layout.addWidget(self.ollama_status)

        layout.addStretch()
        for radio in (
            self.radio_ollama,
            self.radio_ds,
            self.radio_mimo,
            self.radio_custom,
        ):
            radio.toggled.connect(self._on_provider_selected)
        self._on_provider_selected()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._refresh_ollama_status)
        self._status_timer.start(100)

    def _refresh_ollama_status(self):
        running, models = check_ollama_running()
        installed = check_ollama_installed()
        if running:
            m = ", ".join(models[:3])
            set_status(self.ollama_status, "success", f"Ollama 运行中（模型：{m}）")
        elif installed:
            set_status(self.ollama_status, "warning", "Ollama 已安装但未运行，启动后再继续")
        else:
            set_status(
                self.ollama_status,
                "muted",
                "还没装 Ollama？可以先选 DeepSeek，或回头再装",
            )

    def get_backend(self):
        if self.radio_ollama.isChecked():
            return "ollama"
        elif self.radio_mimo.isChecked():
            return "mimo"
        elif self.radio_custom.isChecked():
            return "custom"
        return "deepseek"

    def set_backend(self, backend: str):
        """恢复上次选择的 AI 后端。"""
        backend = (backend or "ollama").lower()
        if backend == "mimo":
            self.radio_mimo.setChecked(True)
        elif backend == "deepseek":
            self.radio_ds.setChecked(True)
        elif backend not in {"ollama", "hermes", "openclaw"}:
            self.radio_custom.setChecked(True)
        else:
            self.radio_ollama.setChecked(True)

    def _on_provider_selected(self, checked: bool = True) -> None:
        if not checked:
            return
        provider = self.get_backend()
        presets = {
            "ollama": (
                "ollama_chat",
                "http://127.0.0.1:11434",
                "qwen3.5:4b",
            ),
            "deepseek": (
                "openai_chat",
                "https://api.deepseek.com/v1",
                "deepseek-v4-flash",
            ),
            "mimo": (
                "openai_chat",
                "https://api.xiaomimimo.com/v1",
                "mimo-v2.5",
            ),
        }
        if provider not in presets:
            return
        protocol, endpoint, model = presets[provider]
        self.set_protocol(protocol)
        self.endpoint_input.setText(endpoint)
        self.model_input.setText(model)

    def set_protocol(self, protocol: str) -> None:
        index = self.protocol_combo.findData(str(protocol or "").strip())
        if index >= 0:
            self.protocol_combo.setCurrentIndex(index)

    def apply_direct_profile(self, profile: dict) -> None:
        profile = profile or {}
        self.set_backend(profile.get("provider", "ollama"))
        self.set_protocol(profile.get("protocol", "ollama_chat"))
        endpoint = (
            profile.get("host")
            if profile.get("protocol") == "ollama_chat"
            else profile.get("api_base")
        )
        self.endpoint_input.setText(str(endpoint or ""))
        self.model_input.setText(str(profile.get("model") or ""))
        self.direct_api_key_input.setText(str(profile.get("api_key") or ""))
        try:
            self.temperature_input.setValue(float(profile.get("temperature", 0.7)))
        except (TypeError, ValueError):
            self.temperature_input.setValue(0.7)
        try:
            self.max_tokens_input.setValue(int(profile.get("max_tokens", 512)))
        except (TypeError, ValueError):
            self.max_tokens_input.setValue(512)

    def collect_direct_profile(self, api_key: str = "") -> dict:
        protocol = self.protocol_combo.currentData() or "openai_chat"
        endpoint = self.endpoint_input.text().strip()
        return {
            "provider": self.get_backend(),
            "protocol": protocol,
            "api_base": "" if protocol == "ollama_chat" else endpoint,
            "host": endpoint if protocol == "ollama_chat" else "",
            "model": self.model_input.text().strip(),
            "api_key": str(api_key or self.direct_api_key_input.text()).strip(),
            "temperature": self.temperature_input.value(),
            "max_tokens": self.max_tokens_input.value(),
        }


# ═══════════════════════════════════════
# 页面：API Key
# ═══════════════════════════════════════
