"""Unified OpenAI-compatible LLM connection configuration page."""
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from urllib.parse import urljoin

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from wizard.styles import (
    STYLE_PAGE_CARD,
    set_status,
)
from wizard.widgets import WheelSafeComboBox


# Common path suffixes to strip before appending /models or /api/tags
_MODEL_ENDPOINT_SUFFIXES = (
    "/v1/chat/completions",
    "/v1/completions",
    "/chat/completions",
    "/completions",
)


def _detect_provider_from_url(url: str) -> str:
    """Infer the provider name from the endpoint URL for env-var resolution."""
    lowered = (url or "").strip().lower()
    if "deepseek" in lowered:
        return "deepseek"
    if "xiaomimimo" in lowered or "mimo.mi.com" in lowered:
        return "mimo"
    if "11434" in lowered or "localhost" in lowered or "127.0.0.1" in lowered:
        return "ollama"
    return "custom"


def _normalize_base_url(url: str) -> str:
    """Strip common chat endpoint suffixes so /models or /api/tags can be appended."""
    lowered = url.strip().lower()
    for suffix in _MODEL_ENDPOINT_SUFFIXES:
        if lowered.endswith(suffix):
            base = url[: -len(suffix)]
            return base.rstrip("/") + "/"
    return url.rstrip("/") + "/"


class LLMPage(QFrame):
    """Unified OpenAI-compatible connection configuration page.

    Replaces the old 4-radio-button provider selector with a simple form:
    Base URL, Model (with auto-fetch), API Key, Temperature, Max tokens.
    """

    models_fetched = pyqtSignal(list, str)  # model_names, error_message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._provider_override = None  # optional explicit provider selection
        self._fetch_running = False
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        # Title
        title = QLabel("AI 模型连接")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel(
            "填写 OpenAI 兼容接口的地址和模型信息，"
            "所有主流服务商（DeepSeek、MiMo、Ollama 等）均支持。"
        )
        desc.setObjectName("PageDescription")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # --- Connection section ---
        conn_card = QFrame()
        conn_card.setObjectName("SectionCard")
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(16, 14, 16, 16)
        conn_layout.setSpacing(10)

        # Base URL
        base_url_label = QLabel("API 地址：")
        base_url_label.setObjectName("FieldLabel")
        conn_layout.addWidget(base_url_label)
        self.endpoint_input = QLineEdit("https://api.openai.com/v1")
        self.endpoint_input.setObjectName("ApiBaseUrl")
        self.endpoint_input.setAccessibleName("API 地址")
        self.endpoint_input.setPlaceholderText("https://api.openai.com/v1")
        self.endpoint_input.setStyleSheet(
            "QLineEdit {"
            "  background: #1B1D2E;"
            "  color: #E8EAF0;"
            "  border: 1px solid #3A3D52;"
            "  border-radius: 6px;"
            "  padding: 0px 12px;"
            "  font-size: 14px;"
            "}"
            "QLineEdit:focus {"
            "  border: 2px solid #7C8AFF;"
            "}"
        )
        conn_layout.addWidget(self.endpoint_input)

        # Model ID with fetch button
        model_row = QHBoxLayout()
        model_col = QVBoxLayout()
        model_label = QLabel("模型名称：")
        model_label.setObjectName("FieldLabel")
        model_col.addWidget(model_label)
        self.model_combo = WheelSafeComboBox()
        self.model_combo.setObjectName("ModelSelector")
        self.model_combo.setAccessibleName("模型名称")
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(WheelSafeComboBox.NoInsert)
        self.model_combo.lineEdit().setPlaceholderText("gpt-4o")
        from PyQt5.QtCore import Qt
        from meapet.ui_theme import MIN_TARGET_SIZE

        le = self.model_combo.lineEdit()
        if le is not None:
            le.setAlignment(Qt.AlignVCenter)
            le.setContentsMargins(4, 0, 0, 0)
            le.setStyleSheet(
                "QLineEdit {"
                "  background: transparent;"
                "  border: none;"
                "  margin: 0px;"
                "  padding: 0px;"
                "}"
            )
        self.model_combo.setMinimumHeight(MIN_TARGET_SIZE)
        self.model_combo.setStyleSheet(
            "WheelSafeComboBox {"
            "  padding: 0px 34px 0px 0px;"
            "}"
        )
        self.model_combo.setMinimumContentsLength(30)
        model_col.addWidget(self.model_combo)
        model_row.addLayout(model_col, 1)

        fetch_col = QVBoxLayout()
        fetch_col.addWidget(QLabel(""))  # spacer to align with input row
        self.fetch_models_btn = QPushButton("获取模型列表")
        self.fetch_models_btn.setObjectName("FetchModelsButton")
        self.fetch_models_btn.setAccessibleName("从接口地址获取可用模型列表")
        self.fetch_models_btn.setProperty("doesNotModifyConfig", True)
        self.fetch_models_btn.setToolTip("向接口地址查询可用模型 ID")
        fetch_col.addWidget(self.fetch_models_btn)
        model_row.addLayout(fetch_col)
        conn_layout.addLayout(model_row)

        self.models_fetch_status = QLabel("")
        self.models_fetch_status.setProperty("status", "muted")
        self.models_fetch_status.setAccessibleName("模型列表获取状态")
        conn_layout.addWidget(self.models_fetch_status)

        # API Key
        api_key_label = QLabel("API Key / 环境变量占位符：")
        api_key_label.setObjectName("FieldLabel")
        conn_layout.addWidget(api_key_label)
        self.direct_api_key_input = QLineEdit()
        self.direct_api_key_input.setObjectName("ApiKey")
        self.direct_api_key_input.setStyleSheet(
            "QLineEdit {"
            "  background: #1B1D2E;"
            "  color: #E8EAF0;"
            "  border: 1px solid #3A3D52;"
            "  border-radius: 6px;"
            "  padding: 0px 12px;"
            "  font-size: 14px;"
            "}"
            "QLineEdit:focus {"
            "  border: 2px solid #7C8AFF;"
            "}"
        )
        self.direct_api_key_input.setEchoMode(QLineEdit.Password)
        self.direct_api_key_input.setAccessibleName("API Key")
        self.direct_api_key_input.setAccessibleDescription(
            "凭据只会保存到本机配置文件，也可填写环境变量占位符"
        )
        self.direct_api_key_input.setPlaceholderText("例如 $MEAPET_API_KEY 或 sk-...")
        key_row = QHBoxLayout()
        key_row.addWidget(self.direct_api_key_input, 1)
        self.api_key_visibility = QPushButton("显示 Key")
        self.api_key_visibility.setCheckable(True)
        self.api_key_visibility.setAccessibleName("切换 API Key 可见性")
        self.api_key_visibility.setProperty("doesNotModifyConfig", True)
        self.api_key_visibility.toggled.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.api_key_visibility)
        conn_layout.addLayout(key_row)

        # Tuning parameters
        tuning = QHBoxLayout()
        temperature_label = QLabel("回复随机性：")
        temperature_label.setObjectName("FieldLabel")
        tuning.addWidget(temperature_label)
        self.temperature_input = QDoubleSpinBox()
        self.temperature_input.setObjectName("Temperature")
        self.temperature_input.setAccessibleName("回复随机性")
        self.temperature_input.setRange(0.0, 2.0)
        self.temperature_input.setDecimals(2)
        self.temperature_input.setSingleStep(0.05)
        self.temperature_input.setValue(0.7)
        self.temperature_input.setStyleSheet(
            "QDoubleSpinBox { padding: 0px 34px 0px 8px; }"
        )
        tuning.addWidget(self.temperature_input)
        max_tokens_label = QLabel("最大回复长度（tokens）：")
        max_tokens_label.setObjectName("FieldLabel")
        tuning.addWidget(max_tokens_label)
        self.max_tokens_input = QSpinBox()
        self.max_tokens_input.setObjectName("MaxTokens")
        self.max_tokens_input.setAccessibleName("最大回复长度")
        self.max_tokens_input.setRange(1, 1_000_000)
        self.max_tokens_input.setValue(4096)
        self.max_tokens_input.setStyleSheet(
            "QSpinBox { padding: 0px 34px 0px 8px; }"
        )
        tuning.addWidget(self.max_tokens_input)
        tuning.addStretch()
        conn_layout.addLayout(tuning)
        tuning_hint = QLabel(
            "回复随机性越高，措辞越多变；最大回复长度会直接写入模型请求，"
            "数值越大越不容易截断，也可能增加耗时与费用。"
        )
        tuning_hint.setObjectName("HelperText")
        tuning_hint.setWordWrap(True)
        conn_layout.addWidget(tuning_hint)

        # Test connection
        test_row = QHBoxLayout()
        self.test_connection_btn = QPushButton("测试连接")
        self.test_connection_btn.setAccessibleName("测试模型连接")
        self.test_connection_btn.setProperty("doesNotModifyConfig", True)
        test_row.addWidget(self.test_connection_btn)
        self.connection_status = QLabel("尚未测试")
        self.connection_status.setProperty("status", "muted")
        self.connection_status.setAccessibleName("连接测试状态")
        self.connection_status.setWordWrap(True)
        test_row.addWidget(self.connection_status, 1)
        conn_layout.addLayout(test_row)

        layout.addWidget(conn_card)
        layout.addStretch()

        # Signals
        self.endpoint_input.textEdited.connect(self._clear_provider_override)
        self.fetch_models_btn.clicked.connect(self._start_fetch_models)
        self.models_fetched.connect(self._apply_fetched_models)

    # ── Provider auto-detection ──────────────────────────────

    def get_backend(self) -> str:
        """Return the resolved provider name for config storage."""
        if self._provider_override:
            return self._provider_override
        return _detect_provider_from_url(self.endpoint_input.text())

    def set_backend(self, backend: str) -> None:
        """Explicitly select a provider.

        The explicit selection lasts until the user edits the API address.
        """
        backend = (backend or "custom").lower()
        self._provider_override = backend

    def _clear_provider_override(self, _endpoint: str) -> None:
        """Let a user-edited address choose its provider automatically."""
        self._provider_override = None

    # ── Fetch models ─────────────────────────────────────────

    def _start_fetch_models(self) -> None:
        """Dispatch model discovery to a background thread."""
        if self._fetch_running:
            return
        base_url = self.endpoint_input.text().strip()
        if not base_url:
            set_status(
                self.models_fetch_status, "error", "请先填写 API 地址。"
            )
            return

        self._fetch_running = True
        self.fetch_models_btn.setEnabled(False)
        set_status(self.models_fetch_status, "warning", "正在获取模型列表...")
        api_key = self.direct_api_key_input.text().strip()
        thread = threading.Thread(
            target=self._fetch_models_worker,
            args=(base_url, api_key),
            name="meapet-wizard-fetch-models",
            daemon=True,
        )
        thread.start()

    def _fetch_models_worker(self, base_url: str, api_key: str) -> None:
        """Background worker: try GET /v1/models, fallback to /api/tags."""
        names = []
        error = ""

        # Normalize the base URL — strip common chat suffixes
        norm_url = _normalize_base_url(base_url)

        # Build headers with optional auth
        headers = {}
        if api_key and not api_key.startswith("$"):
            if api_key.startswith("sk-") or "Bearer" not in headers:
                headers["Authorization"] = f"Bearer {api_key}"

        # Try OpenAI-compatible /v1/models first
        try:
            url = urljoin(norm_url, "models")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict):
                    models_raw = data.get("data") or data.get("models") or []
                    if isinstance(models_raw, list):
                        names = [
                            m.get("id") or m.get("name") or ""
                            for m in models_raw
                            if isinstance(m, dict)
                        ]
                        names = [n for n in names if n]
        except urllib.error.HTTPError as e:
            # 401/403 means auth needed — try without auth or move on
            if e.code in (401, 403):
                error = f"需要鉴权（HTTP {e.code}），请填写有效的 API Key 后重试"
            else:
                error = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            error = f"连接失败：{e.reason}"
        except Exception as e:
            error = str(e)[:120] or type(e).__name__

        # If /v1/models failed with auth error and we have a key, retry with it
        if not names and error and "需要鉴权" in error and api_key:
            error = ""
            try:
                url = urljoin(norm_url, "models")
                auth_headers = dict(headers)
                if "Authorization" not in auth_headers and not api_key.startswith("$"):
                    auth_headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(url, headers=auth_headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        models_raw = data.get("data") or data.get("models") or []
                        if isinstance(models_raw, list):
                            names = [
                                m.get("id") or m.get("name") or ""
                                for m in models_raw
                                if isinstance(m, dict)
                            ]
                            names = [n for n in names if n]
                if names:
                    error = ""
            except urllib.error.HTTPError as e:
                error = f"需要鉴权（HTTP {e.code}），请填写有效的 API Key 后重试"
            except urllib.error.URLError as e:
                error = f"连接失败：{e.reason}"
            except Exception as e:
                error = str(e)[:120] or type(e).__name__

        # Fallback: try Ollama /api/tags
        if not names:
            try:
                ollama_url = urljoin(
                    base_url.rstrip("/") + "/", "../api/tags"
                )
                # If that resolved to the same domain, also try direct /api/tags
                if "../api" in ollama_url:
                    ollama_url = ollama_url.replace("../api", "api")
                req = urllib.request.Request(ollama_url)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        models_raw = data.get("models") or []
                        if isinstance(models_raw, list):
                            names = [
                                m.get("name") or ""
                                for m in models_raw
                                if isinstance(m, dict)
                            ]
                            names = [n for n in names if n]
                if names:
                    error = ""  # clear any prior error
            except urllib.error.HTTPError:
                pass  # expected if not Ollama
            except urllib.error.URLError:
                pass
            except Exception:
                pass

        if not names and not error:
            error = "未能获取到模型列表，请检查地址是否正确。也可手动输入模型名称。"

        try:
            self.models_fetched.emit(names, error)
        except RuntimeError:
            pass

    def _apply_fetched_models(self, names: list[str], error: str) -> None:
        """Populate the model combo with discovered model IDs."""
        self._fetch_running = False
        self.fetch_models_btn.setEnabled(True)

        if error:
            set_status(self.models_fetch_status, "error", error)
            return

        if not names:
            set_status(
                self.models_fetch_status,
                "muted",
                "未发现可用模型，可手动输入。",
            )
            return

        current_text = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for name in sorted(names):
            self.model_combo.addItem(name)
        idx = self.model_combo.findText(current_text)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        elif current_text:
            self.model_combo.setEditText(current_text)
        self.model_combo.blockSignals(False)

        count = len(names)
        set_status(
            self.models_fetch_status,
            "success",
            f"获取到 {count} 个模型。",
        )

    # ── Configuration profile ────────────────────────────────

    def collect_direct_profile(self, api_key: str = "") -> dict:
        """Collect the current form values into a config profile dict."""
        protocol = "openai_chat"
        endpoint = self.endpoint_input.text().strip()
        return {
            "provider": self.get_backend(),
            "protocol": protocol,
            "api_base": endpoint,
            "host": "",  # always use api_base with openai_chat
            "model": self.model_combo.currentText().strip(),
            "api_key": str(api_key or self.direct_api_key_input.text()).strip(),
            "temperature": self.temperature_input.value(),
            "max_tokens": self.max_tokens_input.value(),
        }

    def apply_direct_profile(self, profile: dict) -> None:
        """Restore form fields from a previously saved profile.

        Provider is deliberately derived from the address after restore.  A
        restored provider must not pin a later user-edited address to its old
        backend.
        """
        profile = profile or {}
        endpoint = profile.get("api_base") or profile.get("host") or ""
        self.endpoint_input.setText(str(endpoint))

        model = str(profile.get("model") or "")
        self.model_combo.setEditText(model)

        self.direct_api_key_input.setText(str(profile.get("api_key") or ""))
        try:
            self.temperature_input.setValue(float(profile.get("temperature", 0.7)))
        except (TypeError, ValueError):
            self.temperature_input.setValue(0.7)
        try:
            self.max_tokens_input.setValue(int(profile.get("max_tokens", 4096)))
        except (TypeError, ValueError):
            self.max_tokens_input.setValue(4096)


    # ── Helpers ───────────────────────────────────────────────

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.direct_api_key_input.setEchoMode(
            QLineEdit.Normal if visible else QLineEdit.Password
        )
        self.api_key_visibility.setText("隐藏 Key" if visible else "显示 Key")
        self.api_key_visibility.setAccessibleName(
            "隐藏 API Key" if visible else "显示 API Key"
        )
