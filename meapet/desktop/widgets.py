"""梅尔桌宠 - UI 组件与后台任务"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from PyQt5.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QDialog, QSlider, QPushButton,
)
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QObject, pyqtSignal

from meapet.desktop.theme import DIALOG_STYLE, DIALOGUE_STYLE
from meapet.ui_theme import MIN_TARGET_SIZE
from meapet.utils import safe_print, log_error
from meapet.chat.engine import ChatEngine
from meapet.tts.service import MeaTTS

def wrap_text(text: str, width: int = 10) -> str:
    """中文按字符换行"""
    result = []
    line = ""
    for ch in text:
        line += ch
        if len(line) >= width:
            result.append(line)
            line = ""
    if line:
        result.append(line)
    return "\n".join(result)



class DialogueBox(QWidget):
    """角色名、正文与状态动效组成的浮动消息框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAccessibleName("梅尔的对话消息")

        self._opacity = 1.0
        self._fade_step = 0.0
        self._fading = False
        self._fade_out = False

        # 外容器
        self._container = QFrame(self)
        self._container.setObjectName("DialogueCard")
        self._container.setStyleSheet(DIALOGUE_STYLE)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # 姓名标签（顶部突出的标签）
        self.name_label = QLabel("梅尔")
        self.name_label.setObjectName("DialogueName")
        self.name_label.setAccessibleName("发言角色")
        self.name_label.setFixedHeight(32)
        self.name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.name_label.hide()
        container_layout.addWidget(self.name_label)

        # 内容标签
        self.text_label = QLabel()
        self.text_label.setObjectName("DialogueText")
        self.text_label.setAccessibleName("梅尔的消息")
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumWidth(260)
        self.text_label.setMinimumHeight(40)
        container_layout.addWidget(self.text_label)

        # 底部装饰线
        self._deco_line = QLabel()
        self._deco_line.setObjectName("DialogueAccent")
        self._deco_line.setAccessibleName("装饰分隔线")
        self._deco_line.setFixedHeight(3)
        container_layout.addWidget(self._deco_line)

        self._container.adjustSize()

        # 淡入淡出计时器
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fadeout)

        self.setWindowOpacity(self._opacity)
        self.hide()

    def show_text(self, text: str, duration_ms: int = 6000, name: str = "梅尔"):
        import re
        clean_text = re.sub(r'【.*?】', '', text).strip()

        # 1. 设置文本
        self.text_label.setText(clean_text)
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.name_label.setText(f" {name} ")
        self.name_label.show()

        # 2. 宽度计算
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.text_label.font())
        pad_h = 40
        lines = clean_text.splitlines() or [""]
        longest_line = max(fm.horizontalAdvance(line) for line in lines)
        text_width = max(240, min(longest_line, 520))
        content_w = text_width + pad_h

        # 3. 固定宽度，让高度自适应
        self.text_label.setFixedWidth(content_w)
        self.text_label.adjustSize()  # 高度自动算

        # 4. 获取自适应后的高度
        label_w = self.text_label.width()
        label_h = self.text_label.height()

        # 5. 姓名标签和装饰线的高度
        name_h = self.name_label.height()
        deco_h = self._deco_line.height()

        # 6. 外边框缓冲
        margin = 16
        total_w = label_w + margin * 2
        total_h = name_h + label_h + deco_h + margin * 2

        # 7. 调整容器和窗口
        self._container.resize(total_w, total_h)
        self.resize(total_w, total_h)
        self._container.adjustSize()
        self.adjustSize()

        # 8. 重置透明度
        self._opacity = 1.0
        self._fading = False
        self._fade_out = False
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def _animate(self):
        """透明度动画"""
        if self._fade_out:
            self._opacity -= self._fade_step
            if self._opacity <= 0.0:
                self._opacity = 0.0
                self._anim_timer.stop()
                self._fading = False
                self.hide()
                return
        else:
            self._opacity += self._fade_step
            if self._opacity >= 1.0:
                self._opacity = 1.0
                self._anim_timer.stop()
                self._fading = False
                return
        self.setWindowOpacity(self._opacity)

    def _start_fadeout(self):
        """开始淡出"""
        self._fading = True
        self._fade_out = True
        self._fade_step = 0.06
        self._anim_timer.start(25)

    def close(self):
        self._anim_timer.stop()
        self._hide_timer.stop()
        super().close()



class SizeScaleDialog(QDialog):
    """立绘大小调节对话框 — 滑块实时预览"""
    def __init__(self, current_factor: float, pet=None):
        super().__init__(pet)
        self._pet = pet
        self._factor = current_factor
        self._original = current_factor  # 取消时还原
        self.setWindowTitle("调节立绘大小")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(360, 200)
        self.setAccessibleName("调节立绘大小")
        self.setStyleSheet(DIALOG_STYLE)

        container = QFrame(self)
        container.setObjectName("SizeDialogCard")

        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(20, 16, 20, 18)
        c_layout.setSpacing(12)

        # 百分比标签
        self._pct_label = QLabel(f"{int(current_factor * 100)}%", self)
        self._pct_label.setObjectName("ScaleValue")
        self._pct_label.setAccessibleName("当前立绘缩放比例")
        self._pct_label.setAlignment(Qt.AlignCenter)

        # 滑块 (30%–300%)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setObjectName("ScaleSlider")
        self._slider.setRange(30, 300)
        self._slider.setValue(int(current_factor * 100))
        self._slider.setMinimumHeight(MIN_TARGET_SIZE)
        self._slider.setAccessibleName("立绘缩放比例")
        self._slider.setAccessibleDescription("可在百分之三十到百分之三百之间调节")
        self._slider.valueChanged.connect(self._on_slider)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        reset_btn = QPushButton("重置", self)
        reset_btn.setMinimumHeight(MIN_TARGET_SIZE)
        reset_btn.setAccessibleName("重置立绘大小")
        reset_btn.clicked.connect(self._reset)
        ok_btn = QPushButton("确定", self)
        ok_btn.setObjectName("PrimaryButton")
        ok_btn.setMinimumHeight(MIN_TARGET_SIZE)
        ok_btn.setAccessibleName("应用立绘大小")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消", self)
        cancel_btn.setMinimumHeight(MIN_TARGET_SIZE)
        cancel_btn.setAccessibleName("取消立绘大小调整")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)

        c_layout.addWidget(self._pct_label)
        c_layout.addWidget(self._slider)
        c_layout.addLayout(btn_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(container)
        self.setLayout(outer)

    def _on_slider(self, value: int):
        self._factor = value / 100.0
        self._pct_label.setText(f"{value}%")
        if self._pet and hasattr(self._pet, '_size_factor_preview'):
            self._pet._size_factor_preview(self._factor)

    def _reset(self):
        self._slider.setValue(100)

    def get_value(self) -> float:
        return self._factor

    def reject(self):
        """取消时还原到打开前的值"""
        self._factor = self._original
        if self._pet and hasattr(self._pet, '_size_factor_preview'):
            self._pet._size_factor_preview(self._original)
        super().reject()

