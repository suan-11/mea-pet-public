"""Live2D 启动连续性与回退路径的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from meapet.desktop.chat_flow import PetChatFlowMixin  # noqa: E402
from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 测试辅助类
# ════════════════════════════════════════════════════════════════════

class _SignalStub:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _SpriteRendererStub:
    created = 0

    def __init__(self, *_args) -> None:
        type(self).created += 1
        self.expression_changed = _SignalStub()
        self._pixmap = QPixmap(80, 120)
        self._pixmap.fill(Qt.transparent)

    def get_current_pixmap(self):
        return self._pixmap

    def start_blink_animation(self) -> None:
        pass

    def stop_blink_animation(self) -> None:
        pass


class _RenderHost(PetRenderHostMixin, QWidget):
    """将 PetRenderHostMixin 混入 QWidget，便于单元测试。"""

    def __init__(self, model_dir: str) -> None:
        super().__init__()
        self.config = {
            "character": {"default_outfit": "01", "default_direction": "A"},
            "display": {"scale": 0.5, "size_factor": 1.0},
            "live2d": {"enabled": True, "model_dir": model_dir},
        }
        self.hit_region_updates = 0
        self.placements = 0
        self._l2d_model = None  # 供 _size_factor_preview 调试输出使用

    def init_renderer(self) -> None:
        self._init_renderer()

    def _on_sprite_changed(self, _code: str) -> None:
        self._update_sprite()

    def _on_head_patted(self) -> None:
        pass

    def _on_tail_patted(self) -> None:
        pass

    def _on_lower_left_patted(self) -> None:
        pass

    def _on_lower_right_patted(self) -> None:
        pass

    def _start_chat(self) -> None:
        pass

    def _apply_hit_region(self) -> None:
        self.hit_region_updates += 1

    def _place_bottom_right(self) -> None:
        self.placements += 1

    def _position_bubble(self) -> None:
        pass


class _ChatRenderHost(PetChatFlowMixin, _RenderHost):
    def __init__(self, model_dir: str) -> None:
        super().__init__(model_dir)
        self.bubble = mock.Mock()

    def _on_input_submit(self, _text: str) -> None:
        pass


# ════════════════════════════════════════════════════════════════════
# 测试用例
# ════════════════════════════════════════════════════════════════════

class PNGStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        _SpriteRendererStub.created = 0
        self._hosts = []

    def tearDown(self) -> None:
        for host in self._hosts:
            for timer in host.findChildren(QTimer):
                timer.stop()
            chat_input = getattr(host, "_chat_input", None)
            if chat_input is not None:
                chat_input.close()
                chat_input.deleteLater()
            host.close()
            host.deleteLater()
        QApplication.processEvents()

    def _host(self, model_dir: str) -> _RenderHost:
        host = _RenderHost(model_dir)
        self._hosts.append(host)
        return host

    # ── 回退路径 ────────────────────────────────────────────────────

    def test_force_png_skips_live2d_and_is_ready_immediately(self) -> None:
        """设置 MEA_PET_FORCE_PNG=1 时，应跳过 Live2D 直接走 PNG。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            with (
                mock.patch.dict(os.environ, {"MEA_PET_FORCE_PNG": "1"}),
                mock.patch("meapet.desktop.render_host.SpriteRenderer", _SpriteRendererStub),  # ← 新增
            ):
                host.init_renderer()

            self.assertEqual(_SpriteRendererStub.created, 1)
            self.assertFalse(host._use_live2d)
            self.assertTrue(host._renderer_ready)
            self.assertEqual(host.windowOpacity(), 1.0)

    # ── Live2D 视觉视口 ──────────────────────────────────────────────

    def test_live2d_viewport_crops_canvas_to_configured_visual_bounds(self) -> None:
        """视觉窗口只覆盖 mask 外接矩形，完整画布留在负偏移子控件中。"""
        from meapet.desktop.render_host import calculate_live2d_viewport_layout

        layout = calculate_live2d_viewport_layout(
            1000,
            800,
            0.5,
            {
                "enabled": True,
                "cx": 0.50,
                "cy": 0.50,
                "rw": 0.25,
                "rh": 0.40,
            },
        )

        self.assertEqual(
            (
                layout.widget_x,
                layout.widget_y,
                layout.widget_width,
                layout.widget_height,
            ),
            (-125, -40, 500, 400),
        )
        self.assertEqual(
            (layout.window_width, layout.window_height),
            (250, 320),
        )

    def test_live2d_viewport_disabled_keeps_the_complete_canvas(self) -> None:
        from meapet.desktop.render_host import calculate_live2d_viewport_layout

        layout = calculate_live2d_viewport_layout(
            1000,
            800,
            0.5,
            {
                "enabled": False,
                "cx": 0.50,
                "cy": 0.50,
                "rw": 0.25,
                "rh": 0.40,
            },
        )

        self.assertEqual(
            (
                layout.widget_x,
                layout.widget_y,
                layout.widget_width,
                layout.widget_height,
            ),
            (0, 0, 500, 400),
        )
        self.assertEqual(
            (layout.window_width, layout.window_height),
            (500, 400),
        )

    def test_live2d_host_keeps_full_canvas_behind_the_cropped_window(self) -> None:
        class CanvasModel:
            def get_suggested_size(self):
                return 1000, 800

        host = self._host("")
        host._use_live2d = True
        host._l2d_model = CanvasModel()
        host.config["live2d"]["window_mask"] = {
            "enabled": True,
            "cx": 0.50,
            "cy": 0.50,
            "rw": 0.25,
            "rh": 0.40,
        }
        host.sprite_label = QWidget(host)

        layout = host._apply_live2d_viewport_geometry(0.5)

        self.assertEqual(
            (
                host.sprite_label.x(),
                host.sprite_label.y(),
                host.sprite_label.width(),
                host.sprite_label.height(),
            ),
            (-125, -40, 500, 400),
        )
        self.assertEqual((host.width(), host.height()), (250, 320))
        self.assertEqual(layout.window_width, host.width())

    def test_live2d_first_frame_uses_fallback_canvas_and_preserves_foot_anchor(
        self,
    ) -> None:
        class InvalidCanvasModel:
            def get_suggested_size(self):
                return 1, 2

        host = self._host("")
        host._use_live2d = True
        host._size_factor = 1.0
        host._l2d_model = InvalidCanvasModel()
        host.config["live2d"].update(
            {
                "default_canvas_size": [800, 1000],
                "window_mask": {"enabled": False},
            }
        )
        host.sprite_label = QWidget(host)
        host.sprite_label.setGeometry(0, 0, 200, 300)
        host.resize(200, 300)
        host.move(400, 500)
        old_center_x = host.frameGeometry().center().x()
        old_bottom = host.frameGeometry().bottom()

        with mock.patch(
            "meapet.desktop.render_host.available_geometry_for",
            return_value=None,
        ):
            host._fit_window_to_model()

        self.assertEqual((host.width(), host.height()), (800, 1000))
        self.assertEqual(
            (
                host.sprite_label.x(),
                host.sprite_label.y(),
                host.sprite_label.width(),
                host.sprite_label.height(),
            ),
            (0, 0, 800, 1000),
        )
        self.assertAlmostEqual(host.frameGeometry().center().x(), old_center_x, delta=1)
        self.assertEqual(host.frameGeometry().bottom(), old_bottom)

    # ── PNG 渲染器单元测试 ──────────────────────────────────────────

    def test_png_frames_are_cached_and_reused_across_blinks(self) -> None:
        """眨眼动画应在打开/闭合帧之间正确切换并缓存。"""
        from meapet.desktop.renderer import SpriteRenderer

        loaded_frames = []

        def load_pixmap(path):
            frame = object()
            loaded_frames.append((path, frame))
            return frame

        with (
            mock.patch(
                "meapet.desktop.renderer.os.path.exists",
                return_value=True,
            ),
            mock.patch(
                "meapet.desktop.renderer.QPixmap",
                side_effect=load_pixmap,
            ),
        ):
            renderer = SpriteRenderer("/sprites")
            open_frame = renderer.get_current_pixmap()
            self.assertIs(renderer.get_current_pixmap(), open_frame)

            renderer._is_blinking = True
            closed_frame = renderer.get_current_pixmap()
            self.assertIs(renderer.get_current_pixmap(), closed_frame)

        self.assertIsNot(open_frame, closed_frame)
        self.assertEqual(len(loaded_frames), 2)

    def test_png_canvas_replaces_the_complete_frame_atomically(self) -> None:
        """SpriteCanvas 设置帧后应完整替换像素，不留残影。"""
        from meapet.desktop.renderer import SpriteCanvas

        canvas = SpriteCanvas()
        self._hosts.append(canvas)
        canvas.resize(24, 16)
        canvas.show()

        open_frame = QPixmap(canvas.size())
        open_frame.fill(QColor("#E7B9AD"))
        closed_frame = QPixmap(canvas.size())
        closed_frame.fill(QColor("#20233D"))

        canvas.set_frame(open_frame)
        QApplication.processEvents()
        canvas.set_frame(closed_frame)
        QApplication.processEvents()

        rendered = canvas.grab().toImage()
        expected = QColor("#20233D").rgba()
        self.assertTrue(
            all(
                rendered.pixel(x, y) == expected
                for y in range(rendered.height())
                for x in range(rendered.width())
            )
        )

    # ── app.py 静态检查 ─────────────────────────────────────────────

    def test_app_keeps_splash_until_renderer_reports_ready(self) -> None:
        """app.py 应使用 when_renderer_ready，而非硬编码的 QTimer.singleShot。"""
        source = (
            Path(__file__).resolve().parents[1]
            / "meapet" / "desktop" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("when_renderer_ready", source)
        self.assertNotIn("QTimer.singleShot(200, _ensure_visible)", source)


class Live2DStartupBudgetTests(unittest.TestCase):
    """首帧超时的**起表时刻**：预算只能被渲染耗掉，不能被初始化耗掉。

    回归的事故（本机 L3 冷启动复现）：定时器原先在 `_start_live2d_renderer` 里
    同步 `start(5000)`，而 MeaPet 的同步初始化实测可耗时 9 s > 5 s，于是定时器在
    `app.exec_()` 启动的那一刻就已过期、第一次迭代即触发 ⇒ 控件还没画首帧就被判
    "Live2D 加载失败，已切回 PNG"。热启动不触发 ⇒ 间歇性。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._hosts = []

    def tearDown(self) -> None:
        for host in self._hosts:
            for timer in host.findChildren(QTimer):
                timer.stop()
            host.close()
            host.deleteLater()
        QApplication.processEvents()

    def _host(self, model_dir: str) -> _RenderHost:
        host = _RenderHost(model_dir)
        self._hosts.append(host)
        return host

    def _start_with_stub_widget(self, host: _RenderHost):
        """跑真实的 `_start_live2d_renderer`，只把 OpenGL 控件换成普通 QWidget。"""

        def fake_init_live2d() -> None:
            host.sprite_label = QWidget(host)

        patcher_widget = mock.patch("meapet.desktop.live2d_widget.init_live2d")
        patcher_model = mock.patch.object(host, "_init_live2d", side_effect=fake_init_live2d)
        patcher_widget.start()
        patcher_model.start()
        self.addCleanup(lambda: (patcher_widget.stop(), patcher_model.stop()))
        host._start_live2d_renderer()

    @staticmethod
    def _pump(times: int = 5) -> None:
        for _ in range(times):
            QApplication.processEvents()

    def test_budget_is_not_armed_before_the_event_loop_gets_a_turn(self) -> None:
        """起表前（循环还没转）定时器**不存在**；派发一次后才带着完整预算活着。"""
        from meapet.desktop.render_host import LIVE2D_STARTUP_TIMEOUT_MS

        host = self._host("")
        self._start_with_stub_widget(host)

        # 判据的正面：同步阶段连计时器都没构造 ⇒ 它不可能已经开始计数。
        # 旧写法在这里就已经 isActive() 为真，本断言当场会红。
        self.assertIsNone(getattr(host, "_live2d_startup_timer", None))

        self._pump()

        timer = getattr(host, "_live2d_startup_timer", None)
        self.assertIsNotNone(timer, "事件循环启动后应完成起表，否则超时门禁形同虚设")
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), LIVE2D_STARTUP_TIMEOUT_MS)
        self.assertTrue(timer.isSingleShot())

    def test_budget_is_not_armed_if_live2d_stopped_waiting_first(self) -> None:
        """首帧/取消抢先发生 ⇒ 延迟起表不得把已作废的定时器重新点着。"""
        host = self._host("")
        self._start_with_stub_widget(host)
        host._l2d_pending = False  # 模拟在 singleShot 派发之前首帧已就绪

        self._pump()

        self.assertIsNone(getattr(host, "_live2d_startup_timer", None))

    def test_gate_still_falls_back_when_the_first_frame_never_arrives(self) -> None:
        """反向对照：把预算压到 0 后，门禁**确实**触发回退——它不是永不响的空门。"""
        host = self._host("")
        with mock.patch("meapet.desktop.render_host.LIVE2D_STARTUP_TIMEOUT_MS", 0):
            self._start_with_stub_widget(host)
            with mock.patch.object(host, "_fallback_to_png") as fallback:
                self._pump()

        fallback.assert_called_once_with("等待 Live2D 首帧超时")

    def test_slow_synchronous_init_does_not_consume_the_budget(self) -> None:
        """冷启动的复现形态：循环外的同步耗时**吃掉不了**这份预算。

        本机 L3 实测冷启动为"MeaPet 构造 9 s ＋ 预算 5 s"，热启动为"1–2 s"；
        这里把两者压缩成"睡眠 2.5 × 预算"，判据用 `remainingTime()` 而不是"有没有回退"，
        因为它不依赖派发用了多少毫秒（agents-rules §8：判决用的量须与所关心的属性单调相关）。
        """
        budget_ms = 100
        host = self._host("")
        with mock.patch("meapet.desktop.render_host.LIVE2D_STARTUP_TIMEOUT_MS", budget_ms):
            with mock.patch.object(host, "_fallback_to_png") as fallback:
                self._start_with_stub_widget(host)
                time.sleep(budget_ms * 2.5 / 1000.0)  # 同步初始化，事件循环还没转
                QApplication.processEvents()          # 只派发一次：让延迟起表落地

                timer = host._live2d_startup_timer
                self.assertTrue(timer.isActive())
                self.assertGreaterEqual(
                    timer.remainingTime(),
                    budget_ms // 2,
                    "起表时刻就已过期 ⇒ 预算被循环外的同步耗时吃掉了",
                )
                fallback.assert_not_called()

    def test_control_pre_loop_arming_loses_the_budget(self) -> None:
        """对照：**人为复现旧时序**（循环之前就起表）⇒ 预算确实被同步耗时吃掉并触发回退。

        这条不测被测函数的当前形态，而是测本用例集赖以成立的机制：
        QTimer 在循环停摆期间照样按挂钟过期。没有它，上一条可能只是"永远不会触发"的假绿。
        不经过 `_start_live2d_renderer`，因此不会有延迟起表的 `singleShot` 混进来——时序确定。
        """
        budget_ms = 100
        host = self._host("")
        host._use_live2d = True
        host._l2d_pending = True
        host.sprite_label = QWidget(host)
        host._live2d_startup_widget = host.sprite_label
        host._ensure_live2d_startup_timer().start(budget_ms)

        time.sleep(budget_ms * 2.5 / 1000.0)
        with mock.patch.object(host, "_fallback_to_png") as fallback:
            self._pump()

        fallback.assert_called_once_with("等待 Live2D 首帧超时")


if __name__ == "__main__":
    unittest.main()
