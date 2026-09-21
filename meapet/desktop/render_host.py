"""PNG / Live2D render host: switch modes, size, hit region, standby.

Live2D 保留完整模型画布用于渲染动作，但顶层桌宠窗口只暴露配置的视觉视口：
- OpenGL 子控件尺寸 = 模型画布尺寸 × size_factor
- 顶层窗口尺寸 = ``live2d.window_mask`` 外接矩形对应的视觉区域
- 子控件通过负偏移放在顶层窗口后方，由普通父子窗口矩形裁剪透明留白
- ``live2d.window_shape`` 可选地把多个保留/挖空多边形转换为静态 QRegion
- 不恢复历史椭圆窗形，也不使用 OpenGL stencil；形状关闭时保留普通矩形窗口
"""
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QPolygon, QRegion

from meapet.config.store import (
    normalize_live2d_placement_anchor,
    normalize_live2d_window_mask,
    normalize_live2d_window_shape,
)
from meapet.config.defaults import bubble_duration_ms
from meapet.desktop.renderer import SpriteCanvas, SpriteRenderer
from meapet.desktop.widgets import (
    SizeScaleDialog,
    calculate_bubble_stack_opacities,
)
from meapet.desktop import status_language
from meapet.desktop.screen_geometry import (
    available_geometry_for,
    calculate_centered_position,
    clamp_position,
    is_sufficiently_visible,
    move_within_screen,
    screen_bounds_for_rect,
    widget_size,
)
from meapet.desktop.click_through import (
    ClickThroughState,
    RightClickEdgeDetector,
    disable_click_through,
    enable_click_through,
    is_right_button_down,
)
from meapet.ui_theme import normalize_pet_size_factor
from meapet.utils import safe_print
from meapet.window_state import load_pet_position, save_pet_position


BUBBLE_SCREEN_MARGIN = 24
BUBBLE_PET_GAP = 12
BUBBLE_STACK_GAP = 8
BUBBLE_HEAD_ANCHOR_RATIO = 0.16
BUBBLE_TAIL_CORNER_INSET = 36
LIVE2D_STARTUP_TIMEOUT_MS = 5000
LIVE2D_PREVIEW_MAX_SOURCE_PIXELS = 8_000_000
LIVE2D_PREVIEW_MAX_EDGE = 1600
# 一次分辨率变更会连发多个屏幕信号，合并后再校正位置。
SCREEN_GUARD_DEBOUNCE_MS = 400
# 桌宠宽高各至少露出这么多比例才算「还看得见」，否则拉回可用区域。
PET_MIN_VISIBLE_RATIO = 0.5

# Live2D 模型画布尺寸的合法范围（像素）。
# 低于此值视为 SDK 返回了无效占位值（如 1x2），需要回退。
MIN_CANVAS_SIZE = 256
# 高于此值视为异常（防止 4K 模型撑爆屏幕），会按比例缩放到合理范围。
MAX_CANVAS_SIZE = 4096
# 当模型画布异常时使用的默认尺寸。
DEFAULT_CANVAS_SIZE = (1024, 1024)


@dataclass(frozen=True)
class Live2DViewportLayout:
    """完整 Live2D 画布子控件与顶层视觉窗口的像素几何。"""

    widget_x: int
    widget_y: int
    widget_width: int
    widget_height: int
    window_width: int
    window_height: int


def calculate_live2d_window_region(
    layout: Live2DViewportLayout,
    window_shape: object,
) -> QRegion | None:
    """把完整画布归一化多轮廓转换为裁剪窗口本地 ``QRegion``。"""
    shape = normalize_live2d_window_shape(window_shape)
    if not shape["enabled"]:
        return None

    additions = QRegion()
    subtractions = []
    has_addition = False
    for contour in shape["contours"]:
        polygon = QPolygon(
            [
                QPoint(
                    layout.widget_x
                    + round(layout.widget_width * float(point[0])),
                    layout.widget_y
                    + round(layout.widget_height * float(point[1])),
                )
                for point in contour["points"]
            ]
        )
        contour_region = QRegion(polygon, Qt.OddEvenFill)
        if contour_region.isEmpty():
            continue
        if contour["operation"] == "add":
            additions = additions.united(contour_region)
            has_addition = True
        else:
            subtractions.append(contour_region)

    if not has_addition or additions.isEmpty():
        return None
    for subtraction in subtractions:
        additions = additions.subtracted(subtraction)

    window_bounds = QRegion(
        QRect(0, 0, layout.window_width, layout.window_height)
    )
    region = additions.intersected(window_bounds)
    return None if region.isEmpty() else region


def calculate_live2d_viewport_layout(
    canvas_width: int,
    canvas_height: int,
    factor: float,
    window_mask: dict | None = None,
) -> Live2DViewportLayout:
    """把模型画布与视觉视口换算成稳定的父子窗口几何。

    ``window_mask`` 是已有配置键。这里不恢复椭圆窗口 mask，只使用椭圆
    的外接矩形作为模型专属视觉视口；这样能裁去已知透明留白，同时保留
    外接矩形四角供头发、耳朵等动作伸展。关闭该配置时完整显示模型画布。
    """
    width = max(1, int(canvas_width))
    height = max(1, int(canvas_height))
    scale = normalize_pet_size_factor(factor)
    widget_width = max(MIN_CANVAS_SIZE // 2, round(width * scale))
    widget_height = max(MIN_CANVAS_SIZE // 2, round(height * scale))

    mask = normalize_live2d_window_mask(window_mask)
    if not mask["enabled"]:
        return Live2DViewportLayout(
            widget_x=0,
            widget_y=0,
            widget_width=widget_width,
            widget_height=widget_height,
            window_width=widget_width,
            window_height=widget_height,
        )

    left_ratio = max(0.0, float(mask["cx"]) - float(mask["rw"]))
    top_ratio = max(0.0, float(mask["cy"]) - float(mask["rh"]))
    right_ratio = min(1.0, float(mask["cx"]) + float(mask["rw"]))
    bottom_ratio = min(1.0, float(mask["cy"]) + float(mask["rh"]))

    crop_left = max(0, min(widget_width - 1, round(widget_width * left_ratio)))
    crop_top = max(0, min(widget_height - 1, round(widget_height * top_ratio)))
    crop_right = max(
        crop_left + 1,
        min(widget_width, round(widget_width * right_ratio)),
    )
    crop_bottom = max(
        crop_top + 1,
        min(widget_height, round(widget_height * bottom_ratio)),
    )
    return Live2DViewportLayout(
        widget_x=-crop_left,
        widget_y=-crop_top,
        widget_width=widget_width,
        widget_height=widget_height,
        window_width=crop_right - crop_left,
        window_height=crop_bottom - crop_top,
    )


def calculate_drag_position(
    window_origin: QPoint,
    pointer_origin: QPoint,
    current_pointer: QPoint,
) -> QPoint:
    """根据一次按下时的固定全局锚点计算窗口位置，避免增量累计漂移。"""
    return window_origin + current_pointer - pointer_origin


def calculate_live2d_anchor_preserving_position(
    window_origin: QPoint,
    before_canvas: QRect,
    after_canvas: QRect,
    placement_anchor: object,
) -> QPoint:
    """计算几何变化后仍让同一画布锚点留在原屏幕位置的窗口坐标。"""
    anchor = normalize_live2d_placement_anchor(placement_anchor)

    def local_point(canvas: QRect) -> QPoint:
        return QPoint(
            canvas.x() + round(canvas.width() * anchor["x"]),
            canvas.y() + round(canvas.height() * anchor["y"]),
        )

    return QPoint(window_origin) + local_point(before_canvas) - local_point(
        after_canvas
    )


def calculate_bubble_anchor_rect(
    pet_window_rect: QRect,
    visible_local_rect: QRect | None = None,
) -> QRect:
    """把桌宠窗口内的可见区域转换成用于气泡定位的全局矩形。

    Live2D 顶层窗口已经是裁去透明留白后的视觉视口；PNG 顶层窗口则与
    精灵帧一致，因此两种模式都可以直接使用窗口矩形作为默认锚点。
    """
    window = QRect(pet_window_rect)
    if visible_local_rect is None or visible_local_rect.isEmpty():
        return window
    local_window = QRect(QPoint(0, 0), window.size())
    visible = QRect(visible_local_rect).intersected(local_window)
    if visible.isEmpty():
        return window
    visible.translate(window.topLeft())
    return visible


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
    """按"最旧到最新"返回气泡位置，最新靠近角色、旧消息向上堆叠。"""
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
        force_png = os.environ.get("MEA_PET_FORCE_PNG", "").strip().lower()
        live2d_requested = (
            force_png not in ("1", "true", "yes")
            and l2d_cfg.get("enabled", False)
            and bool(model_dir)
            and os.path.isdir(model_dir)
        )

        if live2d_requested:
            self.setWindowOpacity(1.0)
            try:
                self._start_live2d_renderer()
                return
            except Exception as exc:
                safe_print(f"[pet] Live2D 初始化失败，使用 PNG: {exc}")
                self._fallback_to_png(str(exc))
                return

        if force_png in ("1", "true", "yes"):
            safe_print("[toggle] MEA_PET_FORCE_PNG=1, skip Live2D")
        elif l2d_cfg.get("enabled", False) and not (model_dir and os.path.isdir(model_dir)):
            safe_print(f"[live2d] 模型目录不存在，使用 PNG: {model_dir}")

        self._init_png_renderer()
        self.setWindowOpacity(1.0)
        self._mark_renderer_ready()

    def _init_png_renderer(self):
        """创建 PNG 渲染器；仅用于明确选择 PNG 或 Live2D 失败回退。"""
        self.clearMask()
        char = self.config.get("character", {})
        sprite_dir = self.config.get(
            "sprite_dir",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sprites"),
        )
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

        init_live2d()
        self._use_live2d = True
        self._l2d_pending = True
        self._renderer_ready = False
        self._init_live2d()
        widget = self.sprite_label
        if widget is None:
            raise RuntimeError("Live2D widget not created")
        self._live2d_startup_widget = widget
        # 超时预算从「事件循环真正开始转」起算，而不是从这里起算（agents-rules §2 终局推演）。
        #   * 为什么要推迟起表 = QTimer 只能在循环里派发。构造 MeaPet 的同步初始化
        #     （chat/tts/watcher/DB…）实测可耗时 >LIVE2D_STARTUP_TIMEOUT_MS，于是定时器
        #     在循环启动的那一刻**就已过期**，第一次迭代即触发 → 控件还没来得及画首帧
        #     就被判"加载失败"。后果是**响亮但错误**的降级：本机 L3 实测冷启动一次
        #     复现（初始化 9 s > 5 s ⇒ 回退 PNG），热启动两次都正常 ⇒ 间歇性。
        #   * 什么条件下它会崩 = 首帧永不到来且 `initialization_failed` 也不发
        #     （OpenGL 挂死在 paint 内部、把循环本身堵死）⇒ 定时器同样无法触发，
        #     与改动前**完全一致**（旧写法在这种情况下也同样测不到），不是新增盲区。
        #   * 兜底 = `singleShot(0)` 是投递给循环的第一个事件，因此 5 s 预算只可能被
        #     **渲染**耗掉、不可能被初始化耗掉；起表前先看 `_l2d_pending`，
        #     若首帧/取消已在此之前发生就不起表（避免把已取消的定时器重新点着）。
        QTimer.singleShot(0, self._arm_live2d_startup_timeout)

    def _arm_live2d_startup_timeout(self) -> None:
        """事件循环启动后才开始计首帧预算；已不再等待则不起表。"""
        if not getattr(self, "_l2d_pending", False):
            return
        self._ensure_live2d_startup_timer().start(LIVE2D_STARTUP_TIMEOUT_MS)

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
        """首帧已经绘制并提交后，调整窗口大小以匹配模型画布，再显现。"""
        if not self._l2d_pending or not self._use_live2d:
            return
        self._cancel_live2d_startup_timeout()
        self._l2d_pending = False
        self._live2d_startup_widget = None

        # 首帧就绪后，根据模型实际画布大小调整窗口尺寸
        try:
            self._fit_window_to_model()
        except Exception as exc:
            safe_print(f"[live2d] fit window to model skipped: {exc}")

        # Live2D 的最终窗口尺寸要到首帧后才确定；此时再按保存坐标夹回
        # 当前屏幕，避免先恢复位置、随后尺寸变化又把桌宠推出屏幕。
        self._restore_pet_position()

        self._reveal_live2d_window()
        self._mark_renderer_ready()
        safe_print(
            f"[pet] Live2D 首帧就绪 size={self.width()}x{self.height()} "
            f"pos=({self.x()},{self.y()})"
        )
        # ★ Phase 3: 初始化 layer-shell 双模（穿透 / 交互）
        self._init_layer_overlay_mode()

        # ★ Phase 1: 首帧就绪后，强制启用 layer-shell 穿透（Niri / Wayland）
        # 独立于 standby 状态，确保真实桌宠运行时也能接通 wayland-layer 后端
        from PyQt5.QtCore import QTimer
        from meapet.desktop.click_through import enable_click_through

    # ------------------------------------------------------------ 双模架构
    def _layer_geometry(self):
        """返回离屏渲染内容应显示的全局矩形 (x, y, w, h)。

        ⚠️ Wayland 客户端无法查询自身全局坐标，mapToGlobal() 不可靠
        （会返回相对值导致负坐标）。必须用「顶层窗口坐标 + 子控件相对偏移」
        手动合成。子控件（完整画布）通常比窗口大且带负偏移。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return self.x(), self.y(), self.width(), self.height()
        try:
            # mapTo 是局部映射，可靠；再叠加顶层窗口坐标
            offset = widget.mapTo(self, QPoint(0, 0))
            return (
                self.x() + offset.x(),
                self.y() + offset.y(),
                widget.width(),
                widget.height(),
            )
        except Exception:
            return self.x(), self.y(), self.width(), self.height()

    def _init_layer_overlay_mode(self) -> None:
        # ★ 只在 Linux + Wayland 会话下启用双模
        if sys.platform != "linux":
            return
        try:
            from PyQt5.QtGui import QGuiApplication
            if str(QGuiApplication.platformName() or "").lower() != "wayland":
                return
        except Exception:
            return
        """初始化 layer-shell 双模；不支持时静默保持原有单窗口行为。"""
        if getattr(self, "_layer_inited", False):
            return
        self._layer_inited = True
        try:
            from meapet.desktop.layer_debug_panel import LayerDebugPanel
            from meapet.desktop.wayland_layer import (
                get_backend,
                is_available as layer_available,
            )
        except Exception as exc:
            safe_print(f"[layer] 双模模块导入失败，保持原行为: {exc}")
            return

        try:
            if not layer_available():
                safe_print("[layer] compositor 不支持 layer-shell，保持原行为")
                return
        except Exception as exc:
            safe_print(f"[layer] layer-shell 探测失败: {exc}")
            return

        backend = get_backend()
        self._layer_backend = backend          # ← 只赋值，不创建

        # 推帧定时器：穿透模式下把离屏渲染结果送到 layer surface
        self._layer_timer = QTimer(self)
        self._layer_timer.setInterval(33)  # ~30fps
        self._layer_timer.timeout.connect(self._push_layer_frame)

        # 常驻开关（永远可点，用来切模式）
        self._layer_panel = LayerDebugPanel(self._set_layer_mode)
        self._position_layer_panel()
        self._layer_panel.show()

        self._set_layer_mode(True)             # ← 由它统一创建
        # 本函数体此前被整段复制过一遍（原 699-711 行），于是 _set_layer_mode(True)
        # 会调两次 backend.enable()。失效模式与兜底（agents-rules §1 三条）：
        #   * 会崩的条件 = 任何第二次 enable()：wayland_layer.py 的 enable() 直接
        #     覆盖 self._ctx 而不销毁旧 ctx，旧 ctx 连同它的 RING_DEPTH 个 memfd
        #     和一个已 map 的 OVERLAY surface 一起变成孤儿；两个 QTimer 也会各自
        #     以 33ms 推帧（等于双倍提交——`QTimer(self)` 的父对象是本窗口，引用被
        #     覆盖不会销毁它，记录 1H 的 L1 探针实测两个定时器都 active）。
        #     面板**不**双份：`LayerDebugPanel` 是无父 QWidget，引用覆盖后旧实例
        #     随之回收 —— 老代码的可见后果是双 ctx 与双推帧，不是两只开关窗口。
        #   * 为什么不能靠 disable() 兜底 = disable() 有 `if self._ctx:` 守卫，
        #     destroy_context() 之后 _ctx 已是 None，整块（含 layer_shell_cleanup）
        #     被跳过，门面层没有任何可达路径回收孤儿。
        #   * 实测 = 2026-09-20 L3：修复前 `niri msg -j layers` 稳定 2 只 meapet、
        #     /proc/<pid>/fd 里 memfd:meapet-px 恒为 6（2 ctx × RING_DEPTH）；
        #     门面级复现 enable×2 → destroy → disable 仍是 1 只 surface / 3 个 fd，
        #     只有直接 layer_shell_cleanup() 才归零。桥接层行为符合 spec §4.7 第 12 行
        #     （destroy 只释放点名的注册表槽位）＋第 9 行（cleanup 连坐全部存活 ctx），
        #     所以这里修调用方，不改桥。
        # 本函数只允许一次 enable：由上面这条 `_set_layer_mode(True)` 单点负责。

    def _position_layer_panel(self) -> None:
        """把开关窗口贴到屏幕左下角。"""
        panel = getattr(self, "_layer_panel", None)
        if panel is None:
            return
        try:
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen is not None else None
            if geo is not None:
                panel.move(geo.left() + 12, geo.bottom() - panel.height() - 12)
                return
        except Exception:
            pass
        panel.move(20, 20)

    def _set_layer_mode(self, penetrate: bool) -> None:
        backend = getattr(self, "_layer_backend", None)
        if backend is None:
            return

        if penetrate:
            # ① 必须在 hide() 之前取坐标（Wayland 下隐藏后位置即失效）
            x, y, w, h = self._layer_geometry()
            x, y = max(0, x), max(0, y)
            self._layer_window_pos = (self.x(), self.y())

            # ② 每次都新建（全新 OVERLAY surface）
            try:
                backend.enable(None, w, h, x, y)
            except Exception as exc:
                safe_print(f"[layer] 创建 layer context 失败: {exc}")
                return

            widget = getattr(self, "sprite_label", None)
            if widget is not None:
                widget._proxy_rect = QRect(QPoint(x, y), QSize(w, h))

            self.hide()                        # ③ 最后才隐藏
            self._layer_timer.start()
            safe_print(f"[layer] → 穿透模式 surface={w}x{h} @({x},{y})")
        else:
            self._layer_timer.stop()
            widget = getattr(self, "sprite_label", None)
            if widget is not None:
                widget._proxy_rect = None

            pos = getattr(self, "_layer_window_pos", None)
            if pos is not None:
                self.move(pos[0], pos[1])

            # ④ 销毁而非 clear —— 关键修复
            backend.destroy_context()
            self.show()
            self.raise_()
            safe_print("[layer] → 交互模式（可拖动 / 右键菜单）")


    def _push_layer_frame(self) -> None:
        """把 Live2D 离屏渲染结果推送到 layer surface。"""
        backend = getattr(self, "_layer_backend", None)
        if backend is None:
            return
        widget = getattr(self, "sprite_label", None)
        renderer = getattr(widget, "render_offscreen", None)
        if not callable(renderer):
            return
        try:
            img = renderer()
            if img is None or img.isNull():
                self._layer_fail = getattr(self, "_layer_fail", 0) + 1
                if self._layer_fail <= 3:
                    print(f"[layer] ✗ 第 {self._layer_fail} 次拿到空帧", flush=True)
                return
            backend.update_pixels(img)
        except Exception as exc:
            print(f"[layer] ✗ 推帧异常: {type(exc).__name__}: {exc}", flush=True)



    # ------------------------------------------------------------ 画布尺寸工具

    def _read_canvas_size_from_model(self, model) -> tuple[int, int] | None:
        """从 Live2D 模型读取画布尺寸，带多层回退。

        优先使用 get_suggested_size()，然后对返回值做严格范围校验。
        如果 SDK 返回无效占位值（如 (1, 2)），依次尝试其他可能的 API。
        全部失败时返回 None，由调用方决定回退策略。
        """
        if model is None:
            return None

        # 第 1 层：get_suggested_size()
        try:
            w, h = model.get_suggested_size()
            w, h = int(w), int(h)
            if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                return w, h
            safe_print(f"[WARN] get_suggested_size 返回异常值 ({w}, {h})，尝试其他 API")
        except (AttributeError, TypeError, ValueError) as exc:
            safe_print(f"[WARN] get_suggested_size 调用失败: {exc}")

        # 第 2 层：尝试其他常见 API 名称
        for method_name in ("get_canvas_size", "GetCanvasSize", "getCanvasSize"):
            if hasattr(model, method_name):
                try:
                    func = getattr(model, method_name)
                    result = func() if callable(func) else func
                    if isinstance(result, (tuple, list)) and len(result) >= 2:
                        w, h = int(result[0]), int(result[1])
                    else:
                        w, h = int(result), int(result)
                    if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                        safe_print(f"[INFO] 通过 {method_name} 获取画布尺寸: {w}x{h}")
                        return w, h
                except Exception as exc:
                    safe_print(f"[WARN] {method_name} 调用失败: {exc}")

        # 第 3 层：尝试访问底层 SDK 模型的 canvas 属性
        sdk_model = getattr(model, "_model", None) or getattr(model, "model", None)
        if sdk_model is not None:
            for attr_pair in (
                ("GetCanvasWidth", "GetCanvasHeight"),
                ("getCanvasWidth", "getCanvasHeight"),
                ("canvasWidth", "canvasHeight"),
                ("width", "height"),
            ):
                try:
                    getter_w = getattr(sdk_model, attr_pair[0], None)
                    getter_h = getattr(sdk_model, attr_pair[1], None)
                    if getter_w is not None and getter_h is not None:
                        w = getter_w() if callable(getter_w) else int(getter_w)
                        h = getter_h() if callable(getter_h) else int(getter_h)
                        w, h = int(w), int(h)
                        if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                            safe_print(f"[INFO] 通过 sdk_model.{attr_pair[0]}/{attr_pair[1]} 获取画布尺寸: {w}x{h}")
                            return w, h
                except Exception:
                    continue

        return None

    def _live2d_base_size(self) -> tuple[int, int]:
        """返回 Live2D 模型画布的原始尺寸（无缩放），带多重回退。

        回退顺序：
        1. 从模型 SDK 读取（_read_canvas_size_from_model）
        2. 从 config.json 的 live2d.default_canvas_size 读取
        3. DEFAULT_CANVAS_SIZE 硬编码 (1024, 1024)
        """
        model = getattr(self, "_l2d_model", None)
        canvas = self._read_canvas_size_from_model(model)
        if canvas is not None:
            return canvas

        # 回退 2：配置文件
        l2d_cfg = self.config.get("live2d", {})
        default_size = l2d_cfg.get("default_canvas_size", None)
        if isinstance(default_size, (list, tuple)) and len(default_size) >= 2:
            try:
                w, h = int(default_size[0]), int(default_size[1])
                if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                    safe_print(f"[INFO] 使用配置中的 default_canvas_size: {w}x{h}")
                    return w, h
            except (TypeError, ValueError):
                pass

        # 回退 3：硬编码默认值
        w, h = DEFAULT_CANVAS_SIZE
        safe_print(f"[INFO] 使用硬编码默认画布尺寸: {w}x{h}")
        return w, h

    # ------------------------------------------------------------ 窗口尺寸调整

    def _fit_window_to_model(self):
        """首帧后按实际画布刷新视觉视口，并保持模型脚底位置。"""
        model = self._l2d_model
        if model is None:
            safe_print("[live2d] _fit_window_to_model: 模型未加载，跳过")
            return

        factor = float(getattr(self, "_size_factor", 1.0))
        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = (
            QRect(self.sprite_label.geometry())
            if self.sprite_label is not None
            else None
        )
        layout = self._apply_live2d_viewport_geometry(factor)
        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )

        safe_print(
            "[live2d] 窗口已适配视觉视口: "
            f"window={layout.window_width}x{layout.window_height} "
            f"canvas={layout.widget_width}x{layout.widget_height} "
            f"offset=({layout.widget_x},{layout.widget_y}) factor={factor}"
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
            self._place_initial_position()
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
        # 子控件保留完整模型画布，顶层窗口只显示配置的视觉视口。
        layout = self._apply_live2d_viewport_geometry(self._size_factor)
        widget.show()
        safe_print(
            "[live2d] 控件已创建，等待首帧: "
            f"window={layout.window_width}x{layout.window_height} "
            f"canvas={layout.widget_width}x{layout.widget_height}"
        )

    def _safe_renderer(self):
        if self._use_live2d and self._l2d_model:
            return self._l2d_model
        return self.renderer

    def _scaled_live2d_size(self, factor: float) -> tuple[int, int]:
        """返回裁去模型透明留白后的顶层窗口尺寸。"""
        layout = self._live2d_viewport_layout(factor)
        return layout.window_width, layout.window_height

    def _live2d_viewport_layout(self, factor: float) -> Live2DViewportLayout:
        """按当前模型和配置计算 Live2D 父子窗口几何。"""
        base_w, base_h = self._live2d_base_size()
        live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        return calculate_live2d_viewport_layout(
            base_w,
            base_h,
            factor,
            live2d.get("window_mask"),
        )

    def _apply_live2d_viewport_geometry(
        self,
        factor: float,
    ) -> Live2DViewportLayout:
        """应用完整画布子控件与裁剪顶层窗口的几何。"""
        layout = self._live2d_viewport_layout(factor)
        widget = self.sprite_label
        if widget is not None:
            widget.setGeometry(
                layout.widget_x,
                layout.widget_y,
                layout.widget_width,
                layout.widget_height,
            )
        self.resize(layout.window_width, layout.window_height)
        self._apply_live2d_window_region(layout)
        return layout

    def _apply_live2d_window_region(
        self,
        layout: Live2DViewportLayout | None = None,
    ) -> bool:
        """应用可选静态窗口形状；无有效形状时恢复普通矩形窗口。"""
        if not getattr(self, "_use_live2d", False):
            self.clearMask()
            return False
        if layout is None:
            layout = self._live2d_viewport_layout(self._size_factor)
        live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        region = calculate_live2d_window_region(
            layout,
            live2d.get("window_shape"),
        )
        if region is None:
            self.clearMask()
            return False
        self.setMask(region)
        return True

    def _apply_hit_region(self) -> None:
        """启动后或渲染模式变化时同步当前 Live2D 窗口形状。"""
        if getattr(self, "_use_live2d", False) and self.sprite_label is not None:
            self._apply_live2d_window_region()
        else:
            self.clearMask()

    def _apply_live2d_viewport_preference(self) -> bool:
        """热应用视觉视口与模型锚点，同时维持模型和气泡位置。"""
        if not getattr(self, "_use_live2d", False) or self.sprite_label is None:
            return False
        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = QRect(self.sprite_label.geometry())
        self._apply_live2d_viewport_geometry(self._size_factor)
        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )
        self._position_bubble()
        return True

    def _capture_live2d_viewport_preview(self):
        """抓取一次完整 Live2D 帧供配置页框选；异常或超大画布返回 None。"""
        widget = getattr(self, "sprite_label", None)
        if not getattr(self, "_use_live2d", False) or widget is None:
            return None
        grab = getattr(widget, "grabFramebuffer", None)
        if not callable(grab):
            return None
        try:
            width = max(0, int(widget.width()))
            height = max(0, int(widget.height()))
            dpr_getter = getattr(widget, "devicePixelRatioF", None)
            dpr = float(dpr_getter()) if callable(dpr_getter) else 1.0
            source_pixels = width * height * max(1.0, dpr) ** 2
            if (
                width <= 0
                or height <= 0
                or source_pixels > LIVE2D_PREVIEW_MAX_SOURCE_PIXELS
            ):
                return None
            image = grab()
            if image is None or image.isNull():
                return None
            image = image.copy()
            if max(image.width(), image.height()) > LIVE2D_PREVIEW_MAX_EDGE:
                image = image.scaled(
                    LIVE2D_PREVIEW_MAX_EDGE,
                    LIVE2D_PREVIEW_MAX_EDGE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            return image
        except Exception as exc:
            safe_print(f"[live2d] 配置预览抓取失败: {type(exc).__name__}")
            return None

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

    def _set_size_factor(self, factor: float):
        """按百分比档位直接应用窗口大小并写回配置（右键菜单快捷项）。"""
        safe_print(f"[SET_SIZE_FACTOR] input={factor}, type={type(factor).__name__}")
        new_factor = normalize_pet_size_factor(factor)
        safe_print(f"[SET_SIZE_FACTOR] normalized={new_factor}")

        # 关键修复：只调用一次 _size_factor_preview（原代码调用了两次）
        try:
            self._size_factor_preview(new_factor)
            safe_print(f"[SET_SIZE_FACTOR] after preview: size={self.width()}x{self.height()}")
        except Exception as exc:
            import traceback
            safe_print(f"[SET_SIZE_FACTOR] preview failed: {exc}")
            traceback.print_exc()

        self.config.setdefault("display", {})["size_factor"] = new_factor
        self._save_config()
        show = getattr(self, "_show_bubble", None)
        if callable(show):
            show(
                status_language.window_size_applied(new_factor),
                bubble_duration_ms(self.config, "interaction"),
                mood=None,
            )

    def _apply_display_preference(self):
        """把 `display.size_factor` 应用到当前窗口（配置页保存后立即生效）。"""
        display = (getattr(self, "config", {}) or {}).get("display") or {}
        factor = normalize_pet_size_factor(display.get("size_factor", 1.0))
        if abs(factor - float(getattr(self, "_size_factor", 1.0))) < 1e-3:
            return
        self._size_factor_preview(factor)

    def _size_factor_preview(self, factor: float):
        """预览 size_factor 变化：调整窗口大小，保持位置合理。"""
        factor = normalize_pet_size_factor(factor)
        safe_print(f"[PREVIEW] factor={factor}, use_live2d={self._use_live2d}, model={self._l2d_model is not None}")

        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = (
            QRect(self.sprite_label.geometry())
            if self._use_live2d and self.sprite_label is not None
            else None
        )
        self._size_factor = factor

        if self._use_live2d and self.sprite_label:
            layout = self._apply_live2d_viewport_geometry(factor)
            safe_print(
                "[PREVIEW] Live2D viewport: "
                f"window=({layout.window_width},{layout.window_height}) "
                f"canvas=({layout.widget_width},{layout.widget_height}) "
                f"offset=({layout.widget_x},{layout.widget_y})"
            )
        else:
            if self.renderer is None:
                safe_print("[PREVIEW] no renderer available, skip")
                return
            pixmap = self.renderer.get_current_pixmap()
            if not pixmap.isNull():
                new_w = max(80, int(pixmap.width() * self._scale * factor))
                new_h = max(80, int(pixmap.height() * self._scale * factor))
                safe_print(f"[PREVIEW] PNG resize: ({new_w},{new_h})")
                self.resize(new_w, new_h)
            self._update_sprite()

        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )
        self._position_bubble()

    def _reanchor_after_resize(
        self,
        before: QRect,
        *,
        before_live2d_canvas: QRect | None = None,
    ):
        """按模型锚点或 PNG 底部中心重定位，并夹回屏幕可用区域。"""
        width = self.width()
        height = self.height()
        widget = getattr(self, "sprite_label", None)
        if (
            before_live2d_canvas is not None
            and getattr(self, "_use_live2d", False)
            and widget is not None
        ):
            live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
            anchor = normalize_live2d_placement_anchor(
                live2d.get("placement_anchor"),
                live2d.get("window_mask"),
            )
            target = calculate_live2d_anchor_preserving_position(
                before.topLeft(),
                before_live2d_canvas,
                QRect(widget.geometry()),
                anchor,
            )
        elif before.width() == width and before.height() == height:
            safe_print(f"[REANCHOR] 尺寸未变化 ({width}x{height})，跳过")
            return
        else:
            target = QPoint(
                before.center().x() - width // 2,
                before.bottom() + 1 - height,
            )
        area = available_geometry_for(before)
        if area is not None:
            target = clamp_position(target, QSize(width, height), area, margin=0)
        if target != QPoint(self.x(), self.y()):
            safe_print(f"[REANCHOR] move ({self.x()},{self.y()}) -> ({target.x()},{target.y()})")
            self.move(target)
        else:
            safe_print(f"[REANCHOR] 位置无需调整")

    def _open_size_dialog(self):
        dialog = SizeScaleDialog(self._size_factor, self)
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        area = available_geometry_for(pet_rect)
        if area is not None:
            dialog.move(
                calculate_centered_position(pet_rect, widget_size(dialog), area)
            )
        if dialog.exec_() == QDialog.Accepted:
            new_factor = normalize_pet_size_factor(dialog.get_value())
            self._size_factor = new_factor
            self.config.setdefault("display", {})["size_factor"] = new_factor
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

        pet_window_rect = QRect(
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        )
        # 顶层窗口已经是稳定视觉视口，不再查询动态 mask。
        pet_rect = pet_window_rect
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

    # ------------------------------------------------------------ 屏幕变化
    def _init_screen_guard(self):
        """监听分辨率 / 显示器变化，变化后把桌宠拉回可视范围。"""
        app = QApplication.instance()
        if app is None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._ensure_on_screen)
        self._screen_guard_timer = timer

        for signal_name in ("screenAdded", "screenRemoved", "primaryScreenChanged"):
            signal = getattr(app, signal_name, None)
            if signal is None:
                continue
            handler = (
                self._on_screen_added
                if signal_name == "screenAdded"
                else self._on_screen_layout_changed
            )
            try:
                signal.connect(handler)
            except (AttributeError, TypeError):
                pass
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen):
        if screen is None:
            return
        for signal_name in (
            "geometryChanged",
            "availableGeometryChanged",
            "logicalDotsPerInchChanged",
        ):
            signal = getattr(screen, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self._on_screen_layout_changed)
            except (AttributeError, TypeError, RuntimeError):
                pass

    def _on_screen_added(self, screen):
        self._watch_screen(screen)
        self._on_screen_layout_changed()

    def _on_screen_layout_changed(self, *_args):
        """一次分辨率变更会连发多个信号，且窗口管理器还在重排，去抖后再处理。"""
        timer = getattr(self, "_screen_guard_timer", None)
        if timer is None:
            self._ensure_on_screen()
            return
        try:
            timer.start(SCREEN_GUARD_DEBOUNCE_MS)
        except RuntimeError:
            self._ensure_on_screen()

    def _ensure_on_screen(self):
        """桌宠露出得太少时拉回屏幕可用区域，并同步浮层位置。"""
        if getattr(self, "_dragging", False):
            return
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        bounds = screen_bounds_for_rect(pet_rect)
        if bounds is None:
            return
        geometry, available = bounds
        if not is_sufficiently_visible(
            pet_rect, geometry, ratio=PET_MIN_VISIBLE_RATIO
        ):
            position = clamp_position(
                pet_rect.topLeft(), pet_rect.size(), available, margin=0
            )
            if position != pet_rect.topLeft():
                self.move(position)
                safe_print(
                    f"[screen] 屏幕变化后回到可视范围: "
                    f"({pet_rect.x()},{pet_rect.y()}) -> "
                    f"({position.x()},{position.y()}) "
                    f"available=({available.x()},{available.y()},"
                    f"{available.width()}x{available.height()})"
                )
        self._ensure_overlays_on_screen()
        self._position_bubble()

    def _ensure_overlays_on_screen(self):
        """把仍然打开的浮层拉回屏幕：输入面板重新贴着桌宠，其余就地钳制。"""
        composer = getattr(self, "_chat_input", None)
        place_chat_input = getattr(self, "_place_chat_input", None)
        if composer is not None and callable(place_chat_input):
            try:
                if composer.isVisible():
                    place_chat_input()
            except RuntimeError:
                self._chat_input = None
        for name in (
            "_status_panel",
            "_menu_window",
            "_timeline_dialog",
            "_timeline_turn_dialog",
        ):
            window = getattr(self, name, None)
            if window is None:
                continue
            try:
                if window.isVisible():
                    move_within_screen(window, window.pos())
            except RuntimeError:
                setattr(self, name, None)

    def _save_pet_position(self) -> bool:
        """把当前桌宠左上角保存到独立本机状态文件。"""
        path = str(getattr(self, "_window_state_path", "") or "")
        if not path:
            return False
        return save_pet_position(path, self.x(), self.y())

    def _restore_pet_position(self) -> bool:
        """恢复桌宠位置；显示器变化后自动夹回可用区域。"""
        path = str(getattr(self, "_window_state_path", "") or "")
        if not path:
            return False
        saved = load_pet_position(path)
        if saved is None:
            return False
        position = QPoint(saved["x"], saved["y"])
        size = QSize(max(1, self.width()), max(1, self.height()))
        area = available_geometry_for(QRect(position, size))
        if area is not None:
            position = clamp_position(position, size, area, margin=0)
        self.move(position)
        safe_print(
            f"[place] restored pos=({position.x()},{position.y()}) "
            f"size={size.width()}x{size.height()}"
        )
        return True

    def _place_initial_position(self) -> None:
        """优先恢复上次位置，首次运行才放到主屏右下角。"""
        if not self._restore_pet_position():
            self._place_bottom_right()

    def _place_bottom_right(self):
        """放到主屏右下角，并钳制在可见区域内（防止多屏/DPI 导致"消失"）。"""
        screen = QApplication.primaryScreen().availableGeometry()
        w = max(self.width(), 80)
        h = max(self.height(), 80)
        x = screen.right() - w - 50
        y = screen.bottom() - h - 10
        x = max(screen.left(), min(x, screen.right() - max(80, w // 5)))
        y = max(screen.top(), min(y, screen.bottom() - max(80, h // 5)))
        self.move(x, y)
        safe_print(
            f"[place] screen=({screen.x()},{screen.y()},{screen.width()}x{screen.height()}) "
            f"-> pos=({x},{y}) size={w}x{h}"
        )

    def _toggle_standby(self):
        self._standby = not self._standby
        if self._standby:
            self._watcher_timer.stop()
            self._safe_set_expression("011")
            self._show_bubble(status_language.standby_on(), 0)
            self._position_bubble()
            self._set_standby_click_through(True)
        else:
            self._set_standby_click_through(False)
            self._safe_set_expression("001")
            clear_bubbles = getattr(self, "_clear_bubbles", None)
            if callable(clear_bubbles):
                clear_bubbles()
            elif hasattr(self, "bubble") and self.bubble:
                self.bubble.hide()
            self._show_bubble(
                status_language.standby_off(),
                bubble_duration_ms(self.config, "interaction"),
            )
            self._position_bubble()
            self._start_watcher_timer()
        refresh_tray = getattr(self, "_refresh_tray_state", None)
        if callable(refresh_tray):
            refresh_tray()

    def _set_standby_click_through(self, enabled: bool) -> None:
        """Enable/disable OS click-through + right-click poll + bubble passthrough."""
        if enabled:
            self._ensure_standby_click_through()
            return
        self._stop_standby_right_click_monitor()
        state = getattr(self, "_click_through_state", None)
        if state is not None:
            disable_click_through(state)
        self._click_through_state = ClickThroughState()
        self._set_bubbles_mouse_passthrough(False)
        if getattr(self, "_qt_transparent_for_input", False):
            try:
                self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            except Exception:
                pass
            self._qt_transparent_for_input = False

    def _ensure_standby_click_through(self) -> None:
        """(Re)apply click-through on current winId — safe to call after mode switch."""
        prev = getattr(self, "_click_through_state", None)
        if prev is not None and prev.active:
            disable_click_through(prev)

        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0
        width = max(0, int(self.width()))
        height = max(0, int(self.height()))
        state = enable_click_through(hwnd, width=width, height=height)
        self._click_through_state = state
        if not state.active:
            safe_print(
                "[click_through] native pass-through inactive; "
                "standby still suppresses pet interactions"
            )
        self._set_bubbles_mouse_passthrough(True)
        self._start_standby_right_click_monitor()

    def _start_standby_right_click_monitor(self) -> None:
        timer = getattr(self, "_standby_rc_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(50)
            timer.timeout.connect(self._poll_standby_right_click)
            self._standby_rc_timer = timer
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is None:
            detector = RightClickEdgeDetector()
            self._standby_rc_detector = detector
        else:
            detector.reset()
        if not timer.isActive():
            timer.start()

    def _stop_standby_right_click_monitor(self) -> None:
        timer = getattr(self, "_standby_rc_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            detector.reset()

    def _poll_standby_right_click(self) -> None:
        if not getattr(self, "_standby", False):
            return
        if getattr(self, "_standby_menu_open", False):
            return
        if not self.isVisible():
            return
        try:
            from PyQt5.QtGui import QCursor

            global_pos = QCursor.pos()
            geo = self.frameGeometry()
            cursor_in_pet = geo.contains(global_pos)
            button_down = is_right_button_down()
            detector = getattr(self, "_standby_rc_detector", None)
            if detector is None:
                detector = RightClickEdgeDetector()
                self._standby_rc_detector = detector
            if detector.update(cursor_in_pet=cursor_in_pet, button_down=button_down):
                local = self.mapFromGlobal(global_pos)
                self._open_standby_context_menu(local)
        except Exception as exc:
            safe_print(f"[click_through] right-click poll error: {exc}")

    def _open_standby_context_menu(self, local_pos) -> None:
        """Show context menu while standby: temporarily accept mouse input."""
        if getattr(self, "_standby_menu_open", False):
            return
        self._standby_menu_open = True
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            detector.was_down = True
        self._set_standby_click_through(False)
        try:
            show_menu = getattr(self, "_show_context_menu", None)
            if callable(show_menu):
                show_menu(local_pos)
        finally:
            self._standby_menu_open = False
            if getattr(self, "_standby", False):
                self._set_standby_click_through(True)

    def _set_bubbles_mouse_passthrough(self, enabled: bool) -> None:
        stack = getattr(self, "_bubble_stack", None)
        bubbles = []
        if stack is not None:
            bubbles = list(getattr(stack, "bubbles", ()) or ())
        else:
            bubble = getattr(self, "bubble", None)
            if bubble is not None:
                bubbles = [bubble]
        for bubble in bubbles:
            setter = getattr(bubble, "set_mouse_passthrough", None)
            if callable(setter):
                try:
                    setter(bool(enabled))
                except Exception:
                    pass

    def _toggle_render_mode(self):
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
            self._show_bubble(
                status_language.render_png_enabled(),
                bubble_duration_ms(self.config, "interaction"),
            )
            self.config.setdefault("live2d", {})["enabled"] = False
            self._save_config()
            if getattr(self, "_standby", False):
                self._ensure_standby_click_through()
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
            self.config.setdefault("live2d", {})["enabled"] = True
            self._save_config()
            try:
                self._start_live2d_renderer()
                self._place_initial_position()
            except Exception as exc:
                self._fallback_to_png(str(exc))

            def announce_mode_change():
                if self._use_live2d:
                    self._show_bubble(
                        status_language.render_live2d_enabled(),
                        bubble_duration_ms(self.config, "interaction"),
                    )
                else:
                    self._show_bubble(
                        status_language.render_live2d_failed(),
                        bubble_duration_ms(self.config, "default"),
                    )
                if getattr(self, "_standby", False):
                    self._ensure_standby_click_through()

            self.when_renderer_ready(announce_mode_change)

    def closeEvent(self, event):
        """取消未完成的启动回调，避免关闭后被超时回退重新显示。"""
        self._cancel_live2d_startup_timeout()
        super().closeEvent(event)
