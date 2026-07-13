"""对话时间线和完整本轮回复窗口。"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from meapet.conversation.timeline import TurnTranscript
from meapet.desktop.theme import DIALOG_STYLE
from meapet.ui_theme import MIN_TARGET_SIZE, ensure_application_fonts, set_scaled_stylesheet


_SOURCE_NAMES = {
    "user_reply": "用户对话",
    "agent_proactive": "Agent 主动消息",
    "system": "系统",
}


def render_turn_text(turn: TurnTranscript) -> str:
    lines = []
    if turn.user_text:
        lines.extend(("用户", turn.user_text, ""))
    if turn.segments:
        lines.append("回复")
        for index, segment in enumerate(turn.segments, 1):
            lines.append(f"{index}. {segment.display_text}")
    if turn.system_entries:
        lines.extend(("", "状态"))
        for entry in turn.system_entries:
            text = entry.safe_text or {
                "started": "正在处理",
                "succeeded": "处理完成",
                "failed": "处理失败",
            }.get(entry.state, "状态已更新")
            lines.append(f"- {text}")
    if turn.error_text:
        lines.extend(("", f"错误：{turn.error_text}"))
    return "\n".join(lines).strip()


class TurnDetailDialog(QDialog):
    def __init__(self, turn: TurnTranscript, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.turn = turn
        self.setWindowTitle("本轮完整回复")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(520, 420)
        self.resize(620, 520)
        set_scaled_stylesheet(self, DIALOG_STYLE)

        layout = QVBoxLayout(self)
        title = QLabel("本轮完整回复")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        meta = QLabel(
            f"{_SOURCE_NAMES.get(turn.source, turn.source)} · "
            f"{datetime.fromtimestamp(turn.created_at).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        meta.setObjectName("HelperText")
        layout.addWidget(meta)

        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        self.content.setPlainText(render_turn_text(turn))
        self.content.setAccessibleName("本轮完整回复正文")
        layout.addWidget(self.content, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        copy_button = QPushButton("复制全部")
        copy_button.setAccessibleName("复制本轮完整回复")
        copy_button.setMinimumSize(108, MIN_TARGET_SIZE)
        copy_button.clicked.connect(self._copy_all)
        buttons.addWidget(copy_button)
        close_button = QPushButton("关闭")
        close_button.setAccessibleName("关闭完整回复")
        close_button.setMinimumSize(88, MIN_TARGET_SIZE)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self.content.toPlainText())


class TimelineDialog(QDialog):
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.timeline = timeline
        self._detail = None
        self.setWindowTitle("对话时间线")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(560, 460)
        self.resize(680, 600)
        set_scaled_stylesheet(self, DIALOG_STYLE)

        layout = QVBoxLayout(self)
        title = QLabel("最近对话时间线")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        hint = QLabel("不同后端与 Agent 会话彼此隔离；旧会话仅供只读查看。")
        hint.setObjectName("HelperText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self.turn_layout = QVBoxLayout(body)
        self.turn_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        close_button = QPushButton("关闭")
        close_button.setAccessibleName("关闭对话时间线")
        close_button.setMinimumSize(88, MIN_TARGET_SIZE)
        close_button.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        while self.turn_layout.count():
            item = self.turn_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        turns = tuple(reversed(self.timeline.all_recent()))
        if not turns:
            empty = QLabel("还没有可查看的对话。")
            empty.setObjectName("HelperText")
            self.turn_layout.addWidget(empty)
            return
        for turn in turns:
            preview = turn.display_text or turn.error_text or "状态更新"
            label = (
                f"{_SOURCE_NAMES.get(turn.source, turn.source)} · "
                f"{datetime.fromtimestamp(turn.created_at).strftime('%H:%M:%S')}\n"
                f"{preview[:100]}"
            )
            button = QPushButton(label)
            button.setMinimumHeight(64)
            button.setAccessibleName(f"查看本轮：{preview[:40]}")
            button.clicked.connect(
                lambda _checked=False, current=turn: self.show_turn(current)
            )
            self.turn_layout.addWidget(button)

    def show_turn(self, turn: TurnTranscript) -> None:
        self._detail = TurnDetailDialog(turn, self)
        self._detail.show()
        self._detail.raise_()
