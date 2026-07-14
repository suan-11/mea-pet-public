"""MeaPet 桌面端的主题化安全对话框。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from meapet.desktop.theme import CONSENT_DIALOG_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)
from meapet.ui_controls import WheelSafeComboBox


DEFAULT_CLOUD_CONSENT_MESSAGE = "\n".join(
    [
        "即将截取当前屏幕，并把截图发送到云端识别。",
        "",
        "截图可能包含聊天、密码、邮件、代码或其他隐私信息。",
        "只有本次明确允许后才会上传；取消不会截屏。",
    ]
)


@dataclass(frozen=True)
class CaptureApproval:
    """本地用户对单次截图确定的最终范围。"""

    scope: str
    region: dict[str, int] | None = None
    application: str = ""


class CloudVisionConsentDialog(QDialog):
    """有倒计时且始终默认拒绝的云端截图确认框。"""

    def __init__(
        self,
        parent=None,
        *,
        title: str = "允许本次云端识图？",
        message: str = DEFAULT_CLOUD_CONSENT_MESSAGE,
        timeout_seconds: int = 5,
        accept_text: str = "允许本次上传",
    ) -> None:
        super().__init__(parent)
        ensure_application_fonts()
        self.setObjectName("CloudConsentRoot")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420, 270)
        set_scaled_stylesheet(self, CONSENT_DIALOG_STYLE)
        self.setAccessibleName("云端识图隐私确认")
        self.setAccessibleDescription(
            "五秒内必须明确允许，否则自动取消；Escape 和 Enter 默认取消"
        )

        self.remaining_seconds = max(1, int(timeout_seconds))
        self.auto_cancelled = False
        self._explicit_allow = False
        self._accept_text = accept_text

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QFrame()
        card.setObjectName("CloudConsentCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        eyebrow = QLabel("隐私保护 · 默认取消")
        eyebrow.setObjectName("ConsentEyebrow")
        layout.addWidget(eyebrow)

        title_label = QLabel(title)
        title_label.setObjectName("ConsentTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        body = QLabel(message)
        body.setObjectName("ConsentBody")
        body.setWordWrap(True)
        body.setAccessibleName("上传隐私说明")
        layout.addWidget(body, 1)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("ConsentCountdown")
        self.countdown_label.setWordWrap(True)
        self.countdown_label.setAccessibleName("自动取消倒计时")
        layout.addWidget(self.countdown_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.allow_button = QPushButton(self._accept_text)
        self.allow_button.setObjectName("AllowUploadButton")
        self.allow_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.allow_button.setAccessibleName("明确允许本次截图上传")
        self.allow_button.setAutoDefault(False)
        self.allow_button.setDefault(False)
        self.allow_button.clicked.connect(self._allow_once)
        buttons.addWidget(self.allow_button, 1)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("CancelUploadButton")
        self.cancel_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.cancel_button.setAccessibleName("取消截图上传")
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button, 1)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_countdown()

    def _update_countdown(self) -> None:
        self.countdown_label.setText(
            f"{self.remaining_seconds} 秒后自动取消。"
        )
        self.countdown_label.setAccessibleDescription(
            f"剩余 {self.remaining_seconds} 秒，超时后拒绝上传"
        )

    def _tick(self) -> None:
        if self.remaining_seconds <= 0:
            return
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.auto_cancelled = True
            self.reject()
            return
        self._update_countdown()

    def _allow_once(self) -> None:
        self._explicit_allow = True
        self.accept()

    def accept(self) -> None:
        """阻止 Enter、默认按钮或外部误调用绕过显式允许按钮。"""
        if not self._explicit_allow:
            return
        super().accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
        else:
            screen = QApplication.primaryScreen()
            center = screen.availableGeometry().center() if screen is not None else None
        if center is not None:
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            x = center.x() - self.width() // 2
            y = center.y() - self.height() // 2
            if screen is not None:
                available = screen.availableGeometry().adjusted(24, 24, -24, -24)
                max_x = max(available.left(), available.right() - self.width() + 1)
                max_y = max(available.top(), available.bottom() - self.height() + 1)
                x = min(max(x, available.left()), max_x)
                y = min(max(y, available.top()), max_y)
            self.move(x, y)
        self.cancel_button.setFocus(Qt.OtherFocusReason)
        self._timer.start()


def confirm_cloud_vision(
    parent=None,
    *,
    title: str = "允许本次云端识图？",
    message: str = DEFAULT_CLOUD_CONSENT_MESSAGE,
    timeout_seconds: int = 5,
    accept_text: str = "允许本次上传",
) -> bool:
    """仅在用户明确点击允许按钮时返回 ``True``。"""
    dialog = CloudVisionConsentDialog(
        parent,
        title=title,
        message=message,
        timeout_seconds=timeout_seconds,
        accept_text=accept_text,
    )
    return dialog.exec_() == QDialog.Accepted


class CaptureScopeConsentDialog(QDialog):
    """每次 Agent 截图的本地范围选择；默认拒绝且不持久化。"""

    def __init__(
        self,
        parent=None,
        *,
        requested_scope: str = "full_screen",
        requested_region: dict | None = None,
        requested_application: str = "",
        timeout_seconds: int = 15,
        title: str = "允许 Agent 本次截图？",
        heading: str = "允许 Agent 读取一次桌面截图？",
        message: str = (
            "截图只在点击允许后采集，内存传输给 Agent，不会由 MeaPet 落盘。\n"
            "请在下方确定本次的最终范围；Agent 请求的范围不会自动获批。"
        ),
        accept_text: str = "允许本次截图",
        accessible_name: str = "Agent 截图范围确认",
    ) -> None:
        super().__init__(parent)
        ensure_application_fonts()
        self.setObjectName("CaptureScopeConsentRoot")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(500, 500)
        set_scaled_stylesheet(self, CONSENT_DIALOG_STYLE)
        self.setAccessibleName(accessible_name)
        self.setAccessibleDescription(
            "最终截图范围由本机用户选择，仅本次有效，超时自动拒绝"
        )

        self.remaining_seconds = max(1, int(timeout_seconds))
        self.auto_cancelled = False
        self._explicit_allow = False
        self.approval: CaptureApproval | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QFrame()
        card.setObjectName("CloudConsentCard")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        eyebrow = QLabel("隐私保护 · 本次有效 · 默认取消")
        eyebrow.setObjectName("ConsentEyebrow")
        layout.addWidget(eyebrow)
        title_label = QLabel(heading)
        title_label.setObjectName("ConsentTitle")
        layout.addWidget(title_label)
        body = QLabel(message)
        body.setObjectName("ConsentBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        scope_label = QLabel("本次截图范围：")
        scope_label.setObjectName("FieldLabel")
        layout.addWidget(scope_label)
        self.scope_combo = WheelSafeComboBox()
        self.scope_combo.setObjectName("CaptureConsentScope")
        self.scope_combo.setAccessibleName("本次截图范围")
        self.scope_combo.addItem("全部屏幕（默认）", "full_screen")
        self.scope_combo.addItem("矩形区域", "region")
        self.scope_combo.addItem("Windows 应用窗口", "application")
        layout.addWidget(self.scope_combo)

        self.region_frame = QFrame()
        self.region_frame.setObjectName("SectionCard")
        region_layout = QGridLayout(self.region_frame)
        region_layout.setContentsMargins(10, 8, 10, 8)
        region = requested_region if isinstance(requested_region, dict) else {}
        self.region_x = QSpinBox()
        self.region_y = QSpinBox()
        self.region_width = QSpinBox()
        self.region_height = QSpinBox()
        for row, (label, widget, key, default) in enumerate(
            (
                ("X", self.region_x, "x", 0),
                ("Y", self.region_y, "y", 0),
                ("宽", self.region_width, "width", 1280),
                ("高", self.region_height, "height", 720),
            )
        ):
            widget.setRange(
                -100_000 if key in {"x", "y"} else 1,
                100_000,
            )
            try:
                widget.setValue(int(region.get(key, default)))
            except (TypeError, ValueError):
                widget.setValue(default)
            widget.setAccessibleName(f"本次截图区域{label}")
            region_layout.addWidget(QLabel(label), row // 2, (row % 2) * 2)
            region_layout.addWidget(widget, row // 2, (row % 2) * 2 + 1)
        layout.addWidget(self.region_frame)

        self.application_frame = QFrame()
        self.application_frame.setObjectName("SectionCard")
        application_layout = QVBoxLayout(self.application_frame)
        application_layout.setContentsMargins(10, 8, 10, 8)
        application_hint = QLabel(
            "填写可见窗口标题片段；仅 Windows 支持，最小化窗口无法采集。"
        )
        application_hint.setObjectName("HelperText")
        application_hint.setWordWrap(True)
        application_layout.addWidget(application_hint)
        self.application_input = QLineEdit(
            str(requested_application or "").strip()
        )
        self.application_input.setObjectName("CaptureConsentApplication")
        self.application_input.setPlaceholderText("例如 Visual Studio Code")
        self.application_input.setAccessibleName("本次截图应用窗口")
        application_layout.addWidget(self.application_input)
        layout.addWidget(self.application_frame)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("ConsentCountdown")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)
        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("ConsentCountdown")
        self.countdown_label.setAccessibleName("自动取消倒计时")
        layout.addWidget(self.countdown_label)

        buttons = QHBoxLayout()
        self.allow_button = QPushButton(accept_text)
        self.allow_button.setObjectName("AllowUploadButton")
        self.allow_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.allow_button.setAutoDefault(False)
        self.allow_button.setDefault(False)
        self.allow_button.clicked.connect(self._allow_once)
        buttons.addWidget(self.allow_button, 1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("CancelUploadButton")
        self.cancel_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button, 1)
        layout.addLayout(buttons)

        requested = str(requested_scope or "full_screen").strip().lower()
        index = self.scope_combo.findData(requested)
        self.scope_combo.setCurrentIndex(index if index >= 0 else 0)
        self.scope_combo.currentIndexChanged.connect(self._sync_scope)
        self._sync_scope()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_countdown()

    def _sync_scope(self, *_args) -> None:
        scope = self.scope_combo.currentData() or "full_screen"
        self.region_frame.setVisible(scope == "region")
        self.application_frame.setVisible(scope == "application")
        self.validation_label.clear()

    def _update_countdown(self) -> None:
        self.countdown_label.setText(
            f"{self.remaining_seconds} 秒后自动取消。"
        )

    def _tick(self) -> None:
        if self.remaining_seconds <= 0:
            return
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.auto_cancelled = True
            self.reject()
            return
        self._update_countdown()

    def _allow_once(self) -> None:
        scope = self.scope_combo.currentData() or "full_screen"
        region = None
        application = ""
        if scope == "region":
            region = {
                "x": self.region_x.value(),
                "y": self.region_y.value(),
                "width": self.region_width.value(),
                "height": self.region_height.value(),
            }
        elif scope == "application":
            application = self.application_input.text().strip()[:256]
            if not application:
                self.validation_label.setText("请填写本次要采集的应用窗口标题。")
                self.application_input.setFocus(Qt.OtherFocusReason)
                return
        self.approval = CaptureApproval(scope, region, application)
        self._explicit_allow = True
        self.accept()

    def accept(self) -> None:
        if not self._explicit_allow:
            return
        super().accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if self.parentWidget() is not None:
            center = self.parentWidget().frameGeometry().center()
        elif screen is not None:
            center = screen.availableGeometry().center()
        else:
            center = None
        if center is not None:
            self.move(
                center.x() - self.width() // 2,
                center.y() - self.height() // 2,
            )
        self.cancel_button.setFocus(Qt.OtherFocusReason)
        self._timer.start()


class CloudCaptureScopeConsentDialog(CaptureScopeConsentDialog):
    """云端识图的五秒逐次授权，同时由用户确定本次范围。"""

    def __init__(
        self,
        parent=None,
        *,
        timeout_seconds: int = 5,
    ) -> None:
        super().__init__(
            parent,
            requested_scope="full_screen",
            timeout_seconds=timeout_seconds,
            title="允许本次云端识图？",
            heading="允许截取并上传一次桌面画面？",
            message=(
                "截图可能包含聊天、密码、邮件、代码或其他隐私信息。\n"
                "请在下方确定本次范围；仅点击允许后才会截取并上传。"
            ),
            accept_text="允许本次上传",
            accessible_name="云端识图截图范围确认",
        )


def confirm_cloud_capture_scope(
    parent=None,
    *,
    timeout_seconds: int = 5,
) -> CaptureApproval | None:
    """返回用户在五秒授权框内选择的本次云端截图范围。"""
    dialog = CloudCaptureScopeConsentDialog(
        parent,
        timeout_seconds=timeout_seconds,
    )
    if dialog.exec_() != QDialog.Accepted:
        return None
    return dialog.approval


def confirm_capture_scope(
    parent=None,
    *,
    requested_scope: str = "full_screen",
    requested_region: dict | None = None,
    requested_application: str = "",
    timeout_seconds: int = 15,
) -> CaptureApproval | None:
    dialog = CaptureScopeConsentDialog(
        parent,
        requested_scope=requested_scope,
        requested_region=requested_region,
        requested_application=requested_application,
        timeout_seconds=timeout_seconds,
    )
    if dialog.exec_() != QDialog.Accepted:
        return None
    return dialog.approval
