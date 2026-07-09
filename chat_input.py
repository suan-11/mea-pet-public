"""
梅尔桌宠 - Galgame 风格输入框
半透明渐变背景 + 圆角 + 淡入淡出动画
"""
from PyQt5.QtWidgets import (
    QWidget, QLineEdit, QLabel, QVBoxLayout, QFrame, QHBoxLayout
)
from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QFont


class ChatInputBox(QWidget):
    """Galgame 风格输入框 — 半透明顶部栏 + 文本输入"""

    text_submitted = pyqtSignal(str)  # 用户提交文本

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("")
        self.setFixedSize(480, 80)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._opacity = 0.0
        self._fading = True
        self._fade_step = 0.08

        self._build_ui()

        # 淡入动画
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self.setWindowOpacity(0.0)
        self._anim_timer.start(18)

    def _build_ui(self):
        # 外容器
        container = QFrame(self)
        container.setGeometry(0, 0, 480, 80)
        container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(28, 26, 48, 245),
                    stop:1 rgba(14, 12, 30, 250));
                border: 1px solid rgba(255, 182, 193, 70);
                border-radius: 14px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        # 标题行
        header = QHBoxLayout()
        title = QLabel("和梅尔说点什么吧…")
        title.setStyleSheet("""
            font-size: 11px;
            color: #FFB6C1;
            font-family: "Microsoft YaHei";
            background: transparent;
        """)
        header.addWidget(title)
        header.addStretch()

        # 关闭提示
        hint = QLabel("ESC 关闭")
        hint.setStyleSheet("""
            font-size: 10px;
            color: #666;
            font-family: "Microsoft YaHei";
            background: transparent;
        """)
        header.addWidget(hint)
        layout.addLayout(header)

        # 输入框
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入你想说的话… ✎")
        self.input.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 0, 0, 100);
                color: #fff;
                border: 1px solid rgba(255, 182, 193, 50);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 14px;
                font-family: "Microsoft YaHei";
                selection-background-color: rgba(255, 182, 193, 80);
            }
            QLineEdit:focus {
                border: 1px solid rgba(255, 182, 193, 140);
                background: rgba(0, 0, 0, 130);
            }
        """)
        self.input.returnPressed.connect(self._submit)
        layout.addWidget(self.input)

    def _animate(self):
        if self._opacity < 1.0:
            self._opacity += self._fade_step
            if self._opacity >= 1.0:
                self._opacity = 1.0
                self._anim_timer.stop()
                self._fading = False
            self.setWindowOpacity(self._opacity)

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.text_submitted.emit(text)
        self._close_with_fade()

    def _close_with_fade(self):
        """淡出关闭"""
        self._fading = True
        self._fade_step = 0.10
        self._out = True
        self._anim_timer.disconnect()
        self._anim_timer.timeout.connect(self._fade_out)
        self._anim_timer.start(20)

    def _fade_out(self):
        self._opacity -= self._fade_step
        if self._opacity <= 0.0:
            self._anim_timer.stop()
            self.close()
        else:
            self.setWindowOpacity(self._opacity)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._close_with_fade()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus()
        self.input.selectAll()
