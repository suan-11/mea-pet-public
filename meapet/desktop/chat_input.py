"""MeaPet 的键盘友好型浮动消息输入框。"""

from __future__ import annotations

import os
import logging
import hashlib
from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QMessageBox,
)

from meapet.desktop.theme import CHAT_COMPOSER_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    apply_named_style, )
from meapet.agent.base import TextAttachment
from meapet.agent.text_budget import estimate_tokens, can_attach_files


CHAT_COMPOSER_WIDTH = 480
CHAT_COMPOSER_HEIGHT = 140  # 空态固定高（含底部预留边距）
_CHAT_LAYOUT_SPACING = 4  # 与 _build_ui 中 container 的 spacing 保持一致

# 附件 chip 行（置于输入框上方）。仅在有附件时出现：行本身占位 + 一条 spacing，
# 有附件时把 composer 从空态撑高这么多，从而把整行完整显示出来而非挤压输入框。
CHIP_ROW_HEIGHT = MIN_TARGET_SIZE + 8  # 44 触摸靶 + 上下余量
CHIP_NAME_WIDTH = 150  # chip 内文件名固定显示宽（可省略号截断），不随窗宽压缩
CHIP_ROW_SPACING = 6  # chip 之间的横向间距
# 单枚 chip 的经验宽度：左边距8 + 名称150 + 间距4 + ×按钮44 + 右边距4 ≈ 210。
_CHIP_APPROX_WIDTH = 8 + CHIP_NAME_WIDTH + 4 + MIN_TARGET_SIZE + 4
CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS = (
    CHAT_COMPOSER_HEIGHT + CHIP_ROW_HEIGHT + _CHAT_LAYOUT_SPACING
)

# 文本文件白名单（与 TextAttachment.from_bytes 一致）
_TEXT_FILE_FILTER = (
    "文本文件 (*.txt *.md *.csv *.json *.log *.yaml *.yml *.xml *.ini *.cfg *.env);;"
    "所有文件 (*)"
)


def set_awaiting_reply_state(
    host,
    awaiting: bool,
    message: str = "",
) -> None:
    """同步请求锁与当前消息编辑器，避免回复结束后仍保持只读。"""
    busy = bool(awaiting)
    host._awaiting_reply = busy
    composer = getattr(host, "_chat_input", None)
    if composer is None:
        return
    set_busy = getattr(composer, "set_busy", None)
    if not callable(set_busy):
        return
    try:
        set_busy(busy, message if busy else "")
    except RuntimeError:
        # Qt 对象已被销毁时清理悬空引用；请求状态本身仍已正确更新。
        if getattr(host, "_chat_input", None) is composer:
            host._chat_input = None


class ChatInputBox(QWidget):
    """置顶的消息编辑器，支持 Enter 发送与 Esc 关闭。"""

    text_submitted = pyqtSignal(str)
    # 新增：文件附加请求信号，携带 TextAttachment 列表
    files_attached = pyqtSignal(list)  # list[TextAttachment]
    # 附件 chip 行显隐/高度切换时发出：宿主据此重新贴靠（composer 会向上生长）。
    attachment_row_changed = pyqtSignal()
    # × 移除单个附件时发出，携带被移除的 TextAttachment：宿主据此从待提交集合剔除。
    attachment_removed = pyqtSignal(object)  # TextAttachment

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.setWindowTitle("和梅尔对话")
        self.setObjectName("ChatComposerRoot")
        self.setFixedSize(CHAT_COMPOSER_WIDTH, CHAT_COMPOSER_HEIGHT)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName("和梅尔对话")
        self.setAccessibleDescription("输入消息后按 Enter 或点击发送；按 Escape 关闭")
        apply_named_style(self, "CHAT_COMPOSER_STYLE")

        self._opacity = 0.0
        self._fade_step = 0.08
        self._closing = False
        self.voice_host = None
        self._reduced_motion = os.environ.get("MEA_PET_REDUCED_MOTION", "").lower() in {
            "1",
            "true",
            "yes",
        }

        # 当前已选附件（本轮）
        self._text_attachments: list[TextAttachment] = []
        # 上下文预算回调：由宿主注入，签名 (extra_tokens: int) -> (ok: bool, reason: str)
        self._context_budget_checker = None
        # chip 行控件缓存与显隐状态（替换式：仅有附件时出现）
        self._attach_chips: list[QFrame] = []
        self._attach_row_shown = False

        self._build_ui()

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_in)
        if self._reduced_motion:
            self._opacity = 1.0
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            self._anim_timer.start(18)

    def set_context_budget_checker(self, checker) -> None:
        """注入上下文预算检查回调。

        checker(extra_tokens: int) -> tuple[bool, str]
            返回 (是否允许, 拒绝原因)
        """
        self._context_budget_checker = checker

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame()
        self.container.setObjectName("ChatComposer")
        outer.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(_CHAT_LAYOUT_SPACING)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("发消息给梅尔")
        title.setObjectName("ComposerTitle")
        title.setAccessibleName("消息编辑器")
        header.addWidget(title)

        self.hint_label = QLabel("Enter 发送 · Esc 关闭")
        self.hint_label.setObjectName("ComposerHint")
        header.addWidget(self.hint_label)

        self.feedback_label = QLabel("")
        self.feedback_label.setObjectName("ComposerFeedback")
        self.feedback_label.setAccessibleName("消息输入提示")
        self.feedback_label.hide()
        header.addWidget(self.feedback_label)
        header.addStretch()

        self.voice_button = QPushButton("mic")
        self.voice_button.setObjectName("VoiceButton")
        self.voice_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.voice_button.setAccessibleName("语音输入")
        self.voice_button.setToolTip("点击开始录音，再点停止并转文字")
        self.voice_button.clicked.connect(self._toggle_voice)
        self.voice_button.hide()
        header.addWidget(self.voice_button)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_recording)
        self._pulse_state = False
        self._voice_state = "idle"

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("ComposerCloseButton")
        self.close_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.close_button.setAccessibleName("关闭消息输入框")
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self._close_with_fade)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        # ── 附件 chip 行（替换式：置于输入框【上方】，仅有附件时出现）──
        self._build_attach_area()
        layout.addWidget(self.attach_area)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input = QLineEdit()
        self.input.setObjectName("MessageInput")
        self.input.setMinimumHeight(MIN_TARGET_SIZE)
        self.input.setPlaceholderText("输入你想说的话")
        self.input.setAccessibleName("消息内容")
        self.input.setAccessibleDescription("按 Enter 发送消息")
        self.input.returnPressed.connect(self._submit)
        self.input.textChanged.connect(self._clear_feedback)
        input_row.addWidget(self.input, 1)

        # ── 新增：文件选择按钮 ──
        self.file_button = QPushButton("📎")
        self.file_button.setObjectName("FileAttachButton")
        self.file_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.file_button.setAccessibleName("附加文本文件")
        self.file_button.setToolTip("附加文本文件（.txt/.md/.csv/.json/.log 等）")
        self.file_button.clicked.connect(self._select_and_attach_files)
        input_row.addWidget(self.file_button)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("SendButton")
        self.send_button.setMinimumSize(80, MIN_TARGET_SIZE)
        self.send_button.setAccessibleName("发送消息")
        self.send_button.setDefault(True)
        self.send_button.setAutoDefault(True)
        self.send_button.clicked.connect(self._submit)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)

        self.setTabOrder(self.input, self.file_button)
        self.setTabOrder(self.file_button, self.send_button)
        self.setTabOrder(self.send_button, self.close_button)

        self._busy = False

    def set_busy(self, busy: bool, message: str = "") -> None:
        """异步回复进行中时禁用发送，并给出可读反馈。"""
        self._busy = bool(busy)
        self.send_button.setEnabled(not self._busy)
        self.file_button.setEnabled(not self._busy)
        self.input.setReadOnly(self._busy)
        if self._busy:
            text = message or "正在等待回复…"
            self.feedback_label.setText(text)
            self.hint_label.hide()
            self.feedback_label.show()
            self.send_button.setToolTip(text)
            self.setAccessibleDescription(text)
        else:
            self.send_button.setToolTip("发送消息（Enter）")
            self.setAccessibleDescription(
                "输入消息后按 Enter 或点击发送；按 Escape 关闭"
            )
            self._clear_feedback(self.input.text())

    def _animate_in(self) -> None:
        if self._closing:
            return
        self._opacity = min(1.0, self._opacity + self._fade_step)
        self.setWindowOpacity(self._opacity)
        if self._opacity >= 1.0:
            self._anim_timer.stop()

    def _toggle_voice(self) -> None:
        """点击麦克风按钮，委托给 MeaPet 处理。"""
        host = self.voice_host
        if host and hasattr(host, "_voice_toggle_recording"):
            state = host._voice_toggle_recording()
        else:
            self.voice_button.setToolTip("语音输入未就绪")

    def _on_voice_state_changed(self, state: str) -> None:
        """接收 VoiceEngine 状态更新，更新按钮样式和提示文字。"""
        self._voice_state = state
        self._pulse_timer.stop()

        if state == "recording":
            self.voice_button.setText("●")
            self.voice_button.setObjectName("VoiceButtonRecording")
            self.voice_button.setAccessibleName("录音中，点击停止")
            self.voice_button.setToolTip("录音中…点击停止")
            self.voice_button.setEnabled(True)
            self._pulse_timer.start(500)
            self._pulse_state = False
            self.hint_label.setText("录音中…再次点击停止并转文字")
        elif state == "processing":
            self.voice_button.setText("...")
            self.voice_button.setObjectName("VoiceButtonProcessing")
            self.voice_button.setAccessibleName("识别中，请稍候")
            self.voice_button.setToolTip("正在转文字…")
            self.voice_button.setEnabled(False)
            self.hint_label.setText("正在转文字…")
        elif state == "done":
            self.voice_button.setText("mic")
            self.voice_button.setObjectName("VoiceButton")
            self.voice_button.setAccessibleName("语音输入")
            self.voice_button.setToolTip("点击开始录音，再点停止并转文字")
            self.voice_button.setEnabled(True)
            self.hint_label.setText("Enter 发送 · Esc 关闭")
        elif state == "error":
            self.voice_button.setText("!")
            self.voice_button.setObjectName("VoiceButtonError")
            self.voice_button.setAccessibleName("语音识别失败，点击重试")
            self.voice_button.setToolTip("识别失败，点击重试")
            self.voice_button.setEnabled(True)
            self.hint_label.setText("识别失败，点击重试 · Esc 关闭")

    def _pulse_recording(self):
        """录音中闪烁红点。"""
        self._pulse_state = not self._pulse_state
        if self._pulse_state:
            self.voice_button.setObjectName("VoiceButtonRecordingBright")
        else:
            self.voice_button.setObjectName("VoiceButtonRecording")
        # force style refresh
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)

    def show_voice_button(self, visible: bool) -> None:
        self.voice_button.setVisible(visible)

    # ════════════════════════════════════════════════════════════
    # 文件选择 / 上下文预算 / 附件管理
    # ════════════════════════════════════════════════════════════

    def _select_and_attach_files(self) -> None:
        print(">>> DEBUG: _select_and_attach_files CALLED", flush=True)
        print("[file] _select_and_attach_files 被触发")
        """打开文件对话框，选择文本文件并附加（受上下文预算约束）。"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要附加的文本文件", "", _TEXT_FILE_FILTER
        )
        print(f"[file] getOpenFileNames 返回 files={files!r}")
        if not files:
            print("[file] 用户未选择任何文件或对话框被取消")
            return

        new_attachments: list[TextAttachment] = []
        refused: list[str] = []

        for fpath in files:
            try:
                with open(fpath, "rb") as f:
                    raw = f.read()
            except OSError as exc:
                refused.append(f"{os.path.basename(fpath)}: 读取失败({exc})")
                continue

            try:
                att = TextAttachment.from_bytes(os.path.basename(fpath), raw)
            except ValueError as exc:
                # 扩展名不在白名单等
                refused.append(f"{os.path.basename(fpath)}: {exc}")
                continue

            new_attachments.append(att)

        if not new_attachments and refused:
            QMessageBox.warning(self, "文件附加失败", "\n".join(refused))
            return

        # 上下文预算检查（基于已有附件 + 新增附件）
        if self._context_budget_checker is not None:
            projected = self._text_attachments + new_attachments
            extra_tokens = sum(estimate_tokens(a.text_content) + 50 for a in projected)
            ok, reason = self._context_budget_checker(extra_tokens)
            if not ok:
                QMessageBox.warning(
                    self, "上下文预算不足",
                    f"所选文件总大小超出当前上下文剩余容量。\n{reason}"
                )
                return

        # 接受
        self._text_attachments.extend(new_attachments)
        self._refresh_attachments()
        print(f"[file] 准备发射 files_attached 信号，附件数量={len(new_attachments)}")
        self.files_attached.emit(list(new_attachments))
        print("[file] files_attached 信号已发射")

        if refused:
            # 部分成功：告知用户哪些被拒绝（扩展名不合法）
            QMessageBox.information(
                self, "部分文件已附加",
                f"已附加 {len(new_attachments)} 个文件。\n以下文件被跳过：\n"
                + "\n".join(refused)
            )

    def _build_attach_area(self) -> None:
        """构造附件 chip 行容器：[ 横向滚动 chips 条 ][ ~合计钉在右侧 ]，默认隐藏。"""
        self.attach_area = QWidget()
        self.attach_area.setObjectName("AttachArea")
        self.attach_area.setFixedHeight(CHIP_ROW_HEIGHT)
        area = QHBoxLayout(self.attach_area)
        area.setContentsMargins(0, 0, 0, 0)
        area.setSpacing(CHIP_ROW_SPACING)

        self.chip_scroll = QScrollArea()
        self.chip_scroll.setObjectName("AttachChipRow")
        self.chip_scroll.setWidgetResizable(True)
        self.chip_scroll.setFrameShape(QFrame.NoFrame)
        self.chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.chip_host = QWidget()
        self.chip_row = QHBoxLayout(self.chip_host)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.chip_row.setSpacing(CHIP_ROW_SPACING)
        self.chip_scroll.setWidget(self.chip_host)

        self.total_hint = QLabel("")
        self.total_hint.setObjectName("ChipTokenHint")
        self.total_hint.setAccessibleName("附件预计占用总容量")
        self.total_hint.hide()

        area.addWidget(self.chip_scroll, 1)
        area.addWidget(self.total_hint)
        self.attach_area.hide()  # 空态不占位

    def _make_chip(self, att: TextAttachment) -> QFrame:
        """一枚附件 chip：固定宽省略文件名（tooltip 保全长名）+ × 逐个移除。"""
        chip = QFrame()
        chip.setObjectName("AttachChip")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(4)

        name = QLabel(self.fontMetrics().elidedText(
            att.file_name, Qt.ElideMiddle, CHIP_NAME_WIDTH
        ))
        name.setObjectName("ChipName")
        name.setFixedWidth(CHIP_NAME_WIDTH)
        name.setToolTip(f"{att.file_name}（{att.char_count}字）")
        lay.addWidget(name)

        remove = QPushButton("×")
        remove.setObjectName("ChipRemove")
        remove.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        remove.setAccessibleName(f"移除附件 {att.file_name}")
        remove.setToolTip(f"移除 {att.file_name}")
        remove.clicked.connect(lambda _checked=False, a=att: self._remove_attachment(a))
        lay.addWidget(remove)
        return chip

    def _rebuild_attach_chips(self) -> None:
        """按当前附件重建 chip，重算合计 token，并撑出内容宽以启用横向滚动。"""
        for chip in self._attach_chips:
            self.chip_row.removeWidget(chip)
            chip.deleteLater()
        self._attach_chips = []

        total_tokens = 0
        for att in self._text_attachments:
            total_tokens += estimate_tokens(att.text_content) + 50
            chip = self._make_chip(att)
            self.chip_row.addWidget(chip)
            self._attach_chips.append(chip)

        self.total_hint.setText(f"~合计 {total_tokens} tk")

        # widgetResizable=True 会把手势内容压到视口宽，故用固定 chip 常量显式算出
        # 内容最小宽（不依赖尚未 polish 的 sizeHint）；min 宽 > 视口即激活横向滚动。
        n = len(self._text_attachments)
        if n:
            need = n * _CHIP_APPROX_WIDTH + (n - 1) * CHIP_ROW_SPACING
            self.chip_host.setMinimumWidth(need)

    def _sync_attach_area(self) -> None:
        """仅有附件时显示 chip 行并把 composer 撑高；清空后收回。变化时通知宿主。"""
        has = bool(self._text_attachments)
        if has and not self._attach_row_shown:
            self.attach_area.show()
            self.total_hint.show()
            self.setFixedSize(
                CHAT_COMPOSER_WIDTH, CHAT_COMPOSER_HEIGHT_WITH_ATTACHMENTS
            )
            self._attach_row_shown = True
            self.attachment_row_changed.emit()
        elif not has and self._attach_row_shown:
            self.attach_area.hide()
            self.total_hint.hide()
            self.setFixedSize(CHAT_COMPOSER_WIDTH, CHAT_COMPOSER_HEIGHT)
            self._attach_row_shown = False
            self.attachment_row_changed.emit()

    def _refresh_attachments(self) -> None:
        """附件集合变化后刷新展示：重建 chip、同步显隐与高度。"""
        self._rebuild_attach_chips()
        self._sync_attach_area()

    def _remove_attachment(self, att: TextAttachment) -> None:
        """× 移除单个附件：更新本地集合、刷新展示，并通知宿主从待提交集合剔除。"""
        before = len(self._text_attachments)
        self._text_attachments = [a for a in self._text_attachments if a is not att]
        if len(self._text_attachments) == before:
            return
        self._refresh_attachments()
        self.attachment_removed.emit(att)

    def clear_attachments(self) -> None:
        """清空本轮附件（发送后由宿主调用）。"""
        self._text_attachments.clear()
        self._refresh_attachments()

    def get_attachments(self) -> list[TextAttachment]:
        """返回当前已选附件副本。"""
        return list(self._text_attachments)

    # ════════════════════════════════════════════════════════════

    def _submit(self) -> None:
        if getattr(self, "_busy", False):
            if not self.feedback_label.text():
                self.feedback_label.setText("正在等待回复…")
            self.hint_label.hide()
            self.feedback_label.show()
            return
        text = self.input.text().strip()
        if not text and not self._text_attachments:
            self.feedback_label.setText("请输入内容或附加文件后再发送")
            self.hint_label.hide()
            self.feedback_label.show()
            self.input.setFocus(Qt.OtherFocusReason)
            return
        self.send_button.setEnabled(False)
        self._clear_feedback(text)
        # 先退出编辑浮窗，再同步发出信号；接收方显示气泡时不会与输入框重叠。
        self._closing = True
        self._anim_timer.stop()
        self.hide()
        self.close()
        self.text_submitted.emit(text)

    def _clear_feedback(self, _text: str) -> None:
        if self.feedback_label.text():
            self.feedback_label.clear()
        self.feedback_label.hide()
        self.hint_label.show()

    def _close_with_fade(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._reduced_motion:
            self.close()
            return
        self._fade_step = 0.10
        self._anim_timer.stop()
        try:
            self._anim_timer.timeout.disconnect()
        except TypeError:
            pass
        self._anim_timer.timeout.connect(self._fade_out)
        self._anim_timer.start(20)

    def _fade_out(self) -> None:
        self._opacity = max(0.0, self._opacity - self._fade_step)
        if self._opacity <= 0.0:
            self._anim_timer.stop()
            self.close()
            return
        self.setWindowOpacity(self._opacity)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._close_with_fade()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)
        self.input.selectAll()

    def closeEvent(self, event) -> None:
        self._anim_timer.stop()
        super().closeEvent(event)
