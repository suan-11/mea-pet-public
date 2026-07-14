"""对话模式、Agent 连接与反向控制配置。"""

from __future__ import annotations

import secrets

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from meapet.ui_theme import MIN_TARGET_SIZE
from wizard.styles import STYLE_INPUT, STYLE_PAGE_CARD
from wizard.widgets import WheelSafeComboBox


def _field(layout, label: str, widget, accessible_name: str) -> None:
    caption = QLabel(label)
    caption.setObjectName("FieldLabel")
    layout.addWidget(caption)
    widget.setAccessibleName(accessible_name)
    widget.setMinimumHeight(MIN_TARGET_SIZE)
    layout.addWidget(widget)


class BackendPage(QFrame):
    """只允许一个回复后端活动，同时保留另一种模式的配置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        title = QLabel("回复后端")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        description = QLabel(
            "一次只启用一个后端。直连模式由模型服务商回复；Agent 模式复用 "
            "Agent 自己的模型、记忆和工具。切换不会删除另一侧配置。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        mode_row = QHBoxLayout()
        self.direct_radio = QRadioButton("模型服务商（直连）")
        self.direct_radio.setAccessibleName("使用直连模型服务商")
        self.direct_radio.setChecked(True)
        self.agent_radio = QRadioButton("Agent")
        self.agent_radio.setAccessibleName("使用 Agent 后端")
        mode_row.addWidget(self.direct_radio)
        mode_row.addWidget(self.agent_radio)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        timeline_row = QHBoxLayout()
        timeline_row.addWidget(QLabel("本地最近对话缓存轮数："))
        self.timeline_turns = QSpinBox()
        self.timeline_turns.setRange(0, 100)
        self.timeline_turns.setValue(5)
        self.timeline_turns.setSpecialValueText("关闭")
        self.timeline_turns.setAccessibleName("本地最近对话缓存轮数")
        self.timeline_turns.setAccessibleDescription(
            "按后端与 Agent 会话隔离；零表示不恢复本地时间线"
        )
        self.timeline_turns.setMinimumHeight(MIN_TARGET_SIZE)
        timeline_row.addWidget(self.timeline_turns)
        timeline_row.addStretch()
        layout.addLayout(timeline_row)
        timeline_hint = QLabel(
            "默认保留 5 轮，用于重启后的时间线与最近上下文；不同后端不会串线。"
        )
        timeline_hint.setObjectName("HelperText")
        timeline_hint.setWordWrap(True)
        layout.addWidget(timeline_hint)

        self.agent_frame = QFrame()
        self.agent_frame.setObjectName("SectionCard")
        agent_layout = QVBoxLayout(self.agent_frame)
        agent_layout.setContentsMargins(16, 14, 16, 16)
        agent_layout.setSpacing(10)

        self.agent_kind = WheelSafeComboBox()
        self.agent_kind.addItem("Hermes Agent", "hermes")
        self.agent_kind.addItem("OpenClaw Gateway", "openclaw")
        _field(agent_layout, "Agent 类型：", self.agent_kind, "Agent 类型")

        self.agent_base_url = QLineEdit("http://127.0.0.1:8642")
        self.agent_base_url.setStyleSheet(STYLE_INPUT)
        self.agent_base_url.setPlaceholderText("http://127.0.0.1:8642")
        _field(agent_layout, "Agent 地址：", self.agent_base_url, "Agent 地址")

        self.agent_auth_token = QLineEdit()
        self.agent_auth_token.setStyleSheet(STYLE_INPUT)
        self.agent_auth_token.setEchoMode(QLineEdit.Password)
        self.agent_auth_token.setPlaceholderText("可填 $HERMES_API_SERVER_KEY")
        _field(
            agent_layout,
            "Agent Bearer Token：",
            self.agent_auth_token,
            "Agent 访问令牌",
        )

        session_row = QHBoxLayout()
        session_left = QVBoxLayout()
        session_left.addWidget(QLabel("当前会话 ID（空值会自动生成）："))
        self.agent_session_id = QLineEdit()
        self.agent_session_id.setStyleSheet(STYLE_INPUT)
        self.agent_session_id.setAccessibleName("Agent 当前会话 ID")
        session_left.addWidget(self.agent_session_id)
        session_right = QVBoxLayout()
        session_right.addWidget(QLabel("长期记忆作用域 Key（空值会自动生成）："))
        self.agent_session_key = QLineEdit()
        self.agent_session_key.setStyleSheet(STYLE_INPUT)
        self.agent_session_key.setEchoMode(QLineEdit.Password)
        self.agent_session_key.setAccessibleName("Agent 记忆作用域 Key")
        session_right.addWidget(self.agent_session_key)
        session_row.addLayout(session_left, 1)
        session_row.addLayout(session_right, 1)
        agent_layout.addLayout(session_row)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("发送最近对话轮数："))
        self.agent_history_turns = QSpinBox()
        self.agent_history_turns.setRange(0, 50)
        self.agent_history_turns.setValue(5)
        self.agent_history_turns.setAccessibleName("Agent 最近对话轮数")
        self.agent_history_turns.setMinimumHeight(MIN_TARGET_SIZE)
        history_row.addWidget(self.agent_history_turns)
        history_row.addStretch()
        agent_layout.addLayout(history_row)

        self.agent_allow_insecure_ws = QCheckBox("明确允许远程明文 WS")
        self.agent_allow_insecure_ws.setAccessibleDescription(
            "仅用于可信内网；公网或跨网访问应使用 WSS"
        )
        agent_layout.addWidget(self.agent_allow_insecure_ws)
        self.insecure_ws_warning = QLabel(
            "警告：远程 WS 会让对话和 token 在网络中以明文传输。"
        )
        self.insecure_ws_warning.setProperty("status", "warning")
        self.insecure_ws_warning.setWordWrap(True)
        agent_layout.addWidget(self.insecure_ws_warning)

        self.agent_tls_verify = QCheckBox("校验 Agent HTTPS / WSS 证书")
        self.agent_tls_verify.setChecked(True)
        agent_layout.addWidget(self.agent_tls_verify)
        self.agent_ca_file = QLineEdit()
        self.agent_ca_file.setStyleSheet(STYLE_INPUT)
        self.agent_ca_file.setPlaceholderText("可选：内部 CA 文件路径")
        _field(agent_layout, "Agent CA 文件：", self.agent_ca_file, "Agent CA 文件")

        agent_test_row = QHBoxLayout()
        self.test_agent_connection_btn = QPushButton("测试 Agent 连接")
        self.test_agent_connection_btn.setAccessibleName("测试 Agent 连接")
        self.test_agent_connection_btn.setProperty("doesNotModifyConfig", True)
        agent_test_row.addWidget(self.test_agent_connection_btn)
        self.agent_connection_status = QLabel("尚未测试")
        self.agent_connection_status.setProperty("status", "muted")
        self.agent_connection_status.setAccessibleName("Agent 连接测试状态")
        self.agent_connection_status.setWordWrap(True)
        agent_test_row.addWidget(self.agent_connection_status, 1)
        agent_layout.addLayout(agent_test_row)

        control_title = QLabel("Agent 主动控制桌宠（Companion MCP）")
        control_title.setObjectName("SectionTitle")
        agent_layout.addWidget(control_title)
        self.control_enabled = QCheckBox("允许当前 Agent 主动控制（默认关闭）")
        self.control_enabled.setAccessibleDescription(
            "开启后 Agent 可请求说话、表情、状态和逐次确认的截图"
        )
        agent_layout.addWidget(self.control_enabled)

        self.control_frame = QFrame()
        self.control_frame.setObjectName("SectionCard")
        control_layout = QVBoxLayout(self.control_frame)
        control_layout.setContentsMargins(14, 12, 14, 14)
        control_layout.setSpacing(9)

        address_row = QHBoxLayout()
        listen_col = QVBoxLayout()
        listen_col.addWidget(QLabel("本机监听 IP："))
        self.control_listen_host = QLineEdit("127.0.0.1")
        self.control_listen_host.setStyleSheet(STYLE_INPUT)
        self.control_listen_host.setAccessibleName("Companion MCP 本机监听 IP")
        listen_col.addWidget(self.control_listen_host)
        allowed_col = QVBoxLayout()
        allowed_col.addWidget(QLabel("唯一允许的 Agent IP："))
        self.control_allowed_ip = QLineEdit("127.0.0.1")
        self.control_allowed_ip.setStyleSheet(STYLE_INPUT)
        self.control_allowed_ip.setAccessibleName("Companion MCP 允许的 Agent IP")
        allowed_col.addWidget(self.control_allowed_ip)
        address_row.addLayout(listen_col, 1)
        address_row.addLayout(allowed_col, 1)
        control_layout.addLayout(address_row)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("监听端口："))
        self.control_port = QSpinBox()
        self.control_port.setRange(1, 65535)
        self.control_port.setValue(8765)
        self.control_port.setAccessibleName("Companion MCP 监听端口")
        self.control_port.setMinimumHeight(MIN_TARGET_SIZE)
        port_row.addWidget(self.control_port)
        port_row.addStretch()
        control_layout.addLayout(port_row)

        self.control_auth_token = QLineEdit()
        self.control_auth_token.setStyleSheet(STYLE_INPUT)
        self.control_auth_token.setEchoMode(QLineEdit.Password)
        self.control_auth_token.setPlaceholderText("留空时首次启动自动生成；也可填环境变量占位符")
        _field(
            control_layout,
            "Companion MCP Bearer Token：",
            self.control_auth_token,
            "Companion MCP 访问令牌",
        )
        token_actions = QHBoxLayout()
        self.control_token_visibility = QPushButton("查看")
        self.control_token_visibility.setMinimumHeight(MIN_TARGET_SIZE)
        self.control_token_visibility.setAccessibleName("查看 Companion MCP 令牌")
        self.control_token_visibility.clicked.connect(
            self._toggle_control_token_visibility
        )
        token_actions.addWidget(self.control_token_visibility)
        self.control_token_copy = QPushButton("复制")
        self.control_token_copy.setMinimumHeight(MIN_TARGET_SIZE)
        self.control_token_copy.setAccessibleName("复制 Companion MCP 令牌")
        self.control_token_copy.clicked.connect(self._copy_control_token)
        token_actions.addWidget(self.control_token_copy)
        self.control_token_regenerate = QPushButton("重新生成")
        self.control_token_regenerate.setMinimumHeight(MIN_TARGET_SIZE)
        self.control_token_regenerate.setAccessibleName(
            "重新生成 Companion MCP 令牌"
        )
        self.control_token_regenerate.clicked.connect(
            self._regenerate_control_token
        )
        token_actions.addWidget(self.control_token_regenerate)
        token_actions.addStretch()
        control_layout.addLayout(token_actions)

        self.control_allow_http = QCheckBox("明确允许不安全的内网 HTTP")
        control_layout.addWidget(self.control_allow_http)
        self.insecure_http_warning = QLabel(
            "警告：HTTP 会让对话、截图和 token 在内网中以明文传输。"
        )
        self.insecure_http_warning.setProperty("status", "warning")
        self.insecure_http_warning.setWordWrap(True)
        control_layout.addWidget(self.insecure_http_warning)

        self.control_cert_file = QLineEdit()
        self.control_cert_file.setStyleSheet(STYLE_INPUT)
        _field(control_layout, "HTTPS 证书：", self.control_cert_file, "MCP HTTPS 证书")
        self.control_key_file = QLineEdit()
        self.control_key_file.setStyleSheet(STYLE_INPUT)
        _field(control_layout, "HTTPS 私钥：", self.control_key_file, "MCP HTTPS 私钥")
        self.control_ca_file = QLineEdit()
        self.control_ca_file.setStyleSheet(STYLE_INPUT)
        _field(control_layout, "客户端 CA（可选）：", self.control_ca_file, "MCP 客户端 CA")

        firewall = QLabel(
            "MeaPet 不会自动修改 Windows 防火墙；内网无法连接时请检查端口放行。"
        )
        firewall.setObjectName("HelperText")
        firewall.setWordWrap(True)
        control_layout.addWidget(firewall)
        agent_layout.addWidget(self.control_frame)
        layout.addWidget(self.agent_frame)

        self.direct_radio.toggled.connect(self._sync_visibility)
        self.agent_radio.toggled.connect(self._sync_visibility)
        self.control_enabled.toggled.connect(self._sync_visibility)
        self.control_allow_http.toggled.connect(self._sync_visibility)
        self.agent_allow_insecure_ws.toggled.connect(self._sync_visibility)
        self.agent_kind.currentIndexChanged.connect(self._on_agent_kind_changed)
        self._agent_identity_path = ""
        self._sync_visibility()

    def mode(self) -> str:
        return "agent" if self.agent_radio.isChecked() else "direct"

    def _toggle_control_token_visibility(self) -> None:
        visible = self.control_auth_token.echoMode() == QLineEdit.Normal
        self.control_auth_token.setEchoMode(
            QLineEdit.Password if visible else QLineEdit.Normal
        )
        self.control_token_visibility.setText("查看" if visible else "隐藏")

    def _copy_control_token(self) -> None:
        token = self.control_auth_token.text().strip()
        if token:
            QApplication.clipboard().setText(token)

    def _regenerate_control_token(self) -> None:
        self.control_auth_token.setText(secrets.token_urlsafe(48))

    def set_agent_kind(self, kind: str) -> None:
        index = self.agent_kind.findData(str(kind or "hermes").lower())
        self.agent_kind.setCurrentIndex(index if index >= 0 else 0)

    def _on_agent_kind_changed(self, *_args) -> None:
        kind = self.agent_kind.currentData() or "hermes"
        current = self.agent_base_url.text().strip()
        defaults = {
            "hermes": "http://127.0.0.1:8642",
            "openclaw": "ws://127.0.0.1:18789",
        }
        if not current or current in defaults.values():
            self.agent_base_url.setText(defaults[kind])
        self._sync_visibility()

    def _sync_visibility(self, *_args) -> None:
        agent_mode = self.agent_radio.isChecked()
        openclaw_mode = (self.agent_kind.currentData() or "hermes") == "openclaw"
        self.agent_frame.setVisible(agent_mode)
        self.agent_allow_insecure_ws.setVisible(agent_mode and openclaw_mode)
        self.insecure_ws_warning.setVisible(
            agent_mode
            and openclaw_mode
            and self.agent_allow_insecure_ws.isChecked()
        )
        self.control_frame.setVisible(
            agent_mode and self.control_enabled.isChecked()
        )
        self.insecure_http_warning.setVisible(
            agent_mode
            and self.control_enabled.isChecked()
            and self.control_allow_http.isChecked()
        )

    def apply_config(self, llm: dict, control: dict, ui: dict | None = None) -> None:
        llm = llm or {}
        agent = llm.get("agent") if isinstance(llm.get("agent"), dict) else {}
        mode = str(llm.get("mode") or "direct").lower()
        self.agent_radio.setChecked(mode == "agent")
        self.direct_radio.setChecked(mode != "agent")
        self.set_agent_kind(agent.get("kind", "hermes"))
        self.agent_base_url.setText(str(agent.get("base_url") or ""))
        self.agent_auth_token.setText(str(agent.get("auth_token") or ""))
        self.agent_session_id.setText(str(agent.get("session_id") or ""))
        self.agent_session_key.setText(str(agent.get("session_key") or ""))
        try:
            self.agent_history_turns.setValue(int(agent.get("history_turns", 5)))
        except (TypeError, ValueError):
            self.agent_history_turns.setValue(5)
        self.agent_allow_insecure_ws.setChecked(
            bool(agent.get("allow_insecure_ws", False))
        )
        self._agent_identity_path = str(agent.get("identity_path") or "")
        tls = agent.get("tls") if isinstance(agent.get("tls"), dict) else {}
        self.agent_tls_verify.setChecked(bool(tls.get("verify", True)))
        self.agent_ca_file.setText(str(tls.get("ca_file") or ""))
        ui = ui if isinstance(ui, dict) else {}
        try:
            self.timeline_turns.setValue(int(ui.get("timeline_turns", 5)))
        except (TypeError, ValueError):
            self.timeline_turns.setValue(5)

        control = control or {}
        self.control_enabled.setChecked(bool(control.get("enabled", False)))
        self.control_listen_host.setText(
            str(control.get("listen_host") or "127.0.0.1")
        )
        self.control_allowed_ip.setText(
            str(control.get("allowed_agent_ip") or "127.0.0.1")
        )
        try:
            self.control_port.setValue(int(control.get("port", 8765)))
        except (TypeError, ValueError):
            self.control_port.setValue(8765)
        self.control_auth_token.setText(str(control.get("auth_token") or ""))
        self.control_allow_http.setChecked(
            bool(control.get("allow_insecure_http", False))
        )
        self.control_cert_file.setText(str(control.get("cert_file") or ""))
        self.control_key_file.setText(str(control.get("key_file") or ""))
        self.control_ca_file.setText(str(control.get("ca_file") or ""))
        self._sync_visibility()

    def collect_agent(self) -> dict:
        return {
            "kind": self.agent_kind.currentData() or "hermes",
            "base_url": self.agent_base_url.text().strip(),
            "auth_token": self.agent_auth_token.text().strip(),
            "session_id": self.agent_session_id.text().strip(),
            "session_key": self.agent_session_key.text().strip(),
            "history_turns": self.agent_history_turns.value(),
            "allow_insecure_ws": self.agent_allow_insecure_ws.isChecked(),
            "identity_path": self._agent_identity_path,
            "tls": {
                "verify": self.agent_tls_verify.isChecked(),
                "ca_file": self.agent_ca_file.text().strip(),
            },
        }

    def collect_control(self) -> dict:
        return {
            "enabled": self.control_enabled.isChecked(),
            "listen_host": self.control_listen_host.text().strip() or "127.0.0.1",
            "port": self.control_port.value(),
            "allowed_agent_ip": self.control_allowed_ip.text().strip()
            or "127.0.0.1",
            "auth_token": self.control_auth_token.text().strip(),
            "allow_insecure_http": self.control_allow_http.isChecked(),
            "cert_file": self.control_cert_file.text().strip(),
            "key_file": self.control_key_file.text().strip(),
            "ca_file": self.control_ca_file.text().strip(),
        }
