"""全屏、区域与 Windows 应用窗口截图底座。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


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

    def test_existing_screen_watcher_reuses_scoped_capture_backend(self):
        from meapet.watcher.capture import CapturedImage
        from meapet.watcher.screen import ScreenWatcher

        watcher = ScreenWatcher(
            capture_scope="region",
            capture_region={"x": 1, "y": 2, "width": 30, "height": 40},
            capture_application="",
        )
        captured = CapturedImage(
            image=_Image(),
            metadata={"scope": "region", "width": 100, "height": 80},
        )
        with mock.patch(
            "meapet.watcher.screen.capture_screen_image",
            return_value=captured,
        ) as capture:
            image = watcher._capture_image()

        self.assertIs(image, captured.image)
        capture.assert_called_once_with(
            scope="region",
            region={"x": 1, "y": 2, "width": 30, "height": 40},
            application="",
        )

    def test_inherit_uses_main_backend_once_and_never_writes_screenshot(self):
        from meapet.agent.base import TurnCompleted
        from meapet.conversation.output_protocol import ParseResult
        from meapet.conversation.types import ReplySegment
        from meapet.watcher.screen import ScreenWatcher

        class Adapter:
            def __init__(self):
                self.requests = []

            async def stream_turn(self, request):
                self.requests.append(request)
                yield TurnCompleted(
                    request.turn_id,
                    ParseResult(
                        (
                            ReplySegment(
                                display_text="看到了喵",
                                voice_text="看到了喵",
                                voice_language="zh",
                                mood="curious",
                                tts_style="轻声",
                            ),
                        ),
                        (),
                        True,
                        "meapet",
                    ),
                )

        adapter = Adapter()
        watcher = ScreenWatcher(mode="inherit")
        watcher.configure_reply(
            adapter,
            frontend_context={"frontend_capabilities": {}},
            tts_enabled=True,
        )
        watcher._capture_image = mock.Mock(return_value=Image.new("RGB", (64, 64)))
        watcher._request_visual_observation = mock.Mock(
            side_effect=AssertionError("inherit must not call a relay model")
        )
        results = []
        watcher.result_ready.connect(lambda text, mood: results.append((text, mood)))

        with tempfile.TemporaryDirectory() as td, mock.patch("os.getcwd", return_value=td):
            watcher.run()
            self.assertFalse((Path(td) / "screenshots").exists())

        self.assertEqual(results, [("看到了喵", "curious")])
        self.assertEqual(len(adapter.requests), 1)
        self.assertEqual(len(adapter.requests[0].attachments), 1)
        self.assertEqual(watcher.last_voice_language, "zh")
        self.assertEqual(watcher.last_tts_style, "轻声")

    def test_relay_observes_then_sends_only_structured_text_to_main_backend(self):
        from meapet.agent.base import TurnCompleted
        from meapet.conversation.output_protocol import ParseResult
        from meapet.conversation.types import ReplySegment
        from meapet.watcher.screen import ScreenWatcher

        class Adapter:
            def __init__(self):
                self.requests = []

            async def stream_turn(self, request):
                self.requests.append(request)
                yield TurnCompleted(
                    request.turn_id,
                    ParseResult(
                        (
                            ReplySegment(
                                display_text="原来在看文档喵",
                                voice_text="原来在看文档喵",
                                voice_language="zh",
                                mood="neutral",
                                tts_style="",
                            ),
                        ),
                        (),
                        True,
                        "meapet",
                    ),
                )

        adapter = Adapter()
        watcher = ScreenWatcher(mode="relay", backend="ollama")
        watcher.configure_reply(adapter, frontend_context={}, tts_enabled=False)
        watcher._capture_image = mock.Mock(return_value=Image.new("RGB", (64, 64)))
        watcher._request_visual_observation = mock.Mock(
            return_value=(
                '{"summary":"浏览器文档","application":"Firefox",'
                '"activity":"reading","notable_text":[],"sensitive":false}'
            )
        )

        watcher.run()

        watcher._request_visual_observation.assert_called_once()
        self.assertEqual(adapter.requests[0].attachments, ())
        self.assertIn('"activity":"reading"', adapter.requests[0].user_text)


if __name__ == "__main__":
    unittest.main()
