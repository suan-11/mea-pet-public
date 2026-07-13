"""全屏、区域与 Windows 应用窗口截图底座。"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock


class _Image:
    size = (100, 80)
    mode = "RGB"


class TestScreenCapture(unittest.TestCase):
    def test_full_screen_and_region_use_explicit_imagegrab_bounds(self):
        from meapet.watcher.capture import capture_screen_image

        image = _Image()
        with mock.patch(
            "meapet.watcher.capture.ImageGrab.grab",
            return_value=image,
        ) as grab:
            full = capture_screen_image(scope="full_screen")
            region = capture_screen_image(
                scope="region",
                region={"x": 10, "y": 20, "width": 300, "height": 200},
            )

        self.assertIs(full.image, image)
        self.assertEqual(full.metadata["scope"], "full_screen")
        self.assertEqual(grab.call_args_list[0].kwargs, {"all_screens": True})
        self.assertEqual(
            grab.call_args_list[1].kwargs,
            {"bbox": (10, 20, 310, 220), "all_screens": True},
        )
        self.assertEqual(region.metadata["width"], 100)
        self.assertNotIn("path", repr(region.metadata).lower())

    def test_region_rejects_missing_or_non_positive_dimensions(self):
        from meapet.watcher.capture import CaptureError, capture_screen_image

        for region in (None, {}, {"x": 0, "y": 0, "width": 0, "height": 2}):
            with self.subTest(region=region), self.assertRaises(CaptureError) as ctx:
                capture_screen_image(scope="region", region=region)
            self.assertEqual(ctx.exception.code, "invalid_region")

    def test_application_capture_uses_visible_windows_and_exact_win32_rect(self):
        from meapet.watcher.capture import capture_screen_image

        titles = {101: "Visual Studio Code", 102: "Hidden Window"}

        def enum_windows(callback, extra):
            callback(101, extra)
            callback(102, extra)

        win32gui = SimpleNamespace(
            EnumWindows=enum_windows,
            IsWindowVisible=lambda hwnd: hwnd == 101,
            GetWindowText=lambda hwnd: titles[hwnd],
            IsIconic=lambda _hwnd: False,
            GetWindowRect=lambda _hwnd: (50, 60, 850, 660),
        )
        image = _Image()
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(sys.modules, {"win32gui": win32gui}),
            mock.patch(
                "meapet.watcher.capture.ImageGrab.grab",
                return_value=image,
            ) as grab,
        ):
            result = capture_screen_image(
                scope="application",
                application="code",
            )

        grab.assert_called_once_with(
            bbox=(50, 60, 850, 660),
            all_screens=True,
        )
        self.assertEqual(result.metadata["application"], "Visual Studio Code")
        self.assertNotIn("hwnd", result.metadata)

    def test_application_capture_is_typed_when_unsupported_or_window_missing(self):
        from meapet.watcher.capture import CaptureError, capture_screen_image

        with mock.patch.object(sys, "platform", "linux"):
            with self.assertRaises(CaptureError) as unsupported:
                capture_screen_image(scope="application", application="Code")
        self.assertEqual(unsupported.exception.code, "unsupported_scope")

        win32gui = SimpleNamespace(
            EnumWindows=lambda callback, extra: None,
            IsWindowVisible=lambda _hwnd: True,
            GetWindowText=lambda _hwnd: "",
            IsIconic=lambda _hwnd: False,
            GetWindowRect=lambda _hwnd: (0, 0, 1, 1),
        )
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(sys.modules, {"win32gui": win32gui}),
            self.assertRaises(CaptureError) as missing,
        ):
            capture_screen_image(scope="application", application="Code")
        self.assertEqual(missing.exception.code, "window_not_found")


if __name__ == "__main__":
    unittest.main()
