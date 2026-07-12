"""梅尔桌宠 - UI 组件与后台任务"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QDialog,
    QScrollArea, QGraphicsOpacityEffect, QSlider, QPushButton, QStyle,
)
from PyQt5.QtGui import (
    QColor,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PyQt5.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)

from meapet.desktop.theme import DIALOG_STYLE, DIALOGUE_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    PALETTE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)
from meapet.utils import safe_print, log_error
from meapet.chat.engine import ChatEngine
from meapet.tts.service import MeaTTS

DIALOGUE_MIN_WIDTH = 164
DIALOGUE_MAX_WIDTH = 420
DIALOGUE_MAX_HEIGHT = 240
DIALOGUE_TAIL_SIZE = 16
DIALOGUE_TAIL_BASE = 28
DIALOGUE_TAIL_DEPTH = 26
DIALOGUE_TAIL_REACH = 22
DIALOGUE_RADIUS = 20
DIALOGUE_HORIZONTAL_PADDING = 18
DIALOGUE_VERTICAL_PADDING = 14
DIALOGUE_STACK_LIMIT = 3
DIALOGUE_STACK_STALE_MS = 3500
DIALOGUE_STACK_OPACITIES = (0.52, 0.76, 1.0)
DIALOGUE_MOTION_DURATION_MS = 560
DIALOGUE_ENTRY_OFFSET = 26
DIALOGUE_FADE_DURATION_MS = 1600
DIALOGUE_FADE_FRAME_MS = 25

# 情绪只影响描边色，必须配合角色表情/文案，不作为唯一语义。
MOOD_BORDER_COLORS = {
    "happy": "#FFB36B",
    "annoyed": "#FF8892",
    "sad": "#8FA4D6",
    "shy": "#FF91B4",
    "curious": "#A69BFF",
    "surprised": "#F4CC75",
    "melancholy": "#9FA3BC",
    "talking": "#FF91B4",
    "neutral": "#FF91B4",
}


def _reduced_motion_enabled() -> bool:
    return os.environ.get(
        "MEAPET_REDUCED_MOTION",
        "",
    ).strip().lower() in {"1", "true", "yes"}


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


def calculate_bubble_stack_opacities(count: int) -> tuple[float, ...]:
    """按“最旧到最新”返回层级透明度，最新消息保持完全清晰。"""
    count = max(0, int(count))
    if count == 0:
        return ()
    if count <= len(DIALOGUE_STACK_OPACITIES):
        return DIALOGUE_STACK_OPACITIES[-count:]
    oldest = DIALOGUE_STACK_OPACITIES[0]
    return (
        (oldest,) * (count - len(DIALOGUE_STACK_OPACITIES))
        + DIALOGUE_STACK_OPACITIES
    )



class SpeechBubbleFrame(QFrame):
    """带方向性尾巴的轻量自绘气泡框。"""

    VALID_TAIL_SIDES = frozenset({"top", "right", "bottom", "left"})

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DialogueBubble")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAccessibleName("回复气泡")
        self.tail_side = "bottom"
        self.tail_anchor: int | None = None
        self.mood = "neutral"

    def set_mood(self, mood: str | None) -> None:
        key = str(mood or "neutral").strip().lower() or "neutral"
        self.mood = key if key in MOOD_BORDER_COLORS else "neutral"
        self.update()

    def set_tail(self, side: str, anchor: int | None = None) -> None:
        if side not in self.VALID_TAIL_SIDES:
            raise ValueError(f"不支持的气泡尾巴方向: {side!r}")
        self.tail_side = side
        self.tail_anchor = None if anchor is None else int(anchor)
        self._apply_content_margins()
        self.update()

    def _apply_content_margins(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        half_tail = DIALOGUE_TAIL_SIZE // 2
        left = right = DIALOGUE_HORIZONTAL_PADDING
        top = bottom = DIALOGUE_VERTICAL_PADDING
        if self.tail_side == "left":
            left += DIALOGUE_TAIL_SIZE
            top += half_tail
            bottom += DIALOGUE_TAIL_SIZE - half_tail
        elif self.tail_side == "right":
            right += DIALOGUE_TAIL_SIZE
            top += half_tail
            bottom += DIALOGUE_TAIL_SIZE - half_tail
        elif self.tail_side == "top":
            top += DIALOGUE_TAIL_SIZE
            left += half_tail
            right += DIALOGUE_TAIL_SIZE - half_tail
        else:
            bottom += DIALOGUE_TAIL_DEPTH
            if self._bottom_tail_points_right():
                right += DIALOGUE_TAIL_REACH
            else:
                left += DIALOGUE_TAIL_REACH
        layout.setContentsMargins(left, top, right, bottom)

    def _bottom_tail_points_right(self) -> bool:
        """底部尾巴朝角色所在的外侧角延伸。"""
        if self.tail_anchor is None:
            return True
        return self.tail_anchor >= self.width() / 2

    def _body_rect(self) -> QRectF:
        edge = 1.5
        half_tail = DIALOGUE_TAIL_SIZE / 2
        width = max(0.0, self.width() - 2 * edge)
        height = max(0.0, self.height() - 2 * edge)
        if self.tail_side == "left":
            return QRectF(
                edge + DIALOGUE_TAIL_SIZE,
                edge + half_tail,
                max(0.0, width - DIALOGUE_TAIL_SIZE),
                max(0.0, height - DIALOGUE_TAIL_SIZE),
            )
        if self.tail_side == "right":
            return QRectF(
                edge,
                edge + half_tail,
                max(0.0, width - DIALOGUE_TAIL_SIZE),
                max(0.0, height - DIALOGUE_TAIL_SIZE),
            )
        if self.tail_side == "top":
            return QRectF(
                edge + half_tail,
                edge + DIALOGUE_TAIL_SIZE,
                max(0.0, width - DIALOGUE_TAIL_SIZE),
                max(0.0, height - DIALOGUE_TAIL_SIZE),
            )
        body_x = (
            edge
            if self._bottom_tail_points_right()
            else edge + DIALOGUE_TAIL_REACH
        )
        return QRectF(
            body_x,
            edge,
            max(0.0, width - DIALOGUE_TAIL_REACH),
            max(0.0, height - DIALOGUE_TAIL_DEPTH),
        )

    def _clamped_anchor(self, body: QRectF) -> float:
        vertical_side = self.tail_side in {"top", "bottom"}
        start = body.left() if vertical_side else body.top()
        end = body.right() if vertical_side else body.bottom()
        default = start + (end - start) * (0.68 if vertical_side else 0.55)
        requested = default if self.tail_anchor is None else float(self.tail_anchor)
        if self.tail_side == "bottom":
            inset = DIALOGUE_TAIL_BASE / 2 + DIALOGUE_RADIUS * 0.45
        else:
            inset = DIALOGUE_RADIUS + DIALOGUE_TAIL_BASE / 2
        minimum = start + inset
        maximum = end - inset
        if maximum < minimum:
            return (start + end) / 2
        return min(max(requested, minimum), maximum)

    def _tail_path(self, body: QRectF) -> QPainterPath:
        """构造与气泡轮廓连续的尾巴；底部使用斜向双贝塞尔曲线。"""
        anchor = self._clamped_anchor(body)
        half_base = DIALOGUE_TAIL_BASE / 2
        tail = QPainterPath()
        if self.tail_side == "bottom":
            baseline = body.bottom() - 1
            tip_y = self.height() - 3.5
            if self._bottom_tail_points_right():
                base_start = anchor - half_base
                tip_x = self.width() - 3.5
                corner_x = body.right() - 1
                corner_y = body.bottom() - DIALOGUE_RADIUS * 0.55
                tail.moveTo(base_start, baseline)
                tail.cubicTo(
                    anchor - half_base * 0.15,
                    baseline + 1,
                    tip_x - DIALOGUE_TAIL_REACH * 0.72,
                    tip_y - DIALOGUE_TAIL_DEPTH * 0.45,
                    tip_x,
                    tip_y,
                )
                tail.cubicTo(
                    tip_x - DIALOGUE_TAIL_REACH * 0.08,
                    tip_y - DIALOGUE_TAIL_DEPTH * 0.02,
                    body.right() + DIALOGUE_TAIL_REACH * 0.18,
                    baseline + DIALOGUE_TAIL_DEPTH * 0.32,
                    corner_x,
                    corner_y,
                )
            else:
                base_start = anchor + half_base
                tip_x = 3.5
                corner_x = body.left() + 1
                corner_y = body.bottom() - DIALOGUE_RADIUS * 0.55
                tail.moveTo(base_start, baseline)
                tail.cubicTo(
                    anchor + half_base * 0.15,
                    baseline + 1,
                    tip_x + DIALOGUE_TAIL_REACH * 0.72,
                    tip_y - DIALOGUE_TAIL_DEPTH * 0.45,
                    tip_x,
                    tip_y,
                )
                tail.cubicTo(
                    tip_x + DIALOGUE_TAIL_REACH * 0.08,
                    tip_y - DIALOGUE_TAIL_DEPTH * 0.02,
                    body.left() - DIALOGUE_TAIL_REACH * 0.18,
                    baseline + DIALOGUE_TAIL_DEPTH * 0.32,
                    corner_x,
                    corner_y,
                )
            tail.closeSubpath()
            return tail

        if self.tail_side == "left":
            points = (
                QPointF(body.left() + 1, anchor - half_base),
                QPointF(1.5, anchor),
                QPointF(body.left() + 1, anchor + half_base),
            )
        elif self.tail_side == "right":
            points = (
                QPointF(body.right() - 1, anchor - half_base),
                QPointF(self.width() - 1.5, anchor),
                QPointF(body.right() - 1, anchor + half_base),
            )
        elif self.tail_side == "top":
            points = (
                QPointF(anchor - half_base, body.top() + 1),
                QPointF(anchor, 1.5),
                QPointF(anchor + half_base, body.top() + 1),
            )
        tail.addPolygon(QPolygonF(points))
        tail.closeSubpath()
        return tail

    def _bubble_path(self) -> QPainterPath:
        body = self._body_rect()
        rounded = QPainterPath()
        rounded.addRoundedRect(body, DIALOGUE_RADIUS, DIALOGUE_RADIUS)
        tail = self._tail_path(body)
        return rounded.united(tail)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = self._bubble_path()

        shadow = QColor(0, 0, 0, 82)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawPath(path.translated(0, 2))

        body = self._body_rect()
        gradient = QLinearGradient(body.topLeft(), body.bottomRight())
        gradient.setColorAt(0.0, QColor(PALETTE["surface_elevated"]).lighter(108))
        gradient.setColorAt(1.0, QColor(PALETTE["surface"]))
        accent = MOOD_BORDER_COLORS.get(getattr(self, "mood", "neutral"), PALETTE["primary"])
        border = QColor(accent)
        border.setAlpha(210)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(gradient)
        painter.drawPath(path)


class DialogueBox(QWidget):
    """仅承载桌宠回复的自适应语音气泡。"""

    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.setAttribute(Qt.WA_TranslucentBackground)
        window_flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        app = QApplication.instance()
        if (
            sys.platform.startswith("linux")
            and app is not None
            and app.platformName() == "xcb"
        ):
            # 气泡需要像 tooltip 一样精确跟随桌宠，不能被 X11 窗管重排。
            window_flags |= Qt.X11BypassWindowManagerHint
        self.setWindowFlags(window_flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAccessibleName("桌宠回复气泡")

        self._opacity = 1.0
        self._fade_step = 0.0
        self._fading = False
        self._fade_out = False
        self._dismissed_emitted = False
        self._stack_entry_pending = False

        self._container = SpeechBubbleFrame(self)
        set_scaled_stylesheet(self._container, DIALOGUE_STYLE)
        self._opacity_effect = QGraphicsOpacityEffect(self._container)
        self._opacity_effect.setOpacity(self._opacity)
        self._container.setGraphicsEffect(self._opacity_effect)

        container_layout = QVBoxLayout(self._container)
        container_layout.setSpacing(0)
        self._container.set_tail("bottom")

        # 正文放在无边框滚动区中：短文本自然收缩，超长文本到达上限后可滚动。
        self.text_scroll = QScrollArea()
        self.text_scroll.setObjectName("DialogueScroll")
        self.text_scroll.setFrameShape(QFrame.NoFrame)
        self.text_scroll.setWidgetResizable(False)
        self.text_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text_scroll.setAccessibleName("对话正文滚动区域")

        self.text_label = QLabel()
        self.text_label.setObjectName("DialogueText")
        self.text_label.setAccessibleName("桌宠回复")
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text_label.setContentsMargins(0, 0, 0, 0)
        self.text_scroll.setWidget(self.text_label)
        container_layout.addWidget(self.text_scroll)

        self._container.adjustSize()

        # 淡入淡出计时器
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fadeout)

        self._position_animation = QPropertyAnimation(self, b"pos", self)
        self._position_animation.setDuration(DIALOGUE_MOTION_DURATION_MS)
        self._position_animation.setEasingCurve(QEasingCurve.OutQuad)
        self._opacity_animation = QPropertyAnimation(
            self,
            b"visualOpacity",
            self,
        )
        self._opacity_animation.setDuration(DIALOGUE_MOTION_DURATION_MS)
        self._opacity_animation.setEasingCurve(QEasingCurve.OutQuad)

        self.hide()

    def _get_visual_opacity(self) -> float:
        return self._opacity

    def _set_visual_opacity(self, opacity: float) -> None:
        self._opacity = min(max(float(opacity), 0.0), 1.0)
        self._opacity_effect.setOpacity(self._opacity)

    visualOpacity = pyqtProperty(
        float,
        fget=_get_visual_opacity,
        fset=_set_visual_opacity,
    )

    @property
    def tail_side(self) -> str:
        return self._container.tail_side

    @property
    def tail_anchor(self) -> int | None:
        return self._container.tail_anchor

    def set_tail(self, side: str, anchor: int | None = None) -> None:
        self._container.set_tail(side, anchor)

    def set_mood(self, mood: str | None = None) -> None:
        self._container.set_mood(mood)
        mood_key = getattr(self._container, "mood", "neutral")
        self.setAccessibleDescription(f"情绪 {mood_key}")

    def show_text(
        self,
        text: str,
        duration_ms: int = 6000,
        *,
        initial_opacity: float = 1.0,
        mood: str | None = None,
    ):
        import re
        clean_text = re.sub(r'【.*?】', '', text).strip()
        if mood is not None:
            self.set_mood(mood)
        self._hide_timer.stop()

        # 1. 设置文本
        self.text_label.setText(clean_text)
        self.text_label.setWordWrap(True)
        self.text_label.ensurePolished()
        fm = QFontMetrics(self.text_label.font())
        tail_width = (
            DIALOGUE_TAIL_REACH
            if self.tail_side == "bottom"
            else DIALOGUE_TAIL_SIZE
        )
        tail_height = (
            DIALOGUE_TAIL_DEPTH
            if self.tail_side == "bottom"
            else DIALOGUE_TAIL_SIZE
        )
        max_text_width = (
            DIALOGUE_MAX_WIDTH
            - 2 * DIALOGUE_HORIZONTAL_PADDING
            - tail_width
        )
        min_text_width = max(
            1,
            DIALOGUE_MIN_WIDTH
            - 2 * DIALOGUE_HORIZONTAL_PADDING
            - tail_width,
        )
        max_text_height = (
            DIALOGUE_MAX_HEIGHT
            - 2 * DIALOGUE_VERTICAL_PADDING
            - tail_height
        )

        lines = clean_text.splitlines() or [""]
        longest_line = max(fm.horizontalAdvance(line or " ") for line in lines)
        scroll_width = max(
            min_text_width,
            min(longest_line + 2, max_text_width),
        )
        wrap_flags = (
            Qt.TextWordWrap
            | Qt.TextWrapAnywhere
            | Qt.AlignLeft
            | Qt.AlignTop
        )

        def measured_height(label_width: int) -> int:
            bounds = fm.boundingRect(
                QRect(0, 0, max(1, label_width), 100000),
                wrap_flags,
                clean_text or " ",
            )
            return max(fm.lineSpacing() + 4, bounds.height() + 4)

        label_width = scroll_width
        natural_text_height = measured_height(label_width)
        if natural_text_height > max_text_height:
            scrollbar_width = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
            label_width = max(1, scroll_width - scrollbar_width - 2)
            natural_text_height = measured_height(label_width)

        visible_text_height = min(natural_text_height, max_text_height)
        self.text_label.setFixedSize(label_width, natural_text_height)
        self.text_scroll.setFixedSize(scroll_width, visible_text_height)
        self.text_scroll.verticalScrollBar().setValue(0)

        total_w = (
            scroll_width
            + 2 * DIALOGUE_HORIZONTAL_PADDING
            + tail_width
        )
        total_h = (
            visible_text_height
            + 2 * DIALOGUE_VERTICAL_PADDING
            + tail_height
        )

        self.setFixedSize(total_w, total_h)
        self._container.setFixedSize(total_w, total_h)

        # 新入栈气泡从透明状态进入；直接调用时仍立即清晰显示。
        self._fading = False
        self._fade_out = False
        self._dismissed_emitted = False
        self._set_visual_opacity(initial_opacity)
        self.show()
        self.raise_()
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def mark_stack_entry(self) -> None:
        """标记为新入栈消息，让首次定位从下方淡入。"""
        self._stack_entry_pending = True
        self._set_visual_opacity(0.0)

    def animate_to(
        self,
        position: QPoint,
        opacity: float,
        *,
        animate: bool,
    ) -> None:
        """保持空间连续性地移动到层级位置，并同步层级透明度。"""
        target_position = QPoint(position)
        target_opacity = min(max(float(opacity), 0.0), 1.0)
        self._position_animation.stop()
        if not animate or _reduced_motion_enabled():
            self.move(target_position)
            if not self._fading:
                self._opacity_animation.stop()
                self._set_visual_opacity(target_opacity)
            self._stack_entry_pending = False
            return

        start_position = self.pos()
        if self._stack_entry_pending:
            start_position = target_position + QPoint(0, DIALOGUE_ENTRY_OFFSET)
            self.move(start_position)
            if not self._fading:
                self._opacity_animation.stop()
                self._set_visual_opacity(0.0)

        self._position_animation.setStartValue(start_position)
        self._position_animation.setEndValue(target_position)
        self._position_animation.start()

        if not self._fading:
            start_opacity = self.visualOpacity
            self._opacity_animation.stop()
            self._opacity_animation.setStartValue(start_opacity)
            self._opacity_animation.setEndValue(target_opacity)
            self._opacity_animation.start()
        self._stack_entry_pending = False

    def fade_after(self, delay_ms: int) -> None:
        """确保气泡在指定时间内开始淡出，不延长已有的更短倒计时。"""
        delay_ms = max(0, int(delay_ms))
        if self._fading:
            return
        if delay_ms == 0:
            self._start_fadeout()
            return
        remaining = self._hide_timer.remainingTime()
        if remaining < 0 or remaining > delay_ms:
            self._hide_timer.start(delay_ms)

    def _animate(self):
        """透明度动画"""
        if self._fade_out:
            self._opacity -= self._fade_step
            if self._opacity <= 0.0:
                self._opacity = 0.0
                self._anim_timer.stop()
                self._fading = False
                self._set_visual_opacity(0.0)
                self.hide()
                if not self._dismissed_emitted:
                    self._dismissed_emitted = True
                    self.dismissed.emit()
                return
        else:
            self._opacity += self._fade_step
            if self._opacity >= 1.0:
                self._opacity = 1.0
                self._anim_timer.stop()
                self._fading = False
                self._set_visual_opacity(1.0)
                return
        self._set_visual_opacity(self._opacity)

    def _start_fadeout(self):
        """开始淡出"""
        if self._fading:
            return
        self._hide_timer.stop()
        self._opacity_animation.stop()
        if _reduced_motion_enabled():
            self._anim_timer.stop()
            self._position_animation.stop()
            self._fading = False
            self._fade_out = False
            self._set_visual_opacity(0.0)
            self.hide()
            if not self._dismissed_emitted:
                self._dismissed_emitted = True
                self.dismissed.emit()
            return
        self._fading = True
        self._fade_out = True
        fade_steps = max(
            1,
            DIALOGUE_FADE_DURATION_MS // DIALOGUE_FADE_FRAME_MS,
        )
        self._fade_step = max(self.visualOpacity / fade_steps, 0.001)
        self._anim_timer.start(DIALOGUE_FADE_FRAME_MS)

    def close(self):
        self._anim_timer.stop()
        self._hide_timer.stop()
        self._position_animation.stop()
        self._opacity_animation.stop()
        super().close()


class DialogueBubbleStack(QObject):
    """管理相互独立的桌宠气泡，并限制可见历史数量。"""

    changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        max_bubbles: int = DIALOGUE_STACK_LIMIT,
        stale_duration_ms: int = DIALOGUE_STACK_STALE_MS,
    ) -> None:
        super().__init__(parent)
        self.max_bubbles = max(1, int(max_bubbles))
        self.stale_duration_ms = max(0, int(stale_duration_ms))
        self._bubbles: list[DialogueBox] = []

    @property
    def bubbles(self) -> tuple[DialogueBox, ...]:
        return tuple(self._bubbles)

    @property
    def latest(self) -> DialogueBox | None:
        return self._bubbles[-1] if self._bubbles else None

    def show_message(
        self,
        text: str,
        duration_ms: int = 6000,
        *,
        mood: str | None = None,
    ) -> DialogueBox:
        for bubble in self._bubbles:
            bubble.fade_after(self.stale_duration_ms)

        bubble = DialogueBox(None)
        bubble.dismissed.connect(
            lambda current=bubble: self._discard(current)
        )
        self._bubbles.append(bubble)

        while len(self._bubbles) > self.max_bubbles:
            oldest = self._bubbles.pop(0)
            try:
                oldest.dismissed.disconnect()
            except (TypeError, RuntimeError):
                pass
            oldest.close()

        bubble.show_text(text, duration_ms, initial_opacity=0.0, mood=mood)
        bubble.mark_stack_entry()
        self.changed.emit()
        return bubble

    def _discard(self, bubble: DialogueBox) -> None:
        if bubble not in self._bubbles:
            return
        self._bubbles.remove(bubble)
        try:
            bubble.dismissed.disconnect()
        except (TypeError, RuntimeError):
            pass
        bubble.close()
        self.changed.emit()

    def close_all(self) -> None:
        bubbles = tuple(self._bubbles)
        self._bubbles.clear()
        for bubble in bubbles:
            try:
                bubble.dismissed.disconnect()
            except (TypeError, RuntimeError):
                pass
            bubble.close()
        if bubbles:
            self.changed.emit()

    hide_all = close_all



class SizeScaleDialog(QDialog):
    """立绘大小调节对话框 — 滑块实时预览"""
    def __init__(self, current_factor: float, pet=None):
        super().__init__(pet)
        ensure_application_fonts()
        self._pet = pet
        self._factor = current_factor
        self._original = current_factor  # 取消时还原
        self.setWindowTitle("调节立绘大小")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(360, 200)
        self.setAccessibleName("调节立绘大小")
        set_scaled_stylesheet(self, DIALOG_STYLE)

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
