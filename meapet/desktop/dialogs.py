"""MeaPet 桌面端的主题化安全对话框。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from meapet.desktop.capture_selection import select_screen_region
from meapet.desktop.theme import CONSENT_DIALOG_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)
from meapet.ui_controls import WheelSafeComboBox
from meapet.watcher.capture import (
    CaptureError,
    CaptureWindow,
    list_capture_windows,
)


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


class _PopupAwareComboBox(WheelSafeComboBox):
    """让授权框能在下拉列表打开期间暂停倒计时。"""

    popup_opened = pyqtSignal()
    popup_closed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._popup_visible = False

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        if not self._popup_visible:
            self._popup_visible = True
            self.popup_opened.emit()
        super().showPopup()

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        super().hidePopup()
        if self._popup_visible:
            self._popup_visible = False
            self.popup_closed.emit()


def _normalized_selected_region(region: object) -> dict[str, int] | None:
    if not isinstance(region, dict):
        return None
    try:
        result = {
            key: int(region[key])
            for key in ("x", "y", "width", "height")
        }
    except (KeyError, TypeError, ValueError):
        return None
    if result["width"] < 2 or result["height"] < 2:
        return None
    return result


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
        region_selector: Callable[
            [object, dict[str, int] | None],
            dict[str, int] | None,
        ] | None = None,
        window_provider: Callable[[], Iterable[CaptureWindow]] | None = None,
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
        self.setFixedWidth(440)
        set_scaled_stylesheet(self, CONSENT_DIALOG_STYLE)
        self.setAccessibleName(accessible_name)
        self.setAccessibleDescription(
            "最终截图范围由本机用户选择，仅本次有效；"
            "手动更改截图方式后关闭自动取消倒计时"
        )

        self.remaining_seconds = max(1, int(timeout_seconds))
        self.auto_cancelled = False
        self._explicit_allow = False
        self.approval: CaptureApproval | None = None
        self._countdown_disabled = False
        self._countdown_pause_reasons: set[str] = set()
        self._selection_in_progress = False
        self._refresh_in_progress = False
        self._applications_loaded = False
        self._requested_region = _normalized_selected_region(requested_region)
        # Agent 请求的坐标只用于拖选层预览，不能自动成为本机最终授权。
        self._selected_region: dict[str, int] | None = None
        self._requested_application = str(requested_application or "").strip()
        self._region_selector = region_selector or select_screen_region
        self._window_provider = window_provider or (
            lambda: list_capture_windows(exclude_process_id=os.getpid())
        )

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QFrame()
        card.setObjectName("CloudConsentCard")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        self._outer_layout = outer
        self._content_card = card
        self._content_layout = layout

        eyebrow = QLabel("隐私保护 · 本次有效 · 默认取消")
        eyebrow.setObjectName("ConsentEyebrow")
        layout.addWidget(eyebrow)
        title_label = QLabel(heading)
        title_label.setObjectName("ConsentTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        body = QLabel(message)
        body.setObjectName("ConsentBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        scope_label = QLabel("本次截图范围：")
        scope_label.setObjectName("FieldLabel")
        layout.addWidget(scope_label)
        self.scope_combo = _PopupAwareComboBox()
        self.scope_combo.setObjectName("CaptureConsentScope")
        self.scope_combo.setAccessibleName("本次截图范围")
        self.scope_combo.setMinimumHeight(MIN_TARGET_SIZE)
        self.scope_combo.addItem("全部屏幕（默认）", "full_screen")
        self.scope_combo.addItem("矩形区域", "region")
        self.scope_combo.addItem("Windows 应用窗口", "application")
        self.scope_combo.popup_opened.connect(
            lambda: self._pause_countdown("scope_popup")
        )
        self.scope_combo.popup_closed.connect(
            lambda: self._resume_countdown("scope_popup")
        )
        layout.addWidget(self.scope_combo)

        self.region_frame = QFrame()
        self.region_frame.setObjectName("SectionCard")
        region_layout = QVBoxLayout(self.region_frame)
        region_layout.setContentsMargins(10, 8, 10, 8)
        region_hint = QLabel(
            "确认框会暂时隐藏。像系统截图工具一样按住鼠标拖出矩形；Esc 或右键取消。"
        )
        region_hint.setObjectName("HelperText")
        region_hint.setWordWrap(True)
        region_layout.addWidget(region_hint)
        self.region_summary = QLabel()
        self.region_summary.setObjectName("SelectionSummary")
        self.region_summary.setWordWrap(True)
        self.region_summary.setAccessibleName("已选择的截图区域")
        region_layout.addWidget(self.region_summary)
        self.select_region_button = QPushButton("拖拽选择区域")
        self.select_region_button.setObjectName("SelectRegionButton")
        self.select_region_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.select_region_button.setAccessibleName("打开全屏拖拽区域选择器")
        self.select_region_button.clicked.connect(self._choose_region)
        region_layout.addWidget(self.select_region_button)
        self._update_region_summary()
        layout.addWidget(self.region_frame)

        self.application_frame = QFrame()
        self.application_frame.setObjectName("SectionCard")
        application_layout = QVBoxLayout(self.application_frame)
        application_layout.setContentsMargins(10, 8, 10, 8)
        application_hint = QLabel(
            "从当前可见且未最小化的窗口中选择；列表显示进程、PID 和窗口标题。"
        )
        application_hint.setObjectName("HelperText")
        application_hint.setWordWrap(True)
        application_layout.addWidget(application_hint)
        application_row = QHBoxLayout()
        application_row.setSpacing(8)
        self.application_combo = _PopupAwareComboBox()
        self.application_combo.setObjectName("CaptureConsentApplication")
        self.application_combo.setAccessibleName("本次截图应用窗口列表")
        self.application_combo.setMinimumHeight(MIN_TARGET_SIZE)
        self.application_combo.addItem("切换到此范围后加载可见窗口", None)
        self.application_combo.popup_opened.connect(
            lambda: self._pause_countdown("application_popup")
        )
        self.application_combo.popup_closed.connect(
            lambda: self._resume_countdown("application_popup")
        )
        application_row.addWidget(self.application_combo, 1)
        self.refresh_windows_button = QPushButton("刷新")
        self.refresh_windows_button.setObjectName("RefreshWindowsButton")
        self.refresh_windows_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.refresh_windows_button.setAccessibleName("刷新可截图窗口列表")
        self.refresh_windows_button.clicked.connect(
            lambda: self._refresh_applications(force=True)
        )
        application_row.addWidget(self.refresh_windows_button)
        application_layout.addLayout(application_row)
        layout.addWidget(self.application_frame)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("ConsentValidation")
        self.validation_label.setWordWrap(True)
        self.validation_label.setAccessibleName("截图范围校验提示")
        self.validation_label.hide()
        layout.addWidget(self.validation_label)
        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("ConsentCountdown")
        self.countdown_label.setAccessibleName("自动取消倒计时")
        layout.addWidget(self.countdown_label)
        self._update_countdown()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
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

        # 先在所有可选面板隐藏时记录稳定的紧凑高度。后续显隐按该基线
        # 显式加上面板高度，避免窗口管理器忽略同一事件循环内的 sizeHint。
        self.region_frame.hide()
        self.application_frame.hide()
        self._compact_height = 0
        self._resize_to_content()
        self._compact_height = self.height()

        requested = str(requested_scope or "full_screen").strip().lower()
        index = self.scope_combo.findData(requested)
        self.scope_combo.setCurrentIndex(index if index >= 0 else 0)
        self._last_activated_scope = (
            self.scope_combo.currentData() or "full_screen"
        )
        self.scope_combo.currentIndexChanged.connect(self._sync_scope)
        self.scope_combo.activated.connect(self._scope_activated)
        self._sync_scope()
        self._resize_to_content()

    @property
    def countdown_paused(self) -> bool:
        return bool(self._countdown_pause_reasons)

    def _pause_countdown(self, reason: str) -> None:
        self._countdown_pause_reasons.add(str(reason or "selection"))
        self._timer.stop()
        self._update_countdown()

    def _resume_countdown(self, reason: str) -> None:
        self._countdown_pause_reasons.discard(str(reason or "selection"))
        self._update_countdown()
        if (
            not self._countdown_disabled
            and not self.countdown_paused
            and self.isVisible()
            and self.remaining_seconds > 0
        ):
            self._timer.start()

    def _disable_countdown(self) -> None:
        """用户已主动参与范围选择后，不再用五秒超时打断确认。"""
        if self._countdown_disabled:
            return
        self._countdown_disabled = True
        self._timer.stop()
        self.countdown_label.setAccessibleDescription(
            "用户已手动更改截图方式，自动取消倒计时已关闭"
        )
        self.countdown_label.hide()
        self._resize_to_content()

    def _scope_activated(self, index: int) -> None:
        scope = self.scope_combo.itemData(index) or "full_screen"
        if scope != self._last_activated_scope:
            self._disable_countdown()
        self._last_activated_scope = scope
        if scope == "region":
            QTimer.singleShot(0, self._choose_region)
        elif scope == "application":
            self._ensure_applications()

    def _sync_scope(self, *_args) -> None:
        scope = self.scope_combo.currentData() or "full_screen"
        self.region_frame.setVisible(scope == "region")
        self.application_frame.setVisible(scope == "application")
        self.validation_label.clear()
        self.validation_label.hide()
        self._resize_to_content()
        # 子控件从可见变隐藏时，Qt 会到下一轮事件循环才刷新窗口
        # sizeHint；再收缩一次，避免保留上一个范围面板的高度。
        if self.isVisible():
            QTimer.singleShot(0, self._resize_to_content)

    def _update_region_summary(self) -> None:
        region = self._selected_region
        if region is None:
            requested = self._requested_region
            if requested is None:
                self.region_summary.setText("尚未选择区域。")
            else:
                self.region_summary.setText(
                    f"请求范围：{requested['width']} × {requested['height']}；"
                    "仍需由你重新拖拽确认。"
                )
            return
        self.region_summary.setText(
            f"已选择：{region['width']} × {region['height']}，"
            f"位置 ({region['x']}, {region['y']})"
        )

    def _choose_region(self) -> None:
        if self._selection_in_progress:
            return
        self._selection_in_progress = True
        self._pause_countdown("region_selector")
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            QApplication.processEvents()
        selected = None
        error = ""
        try:
            selected = self._region_selector(
                self,
                dict(self._selected_region or self._requested_region)
                if (self._selected_region or self._requested_region)
                else None,
            )
        except Exception as exc:
            error = f"无法打开区域选择器：{type(exc).__name__}"
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()
        self._selection_in_progress = False
        self._resume_countdown("region_selector")

        normalized = _normalized_selected_region(selected)
        if normalized is not None:
            self._selected_region = normalized
            self.validation_label.clear()
            self.validation_label.hide()
        elif error:
            self.validation_label.setText(error)
            self.validation_label.show()
        self._update_region_summary()
        self._resize_to_content()

    def _ensure_applications(self) -> None:
        if not self._applications_loaded:
            self._refresh_applications()

    def _refresh_applications(self, *, force: bool = False) -> None:
        if self._refresh_in_progress:
            return
        if self._applications_loaded and not force:
            return
        self._refresh_in_progress = True
        self._pause_countdown("window_refresh")
        self.refresh_windows_button.setEnabled(False)
        self.application_combo.setEnabled(False)
        QApplication.processEvents()
        windows: tuple[CaptureWindow, ...] = ()
        error = ""
        try:
            windows = tuple(self._window_provider())
        except CaptureError as exc:
            if exc.code == "dependency_missing":
                error = "应用窗口列表需要安装 Windows pywin32 支持。"
            else:
                error = "暂时无法读取可见窗口，请点击刷新重试。"
        except Exception:
            error = "暂时无法读取可见窗口，请点击刷新重试。"

        self.application_combo.clear()
        for window in windows:
            if isinstance(window, CaptureWindow):
                self.application_combo.addItem(window.label, window)
        if self.application_combo.count() == 0:
            message = error or (
                "仅 Windows 支持应用窗口截图。"
                if os.name != "nt"
                else "没有找到可截图的可见窗口。"
            )
            self.application_combo.addItem(message, None)
            self.application_combo.setEnabled(False)
        else:
            self.application_combo.setEnabled(True)
            requested = self._requested_application.casefold()
            if requested:
                for index in range(self.application_combo.count()):
                    window = self.application_combo.itemData(index)
                    if not isinstance(window, CaptureWindow):
                        continue
                    if (
                        requested == window.title.casefold()
                        or requested == window.process_name.casefold()
                        or requested in window.title.casefold()
                    ):
                        self.application_combo.setCurrentIndex(index)
                        break
        self._applications_loaded = True
        self._refresh_in_progress = False
        self.refresh_windows_button.setEnabled(True)
        self._resume_countdown("window_refresh")
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """在可选范围字段显隐后收缩窗口，不保留空白占位。"""
        # Qt 会分别缓存嵌套布局的 sizeHint；只激活根布局在某些平台或
        # 测试顺序下仍会拿到显隐前的高度。由内向外主动失效并重算。
        for frame in (self.region_frame, self.application_frame):
            frame.updateGeometry()
            frame_layout = frame.layout()
            if frame_layout is not None:
                frame_layout.invalidate()
                frame_layout.activate()
        self._content_layout.invalidate()
        self._content_layout.activate()
        self._content_card.updateGeometry()
        self._outer_layout.invalidate()
        self._outer_layout.activate()
        self.updateGeometry()
        target_height = self._outer_layout.totalSizeHint().height()
        if target_height <= 0:
            target_height = self.sizeHint().height()
        compact_height = int(getattr(self, "_compact_height", 0) or 0)
        if compact_height > 0:
            target_height = compact_height
            if self.countdown_label.isHidden():
                target_height -= (
                    self.countdown_label.sizeHint().height()
                    + self._content_layout.spacing()
                )
            scope = self.scope_combo.currentData() or "full_screen"
            if scope == "region":
                target_height += (
                    self.region_frame.sizeHint().height()
                    + self._content_layout.spacing()
                )
            elif scope == "application":
                target_height += (
                    self.application_frame.sizeHint().height()
                    + self._content_layout.spacing()
                )
            if not self.validation_label.isHidden():
                target_height += (
                    self.validation_label.sizeHint().height()
                    + self._content_layout.spacing()
                )
        target_height = max(
            target_height,
            self.minimumHeight(),
            self.minimumSizeHint().height(),
        )
        self.resize(self.width(), target_height)

    def _update_countdown(self) -> None:
        if self._countdown_disabled:
            self.countdown_label.hide()
            return
        self.countdown_label.show()
        if self.countdown_paused:
            self.countdown_label.setText(
                f"正在选择截图范围，倒计时已暂停（剩余 {self.remaining_seconds} 秒）。"
            )
            self.countdown_label.setAccessibleDescription(
                "选择区域、窗口或截图方式期间不会自动取消"
            )
            return
        self.countdown_label.setText(f"{self.remaining_seconds} 秒后自动取消。")
        self.countdown_label.setAccessibleDescription(
            f"剩余 {self.remaining_seconds} 秒，超时后拒绝截图"
        )

    def _tick(self) -> None:
        if (
            self._countdown_disabled
            or self.countdown_paused
            or self.remaining_seconds <= 0
        ):
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
            region = (
                dict(self._selected_region)
                if self._selected_region is not None
                else None
            )
            if region is None:
                self.validation_label.setText("请先点击“拖拽选择区域”完成框选。")
                self.validation_label.show()
                self._resize_to_content()
                self.select_region_button.setFocus(Qt.OtherFocusReason)
                return
        elif scope == "application":
            window = self.application_combo.currentData()
            if isinstance(window, CaptureWindow):
                application = window.title[:256]
            if not application:
                self.validation_label.setText("请选择一个当前可见的应用窗口。")
                self.validation_label.show()
                self._resize_to_content()
                self.refresh_windows_button.setFocus(Qt.OtherFocusReason)
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
        self._resize_to_content()
        super().showEvent(event)
        if self.parentWidget() is not None:
            center = self.parentWidget().frameGeometry().center()
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
        if not self._countdown_disabled and not self.countdown_paused:
            self._timer.start()
        scope = self.scope_combo.currentData() or "full_screen"
        if scope == "region" and not self._selection_in_progress:
            QTimer.singleShot(0, self._choose_region)
        elif scope == "application":
            QTimer.singleShot(0, self._ensure_applications)


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
                "截图可能包含聊天、密码、邮件、代码等隐私信息。\n"
                "选择本次范围后，只有点击允许才会截取并上传。"
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
