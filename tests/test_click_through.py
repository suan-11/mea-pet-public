"""Unit tests for standby click-through helpers and host wiring."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from PyQt5.QtCore import QPoint

from meapet.desktop.click_through import (
    ClickThroughState,
    RightClickEdgeDetector,
    disable_click_through,
    enable_click_through,
    is_right_button_down,
    platform_backend_name,
)
from meapet.desktop.render_host import PetRenderHostMixin


class RightClickEdgeDetectorTests(unittest.TestCase):
    def test_fires_only_on_rising_edge_inside_pet(self) -> None:
        det = RightClickEdgeDetector()
        self.assertFalse(det.update(cursor_in_pet=True, button_down=False))
        self.assertTrue(det.update(cursor_in_pet=True, button_down=True))
        # held
        self.assertFalse(det.update(cursor_in_pet=True, button_down=True))
        # release
        self.assertFalse(det.update(cursor_in_pet=True, button_down=False))
        # second press
        self.assertTrue(det.update(cursor_in_pet=True, button_down=True))

    def test_ignores_press_outside_pet(self) -> None:
        det = RightClickEdgeDetector()
        self.assertFalse(det.update(cursor_in_pet=False, button_down=True))
        # moving into pet while already down should not fire
        self.assertFalse(det.update(cursor_in_pet=True, button_down=True))
        self.assertFalse(det.update(cursor_in_pet=True, button_down=False))
        self.assertTrue(det.update(cursor_in_pet=True, button_down=True))

    def test_reset_clears_was_down(self) -> None:
        det = RightClickEdgeDetector()
        det.update(cursor_in_pet=True, button_down=True)
        det.reset()
        self.assertFalse(det.was_down)
        self.assertTrue(det.update(cursor_in_pet=True, button_down=True))


class PlatformBackendNameTests(unittest.TestCase):
    def test_explicit_overrides(self) -> None:
        self.assertEqual(platform_backend_name("win32"), "win32")
        self.assertEqual(platform_backend_name("windows"), "win32")
        self.assertEqual(platform_backend_name("xcb"), "x11")
        self.assertEqual(platform_backend_name("x11"), "x11")
        self.assertEqual(platform_backend_name("wayland"), "wayland")
        self.assertEqual(platform_backend_name("unsupported"), "none")

    def test_auto_win32(self) -> None:
        with mock.patch("meapet.desktop.click_through.sys.platform", "win32"):
            self.assertEqual(platform_backend_name(None), "win32")


class Win32ClickThroughTests(unittest.TestCase):
    def test_enable_and_disable_restore_exstyle(self) -> None:
        calls: list[tuple] = []

        class FakeUser32:
            def GetWindowLongW(self, hwnd, index):
                calls.append(("get", int(hwnd), int(index)))
                return 0x00080000  # already layered

            def SetWindowLongW(self, hwnd, index, value):
                calls.append(("set", int(hwnd), int(index), int(value)))
                return 0

        fake_windll = SimpleNamespace(user32=FakeUser32())
        import ctypes

        with mock.patch.object(ctypes, "windll", fake_windll, create=True):
            state = enable_click_through(0x1234, platform_name="win32")
            self.assertTrue(state.active)
            self.assertEqual(state.backend, "win32")
            self.assertEqual(state.previous_exstyle, 0x00080000)
            set_calls = [c for c in calls if c[0] == "set"]
            self.assertEqual(len(set_calls), 1)
            # WS_EX_TRANSPARENT bit
            self.assertEqual(set_calls[0][3] & 0x20, 0x20)

            disable_click_through(state)
            self.assertFalse(state.active)
            set_calls = [c for c in calls if c[0] == "set"]
            self.assertEqual(len(set_calls), 2)
            self.assertEqual(set_calls[1][3], 0x00080000)

    def test_invalid_hwnd_inactive(self) -> None:
        state = enable_click_through(0, platform_name="win32")
        self.assertFalse(state.active)
        self.assertEqual(state.backend, "none")

    def test_wayland_inactive(self) -> None:
        # Invariant under test is "no Wayland session => inactive", so the
        # session variables have to be absent from the environment.  The Rust
        # shim opens its OWN connection (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §4.7-1), so on a dev box
        # with a live compositor Path 0 really maps a layer-shell surface here
        # and leaks an undisabled ctx.  The old C++ shim took the display from
        # Qt (`nativeResourceForWindow`, `layer_shell_shim.cpp:19-21`), which
        # conftest's `QT_QPA_PLATFORM=offscreen` neutralised by accident — that
        # was the previous implementation's behaviour, not a requirement
        # (agents-rules §5).  Same strip the ABI tests use per-subprocess.
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            for var in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
                env.pop(var, None)
            state = enable_click_through(42, platform_name="wayland")
        self.assertFalse(state.active)
        self.assertEqual(state.backend, "none")


class X11ClickThroughTests(unittest.TestCase):
    def test_enable_empty_shape_and_disable_full(self) -> None:
        shape_calls = []

        class FakeX11:
            def XOpenDisplay(self, _name):
                return 0xBEEF

            def XCloseDisplay(self, _dpy):
                return 0

            def XSync(self, _dpy, _discard):
                return 0

            def XDefaultRootWindow(self, _dpy):
                return 1

        class FakeXext:
            def XShapeCombineRectangles(
                self, dpy, window, kind, x_off, y_off, rects, n, op, ordering
            ):
                shape_calls.append(
                    {
                        "window": int(window),
                        "kind": int(kind),
                        "n": int(n),
                        "op": int(op),
                    }
                )

        fake_x11 = FakeX11()
        fake_xext = FakeXext()

        with mock.patch(
            "meapet.desktop.click_through._load_x11_libs",
            return_value=(fake_x11, fake_xext),
        ):
            state = enable_click_through(
                99, platform_name="x11", width=200, height=300
            )
            self.assertTrue(state.active)
            self.assertEqual(state.backend, "x11")
            self.assertEqual(shape_calls[0]["n"], 0)  # empty input shape
            self.assertEqual(shape_calls[0]["kind"], 2)  # ShapeInput

            disable_click_through(state)
            self.assertFalse(state.active)
            self.assertEqual(shape_calls[1]["n"], 1)
            self.assertEqual(shape_calls[1]["window"], 99)


class RightButtonDownTests(unittest.TestCase):
    def test_win32_getasynckeystate(self) -> None:
        class FakeUser32:
            def __init__(self) -> None:
                self.vk = None

            def GetAsyncKeyState(self, vk):
                self.vk = int(vk)
                return 0x8000

        fake = FakeUser32()
        import ctypes

        with mock.patch.object(
            ctypes, "windll", SimpleNamespace(user32=fake), create=True
        ):
            self.assertTrue(is_right_button_down(platform_name="win32"))
            self.assertEqual(fake.vk, 0x02)


class _FakeSignal:
    def __init__(self) -> None:
        self._callback = None

    def connect(self, callback) -> None:
        self._callback = callback


class _FakeTimer:
    """Minimal QTimer stand-in so wiring tests need no Qt event loop."""

    def __init__(self, parent=None) -> None:
        self._interval = 0
        self._active = False
        self.timeout = _FakeSignal()
        self.parent = parent

    def setInterval(self, ms: int) -> None:  # noqa: N802
        self._interval = int(ms)

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def isActive(self) -> bool:  # noqa: N802
        return self._active


def _bind_mixin(host) -> None:
    """Attach PetRenderHostMixin methods so self.foo resolves on the host."""
    import types

    for name, value in PetRenderHostMixin.__dict__.items():
        if callable(value) and not isinstance(value, staticmethod):
            setattr(host, name, types.MethodType(value, host))


class StandbyHostWiringTests(unittest.TestCase):
    def _make_host(self):
        from PyQt5.QtCore import QRect

        host = SimpleNamespace(
            _standby=False,
            _click_through_state=ClickThroughState(),
            _standby_rc_timer=None,
            _standby_rc_detector=RightClickEdgeDetector(),
            _standby_menu_open=False,
            _qt_transparent_for_input=False,
            _use_live2d=False,
            config={},
            _watcher_timer=SimpleNamespace(stop=lambda: None),
            _bubble_stack=None,
            bubble=None,
            expressions=[],
            bubbles_shown=[],
            tray_refreshed=0,
            watcher_starts=0,
            hit_region_updates=0,
            menu_positions=[],
        )

        host.winId = lambda: 0xABC
        host.isVisible = lambda: True
        host.width = lambda: 120
        host.height = lambda: 160
        host.x = lambda: 40
        host.y = lambda: 40
        host.frameGeometry = lambda: QRect(40, 40, 120, 160)
        host.mapFromGlobal = lambda gp: QPoint(gp.x() - 40, gp.y() - 40)
        host.setAttribute = lambda *a, **k: None
        host.renderer = None
        host._l2d_model = None

        # Bind mixin first, then override helpers that need stubs.
        _bind_mixin(host)

        host._safe_set_expression = lambda code: host.expressions.append(code)
        host._show_bubble = (
            lambda text, duration_ms=None, mood=None: host.bubbles_shown.append(
                (text, duration_ms)
            )
        )
        host._position_bubble = lambda *a, **k: None

        def _start_watcher():
            host.watcher_starts += 1

        def _refresh_tray():
            host.tray_refreshed += 1

        def _show_menu(pos):
            host.menu_positions.append(pos)

        def _clear_bubbles():
            host.bubbles_shown.append(("__clear__", None))

        host._start_watcher_timer = _start_watcher
        host._refresh_tray_state = _refresh_tray
        host._show_context_menu = _show_menu
        host._clear_bubbles = _clear_bubbles

        # No-op stubs for methods removed in the ellipse-free refactor:
        # _apply_hit_region is no longer part of PetRenderHostMixin.
        host._apply_hit_region = lambda: None

        return host

    def test_enter_standby_enables_click_through_and_timer(self) -> None:
        host = self._make_host()
        fake_state = ClickThroughState(active=True, backend="win32", hwnd=1)

        with (
            mock.patch(
                "meapet.desktop.render_host.enable_click_through",
                return_value=fake_state,
            ) as enable,
            mock.patch(
                "meapet.desktop.render_host.disable_click_through"
            ) as disable,
            mock.patch("meapet.desktop.render_host.QTimer", _FakeTimer),
        ):
            host._toggle_standby()
            self.assertTrue(host._standby)
            enable.assert_called()
            self.assertIs(host._click_through_state, fake_state)
            self.assertIsNotNone(host._standby_rc_timer)
            self.assertTrue(host._standby_rc_timer.isActive())
            self.assertEqual(host.expressions[-1], "011")

            host._toggle_standby()
            self.assertFalse(host._standby)
            disable.assert_called()
            self.assertFalse(host._standby_rc_timer.isActive())
            self.assertEqual(host.expressions[-1], "001")
            self.assertGreaterEqual(host.watcher_starts, 1)

    def test_open_standby_menu_temp_disables_and_reenables(self) -> None:
        host = self._make_host()
        host._standby = True
        enable_calls = []
        disable_calls = []

        def fake_enable(hwnd, **kwargs):
            enable_calls.append(hwnd)
            return ClickThroughState(active=True, backend="win32", hwnd=hwnd)

        def fake_disable(state):
            disable_calls.append(state)

        with (
            mock.patch(
                "meapet.desktop.render_host.enable_click_through",
                side_effect=fake_enable,
            ),
            mock.patch(
                "meapet.desktop.render_host.disable_click_through",
                side_effect=fake_disable,
            ),
            mock.patch("meapet.desktop.render_host.QTimer", _FakeTimer),
        ):
            host._open_standby_context_menu(QPoint(10, 20))
            self.assertEqual(len(host.menu_positions), 1)
            self.assertFalse(host._standby_menu_open)
            # Should have disabled (via _set_standby_click_through(False))
            self.assertGreaterEqual(len(disable_calls), 1)
            # And re-enabled (via _set_standby_click_through(True) in finally)
            self.assertGreaterEqual(len(enable_calls), 1)

    def test_open_standby_menu_does_not_reenable_after_leave(self) -> None:
        host = self._make_host()
        host._standby = True
        enable_calls = []

        def fake_enable(hwnd, **kwargs):
            enable_calls.append(hwnd)
            return ClickThroughState(active=True, backend="win32", hwnd=hwnd)

        def leave_standby_via_menu(pos):
            host._standby = False

        host._show_context_menu = leave_standby_via_menu

        with (
            mock.patch(
                "meapet.desktop.render_host.enable_click_through",
                side_effect=fake_enable,
            ),
            mock.patch("meapet.desktop.render_host.disable_click_through"),
            mock.patch("meapet.desktop.render_host.QTimer", _FakeTimer),
        ):
            host._open_standby_context_menu(QPoint(1, 1))
            # standby was set to False inside the menu; finally block should
            # NOT re-enable click-through.
            self.assertEqual(enable_calls, [])

    def test_poll_fires_menu_on_edge(self) -> None:
        host = self._make_host()
        host._standby = True
        host._standby_rc_detector = RightClickEdgeDetector()
        opened = []

        def capture_open(pos):
            opened.append(pos)

        host._open_standby_context_menu = capture_open

        with (
            mock.patch(
                "meapet.desktop.render_host.is_right_button_down",
                side_effect=[False, True],
            ),
            mock.patch("PyQt5.QtGui.QCursor.pos", return_value=QPoint(60, 60)),
        ):
            host._poll_standby_right_click()
            self.assertEqual(opened, [])
            host._poll_standby_right_click()
            self.assertEqual(len(opened), 1)

    def test_poll_skips_when_menu_open_or_not_standby(self) -> None:
        host = self._make_host()
        host._standby = False
        host._open_standby_context_menu = mock.Mock()
        with mock.patch(
            "meapet.desktop.render_host.is_right_button_down", return_value=True
        ):
            host._poll_standby_right_click()
            host._open_standby_context_menu.assert_not_called()

        host._standby = True
        host._standby_menu_open = True
        host._poll_standby_right_click()
        host._open_standby_context_menu.assert_not_called()


class DialoguePassthroughTests(unittest.TestCase):
    def test_dialogue_box_exposes_mouse_passthrough_api(self) -> None:
        from meapet.desktop.widgets import DialogueBox

        self.assertTrue(callable(getattr(DialogueBox, "set_mouse_passthrough", None)))
        source = DialogueBox.set_mouse_passthrough.__code__.co_names
        self.assertIn("WA_TransparentForMouseEvents", source)


if __name__ == "__main__":
    unittest.main()

