"""UI 重构的视觉语义、可访问性与组件契约。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
)
from PyQt5.QtGui import QKeyEvent, QPainterPath  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class _MemoryStub:
    """StatusPanel 的最小只读内存替身。"""

    def get_affection(self) -> int:
        return 42

    def get_affection_tier(self) -> tuple[int, str, str]:
        return 1, "熟悉", "今天也一起聊聊吧"

    def get_mood(self) -> str:
        return "开心"

    def get_total_chats(self) -> int:
        return 18

    def get_total_days(self) -> int:
        return 3

    def get_today_chat_count(self) -> int:
        return 4

    def get_important_memories(self, _limit: int) -> list[str]:
        return ["你喜欢安静的夜晚"]


class UiRefactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self._widgets):
            try:
                widget.close()
            except RuntimeError:
                pass

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def test_semantic_palette_meets_text_contrast_targets(self) -> None:
        from meapet.ui_theme import PALETTE, contrast_ratio

        required_pairs = (
            ("text_primary", "surface", 4.5),
            ("text_secondary", "surface", 4.5),
            ("text_muted", "canvas", 4.5),
            ("on_primary", "primary", 4.5),
            ("success", "surface", 4.5),
            ("danger", "surface", 4.5),
        )
        for foreground, background, minimum in required_pairs:
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual(
                    contrast_ratio(PALETTE[foreground], PALETTE[background]),
                    minimum,
                )

    def test_theme_helpers_validate_color_inputs(self) -> None:
        from meapet.ui_theme import contrast_ratio, rgba

        self.assertEqual(rgba("#FF91B4", 128), "rgba(255, 145, 180, 128)")
        self.assertAlmostEqual(contrast_ratio("#FFFFFF", "#000000"), 21.0)
        with self.assertRaises(ValueError):
            rgba("#FFF", 128)
        with self.assertRaises(ValueError):
            rgba("#FFFFFF", 256)
        with self.assertRaises(ValueError):
            contrast_ratio("invalid", "#000000")

    def test_bundled_cute_display_font_loads_with_a_safe_body_font(self) -> None:
        from meapet.ui_theme import (
            BODY_FONT_NAME,
            BUNDLED_DISPLAY_FONT_PATH,
            DISPLAY_FONT_FAMILY,
            FONT_FAMILY,
            ensure_application_fonts,
        )

        self.assertTrue(BUNDLED_DISPLAY_FONT_PATH.is_file())
        self.assertEqual(BUNDLED_DISPLAY_FONT_PATH.name, "LXGWWenKai-Regular.ttf")
        self.assertEqual(DISPLAY_FONT_FAMILY, '"LXGW WenKai"')
        self.assertEqual(BODY_FONT_NAME, "LXGW WenKai")
        self.assertEqual(FONT_FAMILY, f'"{BODY_FONT_NAME}"')
        self.assertIn("LXGW WenKai", ensure_application_fonts())

        from meapet.desktop.theme import CHAT_COMPOSER_STYLE, DIALOGUE_STYLE

        self.assertIn(
            f"QLabel#ComposerTitle {{\n        color: ",
            CHAT_COMPOSER_STYLE,
        )
        self.assertIn(f"font-family: {DISPLAY_FONT_FAMILY};", CHAT_COMPOSER_STYLE)
        self.assertIn(f"font-family: {DISPLAY_FONT_FAMILY};", DIALOGUE_STYLE)

    def test_font_scaling_is_clamped_and_does_not_accumulate(self) -> None:
        from meapet.config.store import normalize_config
        from meapet.ui_theme import (
            apply_ui_font_scale,
            scale_stylesheet_font_sizes,
            set_ui_font_scale,
        )

        self.addCleanup(set_ui_font_scale, 1.0)
        self.assertEqual(
            scale_stylesheet_font_sizes("font-size: 15px;", 1.2),
            "font-size: 18px;",
        )

        label = self._track(QLabel("字号预览"))
        label.setStyleSheet("font-size: 15px;")
        apply_ui_font_scale(label, 1.2)
        self.assertIn("font-size: 18px;", label.styleSheet())
        apply_ui_font_scale(label, 1.4)
        self.assertIn("font-size: 21px;", label.styleSheet())
        self.assertNotIn("font-size: 25px;", label.styleSheet())

        set_ui_font_scale(1.2)
        from meapet.desktop.widgets import DialogueBox

        dialogue = self._track(DialogueBox())
        self.assertIn(
            "font-size: 18px;",
            dialogue._container.styleSheet(),
        )

        self.assertEqual(normalize_config({})["display"]["font_scale"], 1.0)
        self.assertEqual(
            normalize_config({"display": {"font_scale": 9}})["display"][
                "font_scale"
            ],
            1.5,
        )
        self.assertEqual(
            normalize_config({"display": {"font_scale": "invalid"}})[
                "display"
            ]["font_scale"],
            1.0,
        )

    def test_configuration_window_uses_tabs_and_accessible_core_actions(self) -> None:
        from wizard.app import SetupWizard
        from wizard.styles import MIN_TARGET_SIZE

        wizard = self._track(SetupWizard())
        self.assertGreaterEqual(wizard.minimumWidth(), 680)
        self.assertGreater(wizard.maximumWidth(), wizard.minimumWidth())
        self.assertEqual(wizard.objectName(), "WizardRoot")
        self.assertEqual(wizard.container.objectName(), "WizardShell")
        self.assertIsInstance(wizard.tabs, QTabWidget)
        self.assertEqual(
            [wizard.tabs.tabText(index) for index in range(wizard.tabs.count())],
            ["环境", "对话", "语音", "屏幕识图"],
        )
        self.assertFalse(hasattr(wizard, "progress"))
        self.assertFalse(hasattr(wizard, "back_btn"))
        self.assertFalse(hasattr(wizard, "next_btn"))

        for button in (wizard.close_btn, wizard.save_btn):
            with self.subTest(button=button.text()):
                self.assertGreaterEqual(button.minimumWidth(), MIN_TARGET_SIZE)
                self.assertGreaterEqual(button.minimumHeight(), MIN_TARGET_SIZE)
                self.assertTrue(button.accessibleName())

    def test_configuration_exposes_persistent_font_scaling_with_live_preview(self) -> None:
        from meapet.ui_theme import set_ui_font_scale
        from wizard.app import SetupWizard

        self.addCleanup(set_ui_font_scale, 1.0)
        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()

        self.assertIsInstance(wizard.font_scale_slider, QSlider)
        self.assertEqual(wizard.font_scale_slider.minimum(), 80)
        self.assertEqual(wizard.font_scale_slider.maximum(), 150)
        self.assertEqual(wizard.font_scale_slider.singleStep(), 5)
        self.assertTrue(wizard.font_scale_slider.accessibleName())

        wizard.font_scale_slider.setValue(125)
        self.assertEqual(wizard.font_scale_value.text(), "125%")
        self.assertIn("font-size: 18px;", wizard.styleSheet())
        wizard._existing_config = {
            "display": {
                "size_factor": 1.33,
                "custom_display_key": "keep-me",
            }
        }
        display_config = wizard.collect_config()["display"]
        self.assertEqual(display_config["font_scale"], 1.25)
        self.assertEqual(display_config["size_factor"], 1.33)
        self.assertEqual(display_config["custom_display_key"], "keep-me")

    def test_environment_check_timer_is_owned_by_its_page(self) -> None:
        from wizard.page_env import EnvCheckPage

        with patch(
            "wizard.page_env.QTimer.singleShot",
            side_effect=AssertionError("环境检测不能留下无父对象的延迟回调"),
        ):
            page = self._track(EnvCheckPage())

        self.assertIs(page._check_timer.parent(), page)
        self.assertTrue(page._check_timer.isSingleShot())
        self.assertTrue(page._check_timer.isActive())
        page._check_timer.stop()

    def test_environment_check_stops_if_its_widgets_are_destroyed(self) -> None:
        from wizard.page_env import EnvCheckPage

        page = self._track(EnvCheckPage())
        page._check_timer.stop()
        page._checklist = [("requests", "", True)]
        deleted_widget_error = RuntimeError(
            "wrapped C/C++ object of type QLabel has been deleted"
        )

        with patch(
            "wizard.page_env.check_installed",
            return_value=True,
        ), patch.object(
            page,
            "_set_item_status",
            side_effect=deleted_widget_error,
        ):
            page._run_checks()

    def test_configuration_startup_callbacks_use_owned_timers(self) -> None:
        from wizard.app import SetupWizard

        with patch(
            "wizard.app.QTimer.singleShot",
            side_effect=AssertionError("配置页启动回调必须绑定到所属窗口"),
        ):
            wizard = self._track(SetupWizard())

        timers = (
            wizard.env_page._check_timer,
            wizard.llm_page._status_timer,
            wizard._load_timer,
            *wizard.tts_page._startup_timers,
        )
        self.assertTrue(wizard.testAttribute(Qt.WA_DeleteOnClose))
        self.assertTrue(all(timer.parent() is not None for timer in timers))
        self.assertTrue(all(timer.isSingleShot() for timer in timers))
        for timer in timers:
            timer.stop()

    def test_configuration_tabs_mark_and_clear_missing_required_fields(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        wizard.tts_page.enable_cb.setChecked(False)

        wizard.llm_page.radio_ds.setChecked(True)
        wizard.key_page_ds.key_input.clear()
        wizard._refresh_required_tabs()
        self.assertFalse(wizard.tabs.tabIcon(wizard.TAB_CHAT).isNull())
        self.assertIn("缺少", wizard.tabs.tabToolTip(wizard.TAB_CHAT))
        self.assertIn("对话", wizard.config_status.text())

        wizard.key_page_ds.key_input.setText("deepseek-test-key")
        wizard._refresh_required_tabs()
        self.assertTrue(wizard.tabs.tabIcon(wizard.TAB_CHAT).isNull())
        self.assertNotIn("对话", wizard.config_status.text())

        wizard.llm_page.radio_ollama.setChecked(True)
        wizard.tts_page.enable_cb.setChecked(True)
        wizard.tts_page.set_engine("mimo")
        wizard.tts_page.mimo_api_key_input.clear()
        wizard._refresh_required_tabs()
        self.assertFalse(wizard.tabs.tabIcon(wizard.TAB_VOICE).isNull())
        self.assertIn("MiMo TTS API Key", wizard.tabs.tabToolTip(wizard.TAB_VOICE))

        wizard.tts_page.mimo_api_key_input.setText("mimo-tts-test-key")
        wizard._refresh_required_tabs()
        self.assertTrue(wizard.tabs.tabIcon(wizard.TAB_VOICE).isNull())

        wizard.vision_page.enable_cb.setChecked(True)
        wizard.vision_page.backend_combo.setCurrentIndex(1)
        wizard.vision_page.api_key_input.setText("vision-test-key")
        wizard.vision_page.allow_cloud_cb.setChecked(False)
        wizard._refresh_required_tabs()
        self.assertFalse(wizard.tabs.tabIcon(wizard.TAB_VISION).isNull())
        self.assertIn("云端识图授权", wizard.tabs.tabToolTip(wizard.TAB_VISION))

        wizard.vision_page.allow_cloud_cb.setChecked(True)
        wizard._refresh_required_tabs()
        self.assertTrue(wizard.tabs.tabIcon(wizard.TAB_VISION).isNull())

    def test_wizard_form_controls_are_keyboard_ready_and_named(self) -> None:
        from wizard.app import SetupWizard
        from wizard.styles import MIN_TARGET_SIZE

        wizard = self._track(SetupWizard())
        pages = (
            wizard.key_page_ds,
            wizard.key_page_mimo,
            wizard.tts_page,
            wizard.vision_page,
        )
        controls = []
        for page in pages:
            controls.extend(page.findChildren(QLineEdit))
            controls.extend(page.findChildren(QComboBox))

        self.assertGreater(len(controls), 10)
        for control in controls:
            with self.subTest(control=control.objectName() or type(control).__name__):
                self.assertGreaterEqual(control.minimumHeight(), MIN_TARGET_SIZE)
                self.assertTrue(control.accessibleName())
                self.assertNotEqual(control.focusPolicy(), 0)

        tab_chain = [wizard.save_btn]
        for button in tab_chain:
            self.assertNotEqual(button.focusPolicy(), 0)

    def test_chat_composer_exposes_send_close_and_inline_feedback(self) -> None:
        from meapet.desktop.chat_input import ChatInputBox
        from meapet.ui_theme import MIN_TARGET_SIZE

        composer = self._track(ChatInputBox())
        self.assertLessEqual(composer.width(), 480)
        self.assertLessEqual(composer.height(), 120)
        self.assertEqual(composer.input.accessibleName(), "消息内容")
        self.assertTrue(composer.feedback_label.accessibleName())

        for button in (composer.send_button, composer.close_button):
            with self.subTest(button=button.text()):
                self.assertGreaterEqual(button.minimumWidth(), MIN_TARGET_SIZE)
                self.assertGreaterEqual(button.minimumHeight(), MIN_TARGET_SIZE)
                self.assertTrue(button.accessibleName())

        composer.input.clear()
        composer._submit()
        self.assertTrue(composer.feedback_label.text())
        self.assertTrue(composer.feedback_label.isVisibleTo(composer))
        self.assertFalse(composer._closing)

    def test_chat_composer_submission_motion_and_escape_paths(self) -> None:
        from meapet.desktop.chat_input import ChatInputBox

        composer = self._track(ChatInputBox())
        composer._anim_timer.stop()

        composer._opacity = 0.95
        composer._animate_in()
        self.assertEqual(composer._opacity, 1.0)

        composer.feedback_label.setText("旧提示")
        composer.input.setText("  晚上好  ")
        self.assertEqual(composer.feedback_label.text(), "")
        submitted = []
        composer.text_submitted.connect(submitted.append)
        composer._submit()
        self.assertEqual(submitted, ["晚上好"])
        self.assertTrue(composer._closing)
        composer._close_with_fade()

        composer._opacity = 0.2
        composer._fade_step = 0.1
        composer._fade_out()
        self.assertAlmostEqual(composer._opacity, 0.1)
        composer._fade_out()
        self.assertEqual(composer._opacity, 0.0)

        second = self._track(ChatInputBox())
        escape = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        with patch.object(second, "_close_with_fade") as close_with_fade:
            second.keyPressEvent(escape)
            close_with_fade.assert_called_once_with()

        second._closing = True
        before = second._opacity
        second._animate_in()
        self.assertEqual(second._opacity, before)

        with patch.dict(os.environ, {"MEAPET_REDUCED_MOTION": "1"}):
            reduced = self._track(ChatInputBox())
        self.assertTrue(reduced._reduced_motion)
        self.assertEqual(reduced._opacity, 1.0)
        reduced._close_with_fade()
        self.assertTrue(reduced._closing)

    def test_chat_composer_is_hidden_before_submission_is_emitted(self) -> None:
        from meapet.desktop.chat_input import ChatInputBox

        composer = self._track(ChatInputBox())
        composer._anim_timer.stop()
        composer.setWindowOpacity(1.0)
        composer.show()
        QApplication.processEvents()

        visible_during_signal = []
        composer.text_submitted.connect(
            lambda _text: visible_during_signal.append(composer.isVisible())
        )
        composer.input.setText("不要让输入框挡住回复")
        composer._submit()

        self.assertEqual(visible_during_signal, [False])
        self.assertFalse(composer.isVisible())
        self.assertTrue(composer._closing)

    def test_opening_chat_hides_an_existing_dialogue_bubble(self) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin

        class SignalStub:
            def connect(self, callback):
                self.callback = callback

        class InputStub:
            text_submitted = SignalStub()

            @staticmethod
            def width():
                return 480

            @staticmethod
            def height():
                return 112

            def move(self, point_or_x, y=None):
                self.position = (point_or_x, y)

            def show(self):
                self.shown = True

        host = type("ChatHost", (), {})()
        host.bubble = unittest.mock.Mock()
        host.pos = unittest.mock.Mock(return_value=QPoint(600, 500))
        host.width = unittest.mock.Mock(return_value=300)
        host.height = unittest.mock.Mock(return_value=360)
        host._on_input_submit = unittest.mock.Mock()

        input_box = InputStub()
        with patch("meapet.desktop.chat_flow.ChatInputBox", return_value=input_box):
            PetChatFlowMixin._start_chat(host)

        host.bubble.hide.assert_called_once_with()
        self.assertTrue(input_box.shown)
        self.assertIs(host._chat_input, input_box)

    def test_accessibility_helpers_cover_legacy_and_unlabeled_controls(self) -> None:
        from wizard.styles import MIN_TARGET_SIZE, prepare_accessible_page, set_status

        class LegacyStatus:
            def __init__(self) -> None:
                self.text = ""
                self.style_sheet = ""

            def setText(self, text: str) -> None:
                self.text = text

            def setStyleSheet(self, style_sheet: str) -> None:
                self.style_sheet = style_sheet

        legacy = LegacyStatus()
        set_status(legacy, "warning", "需要处理")
        self.assertEqual(legacy.text, "需要处理")
        self.assertIn("color:", legacy.style_sheet)

        label = QLabel()
        set_status(label, "success", "就绪")
        self.assertEqual(label.property("status"), "success")

        root = self._track(QWidget())
        layout = QVBoxLayout(root)
        icon_button = QPushButton("×")
        icon_button.setToolTip("关闭")
        icon_button.setFixedWidth(32)
        line_edit = QLineEdit()
        text_edit = QTextEdit()
        plain_edit = QPlainTextEdit()
        combo = QComboBox()
        slider = QSlider(Qt.Horizontal)
        for control in (icon_button, line_edit, text_edit, plain_edit, combo, slider):
            layout.addWidget(control)

        prepare_accessible_page(root)
        for control in (icon_button, line_edit, text_edit, plain_edit, combo, slider):
            self.assertGreaterEqual(control.minimumHeight(), MIN_TARGET_SIZE)
            self.assertTrue(control.accessibleName())
        self.assertGreaterEqual(icon_button.minimumWidth(), MIN_TARGET_SIZE)

    def test_desktop_surfaces_share_semantic_structure(self) -> None:
        from meapet.desktop.splash import StartupSplash
        from meapet.desktop.status_panel import StatusPanel
        from meapet.desktop.widgets import DialogueBox, SizeScaleDialog
        from meapet.ui_theme import MIN_TARGET_SIZE

        splash = self._track(StartupSplash())
        self.assertEqual(splash.card.objectName(), "SplashCard")
        self.assertTrue(splash.status.accessibleName())
        self.assertTrue(splash.progress.accessibleName())

        dialogue = self._track(DialogueBox())
        self.assertEqual(dialogue.text_label.accessibleName(), "桌宠回复")
        self.assertEqual(dialogue._container.objectName(), "DialogueBubble")
        self.assertFalse(hasattr(dialogue, "name_label"))
        self.assertFalse(hasattr(dialogue, "_deco_line"))

        panel = self._track(StatusPanel(_MemoryStub()))
        self.assertGreaterEqual(panel.close_button.minimumWidth(), MIN_TARGET_SIZE)
        self.assertGreaterEqual(panel.close_button.minimumHeight(), MIN_TARGET_SIZE)
        self.assertTrue(panel.close_button.accessibleName())
        self.assertGreaterEqual(
            len(
                [
                    card
                    for card in panel.findChildren(QFrame)
                    if card.objectName() == "StatusCard"
                ]
            ),
            3,
        )

        dialog = self._track(SizeScaleDialog(1.0))
        self.assertTrue(dialog._slider.accessibleName())
        for button in dialog.findChildren(QAbstractButton):
            with self.subTest(button=button.text()):
                self.assertGreaterEqual(button.minimumHeight(), MIN_TARGET_SIZE)
                self.assertTrue(button.accessibleName())

    def test_splash_success_failure_and_completion_states(self) -> None:
        from meapet.desktop.splash import StartupSplash

        splash = self._track(StartupSplash())
        splash.set_steps([("加载资源", lambda: "ready")])
        self.assertEqual(splash.progress.maximum(), 1)

        with patch("meapet.desktop.splash.QTimer") as timer:
            splash._run_next()
            timer.singleShot.assert_called_once()
            self.assertEqual(splash.result, "ready")
            self.assertEqual(splash._index, 1)

            timer.reset_mock()
            splash._run_next()
            timer.singleShot.assert_called_once()
            self.assertEqual(splash.status.property("status"), "success")

        finished = []
        splash.finished.connect(lambda: finished.append(True))
        splash._emit_finished()
        self.assertEqual(finished, [True])

        failed_splash = self._track(StartupSplash())

        def fail() -> None:
            raise RuntimeError("资源损坏")

        failed_splash.set_steps([("加载失败项", fail)])
        failures = []
        failed_splash.failed.connect(failures.append)
        with patch("meapet.desktop.splash.QTimer"):
            failed_splash._run_next()
        self.assertEqual(failures, ["资源损坏"])
        self.assertEqual(failed_splash.status.property("status"), "error")

        empty = self._track(StartupSplash())
        empty.set_steps([])
        with (
            patch.object(empty, "show") as show,
            patch.object(empty, "raise_") as raise_window,
            patch("meapet.desktop.splash.QTimer") as timer,
        ):
            empty.start()
            show.assert_called_once_with()
            raise_window.assert_called_once_with()
            timer.singleShot.assert_called_once()

    def test_dialogue_motion_and_scale_dialog_preview_paths(self) -> None:
        from meapet.desktop.widgets import (
            DIALOGUE_MAX_HEIGHT,
            DIALOGUE_MAX_WIDTH,
            DialogueBox,
            SizeScaleDialog,
        )

        dialogue = self._track(DialogueBox())
        self.assertEqual(dialogue.tail_side, "bottom")
        dialogue.set_tail("left", 72)
        self.assertEqual(dialogue.tail_side, "left")
        self.assertEqual(dialogue.tail_anchor, 72)
        with self.assertRaises(ValueError):
            dialogue.set_tail("diagonal")

        with patch.object(dialogue, "show"), patch.object(dialogue, "raise_"):
            dialogue.show_text("【happy】今天也辛苦啦", duration_ms=1000)
        self.assertEqual(dialogue.text_label.text(), "今天也辛苦啦")
        self.assertTrue(dialogue._hide_timer.isActive())
        short_size = dialogue.size()
        self.assertLess(short_size.width(), 260)
        self.assertLess(short_size.height(), 130)

        long_text = "这是一段需要自动换行的较长对话。" * 120
        with patch.object(dialogue, "show"), patch.object(dialogue, "raise_"):
            dialogue.show_text(long_text, duration_ms=0)
        self.assertLessEqual(dialogue.width(), DIALOGUE_MAX_WIDTH)
        self.assertLessEqual(dialogue.height(), DIALOGUE_MAX_HEIGHT)
        self.assertGreater(dialogue.width(), short_size.width())
        self.assertGreater(dialogue.text_label.height(), dialogue.text_scroll.height())

        from meapet.desktop.theme import DIALOGUE_STYLE

        self.assertIn("QFrame#DialogueBubble", DIALOGUE_STYLE)
        self.assertNotIn("DialogueName", DIALOGUE_STYLE)
        self.assertNotIn("DialogueAccent", DIALOGUE_STYLE)

        dialogue._fade_out = True
        dialogue._opacity = 0.05
        dialogue._fade_step = 0.1
        dialogue._animate()
        self.assertEqual(dialogue._opacity, 0.0)
        self.assertFalse(dialogue.isVisible())

        dialogue._fade_out = False
        dialogue._opacity = 0.95
        dialogue._fade_step = 0.1
        dialogue._animate()
        self.assertEqual(dialogue._opacity, 1.0)
        dialogue._start_fadeout()
        self.assertTrue(dialogue._fade_out)

        previews = []
        pet = self._track(QWidget())
        pet._size_factor_preview = previews.append
        dialog = self._track(SizeScaleDialog(1.25, pet))
        dialog._on_slider(150)
        self.assertEqual(dialog.get_value(), 1.5)
        self.assertEqual(previews[-1], 1.5)
        dialog._reset()
        self.assertEqual(dialog._slider.value(), 100)
        dialog._on_slider(180)
        dialog.reject()
        self.assertEqual(dialog.get_value(), 1.25)
        self.assertEqual(previews[-1], 1.25)

    def test_speech_bubble_tail_curves_outward_from_the_lower_corner(self) -> None:
        from meapet.desktop.widgets import (
            DIALOGUE_TAIL_DEPTH,
            DIALOGUE_TAIL_REACH,
            SpeechBubbleFrame,
        )

        right_tail = self._track(SpeechBubbleFrame())
        right_tail.setFixedSize(260, 124)
        right_tail.set_tail("bottom", 224)
        right_body = right_tail._body_rect()
        right_path = right_tail._tail_path(right_body)
        right_bounds = right_path.boundingRect()

        self.assertLessEqual(DIALOGUE_TAIL_REACH, 24)
        self.assertLessEqual(DIALOGUE_TAIL_DEPTH, 28)
        self.assertLessEqual(
            right_body.right(),
            right_tail.width() - DIALOGUE_TAIL_REACH,
        )
        self.assertGreater(
            right_bounds.right(),
            right_body.right() + DIALOGUE_TAIL_REACH * 0.7,
        )
        self.assertGreater(
            right_bounds.bottom(),
            right_body.bottom() + DIALOGUE_TAIL_DEPTH * 0.75,
        )
        self.assertGreaterEqual(
            sum(
                right_path.elementAt(index).type
                == QPainterPath.CurveToElement
                for index in range(right_path.elementCount())
            ),
            2,
        )
        self.assertTrue(
            right_path.contains(
                QPointF(right_body.right() - 4, right_body.bottom() - 6)
            )
        )

        left_tail = self._track(SpeechBubbleFrame())
        left_tail.setFixedSize(260, 124)
        left_tail.set_tail("bottom", 36)
        left_body = left_tail._body_rect()
        left_path = left_tail._tail_path(left_body)
        left_bounds = left_path.boundingRect()

        self.assertGreaterEqual(left_body.left(), DIALOGUE_TAIL_REACH)
        self.assertLess(
            left_bounds.left(),
            left_body.left() - DIALOGUE_TAIL_REACH * 0.7,
        )
        self.assertAlmostEqual(left_body.width(), right_body.width())
        self.assertTrue(
            left_path.contains(
                QPointF(left_body.left() + 4, left_body.bottom() - 6)
            )
        )

    def test_dialogue_motion_and_fade_are_slow_enough_to_read(self) -> None:
        from meapet.desktop.widgets import (
            DIALOGUE_ENTRY_OFFSET,
            DIALOGUE_FADE_DURATION_MS,
            DIALOGUE_FADE_FRAME_MS,
            DIALOGUE_MOTION_DURATION_MS,
            DialogueBox,
        )

        self.assertGreaterEqual(DIALOGUE_MOTION_DURATION_MS, 480)
        self.assertLessEqual(DIALOGUE_MOTION_DURATION_MS, 650)
        self.assertGreaterEqual(DIALOGUE_ENTRY_OFFSET, 20)
        self.assertGreaterEqual(DIALOGUE_FADE_DURATION_MS, 1200)
        self.assertLessEqual(DIALOGUE_FADE_DURATION_MS, 1800)

        dialogue = self._track(DialogueBox())
        dialogue.show_text("这次会慢慢淡出。", duration_ms=0)
        dialogue._start_fadeout()
        for _ in range(500 // DIALOGUE_FADE_FRAME_MS):
            dialogue._animate()

        self.assertTrue(dialogue.isVisible())
        self.assertGreater(dialogue.visualOpacity, 0.45)
        self.assertLess(dialogue.visualOpacity, 0.8)

        reduced_target = QPoint(180, 120)
        reduced = self._track(DialogueBox())
        reduced.show_text("减少动态效果。", duration_ms=0)
        reduced.mark_stack_entry()
        with patch.dict(os.environ, {"MEAPET_REDUCED_MOTION": "1"}):
            reduced.animate_to(reduced_target, 0.76, animate=True)
        self.assertEqual(reduced.pos(), reduced_target)
        self.assertAlmostEqual(reduced.visualOpacity, 0.76)
        self.assertEqual(
            reduced._position_animation.state(),
            QAbstractAnimation.Stopped,
        )

    def test_dialogue_fades_gradually_before_it_is_dismissed(self) -> None:
        from meapet.desktop.widgets import (
            DIALOGUE_FADE_DURATION_MS,
            DIALOGUE_FADE_FRAME_MS,
            DialogueBox,
        )

        dialogue = self._track(DialogueBox())
        self.assertIsInstance(
            dialogue._container.graphicsEffect(),
            QGraphicsOpacityEffect,
        )
        dismissed = []
        dialogue.dismissed.connect(lambda: dismissed.append(True))
        dialogue.show_text("我会慢慢消失。", duration_ms=0)

        dialogue._start_fadeout()
        initial_opacity = dialogue.visualOpacity
        dialogue._animate()

        self.assertTrue(dialogue.isVisible())
        self.assertGreater(dialogue.visualOpacity, 0.0)
        self.assertLess(dialogue.visualOpacity, initial_opacity)
        self.assertAlmostEqual(
            dialogue._container.graphicsEffect().opacity(),
            dialogue.visualOpacity,
        )
        self.assertEqual(dismissed, [])

        for _ in range(
            DIALOGUE_FADE_DURATION_MS // DIALOGUE_FADE_FRAME_MS + 2
        ):
            dialogue._animate()

        self.assertFalse(dialogue.isVisible())
        self.assertEqual(dismissed, [True])

    def test_dialogue_stack_uses_tiered_opacity_from_oldest_to_newest(self) -> None:
        from meapet.desktop.widgets import calculate_bubble_stack_opacities

        self.assertEqual(calculate_bubble_stack_opacities(0), ())
        self.assertEqual(calculate_bubble_stack_opacities(1), (1.0,))
        self.assertEqual(calculate_bubble_stack_opacities(2), (0.76, 1.0))
        self.assertEqual(
            calculate_bubble_stack_opacities(3),
            (0.52, 0.76, 1.0),
        )

    def test_dialogue_stack_keeps_three_distinct_messages_and_drops_the_oldest(self) -> None:
        from meapet.desktop.widgets import DialogueBubbleStack

        stack = DialogueBubbleStack()
        self.addCleanup(stack.close_all)
        first = stack.show_message("第一条", duration_ms=0)
        stack.show_message("第二条", duration_ms=0)
        stack.show_message("第三条", duration_ms=0)
        fourth = stack.show_message("第四条", duration_ms=0)

        self.assertEqual(
            [bubble.text_label.text() for bubble in stack.bubbles],
            ["第二条", "第三条", "第四条"],
        )
        self.assertEqual(len({id(bubble) for bubble in stack.bubbles}), 3)
        self.assertIs(stack.latest, fourth)
        self.assertFalse(first.isVisible())
        self.assertTrue(all(bubble.isVisible() for bubble in stack.bubbles))

    def test_dialogue_stack_releases_a_bubble_after_its_fade_finishes(self) -> None:
        from meapet.desktop.widgets import (
            DIALOGUE_FADE_DURATION_MS,
            DIALOGUE_FADE_FRAME_MS,
            DialogueBubbleStack,
        )

        stack = DialogueBubbleStack()
        self.addCleanup(stack.close_all)
        bubble = stack.show_message("淡出后释放", duration_ms=0)

        bubble._start_fadeout()
        for _ in range(
            DIALOGUE_FADE_DURATION_MS // DIALOGUE_FADE_FRAME_MS + 2
        ):
            bubble._animate()

        self.assertEqual(stack.bubbles, ())
        self.assertIsNone(stack.latest)
        self.assertFalse(bubble.isVisible())

    def test_real_host_pushes_previous_bubbles_up_without_replacing_them(self) -> None:
        from meapet.desktop.interaction import PetInteractionMixin
        from meapet.desktop.render_host import PetRenderHostMixin
        from meapet.desktop.widgets import (
            DIALOGUE_MOTION_DURATION_MS,
            DialogueBubbleStack,
        )

        class BubbleHost(PetInteractionMixin, PetRenderHostMixin, QWidget):
            def __init__(self):
                super().__init__()
                self.config = {"bubble_duration_ms": {"default": 5000}}
                self._bubble_stack = DialogueBubbleStack(self)
                self._bubble_stack.changed.connect(
                    self._on_bubble_stack_changed
                )
                self.bubble = None

        screen = QApplication.primaryScreen().availableGeometry()
        host = self._track(BubbleHost())
        host.resize(180, 300)
        host.move(screen.right() - host.width() - 40, screen.top() + 240)
        self.addCleanup(host._bubble_stack.close_all)

        host._show_bubble("第一条", 5000)
        first = host._bubble_stack.latest
        first_target = first._position_animation.endValue()
        self.assertEqual(
            first._position_animation.state(),
            QAbstractAnimation.Running,
        )
        self.assertGreater(first.pos().y(), first_target.y())
        self.assertLess(first.visualOpacity, 1.0)
        QTest.qWait(260)
        self.assertEqual(
            first._position_animation.state(),
            QAbstractAnimation.Running,
        )
        self.assertNotEqual(first.pos(), first_target)
        QTest.qWait(DIALOGUE_MOTION_DURATION_MS)
        self.assertEqual(first.pos(), first_target)

        host._show_bubble("第二条", 5000)
        second = host._bubble_stack.latest
        first_new_target = first._position_animation.endValue()
        self.assertEqual(
            first._position_animation.state(),
            QAbstractAnimation.Running,
        )
        self.assertEqual(
            second._position_animation.state(),
            QAbstractAnimation.Running,
        )
        self.assertNotEqual(first.pos(), first_new_target)
        self.assertGreater(second.pos().y(), second._position_animation.endValue().y())
        QTest.qWait(DIALOGUE_MOTION_DURATION_MS + 80)

        host._show_bubble("第三条", 5000)
        QTest.qWait(DIALOGUE_MOTION_DURATION_MS + 80)

        bubbles = host._bubble_stack.bubbles
        self.assertEqual(
            [bubble.text_label.text() for bubble in bubbles],
            ["第一条", "第二条", "第三条"],
        )
        self.assertLess(bubbles[0].geometry().bottom(), bubbles[1].geometry().top())
        self.assertLess(bubbles[1].geometry().bottom(), bubbles[2].geometry().top())
        self.assertTrue(all(bubble.isVisible() for bubble in bubbles))
        self.assertIs(host.bubble, bubbles[-1])
        self.assertAlmostEqual(bubbles[0].visualOpacity, 0.52, places=2)
        self.assertAlmostEqual(bubbles[1].visualOpacity, 0.76, places=2)
        self.assertAlmostEqual(bubbles[2].visualOpacity, 1.0, places=2)

    def test_bubble_position_stays_on_screen_and_avoids_the_pet(self) -> None:
        from meapet.desktop.render_host import calculate_bubble_position

        screen = QRect(0, 0, 1200, 900)
        pet = QRect(760, 500, 280, 360)
        bubble_size = QSize(360, 180)
        position = calculate_bubble_position(pet, bubble_size, screen)
        bubble = QRect(position, bubble_size)
        self.assertTrue(screen.adjusted(24, 24, -24, -24).contains(bubble))
        self.assertFalse(bubble.intersects(pet))

        edge_pet = QRect(1050, 20, 140, 300)
        edge_position = calculate_bubble_position(edge_pet, bubble_size, screen)
        edge_bubble = QRect(edge_position, bubble_size)
        self.assertTrue(screen.adjusted(24, 24, -24, -24).contains(edge_bubble))
        self.assertFalse(edge_bubble.intersects(edge_pet))

    def test_bubble_prefers_the_horizontal_side_away_from_the_screen_edge(self) -> None:
        from meapet.desktop.render_host import (
            calculate_bubble_position,
            calculate_bubble_tail,
        )

        screen = QRect(0, 0, 1200, 900)
        bubble_size = QSize(320, 150)

        right_pet = QRect(850, 500, 250, 300)
        right_position = calculate_bubble_position(
            right_pet,
            bubble_size,
            screen,
        )
        right_bubble = QRect(right_position, bubble_size)
        self.assertLess(right_bubble.right(), right_pet.left())
        self.assertLessEqual(
            right_bubble.center().y(),
            right_pet.top() + right_pet.height() // 4,
        )
        right_tail_side, right_tail_anchor = calculate_bubble_tail(
            right_pet,
            right_bubble,
        )
        self.assertEqual(right_tail_side, "bottom")
        self.assertGreater(right_tail_anchor, right_bubble.width() * 0.65)

        left_pet = QRect(100, 500, 250, 300)
        left_position = calculate_bubble_position(
            left_pet,
            bubble_size,
            screen,
        )
        left_bubble = QRect(left_position, bubble_size)
        self.assertGreater(left_bubble.left(), left_pet.right())
        self.assertLessEqual(
            left_bubble.center().y(),
            left_pet.top() + left_pet.height() // 4,
        )
        left_tail_side, left_tail_anchor = calculate_bubble_tail(
            left_pet,
            left_bubble,
        )
        self.assertEqual(left_tail_side, "bottom")
        self.assertLess(left_tail_anchor, left_bubble.width() * 0.35)

    def test_newest_bubble_stays_near_the_pet_while_older_bubbles_stack_upward(self) -> None:
        from meapet.desktop.render_host import calculate_bubble_stack_positions

        screen = QRect(0, 0, 1200, 900)
        pet = QRect(850, 430, 250, 360)
        sizes = (
            QSize(240, 72),
            QSize(300, 88),
            QSize(220, 64),
        )

        positions = calculate_bubble_stack_positions(
            pet,
            sizes,
            screen,
        )
        bubbles = [QRect(position, size) for position, size in zip(positions, sizes)]
        safe = screen.adjusted(24, 24, -24, -24)

        self.assertEqual(len(positions), 3)
        self.assertTrue(all(safe.contains(bubble) for bubble in bubbles))
        self.assertTrue(all(bubble.right() < pet.left() for bubble in bubbles))
        self.assertLess(bubbles[0].bottom(), bubbles[1].top())
        self.assertLess(bubbles[1].bottom(), bubbles[2].top())
        self.assertLess(bubbles[2].center().y(), pet.center().y())

    def test_drag_position_always_uses_the_original_global_anchor(self) -> None:
        from meapet.desktop.render_host import calculate_drag_position

        window_origin = QPoint(720, 480)
        pointer_origin = QPoint(900, 620)
        current_pointer = QPoint(948, 677)

        expected = QPoint(768, 537)
        self.assertEqual(
            calculate_drag_position(window_origin, pointer_origin, current_pointer),
            expected,
        )
        # 同一鼠标坐标重复到达时结果必须完全一致，不能累计上一次移动误差。
        self.assertEqual(
            calculate_drag_position(window_origin, pointer_origin, current_pointer),
            expected,
        )

    def test_bubble_position_avoids_visible_chat_composer(self) -> None:
        from meapet.desktop.render_host import calculate_bubble_position

        screen = QRect(0, 0, 1200, 900)
        pet = QRect(760, 500, 280, 360)
        composer = QRect(690, 280, 480, 220)
        bubble_size = QSize(360, 180)

        position = calculate_bubble_position(
            pet,
            bubble_size,
            screen,
            avoid_rects=(composer,),
        )
        bubble = QRect(position, bubble_size)
        self.assertTrue(screen.adjusted(24, 24, -24, -24).contains(bubble))
        self.assertFalse(bubble.intersects(pet))
        self.assertFalse(bubble.intersects(composer))

    def test_bubble_tail_points_from_the_bubble_toward_the_pet(self) -> None:
        from meapet.desktop.render_host import calculate_bubble_tail

        pet = QRect(500, 400, 200, 300)
        cases = (
            (QRect(450, 180, 300, 160), "bottom", 150),
            (QRect(160, 430, 280, 160), "bottom", 244),
            (QRect(760, 430, 280, 160), "bottom", 36),
            (QRect(450, 740, 300, 160), "top", 150),
        )
        for bubble, expected_side, expected_anchor in cases:
            with self.subTest(side=expected_side):
                self.assertEqual(
                    calculate_bubble_tail(pet, bubble),
                    (expected_side, expected_anchor),
                )

    def test_context_menu_groups_secondary_actions_into_submenus(self) -> None:
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        class MenuHost(QWidget, PetWindowChromeMixin):
            def __init__(self):
                super().__init__()
                self.config = {
                    "vision": {"backend": "ollama", "model": "qwen3.5:4b"},
                    "llm": {"backend": "ollama"},
                    "watcher": {"enabled": False},
                }
                self._standby = False
                self._use_live2d = False

            def _safe_set_mood(self, _mood):
                pass

            def _set_vision_backend(self, _backend):
                pass

            def _set_vision_model(self, _model):
                pass

            def _toggle_watcher_enabled(self):
                pass

            def _toggle_standby(self):
                pass

            def _toggle_render_mode(self):
                pass

            def _open_size_dialog(self):
                pass

            def _do_screen_watch(self, force=False):
                pass

        host = self._track(MenuHost())
        menu = self._track(host._build_context_menu())
        self.assertIsInstance(menu, QMenu)
        root_labels = [
            action.text()
            for action in menu.actions()
            if not action.isSeparator()
        ]
        self.assertEqual(
            root_labels,
            [
                "养成状态",
                "看看我在干嘛",
                "切换表情",
                "识图与观察",
                "显示与立绘",
                "设置与数据",
                "退出",
            ],
        )
        submenu_labels = {
            action.menu().title()
            for action in menu.actions()
            if action.menu() is not None
        }
        self.assertEqual(
            submenu_labels,
            {"切换表情", "识图与观察", "显示与立绘", "设置与数据"},
        )

    def test_context_menu_uses_readable_type_and_row_height(self) -> None:
        from meapet.desktop.theme import MENU_STYLE

        menu = self._track(QMenu())
        menu.setStyleSheet(MENU_STYLE)
        action = menu.addAction("看看我在干嘛")
        menu.show()
        QApplication.processEvents()

        self.assertGreaterEqual(menu.font().pixelSize(), 14)
        self.assertGreaterEqual(menu.actionGeometry(action).height(), 38)

    def test_cloud_consent_dialog_is_compact_and_has_balanced_actions(self) -> None:
        from meapet.desktop.dialogs import CloudVisionConsentDialog

        dialog = self._track(CloudVisionConsentDialog(timeout_seconds=5))
        dialog.show()
        dialog._timer.stop()
        QApplication.processEvents()

        self.assertEqual(dialog.size(), QSize(420, 270))
        self.assertEqual(dialog.allow_button.width(), dialog.cancel_button.width())

    def test_cloud_consent_dialog_defaults_to_cancel_and_times_out(self) -> None:
        from meapet.desktop.dialogs import CloudVisionConsentDialog

        dialog = self._track(CloudVisionConsentDialog(timeout_seconds=5))
        self.assertEqual(dialog.remaining_seconds, 5)
        self.assertEqual(dialog._timer.interval(), 1000)
        self.assertTrue(dialog.cancel_button.isDefault())
        self.assertFalse(dialog.allow_button.isDefault())
        self.assertEqual(dialog.allow_button.text(), "允许本次上传")
        self.assertEqual(dialog.countdown_label.text(), "5 秒后自动取消。")

        dialog._tick()
        self.assertEqual(dialog.allow_button.text(), "允许本次上传")
        self.assertEqual(dialog.countdown_label.text(), "4 秒后自动取消。")

        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        dialog.show()
        dialog._timer.stop()
        for _ in range(4):
            dialog._tick()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertEqual(rejected, [True])
        self.assertTrue(dialog.auto_cancelled)

    def test_cloud_consent_accepts_only_an_explicit_allow_click(self) -> None:
        from meapet.desktop.dialogs import CloudVisionConsentDialog

        dialog = self._track(CloudVisionConsentDialog(timeout_seconds=5))
        dialog.show()
        dialog._timer.stop()
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Rejected)

        dialog.allow_button.click()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertFalse(dialog.auto_cancelled)

    def test_capture_consent_lets_user_override_scope_for_this_request_only(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog

        dialog = self._track(
            CaptureScopeConsentDialog(
                requested_scope="full_screen",
                timeout_seconds=15,
            )
        )
        self.assertEqual(dialog.scope_combo.currentData(), "full_screen")
        self.assertEqual(dialog.countdown_label.text(), "15 秒后自动取消。")
        dialog._tick()
        self.assertEqual(dialog.countdown_label.text(), "14 秒后自动取消。")
        dialog.scope_combo.setCurrentIndex(
            dialog.scope_combo.findData("region")
        )
        dialog.region_x.setValue(-120)
        dialog.region_y.setValue(40)
        dialog.region_width.setValue(1280)
        dialog.region_height.setValue(720)

        dialog.show()
        dialog._timer.stop()
        dialog.allow_button.click()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.approval.scope, "region")
        self.assertEqual(
            dialog.approval.region,
            {"x": -120, "y": 40, "width": 1280, "height": 720},
        )
        self.assertEqual(dialog.approval.application, "")

    def test_watcher_cloud_confirmation_uses_themed_safe_dialog(self) -> None:
        from meapet.desktop.watch_ctrl import PetWatcherMixin

        pet = type("WatcherHost", (PetWatcherMixin,), {})()
        pet.config = {"watcher": {"allow_cloud": True}}
        pet._is_cloud_vision = lambda: True
        pet._show_bubble = unittest.mock.Mock()

        with patch(
            "meapet.desktop.watch_ctrl.confirm_cloud_vision",
            return_value=False,
        ) as confirm, patch(
            "PyQt5.QtWidgets.QMessageBox.question",
            side_effect=AssertionError("不应调用系统确认框"),
        ):
            self.assertFalse(pet._confirm_cloud_capture())
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.kwargs["timeout_seconds"], 5)



    def test_status_language_covers_core_states(self) -> None:
        from meapet.desktop import status_language

        self.assertIn("思考", status_language.thinking())
        self.assertIn("稍等", status_language.thinking_busy())
        self.assertIn("截屏", status_language.menu_watch_enable())
        self.assertIn("Live2D", status_language.menu_render_to_live2d())
        self.assertIn("回忆", status_language.empty_memories())

    def test_chat_input_set_busy_disables_send(self) -> None:
        from meapet.desktop.chat_input import ChatInputBox

        composer = self._track(ChatInputBox())
        composer.set_busy(True, "还在想上一条…稍等喵")
        self.assertFalse(composer.send_button.isEnabled())
        self.assertTrue(composer.input.isReadOnly())
        self.assertIn("还在想", composer.feedback_label.text())
        composer.set_busy(False)
        self.assertTrue(composer.send_button.isEnabled())
        self.assertFalse(composer.input.isReadOnly())

    def test_chat_completion_keeps_input_busy_until_tts_finishes(self) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.chat_input import ChatInputBox

        class Engine:
            _MOOD_TAGS = {"neutral"}

            @staticmethod
            def take_voice_text():
                return ""

            @staticmethod
            def take_tts_style():
                return ""

        class Worker:
            done = True

            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                pass

            def get_result(self):
                return None

        class Voice:
            enabled = True

        class Host(PetChatFlowMixin):
            chat_engine = Engine()
            tts = Voice()
            _awaiting_reply = True

            def show_reply(self, *_args, **_kwargs):
                pass

            def _detect_mood(self, _text):
                return "neutral"

            def _ensure_tts_poll(self):
                pass

            def _do_memory_ops(self, *_args):
                pass

        host = Host()
        host._chat_input = self._track(ChatInputBox())
        host._chat_input.set_busy(True, "还在想上一条…稍等喵")

        with patch("meapet.desktop.chat_flow.TTSWorker", Worker), patch(
            "meapet.desktop.chat_flow.QTimer.singleShot"
        ):
            host._on_chat_done("回复完成", "neutral")

        self.assertTrue(host._awaiting_reply)
        self.assertTrue(host._chat_input.input.isReadOnly())
        self.assertFalse(host._chat_input.send_button.isEnabled())

        host._poll_tts()

        self.assertFalse(host._awaiting_reply)
        self.assertFalse(host._chat_input.input.isReadOnly())
        self.assertTrue(host._chat_input.send_button.isEnabled())

    def test_chat_failure_paths_release_visible_busy_input(self) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.chat_input import ChatInputBox

        class Host(PetChatFlowMixin):
            _awaiting_reply = True
            _chat_worker = None

            def show_reply(self, *_args, **_kwargs):
                pass

            def _show_bubble(self, *_args, **_kwargs):
                pass

            def _position_bubble(self):
                pass

        host = Host()
        host._chat_input = self._track(ChatInputBox())
        host._chat_input.set_busy(True, "等待失败结果")

        with patch("meapet.desktop.chat_flow.log_error"):
            host._on_chat_error("测试错误")

        self.assertFalse(host._chat_input.input.isReadOnly())
        self.assertTrue(host._chat_input.send_button.isEnabled())

        host._awaiting_reply = True
        host._chat_input.set_busy(True, "等待超时结果")
        host._on_chat_timeout()

        self.assertFalse(host._awaiting_reply)
        self.assertFalse(host._chat_input.input.isReadOnly())
        self.assertTrue(host._chat_input.send_button.isEnabled())

    def test_awaiting_state_ignores_stale_or_incompatible_composer(self) -> None:
        from meapet.desktop.chat_input import set_awaiting_reply_state

        class Host:
            _awaiting_reply = True

        host = Host()
        host._chat_input = object()
        set_awaiting_reply_state(host, False)
        self.assertFalse(host._awaiting_reply)

        class DeletedComposer:
            @staticmethod
            def set_busy(_busy, _message):
                raise RuntimeError("wrapped C/C++ object has been deleted")

        deleted = DeletedComposer()
        host._chat_input = deleted
        set_awaiting_reply_state(host, False)
        self.assertIsNone(host._chat_input)

    def test_watcher_completion_releases_visible_busy_input(self) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.chat_input import ChatInputBox
        from meapet.desktop.watch_ctrl import PetWatcherMixin

        class Host(PetWatcherMixin, PetChatFlowMixin):
            _awaiting_reply = True
            config = {"bubble_duration_ms": {"default": 5000}}

            def _show_bubble(self, *_args, **_kwargs):
                pass

            def _start_watcher_timer(self):
                pass

        host = Host()
        host._chat_input = self._track(ChatInputBox())
        host._chat_input.set_busy(True, "还在识图…稍等喵")

        host._on_watch_silent()

        self.assertFalse(host._awaiting_reply)
        self.assertFalse(host._chat_input.input.isReadOnly())
        self.assertTrue(host._chat_input.send_button.isEnabled())


    def test_bubble_mood_accent_changes_border_color(self) -> None:
        from meapet.desktop.widgets import DialogueBox, MOOD_BORDER_COLORS

        bubble = self._track(DialogueBox())
        bubble.show_text("今天也要加油喵", duration_ms=0, mood="happy")
        self.assertEqual(bubble._container.mood, "happy")
        self.assertEqual(
            MOOD_BORDER_COLORS["happy"].lower(),
            "#ffb36b",
        )
        bubble.set_mood("annoyed")
        self.assertEqual(bubble._container.mood, "annoyed")

    def test_normalize_config_includes_motion_and_first_run_flags(self) -> None:
        from meapet.config.store import normalize_config

        cfg = normalize_config({"display": {"font_scale": 1.2}})
        self.assertIn("reduced_motion", cfg["display"])
        self.assertFalse(cfg["display"]["reduced_motion"])
        self.assertIn("first_run_hint_shown", cfg["ui"])
        self.assertFalse(cfg["ui"]["first_run_hint_shown"])


    def test_standard_icons_resolve_for_core_roles(self) -> None:
        from meapet.desktop.icons import standard_icon

        for role in ("status", "watch", "settings", "quit", "wake", "standby"):
            icon = standard_icon(role)
            self.assertFalse(icon.isNull(), msg=role)

    def test_resolve_reduced_motion_respects_config_true(self) -> None:
        from meapet.ui_theme import resolve_reduced_motion
        import os

        os.environ.pop("MEAPET_REDUCED_MOTION", None)
        self.assertTrue(resolve_reduced_motion(True))
        self.assertFalse(resolve_reduced_motion(False) and os.environ.get("MEAPET_REDUCED_MOTION") == "force")

    def test_reduced_motion_dismisses_bubble_without_fade_timer(self) -> None:
        from meapet.desktop.widgets import DialogueBox

        dismissed = []
        with patch.dict(os.environ, {"MEAPET_REDUCED_MOTION": "1"}):
            bubble = self._track(DialogueBox())
            bubble.dismissed.connect(lambda: dismissed.append(True))
            bubble.show_text("立即消失", duration_ms=0)
            bubble._start_fadeout()

        self.assertFalse(bubble._anim_timer.isActive())
        self.assertFalse(bubble.isVisible())
        self.assertEqual(dismissed, [True])

    def test_loading_disabled_vision_config_keeps_advanced_options_collapsed(self) -> None:
        from wizard.page_vision import VisionPage

        page = self._track(VisionPage())
        page.show()
        QApplication.processEvents()
        page.apply_config({}, {"enabled": False})
        QApplication.processEvents()

        self.assertFalse(page.advanced_toggle.isChecked())
        self.assertTrue(page.advanced_frame.isHidden())
        for widget in (
            page.model_label,
            page.model_combo,
            page.cloud_box,
            page.min_min_input,
            page.max_min_input,
        ):
            with self.subTest(widget=widget.objectName()):
                self.assertFalse(widget.isVisibleTo(page))

    def test_tts_engine_details_are_collapsible(self) -> None:
        from wizard.page_tts import TTSPage

        page = self._track(TTSPage())
        page.enable_cb.setChecked(True)
        page.engine_details_toggle.setChecked(False)
        page._sync_engine_details_visibility()
        # 未 show 的窗口上 isVisible() 恒为 False，用 isHidden() 验证显式折叠
        self.assertTrue(page.backend_combo.isHidden())
        page.engine_details_toggle.setChecked(True)
        page._sync_engine_details_visibility()
        self.assertFalse(page.backend_combo.isHidden())

    def test_gsv_reference_audio_paths_are_restored_and_saved_per_language(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        for timer in wizard.tts_page._startup_timers:
            timer.stop()
        wizard._existing_config = {}

        wizard.tts_page.apply_config(
            {
                "engine": "gpt_sovits",
                "enabled": True,
                "gsv_ref_wav": "./refs/legacy-ja.wav",
                "gsv_ref_lang": "jp",
                "reference_audios": {
                    "jp": {"path": "./refs/jp.wav", "text": ""},
                    "zh": {"path": "./refs/zh.wav", "text": "你好"},
                    "en": {"path": "./refs/en.wav", "text": ""},
                },
            }
        )

        inputs = wizard.tts_page.gsv_reference_inputs
        self.assertEqual(set(inputs), {"jp", "zh", "en"})
        self.assertEqual(inputs["jp"].text(), "./refs/jp.wav")
        self.assertEqual(inputs["zh"].text(), "./refs/zh.wav")
        self.assertEqual(inputs["en"].text(), "./refs/en.wav")
        self.assertTrue(all(widget.accessibleName() for widget in inputs.values()))

        tts_config = wizard.collect_config()["tts"]
        self.assertEqual(
            tts_config["reference_audios"],
            {
                "jp": {"path": "./refs/jp.wav", "text": ""},
                "zh": {"path": "./refs/zh.wav", "text": "你好"},
                "en": {"path": "./refs/en.wav", "text": ""},
            },
        )
        self.assertEqual(tts_config["gsv_ref_wav"], "./refs/jp.wav")
        self.assertEqual(tts_config["gsv_ref_lang"], "jp")

    def test_translation_is_an_explicit_language_fallback_not_a_model_fallback(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        for timer in wizard.tts_page._startup_timers:
            timer.stop()
        wizard._existing_config = {}

        wizard.tts_page.apply_config(
            {
                "engine": "gpt_sovits",
                "enabled": True,
                "translate_to_jp": True,
                "translate_target_language": "zh",
                "translate_api_key": "translate-test-key",
            }
        )

        page = wizard.tts_page
        self.assertTrue(page.translation_enabled_cb.isChecked())
        self.assertEqual(page.translate_target_combo.currentData(), "zh")
        self.assertIn("输出语言不受支持", page.translation_enabled_cb.text())
        self.assertNotIn("免费翻译失效", page.translate_key.placeholderText())

        tts_config = wizard.collect_config()["tts"]
        self.assertTrue(tts_config["translate_to_jp"])
        self.assertEqual(tts_config["translate_target_language"], "zh")
        self.assertEqual(tts_config["translate_api_key"], "translate-test-key")

    def test_tray_menu_offers_standby_recovery(self) -> None:
        from meapet.desktop.window_chrome import PetWindowChromeMixin
        from meapet.desktop import status_language

        class Host(QWidget, PetWindowChromeMixin):
            def __init__(self):
                super().__init__()
                self._standby = True
                self._toggled = False

            def _toggle_standby(self):
                self._toggled = True
                self._standby = False

            def _is_auto_start(self):
                return False

            def _toggle_auto_start(self):
                pass

            def _quit(self):
                pass

            def _do_screen_watch(self, force=False):
                pass

            def _toggle_visibility(self):
                pass

        host = self._track(Host())
        menu = self._track(host._build_tray_menu())
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn(status_language.tray_recover_standby(), labels)

if __name__ == "__main__":
    unittest.main()
