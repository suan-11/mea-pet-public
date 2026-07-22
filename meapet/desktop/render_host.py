"""PNG / Live2D render host: switch modes, size, hit region, standby."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable

from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QRegion

from meapet.desktop.renderer import SpriteCanvas, SpriteRenderer
from meapet.desktop.widgets import (
    SizeScaleDialog,
    calculate_bubble_stack_opacities,
)
from meapet.desktop import status_language
from meapet.utils import safe_print


BUBBLE_SCREEN_MARGIN = 24
BUBBLE_PET_GAP = 12
BUBBLE_STACK_GAP = 8
BUBBLE_HEAD_ANCHOR_RATIO = 0.16
BUBBLE_TAIL_CORNER_INSET = 36
LIVE2D_STARTUP_TIMEOUT_MS = 5000


def calculate_drag_position(
    window_origin: QPoint,
    pointer_origin: QPoint,
    current_pointer: QPoint,
) -> QPoint:
    """根据一次按下时的固定全局锚点计算窗口位置，避免增量累计漂移。"""
    return window_origin + current_pointer - pointer_origin


def calculate_bubble_position(
    pet_rect: QRect,
    bubble_size: QSize,
    screen_rect: QRect,
    *,
    margin: int = BUBBLE_SCREEN_MARGIN,
    gap: int = BUBBLE_PET_GAP,
    avoid_rects: tuple[QRect, ...] = (),
) -> QPoint:
    """在屏幕安全区内放置气泡，并避开桌宠及其他浮层。"""
    safe = screen_rect.adjusted(margin, margin, -margin, -margin)
    width = bubble_size.width()
    height = bubble_size.height()
    centered_x = pet_rect.center().x() - width // 2
    head_anchor_y = (
        pet_rect.top() + int(pet_rect.height() * BUBBLE_HEAD_ANCHOR_RATIO)
    )
    upper_y = head_anchor_y - height // 2
    left = QPoint(pet_rect.left() - gap - width, upper_y)
    right = QPoint(pet_rect.right() + gap + 1, upper_y)
    top = QPoint(centered_x, pet_rect.top() - gap - height)
    bottom = QPoint(centered_x, pet_rect.bottom() + gap + 1)
    # 桌宠在屏幕右半侧时气泡优先放左边；在左半侧时优先放右边。
    # 上下方仅作为两侧空间不足或被其他浮层占用时的回退位置。
    if pet_rect.center().x() >= safe.center().x():
        candidates = (left, right, top, bottom)
    else:
        candidates = (right, left, top, bottom)
    blocked_rects = (pet_rect.adjusted(-gap, -gap, gap, gap),) + tuple(
        rect.adjusted(-gap, -gap, gap, gap)
        for rect in avoid_rects
        if not rect.isEmpty()
    )

    def is_clear(candidate: QPoint) -> bool:
        candidate_rect = QRect(candidate, bubble_size)
        return safe.contains(candidate_rect) and not any(
            candidate_rect.intersects(blocked) for blocked in blocked_rects
        )

    for candidate in candidates:
        if is_clear(candidate):
            return candidate

    # 候选点不完整可见时先钳制，再找一个没有碰撞的位置。
    max_x = safe.right() - width + 1
    max_y = safe.bottom() - height + 1

    def clamped(candidate: QPoint) -> QPoint:
        x = (
            safe.left()
            if max_x < safe.left()
            else min(max(candidate.x(), safe.left()), max_x)
        )
        y = (
            safe.top()
            if max_y < safe.top()
            else min(max(candidate.y(), safe.top()), max_y)
        )
        return QPoint(x, y)

    clamped_candidates = tuple(clamped(candidate) for candidate in candidates)
    for candidate in clamped_candidates:
        candidate_rect = QRect(candidate, bubble_size)
        if not any(
            candidate_rect.intersects(blocked) for blocked in blocked_rects
        ):
            return candidate

    # 首选锚点被聊天框等浮层占用时，优先水平让开，保持气泡仍在角色上部。
    # 如果水平空间不足，再尝试沿垂直方向避让。
    for candidate in clamped_candidates:
        adjusted_candidates = []
        for blocked in blocked_rects:
            adjusted_candidates.extend(
                (
                    QPoint(blocked.left() - width, candidate.y()),
                    QPoint(blocked.right() + 1, candidate.y()),
                    QPoint(candidate.x(), blocked.top() - height),
                    QPoint(candidate.x(), blocked.bottom() + 1),
                )
            )
        for adjusted in adjusted_candidates:
            adjusted = clamped(adjusted)
            adjusted_rect = QRect(adjusted, bubble_size)
            if safe.contains(adjusted_rect) and not any(
                adjusted_rect.intersects(blocked)
                for blocked in blocked_rects
            ):
                return adjusted

    def overlap_area(candidate: QPoint) -> int:
        candidate_rect = QRect(candidate, bubble_size)
        return sum(
            max(0, overlap.width()) * max(0, overlap.height())
            for blocked in blocked_rects
            for overlap in (candidate_rect.intersected(blocked),)
        )

    return min(clamped_candidates, key=overlap_area)


def calculate_bubble_stack_positions(
    pet_rect: QRect,
    bubble_sizes: tuple[QSize, ...],
    screen_rect: QRect,
    *,
    margin: int = BUBBLE_SCREEN_MARGIN,
    gap: int = BUBBLE_PET_GAP,
    stack_gap: int = BUBBLE_STACK_GAP,
    avoid_rects: tuple[QRect, ...] = (),
) -> tuple[QPoint, ...]:
    """按“最旧到最新”返回气泡位置，最新靠近角色、旧消息向上堆叠。"""
    sizes = tuple(bubble_sizes)
    if not sizes:
        return ()

    safe = screen_rect.adjusted(margin, margin, -margin, -margin)
    blocked_rects = (pet_rect.adjusted(-gap, -gap, gap, gap),) + tuple(
        rect.adjusted(-gap, -gap, gap, gap)
        for rect in avoid_rects
        if not rect.isEmpty()
    )
    head_anchor_y = (
        pet_rect.top() + int(pet_rect.height() * BUBBLE_HEAD_ANCHOR_RATIO)
    )

    def vertical_positions(newest_y: int) -> list[int]:
        positions = [0] * len(sizes)
        positions[-1] = newest_y
        for index in range(len(sizes) - 2, -1, -1):
            positions[index] = (
                positions[index + 1]
                - stack_gap
                - sizes[index].height()
            )

        group_top = positions[0]
        group_bottom = positions[-1] + sizes[-1].height() - 1
        if group_top < safe.top():
            shift = safe.top() - group_top
            positions = [value + shift for value in positions]
            group_bottom += shift
        if group_bottom > safe.bottom():
            shift = safe.bottom() - group_bottom
            positions = [value + shift for value in positions]
        return positions

    newest_upper_y = head_anchor_y - sizes[-1].height() // 2
    upper_positions = vertical_positions(newest_upper_y)

    def side_positions(side: str) -> tuple[QPoint, ...]:
        if side == "left":
            return tuple(
                QPoint(pet_rect.left() - gap - size.width(), y)
                for size, y in zip(sizes, upper_positions)
            )
        return tuple(
            QPoint(pet_rect.right() + gap + 1, y)
            for y in upper_positions
        )

    def is_clear(positions: tuple[QPoint, ...]) -> bool:
        rects = tuple(
            QRect(position, size)
            for position, size in zip(positions, sizes)
        )
        return all(safe.contains(rect) for rect in rects) and not any(
            rect.intersects(blocked)
            for rect in rects
            for blocked in blocked_rects
        )

    preferred_sides = (
        ("left", "right")
        if pet_rect.center().x() >= safe.center().x()
        else ("right", "left")
    )
    for side in preferred_sides:
        positions = side_positions(side)
        if is_clear(positions):
            return positions

    # 极窄屏幕或额外浮层占满两侧时，沿用单气泡的安全回退方向。
    latest_position = calculate_bubble_position(
        pet_rect,
        sizes[-1],
        screen_rect,
        margin=margin,
        gap=gap,
        avoid_rects=avoid_rects,
    )
    latest_rect = QRect(latest_position, sizes[-1])
    fallback_y = vertical_positions(latest_position.y())
    if latest_rect.right() < pet_rect.left():
        fallback_side = "left"
    elif latest_rect.left() > pet_rect.right():
        fallback_side = "right"
    else:
        fallback_side = "center"

    positions = []
    for size, y in zip(sizes, fallback_y):
        if fallback_side == "left":
            x = (
                latest_position.x()
                + sizes[-1].width()
                - size.width()
            )
        elif fallback_side == "right":
            x = latest_position.x()
        else:
            x = pet_rect.center().x() - size.width() // 2
        max_x = safe.right() - size.width() + 1
        if max_x >= safe.left():
            x = min(max(x, safe.left()), max_x)
        positions.append(QPoint(x, y))
    return tuple(positions)


def calculate_bubble_tail(pet_rect: QRect, bubble_rect: QRect) -> tuple[str, int]:
    """返回气泡朝向桌宠的尾巴边与相对锚点。"""
    pet_center_x = pet_rect.x() + pet_rect.width() // 2
    corner_inset = min(
        BUBBLE_TAIL_CORNER_INSET,
        max(0, bubble_rect.width() // 2),
    )
    if bubble_rect.right() < pet_rect.left():
        return "bottom", bubble_rect.width() - corner_inset
    if bubble_rect.left() > pet_rect.right():
        return "bottom", corner_inset
    if bubble_rect.bottom() < pet_rect.top():
        return "bottom", pet_center_x - bubble_rect.left()
    return "top", pet_center_x - bubble_rect.left()


class PetRenderHostMixin:
    def _init_renderer(self):
        """直接初始化目标渲染器；Live2D 首帧完成前保持顶层窗口透明。"""
        display_cfg = self.config.get("display", {})
        self._scale = display_cfg.get("scale", 0.5)
        self._size_factor = display_cfg.get("size_factor", 1.0)

        self._use_live2d = False
        self._l2d_model = None
        self._l2d_pending = False
        self._live2d_startup_widget = None
        self._cancel_live2d_startup_timeout()
        self._ensure_live2d_startup_timer()
        self._renderer_ready = False
        self._renderer_ready_callbacks: list[Callable[[], None]] = []
        self.renderer = None
        self.sprite_label = None

        from meapet.config.store import resolve_resource_path

        l2d_cfg = self.config.get("live2d", {})
        model_dir = resolve_resource_path(l2d_cfg.get("model_dir", ""))
        force_png = os.environ.get("MEAPET_FORCE_PNG", "").strip().lower()
        live2d_requested = (
            force_png not in ("1", "true", "yes")
            and l2d_cfg.get("enabled", False)
            and bool(model_dir)
            and os.path.isdir(model_dir)
        )

        if live2d_requested:
            # 保持顶层窗口正常映射；背景和未完成 framebuffer 本身透明。
            # Windows 的 QOpenGLWidget 不应以 0 opacity 首次映射，否则可能
            # 永久丢失 DWM 合成表面。
            self.setWindowOpacity(1.0)
            try:
                self._start_live2d_renderer()
                return
            except Exception as exc:
                safe_print(f"[pet] Live2D 初始化失败，使用 PNG: {exc}")
                self._fallback_to_png(str(exc))
                return

        if force_png in ("1", "true", "yes"):
            safe_print("[toggle] MEAPET_FORCE_PNG=1, skip Live2D")
        elif l2d_cfg.get("enabled", False) and not (model_dir and os.path.isdir(model_dir)):
            safe_print(f"[live2d] 模型目录不存在，使用 PNG: {model_dir}")

        self._init_png_renderer()
        self.setWindowOpacity(1.0)
        self._mark_renderer_ready()

    def _init_png_renderer(self):
        """创建 PNG 渲染器；仅用于明确选择 PNG 或 Live2D 失败回退。"""
        char = self.config.get("character", {})
        sprite_dir = self.config.get(
            "sprite_dir",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sprites"),
        )
        # Prefer project sprites via config default; fall back to PROJECT_ROOT
        if not os.path.isdir(sprite_dir):
            from meapet.paths import project_path
            sprite_dir = project_path("sprites")
        outfit = char.get("default_outfit", "01")
        direction = char.get("default_direction", "A")
        self.sprite_label = SpriteCanvas(self)
        self.sprite_label.setAttribute(Qt.WA_TranslucentBackground)
        self.sprite_label.setStyleSheet("background: transparent;")
        self.sprite_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.sprite_label.show()
        self.renderer = SpriteRenderer(sprite_dir, outfit, direction)
        safe_print(f"[toggle] PNG renderer 创建成功: {self.renderer is not None}")
        self.renderer.expression_changed.connect(self._on_sprite_changed)
        self._update_sprite()
        if hasattr(self.renderer, "preload_scaled_frames"):
            self.renderer.preload_scaled_frames(
                self.sprite_label.width(),
                self.sprite_label.height(),
            )
        self.renderer.start_blink_animation()

    def _start_live2d_renderer(self):
        """创建 Live2D 控件，但把可见性推迟到它报告真实首帧以后。"""
        from meapet.desktop.live2d_widget import init_live2d

        self._clear_window_region()
        init_live2d()
        self._use_live2d = True
        self._l2d_pending = True
        self._renderer_ready = False
        self._init_live2d()
        widget = self.sprite_label
        if widget is None:
            raise RuntimeError("Live2D widget not created")
        self._live2d_startup_widget = widget
        self._ensure_live2d_startup_timer().start(
            LIVE2D_STARTUP_TIMEOUT_MS
        )

    def _ensure_live2d_startup_timer(self) -> QTimer:
        timer = getattr(self, "_live2d_startup_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_live2d_startup_timeout)
            self._live2d_startup_timer = timer
        return timer

    def _cancel_live2d_startup_timeout(self) -> None:
        timer = getattr(self, "_live2d_startup_timer", None)
        if timer is not None:
            timer.stop()

    def _deferred_init_live2d(self):
        """兼容旧调用点；新启动流程不再用 800ms 的 PNG 中间态。"""
        if self._use_live2d or not self._l2d_pending:
            return
        self._start_live2d_renderer()

    def when_renderer_ready(self, callback: Callable[[], None]):
        """在渲染器可安全显示时调用 callback；已就绪时立即调用。"""
        if self._renderer_ready:
            callback()
            return
        self._renderer_ready_callbacks.append(callback)

    def _mark_renderer_ready(self):
        if self._renderer_ready:
            return
        self._renderer_ready = True
        callbacks = tuple(self._renderer_ready_callbacks)
        self._renderer_ready_callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                safe_print(f"[pet] renderer-ready callback failed: {exc}")

    def _on_live2d_first_frame(self):
        """首帧已经绘制并提交后，仅做一次显现，不再改变尺寸或位置。"""
        if not self._l2d_pending or not self._use_live2d:
            return
        self._cancel_live2d_startup_timeout()
        self._l2d_pending = False
        self._live2d_startup_widget = None
        try:
            self._apply_hit_region()
        except Exception as exc:
            safe_print(f"[live2d] hit region skipped: {exc}")
        self._reveal_live2d_window()
        self._mark_renderer_ready()
        safe_print(
            f"[pet] Live2D 首帧就绪 size={self.width()}x{self.height()} "
            f"pos=({self.x()},{self.y()})"
        )

    def _reveal_live2d_window(self):
        """刷新已经正常映射的 OpenGL 子控件，不重置顶层窗口。"""
        widget = self.sprite_label
        self.setWindowOpacity(1.0)
        if widget is not None:
            widget.show()
            widget.raise_()
            widget.update()

    def _on_live2d_initialization_failed(self, reason: str):
        if not self._l2d_pending:
            return
        self._fallback_to_png(reason or "unknown OpenGL error")

    def _on_live2d_startup_timeout(self):
        if (
            self._l2d_pending
            and self._live2d_startup_widget is self.sprite_label
        ):
            self._fallback_to_png("等待 Live2D 首帧超时")

    def _fallback_to_png(self, reason: str):
        """清理未就绪的 OpenGL 控件，并在同一最终位置显现 PNG。"""
        self._cancel_live2d_startup_timeout()
        safe_print(f"[pet] Live2D 不可用，回退 PNG: {reason}")
        old_widget = self.sprite_label
        if old_widget is not None and not isinstance(old_widget, SpriteCanvas):
            try:
                if hasattr(old_widget, "shutdown"):
                    old_widget.shutdown()
                old_widget.hide()
                old_widget.deleteLater()
            except Exception:
                pass
        self.sprite_label = None
        self._l2d_model = None
        self._live2d_startup_widget = None
        self._l2d_pending = False
        self._use_live2d = False
        self.renderer = None
        self._init_png_renderer()
        try:
            self._place_bottom_right()
            self._apply_hit_region()
        except Exception as exc:
            safe_print(f"[pet] PNG fallback placement skipped: {exc}")
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._mark_renderer_ready()

    def _init_live2d(self):
        from meapet.config.store import resolve_resource_path
        from meapet.desktop.live2d_widget import Live2DModel
        l2d_cfg = self.config.get("live2d", {})
        model_dir = resolve_resource_path(l2d_cfg.get("model_dir", ""))
        safe_print(f"[live2d] 开始初始化，model_dir={model_dir}")
        if not model_dir or not os.path.isdir(model_dir):
            safe_print("[live2d] 模型目录不存在，回退至 PNG")
            self._use_live2d = False
            return
        self._l2d_model = Live2DModel(model_dir)
        widget = self._l2d_model.create_widget(self)
        self.sprite_label = widget
        widget.head_patted.connect(self._on_head_patted)
        widget.lower_left_patted.connect(self._on_lower_left_patted)
        widget.lower_right_patted.connect(self._on_lower_right_patted)
        widget.chat_requested.connect(self._start_chat)
        widget.first_frame_ready.connect(self._on_live2d_first_frame)
        widget.initialization_failed.connect(
            self._on_live2d_initialization_failed
        )
        w0, h0 = self._scaled_live2d_size(self._size_factor)
        widget.move(0, 0)
        widget.resize(w0, h0)
        self.resize(w0, h0)
        widget.show()
        safe_print(f"[live2d] 控件已创建，等待首帧: {w0}x{h0}")

    def _safe_renderer(self):
        if self._use_live2d and self._l2d_model:
            return self._l2d_model
        return self.renderer

    def _live2d_base_size(self) -> tuple[int, int]:
        model = getattr(self, "_l2d_model", None)
        if model is not None:
            try:
                width, height = model.get_suggested_size()
                width = int(width)
                height = int(height)
                if width > 0 and height > 0:
                    return width, height
            except (AttributeError, TypeError, ValueError):
                pass
        return 525, 735

    def _scaled_live2d_size(self, factor: float) -> tuple[int, int]:
        base_w, base_h = self._live2d_base_size()
        return (
            max(80, round(base_w * factor)),
            max(80, round(base_h * factor)),
        )

    def _safe_set_mood(self, mood: str):
        r = self._safe_renderer()
        if r:
            r.set_mood(mood)

    def _safe_set_expression(self, expr: str):
        r = self._safe_renderer()
        if r:
            r.set_expression(expr)

    def _update_sprite(self):
        if self._use_live2d:
            return
        pixmap = self.renderer.get_current_pixmap()
        if pixmap.isNull():
            return
        target_w = int(pixmap.width() * self._scale * self._size_factor)
        target_h = int(pixmap.height() * self._scale * self._size_factor)
        if hasattr(self.renderer, "get_scaled_pixmap"):
            scaled = self.renderer.get_scaled_pixmap(target_w, target_h)
        else:
            scaled = pixmap.scaled(
                target_w,
                target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        target_size = scaled.size()
        if self.sprite_label.pos() != QPoint(0, 0):
            self.sprite_label.move(0, 0)
        if self.sprite_label.size() != target_size:
            self.sprite_label.resize(target_size)
        if self.size() != target_size:
            self.resize(target_size)
        if hasattr(self.sprite_label, "set_frame"):
            self.sprite_label.set_frame(scaled)
        else:
            self.sprite_label.setPixmap(scaled)

    def _on_sprite_changed(self, code: str):
        self._update_sprite()

    def _size_factor_preview(self, factor: float):
        self._size_factor = factor
        if self._use_live2d and self.sprite_label:
            self._clear_window_region()
            new_w, new_h = self._scaled_live2d_size(factor)
            self.sprite_label.resize(new_w, new_h)
            self.resize(new_w, new_h)
            self._apply_hit_region()
            QApplication.processEvents()
        else:
            if self.renderer is None:
                return
            pixmap = self.renderer.get_current_pixmap()
            if not pixmap.isNull():
                new_w = max(80, int(pixmap.width() * self._scale * factor))
                new_h = max(80, int(pixmap.height() * self._scale * factor))
                self.resize(new_w, new_h)
            self._update_sprite()
            self._apply_hit_region()
            QApplication.processEvents()
        self._position_bubble()

    def _open_size_dialog(self):
        dialog = SizeScaleDialog(self._size_factor, self)
        screen = QApplication.primaryScreen().availableGeometry()
        dlg_w, dlg_h = 280, 130
        x = self.x() + (self.width() - dlg_w) // 2
        y = self.y() + (self.height() - dlg_h) // 2
        x = max(screen.x(), min(x, screen.x() + screen.width() - dlg_w))
        y = max(screen.y(), min(y, screen.y() + screen.height() - dlg_h))
        dialog.move(x, y)
        if dialog.exec_() == QDialog.Accepted:
            new_factor = dialog.get_value()
            self._size_factor = new_factor
            self.config.setdefault("display", {})["size_factor"] = round(new_factor, 2)
            self._save_config()

    def _position_bubble(self, *, animate: bool = False):
        stack = getattr(self, "_bubble_stack", None)
        if stack is not None:
            bubbles = tuple(
                bubble for bubble in stack.bubbles if bubble.isVisible()
            )
        else:
            bubble = getattr(self, "bubble", None)
            bubbles = (
                (bubble,)
                if bubble is not None and bubble.isVisible()
                else ()
            )
        if not bubbles:
            return

        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        screen = QApplication.screenAt(pet_rect.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        avoid_rects = []
        chat_input = getattr(self, "_chat_input", None)
        try:
            if chat_input is not None and chat_input.isVisible():
                avoid_rects.append(QRect(chat_input.frameGeometry()))
        except RuntimeError:
            pass

        positions = calculate_bubble_stack_positions(
            pet_rect,
            tuple(bubble.size() for bubble in bubbles),
            screen.availableGeometry(),
            avoid_rects=tuple(avoid_rects),
        )
        opacities = calculate_bubble_stack_opacities(len(bubbles))
        for bubble, position, opacity in zip(bubbles, positions, opacities):
            bubble_rect = QRect(position, bubble.size())
            set_tail = getattr(bubble, "set_tail", None)
            if callable(set_tail):
                side, anchor = calculate_bubble_tail(pet_rect, bubble_rect)
                set_tail(side, anchor)
            animate_to = getattr(bubble, "animate_to", None)
            if callable(animate_to):
                animate_to(position, opacity, animate=animate)
            else:
                bubble.move(position)

    def _place_bottom_right(self):
        """放到主屏右下角，并钳制在可见区域内（防止多屏/DPI 导致“消失”）。"""
        screen = QApplication.primaryScreen().availableGeometry()
        w = max(self.width(), 80)
        h = max(self.height(), 80)
        x = screen.right() - w - 50
        y = screen.bottom() - h - 10
        # 钳制：至少 80% 窗口在主屏内
        x = max(screen.left(), min(x, screen.right() - max(80, w // 5)))
        y = max(screen.top(), min(y, screen.bottom() - max(80, h // 5)))
        self.move(x, y)
        safe_print(
            f"[place] screen=({screen.x()},{screen.y()},{screen.width()}x{screen.height()}) "
            f"-> pos=({x},{y}) size={w}x{h}"
        )

    def _clear_window_region(self):
        """移除会裁剪可见内容的 Qt/Win32 窗口区域。"""
        self.clearMask()
        if sys.platform == "win32":
            try:
                import win32gui

                win32gui.SetWindowRgn(int(self.winId()), 0, True)
            except Exception as e:
                safe_print(f"[WARN] Win32 window region reset failed: {e}")

    def _apply_hit_region(self):
        # QWidget mask / SetWindowRgn 会同时裁掉绘制与鼠标区域。Live2D
        # 动作会越过上一帧包围盒，因此始终保留完整透明绘制表面。
        self._clear_window_region()

    def _toggle_standby(self):
        self._standby = not self._standby
        if self._standby:
            self._watcher_timer.stop()
            self._safe_set_expression("011")
            self._show_bubble(status_language.standby_on(), 0)
            self._position_bubble()
            self._apply_hit_region()
        else:
            self._safe_set_expression("001")
            clear_bubbles = getattr(self, "_clear_bubbles", None)
            if callable(clear_bubbles):
                clear_bubbles()
            elif hasattr(self, "bubble") and self.bubble:
                self.bubble.hide()
            self._show_bubble(status_language.standby_off(), 2500)
            self._position_bubble()
            self._apply_hit_region()
            self._start_watcher_timer()
        refresh_tray = getattr(self, "_refresh_tray_state", None)
        if callable(refresh_tray):
            refresh_tray()

    def _toggle_render_mode(self):
        self._clear_window_region()
        if self._use_live2d:
            self._cancel_live2d_startup_timeout()
            if self.sprite_label:
                self.sprite_label.shutdown()
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self._l2d_model = None
            self._use_live2d = False
            self._l2d_pending = False
            self._live2d_startup_widget = None
            self._init_png_renderer()
            self._apply_hit_region()
            self._show_bubble("已切回 PNG 立绘喵", 2500)
            self.config.setdefault("live2d", {})["enabled"] = False
            self._save_config()
        else:
            if self.renderer:
                self.renderer.stop_blink_animation()
                self.renderer = None
            if self.sprite_label:
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self.renderer = None
            self.setWindowOpacity(1.0)
            self._renderer_ready = False
            # 先写 config，异常退出后下次仍会尝试用户明确选择的 Live2D。
            self.config.setdefault("live2d", {})["enabled"] = True
            self._save_config()
            try:
                self._start_live2d_renderer()
                # 在透明阶段确定最终位置；首帧回调只负责显现。
                self._place_bottom_right()
            except Exception as exc:
                self._fallback_to_png(str(exc))

            def announce_mode_change():
                if self._use_live2d:
                    self._show_bubble("已切换到 Live2D 喵", 2500)
                else:
                    self._show_bubble("Live2D 加载失败，已切回 PNG 喵", 3000)

            self.when_renderer_ready(announce_mode_change)

    def closeEvent(self, event):
        """取消未完成的启动回调，避免关闭后被超时回退重新显示。"""
        self._cancel_live2d_startup_timeout()
        super().closeEvent(event)
