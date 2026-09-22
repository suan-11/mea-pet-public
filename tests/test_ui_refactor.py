"""UI 重构的视觉语义、可访问性与组件契约。"""

from __future__ import annotations

import os
import re
import sys
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PyQt5.QtGui import QKeyEvent, QMouseEvent, QPainterPath  # noqa: E402
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
    QListView,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
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
                for timer in widget.findChildren(QTimer):
                    timer.stop()
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QApplication.processEvents()

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def test_semantic_palette_meets_text_contrast_targets(self) -> None:
        from meapet.ui_theme import PALETTES, contrast_ratio

        required_pairs = (
            ("text_primary", "surface", 4.5),
            ("text_secondary", "surface", 4.5),
            ("text_muted", "canvas", 4.5),
            ("on_primary", "primary", 4.5),
            ("success", "surface", 4.5),
            ("danger", "surface", 4.5),
        )
        for scheme, palette in PALETTES.items():
            for foreground, background, minimum in required_pairs:
                with self.subTest(scheme=scheme, foreground=foreground, background=background):
                    self.assertGreaterEqual(
                        contrast_ratio(palette[foreground], palette[background]),
                        minimum,
                    )

    def test_interactive_borders_meet_ui_graphic_contrast(self) -> None:
        from meapet.ui_theme import PALETTES, contrast_ratio

        required_pairs = (
            ("border_strong", "surface", 3.0),
            ("border_strong", "surface_input", 3.0),
            ("border_strong", "surface_elevated", 3.0),
            ("focus", "surface", 3.0),
            ("focus", "canvas", 3.0),
        )
        for scheme, palette in PALETTES.items():
            for foreground, background, minimum in required_pairs:
                with self.subTest(scheme=scheme, foreground=foreground, background=background):
                    self.assertGreaterEqual(
                        contrast_ratio(palette[foreground], palette[background]),
                        minimum,
                    )

    def test_input_surface_reads_as_recessed_well(self) -> None:
        from meapet.ui_theme import PALETTES, _relative_luminance

        for scheme, palette in PALETTES.items():
            with self.subTest(scheme=scheme):
                self.assertLess(
                    _relative_luminance(palette["surface_input"]),
                    _relative_luminance(palette["canvas"]),
                )

    def test_theme_mode_switches_palette_and_named_styles(self) -> None:
        from meapet.ui_theme import (
            get_active_scheme,
            get_named_style,
            get_ui_theme_mode,
            normalize_theme_mode,
            resolved_scheme,
            set_ui_theme_mode,
        )

        self.addCleanup(set_ui_theme_mode, get_ui_theme_mode())
        self.assertEqual(normalize_theme_mode("LIGHT"), "light")
        self.assertEqual(normalize_theme_mode("whatever"), "system")
        self.assertIn(resolved_scheme("system"), ("light", "dark"))
        self.assertEqual(resolved_scheme("light"), "light")

        set_ui_theme_mode("dark")
        self.assertEqual(get_active_scheme(), "dark")
        dark_sheet = get_named_style("WIZARD_STYLESHEET")
        set_ui_theme_mode("light")
        self.assertEqual(get_active_scheme(), "light")
        light_sheet = get_named_style("WIZARD_STYLESHEET")
        self.assertNotEqual(dark_sheet, light_sheet)
        set_ui_theme_mode("dark")
        self.assertEqual(get_named_style("WIZARD_STYLESHEET"), dark_sheet)

    def test_wizard_check_controls_have_visible_focus_ring(self) -> None:
        from wizard.styles import WIZARD_STYLESHEET

        focus_rules = re.findall(
            r"QCheckBox:focus[^{]*\{([^}]*)\}",
            WIZARD_STYLESHEET,
        )
        self.assertTrue(focus_rules)
        self.assertTrue(
            any("2px solid" in rule for rule in focus_rules),
            "QCheckBox/QRadioButton 聚焦时必须出现 2px 可见焦点环，而不是只变字色",
        )

    def test_live_refreshing_surfaces_avoid_pixel_effects(self) -> None:
        """光标闪烁/倒计时/进度刷新会让 QGraphicsEffect 反复整容器重模糊，导致卡顿。"""
        from meapet.desktop.dialogs import CloudVisionConsentDialog
        from meapet.desktop.splash import StartupSplash
        from meapet.desktop.status_panel import StatusPanel
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        self.assertIsNone(wizard.container.graphicsEffect())

        splash = self._track(StartupSplash())
        self.assertIsNone(splash.card.graphicsEffect())

        panel = self._track(StatusPanel(_MemoryStub()))
        for card in panel.findChildren(QFrame):
            if card.objectName() == "StatusCard":
                self.assertIsNone(card.graphicsEffect())

        consent = self._track(CloudVisionConsentDialog(timeout_seconds=5))
        for card in consent.findChildren(QFrame):
            if card.objectName() == "CloudConsentCard":
                self.assertIsNone(card.graphicsEffect())

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

    def test_message_dialog_owns_its_chrome_and_localizes_standard_buttons(
        self,
    ) -> None:
        from meapet.message_dialog import (
            MESSAGE_DIALOG_STYLE,
            MeaMessageDialog,
        )
        from wizard.styles import styled_message_box

        box = self._track(
            MeaMessageDialog(
                None,
                title="仍有必要配置未完成",
                text="仍要保存吗？",
                icon=QMessageBox.Warning,
                buttons=QMessageBox.Save | QMessageBox.Cancel,
                default_button=QMessageBox.Cancel,
            )
        )
        self.assertTrue(box.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(box.testAttribute(Qt.WA_TranslucentBackground))
        self.assertEqual(box.objectName(), "MeaMessageDialog")
        self.assertEqual(box.card.objectName(), "MessageCard")
        self.assertEqual(box.title_label.text(), "仍有必要配置未完成")
        self.assertEqual(box.kind_label.text(), "注意")
        self.assertEqual(box.button(QMessageBox.Save).text(), "保存")
        self.assertEqual(box.button(QMessageBox.Cancel).text(), "取消")
        self.assertEqual(
            box.button(QMessageBox.Cancel).objectName(),
            "MessagePrimaryButton",
        )
        self.assertGreaterEqual(box.close_button.minimumWidth(), 44)
        self.assertGreaterEqual(box.close_button.minimumHeight(), 44)
        self.assertIn("QFrame#MessageCard", MESSAGE_DIALOG_STYLE)
        self.assertIn("QTextBrowser#MessageBody", MESSAGE_DIALOG_STYLE)

        with patch(
            "meapet.message_dialog.MeaMessageDialog.exec_",
            return_value=QMessageBox.Cancel,
        ):
            result = styled_message_box(
                None,
                title="仍有必要配置未完成",
                text="仍要保存吗？",
                icon=QMessageBox.Warning,
                buttons=QMessageBox.Save | QMessageBox.Cancel,
                default_button=QMessageBox.Cancel,
            )

        self.assertEqual(result, QMessageBox.Cancel)

    def test_message_dialog_escape_uses_safe_result_and_long_text_scrolls(
        self,
    ) -> None:
        from meapet.message_dialog import MeaMessageDialog

        dialog = self._track(
            MeaMessageDialog(
                None,
                title="启动失败",
                text="\n".join(
                    f"第 {index} 行错误：这是可复制的详细诊断信息。"
                    for index in range(80)
                ),
                icon=QMessageBox.Critical,
                buttons=QMessageBox.Retry | QMessageBox.Cancel,
                default_button=QMessageBox.Cancel,
            )
        )
        dialog.show()
        QApplication.processEvents()
        area = QApplication.primaryScreen().availableGeometry()
        self.assertLessEqual(dialog.height(), area.height())
        self.assertGreater(dialog.body.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.body.toPlainText().count("\n"), 79)

        QTest.keyClick(dialog, Qt.Key_Escape)
        QApplication.processEvents()
        self.assertEqual(dialog.result(), QMessageBox.Cancel)
        self.assertFalse(dialog.isVisible())

    def test_normal_system_font_is_used_for_body_and_display(self) -> None:
        from meapet.ui_theme import (
            BODY_FONT_NAME,
            BUNDLED_DISPLAY_FONT_PATH,
            DISPLAY_FONT_FAMILY,
            FALLBACK_BODY_FONT_NAME,
            FONT_FAMILY,
            ensure_application_fonts,
        )

        self.assertTrue(BUNDLED_DISPLAY_FONT_PATH.is_file())
        # 正文与标题统一使用系统正常 UI 字体（Windows 为微软雅黑），不再是手写风内置字体。
        self.assertEqual(DISPLAY_FONT_FAMILY, f'"{FALLBACK_BODY_FONT_NAME}"')
        self.assertEqual(BODY_FONT_NAME, FALLBACK_BODY_FONT_NAME)
        self.assertEqual(FONT_FAMILY, f'"{BODY_FONT_NAME}"')
        if sys.platform == "win32":
            self.assertEqual(FALLBACK_BODY_FONT_NAME, "Microsoft YaHei UI")
        self.assertIn(FALLBACK_BODY_FONT_NAME, ensure_application_fonts())

        from meapet.desktop.theme import CHAT_COMPOSER_STYLE, DIALOGUE_STYLE

        self.assertIn(
            "QLabel#ComposerTitle {\n            color: ",
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

    def test_configuration_window_uses_side_nav_and_accessible_core_actions(
        self,
    ) -> None:
        from wizard.app import SetupWizard
        from wizard.side_nav import SideNavTabs
        from wizard.styles import MIN_TARGET_SIZE

        wizard = self._track(SetupWizard())
        self.assertGreaterEqual(wizard.minimumWidth(), 680)
        self.assertGreater(wizard.maximumWidth(), wizard.minimumWidth())
        self.assertEqual(wizard.objectName(), "WizardRoot")
        self.assertEqual(wizard.container.objectName(), "WizardShell")
        shell_margins = wizard.layout().contentsMargins()
        self.assertEqual(
            (
                shell_margins.left(),
                shell_margins.top(),
                shell_margins.right(),
                shell_margins.bottom(),
            ),
            (1, 1, 1, 1),
        )
        self.assertIsInstance(wizard.tabs, SideNavTabs)
        self.assertIsInstance(wizard.tabs.list, QListWidget)
        self.assertEqual(
            [wizard.tabs.tabText(index) for index in range(wizard.tabs.count())],
            ["环境", "Live2D", "对话", "语音", "屏幕识图", "语音输入"],
        )
        live2d_scroll = wizard.tabs.widget(wizard.TAB_LIVE2D)
        self.assertIs(
            live2d_scroll.widget().findChild(
                type(wizard.live2d_viewport_settings)
            ),
            wizard.live2d_viewport_settings,
        )
        self.assertFalse(
            wizard.display_page.isAncestorOf(
                wizard.live2d_viewport_settings
            )
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
                "custom_display_key": "keep-me",
            }
        }
        # 窗口大小改由配置页管理，其余 display 字段仍原样保留。
        wizard.pet_size_slider.setValue(133)
        display_config = wizard.collect_config()["display"]
        self.assertEqual(display_config["font_scale"], 1.25)
        self.assertEqual(display_config["size_factor"], 1.33)
        self.assertEqual(display_config["custom_display_key"], "keep-me")

    def test_configuration_exposes_pet_window_size_in_percent(self) -> None:
        from meapet.ui_theme import (
            PET_SIZE_FACTOR_MAX,
            PET_SIZE_FACTOR_MIN,
            set_ui_font_scale,
        )
        from wizard.app import SetupWizard

        self.addCleanup(set_ui_font_scale, 1.0)
        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()

        slider = wizard.pet_size_slider
        self.assertIsInstance(slider, QSlider)
        self.assertEqual(slider.minimum(), round(PET_SIZE_FACTOR_MIN * 100))
        self.assertEqual(slider.maximum(), round(PET_SIZE_FACTOR_MAX * 100))
        self.assertTrue(slider.accessibleName())

        slider.setValue(150)
        self.assertEqual(wizard.pet_size_value.text(), "150%")
        self.assertEqual(
            wizard.collect_config()["display"]["size_factor"], 1.5
        )

        # 越界值按支持范围收敛，不会写出畸形配置。
        slider.setValue(slider.maximum())
        self.assertEqual(
            wizard.collect_config()["display"]["size_factor"],
            PET_SIZE_FACTOR_MAX,
        )

    def test_environment_page_wraps_at_max_font_scale_without_horizontal_clipping(
        self,
    ) -> None:
        from meapet.ui_theme import set_ui_font_scale
        from wizard.app import SetupWizard
        from wizard.platform_info import PYTHON_CHECK_NAME

        self.addCleanup(set_ui_font_scale, 1.0)
        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        wizard.env_page._check_timer.stop()
        wizard._suppress_dirty = True
        wizard.resize(wizard.minimumWidth(), wizard.minimumHeight())
        wizard.font_scale_slider.setValue(
            wizard.font_scale_slider.maximum()
        )
        wizard.env_page._set_item_status(
            PYTHON_CHECK_NAME,
            True,
            "就绪 · 3.13.3（桌宠可运行；本地 VITS 推荐 3.10–3.12）",
        )
        wizard.setWindowOpacity(1.0)
        wizard.show()
        self.app.processEvents()

        scroll = wizard.tabs.widget(wizard.TAB_ENV)
        content = scroll.widget()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(content.width(), scroll.viewport().width())
        for page in (wizard.display_page, wizard.env_page):
            with self.subTest(page=page):
                self.assertLessEqual(
                    page.x() + page.width(),
                    content.width(),
                )

        _, python_status, _, _ = wizard.env_page.items[PYTHON_CHECK_NAME]
        self.assertTrue(python_status.wordWrap())
        wizard._dirty = False

    def test_configuration_scale_values_use_the_same_compact_style(self) -> None:
        from wizard.app import SetupWizard
        from wizard.styles import WIZARD_STYLESHEET

        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        self.assertEqual(
            wizard.font_scale_value.minimumWidth(),
            wizard.pet_size_value.minimumWidth(),
        )
        self.assertIn(
            "QLabel#FontScaleValue,\n        QLabel#PetSizeValue",
            WIZARD_STYLESHEET,
        )

    def test_configuration_window_supports_all_native_resize_edges(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        wizard.env_page._check_timer.stop()
        wizard.setWindowOpacity(1.0)
        wizard.show()
        self.app.processEvents()

        midpoint_x = wizard.width() // 2
        midpoint_y = wizard.height() // 2
        expected = (
            (QPoint(1, midpoint_y), Qt.LeftEdge),
            (QPoint(wizard.width() - 2, midpoint_y), Qt.RightEdge),
            (QPoint(midpoint_x, 1), Qt.TopEdge),
            (QPoint(midpoint_x, wizard.height() - 2), Qt.BottomEdge),
            (QPoint(1, 1), Qt.LeftEdge | Qt.TopEdge),
            (
                QPoint(wizard.width() - 2, wizard.height() - 2),
                Qt.RightEdge | Qt.BottomEdge,
            ),
        )
        for point, edges in expected:
            with self.subTest(point=point):
                self.assertEqual(wizard._resize_edges_at(point), edges)
        self.assertEqual(
            wizard._resize_edges_at(QPoint(midpoint_x, midpoint_y)),
            Qt.Edges(),
        )

        with patch.object(
            wizard,
            "_start_system_resize",
            return_value=True,
        ) as start_resize:
            local = QPoint(1, midpoint_y)
            event = QMouseEvent(
                QEvent.MouseButtonPress,
                local,
                wizard.mapToGlobal(local),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            self.assertTrue(wizard.eventFilter(wizard, event))
        start_resize.assert_called_once_with(Qt.LeftEdge)

    def test_configuration_window_exposes_resize_and_maximize_controls(
        self,
    ) -> None:
        from meapet.ui_theme import MIN_TARGET_SIZE
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        wizard._load_timer.stop()
        wizard.env_page._check_timer.stop()
        wizard.setWindowOpacity(1.0)
        wizard.show()
        self.app.processEvents()

        self.assertTrue(wizard.windowFlags() & Qt.WindowMaximizeButtonHint)
        self.assertGreater(wizard.maximumWidth(), wizard.minimumWidth())
        self.assertGreater(wizard.maximumHeight(), wizard.minimumHeight())
        self.assertGreaterEqual(
            wizard.maximize_btn.minimumHeight(),
            MIN_TARGET_SIZE,
        )
        self.assertTrue(wizard.maximize_btn.isVisible())
        self.assertTrue(wizard.maximize_btn.accessibleName())
        self.assertFalse(hasattr(wizard, "size_grip"))

        before = wizard.size()
        wizard.resize(before.width() + 120, before.height() + 80)
        self.app.processEvents()
        self.assertGreater(wizard.width(), before.width())
        self.assertGreater(wizard.height(), before.height())

        QTest.mouseClick(wizard.maximize_btn, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(wizard.isMaximized())
        self.assertIn("还原", wizard.maximize_btn.text())
        self.assertTrue(wizard.mask().isEmpty())

        QTest.mouseClick(wizard.maximize_btn, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(wizard.isMaximized())
        self.assertIn("最大化", wizard.maximize_btn.text())

    def test_configuration_window_restores_its_last_normal_size(self) -> None:
        import tempfile

        from wizard.app import SetupWizard

        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            state_path = os.path.join(directory, "wizard-window.json")
            first = self._track(
                SetupWizard(
                    config_path=config_path,
                    window_state_path=state_path,
                )
            )
            first._load_timer.stop()
            first.env_page._check_timer.stop()
            first.setWindowOpacity(1.0)
            first.show()
            first.resize(1020, 720)
            first.move(32, 28)
            self.app.processEvents()
            first.close()
            self.app.processEvents()

            reopened = self._track(
                SetupWizard(
                    config_path=config_path,
                    window_state_path=state_path,
                )
            )
            reopened._load_timer.stop()
            reopened.env_page._check_timer.stop()

            self.assertEqual(reopened.size(), QSize(1020, 720))
            self.assertEqual(
                reopened._restored_window_position,
                QPoint(32, 28),
            )

    def test_configuration_restores_pet_window_size_from_existing_config(
        self,
    ) -> None:
        import json
        import tempfile

        from meapet.ui_theme import set_ui_font_scale
        from wizard.app import SetupWizard

        self.addCleanup(set_ui_font_scale, 1.0)
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump({"display": {"size_factor": 2.25}}, file)

            wizard = self._track(SetupWizard(config_path=config_path))
            wizard._load_timer.stop()
            wizard._load_existing_config()

            self.assertEqual(wizard.pet_size_slider.value(), 225)
            self.assertEqual(wizard.pet_size_value.text(), "225%")
            self.assertEqual(
                wizard.collect_config()["display"]["size_factor"], 2.25
            )

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

        wizard.backend_page.direct_radio.setChecked(True)

        wizard.llm_page.endpoint_input.clear()
        wizard.llm_page.model_combo.setEditText("")
        wizard._refresh_required_tabs()
        self.assertFalse(wizard.tabs.tabIcon(wizard.TAB_CHAT).isNull())
        self.assertIn("缺少", wizard.tabs.tabToolTip(wizard.TAB_CHAT))
        self.assertIn("对话", wizard.config_status.text())

        wizard.llm_page.endpoint_input.setText("https://api.openai.com/v1")
        wizard.llm_page.model_combo.setEditText("gpt-4o")
        wizard._refresh_required_tabs()
        self.assertTrue(wizard.tabs.tabIcon(wizard.TAB_CHAT).isNull())
        self.assertNotIn("对话", wizard.config_status.text())

        # TTS MiMo key check — this does not depend on LLM provider anymore
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
            wizard.llm_page,
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
        self.assertLessEqual(composer.height(), 160)
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

        with patch.dict(os.environ, {"MEA_PET_REDUCED_MOTION": "1"}):
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
        with patch.dict(os.environ, {"MEA_PET_REDUCED_MOTION": "1"}):
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

    def test_live2d_bubble_uses_visible_mask_instead_of_transparent_window(
        self,
    ) -> None:
        from PyQt5.QtGui import QRegion

        from meapet.desktop.render_host import (
            BUBBLE_PET_GAP,
            PetRenderHostMixin,
            calculate_bubble_anchor_rect,
        )

        class BubbleHost(PetRenderHostMixin, QWidget):
            pass

        class BubbleStub:
            def __init__(self) -> None:
                self.position = None
                self._size = QSize(140, 80)

            def isVisible(self) -> bool:
                return True

            def size(self) -> QSize:
                return QSize(self._size)

            def move(self, position: QPoint) -> None:
                self.position = QPoint(position)

        screen = QApplication.primaryScreen().availableGeometry()
        host = self._track(BubbleHost())
        host.resize(300, 400)
        host_rect = QRect(
            host.x(),
            host.y(),
            host.width(),
            host.height(),
        )
        host.move(
            screen.right() - host.width() - 40,
            screen.top() + 100,
        )
        visible_local = QRect(host_rect.x() + 72, host_rect.y() + 32, 180, 320)
        host.setMask(QRegion(visible_local, QRegion.Ellipse))
        bubble = BubbleStub()
        host.bubble = bubble

        host._position_bubble()
        self.assertIsNotNone(bubble.position)
        bubble_rect = QRect(bubble.position, bubble.size())
        host_rect = QRect(
            host.x(),
            host.y(),
            host.width(),
            host.height(),
        )
        anchor = QRect(host_rect.right() + 1, host_rect.top(), 1, 1)
        visible_gap = host_rect.left() - bubble_rect.right() - 1
        transparent_window_gap = (
            anchor.left() - host_rect.left()
        )
        print(f"host_rect: {host_rect}")
        print(f"bubble_rect: {bubble_rect}")
        print(f"visible_gap: {visible_gap}")


        self.assertEqual(visible_gap, 12)
        self.assertGreater(transparent_window_gap, BUBBLE_PET_GAP * 4)
        self.assertLess(bubble_rect.right(), anchor.left())

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

    def _assert_within_screen(self, rect: QRect, area: QRect) -> None:
        """窗口必须完整可见；比屏幕还大的窗口只要求左上角对齐在可见区域内。"""
        self.assertGreaterEqual(rect.left(), area.left())
        self.assertGreaterEqual(rect.top(), area.top())
        if rect.width() <= area.width():
            self.assertLessEqual(rect.right(), area.right())
        if rect.height() <= area.height():
            self.assertLessEqual(rect.bottom(), area.bottom())

    def test_popup_position_falls_back_when_the_preferred_side_is_off_screen(
        self,
    ) -> None:
        from meapet.desktop.screen_geometry import (
            POPUP_SCREEN_MARGIN,
            calculate_centered_position,
            calculate_popup_position,
        )

        screen = QRect(0, 0, 1200, 900)
        safe = screen.adjusted(
            POPUP_SCREEN_MARGIN,
            POPUP_SCREEN_MARGIN,
            -POPUP_SCREEN_MARGIN,
            -POPUP_SCREEN_MARGIN,
        )
        popup = QSize(480, 220)

        # 桌宠贴着屏幕顶部：上方放不下，退到下方而不是被截断在屏幕外。
        top_pet = QRect(500, 0, 200, 300)
        top = QRect(
            calculate_popup_position(top_pet, popup, screen, placement="above"),
            popup,
        )
        self.assertTrue(safe.contains(top))
        self.assertGreater(top.top(), top_pet.bottom())

        # 四个角落：无论首选方位是什么，结果都必须完整落在安全区内。
        corners = (
            QRect(0, 0, 200, 300),
            QRect(1000, 0, 200, 300),
            QRect(0, 600, 200, 300),
            QRect(1000, 600, 200, 300),
        )
        for pet in corners:
            for placement in ("above", "below", "left", "right"):
                with self.subTest(pet=pet, placement=placement):
                    position = calculate_popup_position(
                        pet, popup, screen, placement=placement
                    )
                    self.assertTrue(safe.contains(QRect(position, popup)))
                    self.assertTrue(
                        safe.contains(
                            QRect(
                                calculate_centered_position(pet, popup, screen),
                                popup,
                            )
                        )
                    )

    def test_clamp_position_keeps_oversized_popups_anchored_top_left(self) -> None:
        from meapet.desktop.screen_geometry import clamp_position

        screen = QRect(0, 0, 400, 300)
        oversized = QSize(900, 700)
        position = clamp_position(QPoint(600, 400), oversized, screen, margin=8)
        self.assertEqual(position, QPoint(8, 8))

    def test_chat_composer_pops_up_inside_the_screen_at_any_pet_position(
        self,
    ) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.chat_input import ChatInputBox

        available = QApplication.primaryScreen().availableGeometry()

        class ChatHost:
            def __init__(self, point: QPoint) -> None:
                self._pos = point
                self.bubble = None

            def pos(self) -> QPoint:
                return self._pos

            def width(self) -> int:
                return 200

            def height(self) -> int:
                return 300

            def _on_input_submit(self, _text: str) -> None:
                pass

            def _on_chat_input_files_attached(self, attachments: list) -> None:
                pass

        corners = (
            QPoint(available.left(), available.top()),
            QPoint(available.right() - 200, available.top()),
            QPoint(available.left(), available.bottom() - 300),
            QPoint(available.right() - 200, available.bottom() - 300),
        )
        for corner in corners:
            with self.subTest(corner=(corner.x(), corner.y())):
                host = ChatHost(corner)
                PetChatFlowMixin._start_chat(host)
                composer = self._track(host._chat_input)
                self.assertIsInstance(composer, ChatInputBox)
                self._assert_within_screen(
                    QRect(composer.pos(), composer.size()), available
                )
                composer.close()

    def test_status_panel_pops_up_inside_the_screen_when_the_pet_hugs_an_edge(
        self,
    ) -> None:
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        class PanelHost(QWidget, PetWindowChromeMixin):
            def __init__(self) -> None:
                super().__init__()
                self.memory = _MemoryStub()

        available = QApplication.primaryScreen().availableGeometry()
        host = self._track(PanelHost())
        host.resize(200, 300)
        host.move(available.right() - 200, available.bottom() - 300)
        host._show_status_panel()
        panel = self._track(host._status_panel)
        self._assert_within_screen(QRect(panel.pos(), panel.size()), available)

        # 面板被拖出屏幕后再次打开时会被拉回可见区域。
        panel.move(available.right() + 400, available.bottom() + 400)
        host._show_status_panel()
        self._assert_within_screen(QRect(panel.pos(), panel.size()), available)

    def test_choose_screen_index_prefers_the_center_then_the_overlap(self) -> None:
        from meapet.desktop.screen_geometry import choose_screen_index

        left = QRect(0, 0, 1920, 1080)
        right = QRect(1920, 0, 1280, 1024)
        screens = (left, right)

        # 中心点落在哪块屏就归哪块屏。
        self.assertEqual(choose_screen_index(QRect(200, 200, 300, 300), screens), 0)
        self.assertEqual(choose_screen_index(QRect(2400, 200, 300, 300), screens), 1)
        # 跨屏时按重叠面积归属。
        self.assertEqual(choose_screen_index(QRect(1800, 100, 400, 300), screens), 1)
        # 完全脱离所有屏幕（分辨率调小、显示器拔掉）时返回 None，由调用方回退主屏。
        self.assertIsNone(choose_screen_index(QRect(4000, 3000, 200, 300), screens))
        self.assertIsNone(choose_screen_index(QRect(0, 0, 200, 300), ()))

    def test_is_sufficiently_visible_tolerates_edge_parking(self) -> None:
        from meapet.desktop.screen_geometry import is_sufficiently_visible

        screen = QRect(0, 0, 1200, 900)
        # 压在任务栏上/稍微贴边：仍算可见，不该被挪动。
        self.assertTrue(is_sufficiently_visible(QRect(60, 700, 200, 300), screen))
        # 只剩一角露在屏幕里，以及整块跑到屏幕外：都需要拉回来。
        self.assertFalse(is_sufficiently_visible(QRect(1150, 850, 200, 300), screen))
        self.assertFalse(is_sufficiently_visible(QRect(1600, 1200, 200, 300), screen))

    def _make_screen_guard_host(self):
        from meapet.desktop.interaction import PetInteractionMixin
        from meapet.desktop.render_host import PetRenderHostMixin

        class ScreenGuardHost(PetInteractionMixin, PetRenderHostMixin, QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.config = {"bubble_duration_ms": {"default": 5000}}
                self.bubble = None

        host = self._track(ScreenGuardHost())
        host.resize(200, 300)
        return host

    def test_pet_returns_to_the_visible_area_after_the_screen_changes(self) -> None:
        available = QApplication.primaryScreen().availableGeometry()
        host = self._make_screen_guard_host()

        # 分辨率调小后遗留的旧坐标：整块落在桌面之外。
        host.move(available.right() + 600, available.bottom() + 400)
        host._ensure_on_screen()
        self._assert_within_screen(
            QRect(host.pos(), host.size()), available
        )

        # 已经完整可见的桌宠不该被挪动。
        inside = QPoint(available.left() + 120, available.top() + 90)
        host.move(inside)
        host._ensure_on_screen()
        self.assertEqual(host.pos(), inside)

    def test_pet_restores_the_position_saved_after_the_last_drag(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            state_path = os.path.join(directory, "pet-window.json")
            first = self._make_screen_guard_host()
            first._window_state_path = state_path
            expected = QPoint(120, 90)
            first.move(expected)
            first._save_pet_position()

            reopened = self._make_screen_guard_host()
            reopened._window_state_path = state_path
            self.assertTrue(reopened._restore_pet_position())
            self.assertEqual(reopened.pos(), expected)

    def test_screen_layout_signals_are_debounced_into_one_correction(self) -> None:
        from meapet.desktop.render_host import SCREEN_GUARD_DEBOUNCE_MS

        available = QApplication.primaryScreen().availableGeometry()
        host = self._make_screen_guard_host()
        host._init_screen_guard()
        self.assertTrue(host._screen_guard_timer.isSingleShot())

        host.move(available.right() + 600, available.bottom() + 400)
        for _ in range(3):
            host._on_screen_layout_changed()
        self.assertTrue(host._screen_guard_timer.isActive())
        QTest.qWait(SCREEN_GUARD_DEBOUNCE_MS + 150)
        self._assert_within_screen(QRect(host.pos(), host.size()), available)

    def test_open_overlays_follow_the_pet_back_onto_the_screen(self) -> None:
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.status_panel import StatusPanel

        available = QApplication.primaryScreen().availableGeometry()

        from meapet.desktop.interaction import PetInteractionMixin
        from meapet.desktop.render_host import PetRenderHostMixin

        class OverlayHost(
            PetChatFlowMixin, PetInteractionMixin, PetRenderHostMixin, QWidget
        ):
            def __init__(self) -> None:
                super().__init__()
                self.config = {"bubble_duration_ms": {"default": 5000}}
                self.bubble = None

        host = self._track(OverlayHost())
        host.resize(200, 300)
        host.move(available.right() + 600, available.bottom() + 400)

        host._start_chat()
        composer = self._track(host._chat_input)
        panel = self._track(StatusPanel(_MemoryStub()))
        host._status_panel = panel
        panel.show()
        panel.move(available.right() + 900, available.bottom() + 900)

        host._ensure_on_screen()

        self._assert_within_screen(QRect(host.pos(), host.size()), available)
        self._assert_within_screen(QRect(composer.pos(), composer.size()), available)
        self._assert_within_screen(QRect(panel.pos(), panel.size()), available)

    def _make_size_host(self, factor: float = 1.0):
        """带 PNG 渲染替身的桌宠宿主，用于验证窗口大小调节。"""
        from meapet.desktop.interaction import PetInteractionMixin
        from meapet.desktop.render_host import PetRenderHostMixin

        class _RendererStub:
            def get_current_pixmap(self):
                from PyQt5.QtGui import QPixmap

                pixmap = QPixmap(200, 300)
                pixmap.fill(Qt.transparent)
                return pixmap

        class SizeHost(PetInteractionMixin, PetRenderHostMixin, QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.config = {
                    "bubble_duration_ms": {"default": 5000},
                    "display": {"scale": 1.0, "size_factor": factor},
                }
                self.bubble = None
                self._use_live2d = False
                self._scale = 1.0
                self._size_factor = factor
                self.renderer = _RendererStub()
                self.sprite_label = QLabel(self)
                self.saved = 0
                self._l2d_model = None

            def _save_config(self):
                self.saved += 1

            def _apply_hit_region(self):
                pass

        return self._track(SizeHost())

    def test_size_presets_resize_the_window_and_persist_the_percentage(self) -> None:
        host = self._make_size_host()
        host._size_factor_preview(1.0)
        base = QSize(host.width(), host.height())

        host._set_size_factor(1.5)

        self.assertAlmostEqual(host._size_factor, 1.5)
        self.assertEqual(host.config["display"]["size_factor"], 1.5)
        self.assertEqual(host.saved, 1)
        self.assertGreater(host.width(), base.width())
        self.assertGreater(host.height(), base.height())

        # 超出支持范围的值被收敛到 30%–300%。
        host._set_size_factor(9.0)
        self.assertEqual(host.config["display"]["size_factor"], 3.0)
        host._set_size_factor(0.01)
        self.assertEqual(host.config["display"]["size_factor"], 0.3)

    def test_saved_config_applies_the_new_window_size_without_restart(self) -> None:
        host = self._make_size_host()
        host._size_factor_preview(1.0)
        before = QSize(host.width(), host.height())

        host.config["display"]["size_factor"] = 2.0
        host._apply_display_preference()

        self.assertAlmostEqual(host._size_factor, 2.0)
        self.assertGreater(host.width(), before.width())

        # 配置值未变化时不重复缩放。
        size = QSize(host.width(), host.height())
        host._apply_display_preference()
        self.assertEqual(QSize(host.width(), host.height()), size)

    def test_resizing_keeps_the_pet_anchored_and_on_screen(self) -> None:
        available = QApplication.primaryScreen().availableGeometry()
        host = self._make_size_host()
        host._size_factor_preview(1.0)
        host.move(
            available.left() + 200,
            available.bottom() - host.height() + 1,
        )
        bottom_center = QRect(host.pos(), host.size()).center().x()
        bottom = QRect(host.pos(), host.size()).bottom()

        host._size_factor_preview(1.4)

        grown = QRect(host.pos(), host.size())
        # 以脚下为锚点：底边与水平中心保持，不会往右下角长出去。
        self.assertAlmostEqual(grown.center().x(), bottom_center, delta=2)
        self.assertAlmostEqual(grown.bottom(), bottom, delta=2)
        self._assert_within_screen(grown, available)

        # 贴着屏幕边缘放大时仍会被夹回可见区域。
        host.move(available.right() - host.width() + 1, available.top())
        host._size_factor_preview(2.0)
        self._assert_within_screen(QRect(host.pos(), host.size()), available)

    def test_context_menu_offers_window_size_presets_and_custom_entry(self) -> None:
        from meapet.ui_theme import PET_SIZE_PRESETS

        host = self._make_menu_host()
        host.config["display"] = {"size_factor": 1.25}
        menu = self._track(host._build_context_menu())
        display_menu = next(
            action.menu()
            for action in menu.actions()
            if action.menu() is not None
            and action.menu().title() == "显示与立绘"
        )
        size_menu = next(
            action.menu()
            for action in display_menu.actions()
            if action.menu() is not None
        )
        self.assertEqual(size_menu.title(), "窗口大小 · 125%")
        labels = [
            action.text()
            for action in size_menu.actions()
            if not action.isSeparator()
        ]
        self.assertEqual(
            labels,
            [f"{percent}%" for percent in PET_SIZE_PRESETS] + ["自定义…"],
        )
        checked = [
            action.text()
            for action in size_menu.actions()
            if action.isCheckable() and action.isChecked()
        ]
        self.assertEqual(checked, ["125%"])

    def _make_menu_host(self):
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
                self.moods = []

            def _safe_set_mood(self, mood):
                self.moods.append(mood)

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

            def _show_status_panel(self):
                pass

            def _show_timeline(self):
                pass

            def _reset_memory(self):
                pass

            def _reopen_setup_wizard(self):
                pass

            def _show_about(self):
                pass

            def _quit(self):
                pass

            def _toggle_voice_input(self):
                pass


        return self._track(MenuHost())

    def test_context_menu_groups_secondary_actions_into_submenus(self) -> None:
        host = self._make_menu_host()
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
                "开启语音输入",
                "显示与立绘",
                "设置与数据",
                "关于",
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

    def test_context_menu_opens_as_movable_standalone_window(self) -> None:
        from meapet.desktop.menu_window import PetMenuWindow

        host = self._make_menu_host()
        host.show()
        host._show_context_menu(QPoint(20, 20))
        window = self._track(host._menu_window)
        self.assertIsInstance(window, PetMenuWindow)
        # 独立顶层窗口，而不是依附桌宠的 popup。
        self.assertTrue(window.isWindow())
        self.assertTrue(window.isVisible())
        flags = int(window.windowFlags())
        # Qt.Tool（工具窗口）而不是 Qt.Popup（弹出即抓取输入、失焦自关）。
        self.assertEqual(flags & int(Qt.WindowType_Mask), int(Qt.Tool))
        self.assertTrue(flags & int(Qt.FramelessWindowHint))
        self.assertTrue(flags & int(Qt.WindowStaysOnTopHint))

        # 根窗口只登记第一层分组；更深层目录在侧边面板打开后才按需创建。
        self.assertEqual(
            [title for _button, _menu, title in window._groups],
            [
                "切换表情",
                "识图与观察",
                "显示与立绘",
                "设置与数据",
            ],
        )
        self.assertIn("退出", [b.text() for b, _a in window._action_buttons])

        # 拖动窗口：按住空白处移动即可改变位置。
        start = window.pos()
        window.mousePressEvent(
            QMouseEvent(
                QEvent.MouseButtonPress,
                QPoint(10, 6),
                window.mapToGlobal(QPoint(10, 6)),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        window.mouseMoveEvent(
            QMouseEvent(
                QEvent.MouseMove,
                QPoint(70, 66),
                window.mapToGlobal(QPoint(70, 66)),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(window.pos() - start, QPoint(60, 60))

        # 分组打开独立侧边面板，根窗口自身不再向下长高。
        root_height = window.height()
        toggle, _submenu, _title = window._groups[0]
        toggle.click()
        QApplication.processEvents()
        self.assertEqual(window.height(), root_height)
        self.assertEqual(len(window._submenu_panels), 1)
        panel = window._submenu_panels[0]
        self.assertTrue(panel.isWindow())
        self.assertIsNone(panel.parentWidget())
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel._title, "切换表情")
        self.assertIn(panel.placement_side, {"left", "right"})
        requested_rect = QRect(panel._requested_pos, panel.size())
        self.assertTrue(
            requested_rect.right() <= window.geometry().left() + 10
            or requested_rect.left() >= window.geometry().right() - 10
        )

    def test_context_menu_window_item_triggers_action_and_closes(self) -> None:
        host = self._make_menu_host()
        host.show()
        host._show_context_menu(QPoint(20, 20))
        window = self._track(host._menu_window)
        toggle, _submenu, _title = window._groups[0]
        toggle.click()
        QApplication.processEvents()
        panel = window._submenu_panels[0]

        button = next(b for b, a in window._action_buttons if a.text() == "开心")
        button.click()
        QApplication.processEvents()
        self.assertFalse(window.isVisible())
        try:
            panel_visible = panel.isVisible()
        except RuntimeError:
            panel_visible = False
        self.assertFalse(panel_visible)
        self.assertEqual(window._submenu_panels, [])
        self.assertEqual(host.moods, ["happy"])

    def test_context_menu_cascades_nested_groups_without_growing_root(self) -> None:
        host = self._make_menu_host()
        host.show()
        host._show_context_menu(QPoint(20, 20))
        window = self._track(host._menu_window)
        root_height = window.height()

        display_button = next(
            button
            for button, _menu, title in window._menu_groups
            if title == "显示与立绘"
        )
        display_button.click()
        QApplication.processEvents()
        self.assertEqual(window.height(), root_height)
        self.assertEqual(len(window._submenu_panels), 1)

        size_button = next(
            button
            for button, _menu, title in window._groups
            if title == "窗口大小 · 100%"
        )
        size_button.click()
        QApplication.processEvents()
        self.assertEqual(window.height(), root_height)
        self.assertEqual(len(window._submenu_panels), 2)
        first, second = window._submenu_panels
        self.assertEqual(second.parent_surface, first)
        self.assertEqual(second.placement_side, first.placement_side)

        # 同层切换目录时复用级联深度，不累积已经关闭的面板。
        vision_button = next(
            button
            for button, _menu, title in window._menu_groups
            if title == "识图与观察"
        )
        vision_button.click()
        QApplication.processEvents()
        self.assertEqual(len(window._submenu_panels), 1)
        self.assertEqual(window._submenu_panels[0]._title, "识图与观察")
        self.assertEqual(window.height(), root_height)

    def test_context_menu_side_panel_chooses_screen_safe_direction(self) -> None:
        from meapet.desktop.menu_window import calculate_submenu_position

        area = QRect(0, 0, 800, 600)
        panel_size = QSize(240, 260)

        parent = QRect(30, 80, 280, 420)
        trigger = QRect(50, 200, 220, 44)
        position, side = calculate_submenu_position(
            parent, trigger, panel_size, area
        )
        self.assertEqual(side, "right")
        self.assertGreaterEqual(position.x(), parent.right() - 10)
        self.assertTrue(area.contains(QRect(position, panel_size)))

        parent = QRect(520, 80, 280, 420)
        trigger = QRect(540, 520, 220, 44)
        position, side = calculate_submenu_position(
            parent, trigger, panel_size, area
        )
        self.assertEqual(side, "left")
        self.assertLessEqual(position.x() + panel_size.width(), parent.left() + 10)
        self.assertTrue(area.contains(QRect(position, panel_size)))

        # 更深层继承向左方向，避免折返覆盖根菜单。
        next_parent = QRect(position, panel_size)
        next_trigger = QRect(
            next_parent.left() + 20,
            next_parent.top() + 80,
            next_parent.width() - 40,
            44,
        )
        next_position, next_side = calculate_submenu_position(
            next_parent,
            next_trigger,
            panel_size,
            area,
            preferred_side=side,
        )
        self.assertEqual(next_side, "left")
        self.assertTrue(area.contains(QRect(next_position, panel_size)))

    def test_context_menu_uses_readable_type_and_row_height(self) -> None:
        from meapet.desktop.theme import MENU_STYLE

        menu = self._track(QMenu())
        menu.setStyleSheet(MENU_STYLE)
        action = menu.addAction("看看我在干嘛")
        menu.show()
        QApplication.processEvents()

        self.assertGreaterEqual(menu.font().pixelSize(), 14)
        self.assertGreaterEqual(menu.actionGeometry(action).height(), 38)

    def test_cloud_consent_dialog_fits_max_font_scale_and_has_balanced_actions(
        self,
    ) -> None:
        from meapet.desktop.dialogs import CloudVisionConsentDialog
        from meapet.ui_theme import set_ui_font_scale

        self.addCleanup(set_ui_font_scale, 1.0)
        set_ui_font_scale(1.5)
        dialog = self._track(CloudVisionConsentDialog(timeout_seconds=5))
        dialog.show()
        dialog._timer.stop()
        QApplication.processEvents()

        self.assertGreaterEqual(
            dialog.width(),
            dialog.minimumSizeHint().width(),
        )
        self.assertGreaterEqual(
            dialog.height(),
            dialog.minimumSizeHint().height(),
        )
        self.assertGreater(dialog.maximumWidth(), dialog.minimumWidth())
        self.assertEqual(dialog.allow_button.width(), dialog.cancel_button.width())

    def test_capture_and_size_dialogs_fit_max_font_scale(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog
        from meapet.desktop.widgets import SizeScaleDialog
        from meapet.ui_theme import set_ui_font_scale

        self.addCleanup(set_ui_font_scale, 1.0)
        set_ui_font_scale(1.5)
        dialogs = (
            self._track(
                CaptureScopeConsentDialog(
                    timeout_seconds=5,
                    window_provider=lambda: (),
                )
            ),
            self._track(SizeScaleDialog(1.0)),
        )
        for dialog in dialogs:
            dialog.show()
            timer = getattr(dialog, "_timer", None)
            if timer is not None:
                timer.stop()
            QApplication.processEvents()
            with self.subTest(dialog=type(dialog).__name__):
                self.assertGreaterEqual(
                    dialog.width(),
                    dialog.minimumSizeHint().width(),
                )
                self.assertGreaterEqual(
                    dialog.height(),
                    dialog.minimumSizeHint().height(),
                )
                self.assertGreater(
                    dialog.maximumWidth(),
                    dialog.minimumWidth(),
                )

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

        holder = {}

        def select_region(_parent, _initial_region):
            dialog = holder["dialog"]
            self.assertTrue(dialog.countdown_paused)
            before = dialog.remaining_seconds
            dialog._tick()
            self.assertEqual(dialog.remaining_seconds, before)
            return {"x": -120, "y": 40, "width": 1280, "height": 720}

        dialog = self._track(
            CaptureScopeConsentDialog(
                requested_scope="full_screen",
                timeout_seconds=15,
                region_selector=select_region,
                window_provider=lambda: (),
            )
        )
        holder["dialog"] = dialog
        self.assertEqual(dialog.scope_combo.currentData(), "full_screen")
        self.assertEqual(dialog.countdown_label.text(), "15 秒后自动取消。")
        dialog._tick()
        self.assertEqual(dialog.countdown_label.text(), "14 秒后自动取消。")
        dialog.scope_combo.setCurrentIndex(
            dialog.scope_combo.findData("region")
        )

        dialog.show()
        QApplication.processEvents()
        remaining = dialog.remaining_seconds
        dialog.select_region_button.click()
        self.assertEqual(dialog.remaining_seconds, remaining)
        self.assertFalse(dialog.countdown_paused)
        self.assertIn("1280 × 720", dialog.region_summary.text())
        dialog._timer.stop()
        dialog.allow_button.click()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.approval.scope, "region")
        self.assertEqual(
            dialog.approval.region,
            {"x": -120, "y": 40, "width": 1280, "height": 720},
        )
        self.assertEqual(dialog.approval.application, "")

    def test_capture_consent_keeps_modal_result_while_region_selector_runs(
        self,
    ) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog

        selector_calls = []

        def select_region(parent, _initial_region):
            selector_calls.append(
                {
                    "visible": parent.isVisible(),
                    "opacity": parent.windowOpacity(),
                }
            )
            # 模拟用户完成拖选后再点击允许。排到下一轮事件循环，确保
            # 点击发生在区域选择器返回、授权框恢复交互之后。
            QTimer.singleShot(0, parent.allow_button.click)
            return {"x": 10, "y": 20, "width": 300, "height": 200}

        dialog = self._track(
            CaptureScopeConsentDialog(
                requested_scope="full_screen",
                timeout_seconds=15,
                region_selector=select_region,
                window_provider=lambda: (),
            )
        )

        def select_and_allow():
            index = dialog.scope_combo.findData("region")
            dialog.scope_combo.setCurrentIndex(index)
            dialog.scope_combo.activated.emit(index)

        safety_timer = QTimer(dialog)
        safety_timer.setSingleShot(True)
        safety_timer.timeout.connect(dialog.reject)
        safety_timer.start(1000)
        QTimer.singleShot(0, select_and_allow)

        result = dialog.exec_()
        safety_timer.stop()

        self.assertEqual(result, QDialog.Accepted)
        self.assertEqual(len(selector_calls), 1)
        self.assertTrue(selector_calls[0]["visible"])
        self.assertLessEqual(selector_calls[0]["opacity"], 0.01)
        self.assertEqual(dialog.windowOpacity(), 1.0)
        self.assertEqual(
            dialog.approval.region,
            {"x": 10, "y": 20, "width": 300, "height": 200},
        )

    def test_capture_consent_lists_windows_and_pauses_during_refresh(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog
        from meapet.watcher.capture import CaptureWindow

        holder = {}

        def windows():
            dialog = holder["dialog"]
            self.assertTrue(dialog.countdown_paused)
            before = dialog.remaining_seconds
            dialog._tick()
            self.assertEqual(dialog.remaining_seconds, before)
            return (
                CaptureWindow(101, "main.py - Visual Studio Code", "Code.exe", 10),
                CaptureWindow(202, "项目 - 记事本", "notepad.exe", 20),
            )

        dialog = self._track(
            CaptureScopeConsentDialog(
                timeout_seconds=5,
                region_selector=lambda _parent, _initial: None,
                window_provider=windows,
            )
        )
        holder["dialog"] = dialog
        dialog.show()
        QApplication.processEvents()
        remaining = dialog.remaining_seconds
        index = dialog.scope_combo.findData("application")
        dialog.scope_combo.setCurrentIndex(index)
        dialog.scope_combo.activated.emit(index)

        self.assertEqual(dialog.remaining_seconds, remaining)
        self.assertFalse(dialog.countdown_paused)
        self.assertEqual(dialog.application_combo.count(), 2)
        self.assertIn("Code.exe", dialog.application_combo.itemText(0))
        self.assertFalse(hasattr(dialog, "application_input"))
        dialog.application_combo.setCurrentIndex(1)
        dialog._timer.stop()
        dialog.allow_button.click()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.approval.scope, "application")
        self.assertEqual(dialog.approval.application, "项目 - 记事本")

    def test_capture_consent_pauses_while_scope_menu_is_open(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog

        dialog = self._track(
            CaptureScopeConsentDialog(
                timeout_seconds=5,
                region_selector=lambda _parent, _initial: None,
                window_provider=lambda: (),
            )
        )
        dialog.show()
        QApplication.processEvents()
        remaining = dialog.remaining_seconds

        dialog.scope_combo.popup_opened.emit()
        dialog._tick()
        self.assertEqual(dialog.remaining_seconds, remaining)
        self.assertTrue(dialog.countdown_paused)
        self.assertIn("已暂停", dialog.countdown_label.text())

        dialog.scope_combo.popup_closed.emit()
        dialog._timer.stop()
        dialog._tick()
        self.assertEqual(dialog.remaining_seconds, remaining - 1)
        self.assertFalse(dialog.countdown_paused)

    def test_capture_consent_removes_timeout_after_manual_scope_change(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog
        from meapet.watcher.capture import CaptureWindow

        dialog = self._track(
            CaptureScopeConsentDialog(
                timeout_seconds=5,
                region_selector=lambda _parent, _initial: None,
                window_provider=lambda: (
                    CaptureWindow(101, "项目 - 记事本", "notepad.exe", 20),
                ),
            )
        )
        dialog.show()
        QApplication.processEvents()
        remaining = dialog.remaining_seconds

        index = dialog.scope_combo.findData("application")
        dialog.scope_combo.setCurrentIndex(index)
        dialog.scope_combo.activated.emit(index)
        dialog.scope_combo.popup_closed.emit()
        dialog._tick()

        self.assertTrue(dialog.countdown_label.isHidden())
        self.assertFalse(dialog._timer.isActive())
        self.assertEqual(dialog.remaining_seconds, remaining)

    def test_capture_consent_never_requests_geometry_below_its_minimum(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog

        dialog = self._track(
            CaptureScopeConsentDialog(
                timeout_seconds=5,
                region_selector=lambda _parent, _initial: None,
                window_provider=lambda: (),
            )
        )
        dialog.setMinimumHeight(dialog.height() + 19)
        preferred_height = dialog.height() + 37

        with patch.object(
            dialog,
            "sizeHint",
            return_value=QSize(dialog.width(), preferred_height),
        ), patch.object(dialog, "resize") as resize:
            dialog._resize_to_content()

        requested_height = resize.call_args.args[1]
        self.assertGreaterEqual(requested_height, dialog.minimumHeight())
        self.assertGreaterEqual(requested_height, preferred_height)

    def test_agent_requested_region_still_requires_a_local_mouse_drag(self) -> None:
        from meapet.desktop.dialogs import CaptureScopeConsentDialog

        dialog = self._track(
            CaptureScopeConsentDialog(
                requested_scope="region",
                requested_region={"x": 10, "y": 20, "width": 800, "height": 600},
                region_selector=lambda _parent, _initial: None,
                window_provider=lambda: (),
            )
        )
        self.assertIn("仍需由你重新拖拽确认", dialog.region_summary.text())

        dialog.allow_button.click()

        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertIsNone(dialog.approval)
        self.assertIn("拖拽选择区域", dialog.validation_label.text())

    def test_cloud_capture_consent_includes_scope_and_uses_five_seconds(self) -> None:
        from meapet.desktop.dialogs import CloudCaptureScopeConsentDialog
        from meapet.ui_theme import get_ui_font_scale, set_ui_font_scale

        previous_scale = get_ui_font_scale()
        set_ui_font_scale(1.0)
        self.addCleanup(set_ui_font_scale, previous_scale)
        dialog = self._track(CloudCaptureScopeConsentDialog())
        self.assertEqual(dialog.remaining_seconds, 5)
        self.assertEqual(dialog.scope_combo.currentData(), "full_screen")
        self.assertIn("云端", dialog.windowTitle())
        self.assertLessEqual(dialog.width(), 460)
        self.assertLess(dialog.height(), 430)
        self.assertTrue(dialog.region_frame.isHidden())
        self.assertTrue(dialog.application_frame.isHidden())
        self.assertTrue(dialog.validation_label.isHidden())

        stylesheet = dialog.styleSheet()
        for selector in (
            "QDialog#CaptureScopeConsentRoot",
            "QFrame#SectionCard",
            "QComboBox",
            "QPushButton#SelectRegionButton",
            "QPushButton#RefreshWindowsButton",
            "QLabel#ConsentValidation",
            "QComboBox::down-arrow",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)

        dialog.show()
        dialog._timer.stop()
        QApplication.processEvents()
        compact_height = dialog.height()
        dialog.scope_combo.setCurrentIndex(
            dialog.scope_combo.findData("region")
        )
        QApplication.processEvents()
        self.assertFalse(dialog.region_frame.isHidden())
        self.assertGreater(
            dialog.height(),
            compact_height,
            {
                "dialog_size_hint": dialog.sizeHint().height(),
                "outer_total_hint": dialog._outer_layout.totalSizeHint().height(),
                "content_hint": dialog._content_layout.sizeHint().height(),
                "region_hint": dialog.region_frame.sizeHint().height(),
                "region_height": dialog.region_frame.height(),
            },
        )

        dialog.scope_combo.setCurrentIndex(
            dialog.scope_combo.findData("application")
        )
        QApplication.processEvents()
        application_height = dialog.height()
        self.assertFalse(dialog.application_frame.isHidden())
        self.assertGreater(application_height, compact_height)
        dialog.allow_button.click()
        QApplication.processEvents()
        self.assertFalse(dialog.validation_label.isHidden())
        self.assertGreater(dialog.height(), application_height)

        dialog.scope_combo.setCurrentIndex(
            dialog.scope_combo.findData("full_screen")
        )
        QApplication.processEvents()
        self.assertEqual(dialog.height(), compact_height)

    def test_watcher_cloud_confirmation_uses_themed_safe_dialog(self) -> None:
        from meapet.desktop.dialogs import CaptureApproval
        from meapet.desktop.watch_ctrl import PetWatcherMixin

        pet = type("WatcherHost", (PetWatcherMixin,), {})()
        pet.config = {"watcher": {"allow_cloud": True}}
        pet._is_cloud_vision = lambda: True
        pet._show_bubble = unittest.mock.Mock()
        pet._watcher = unittest.mock.Mock()
        approval = CaptureApproval(
            "region",
            {"x": 10, "y": 20, "width": 800, "height": 600},
            "",
        )

        with patch(
            "meapet.desktop.watch_ctrl.confirm_cloud_capture_scope",
            return_value=approval,
        ) as confirm, patch(
            "PyQt5.QtWidgets.QMessageBox.question",
            side_effect=AssertionError("不应调用系统确认框"),
        ):
            self.assertTrue(pet._confirm_cloud_capture())
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.kwargs["timeout_seconds"], 5)
        self.assertEqual(pet._watcher.capture_scope, "region")
        self.assertEqual(pet._watcher.capture_region, approval.region)



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
        from meapet.ui_theme import PALETTE

        bubble = self._track(DialogueBox())
        bubble.show_text("今天也要加油喵", duration_ms=0, mood="happy")
        self.assertEqual(bubble._container.mood, "happy")
        # 情绪描边色跟随语义令牌，而不是写死的裸色值。
        self.assertEqual(
            MOOD_BORDER_COLORS["happy"].lower(),
            PALETTE["secondary"].lower(),
        )
        self.assertEqual(
            MOOD_BORDER_COLORS["neutral"].lower(),
            PALETTE["primary"].lower(),
        )
        self.assertNotEqual(
            MOOD_BORDER_COLORS["happy"].lower(),
            MOOD_BORDER_COLORS["neutral"].lower(),
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

        os.environ.pop("MEA_PET_REDUCED_MOTION", None)
        self.assertTrue(resolve_reduced_motion(True))
        self.assertFalse(resolve_reduced_motion(False) and os.environ.get("MEA_PET_REDUCED_MOTION") == "force")

    def test_reduced_motion_dismisses_bubble_without_fade_timer(self) -> None:
        from meapet.desktop.widgets import DialogueBox

        dismissed = []
        with patch.dict(os.environ, {"MEA_PET_REDUCED_MOTION": "1"}):
            bubble = self._track(DialogueBox())
            bubble.dismissed.connect(lambda: dismissed.append(True))
            bubble.show_text("立即消失", duration_ms=0)
            bubble._start_fadeout()

        self.assertFalse(bubble._anim_timer.isActive())
        self.assertFalse(bubble.isVisible())
        self.assertEqual(dismissed, [True])

    def test_loading_disabled_vision_config_keeps_mode_options_collapsed(self) -> None:
        from wizard.page_vision import VisionPage

        page = self._track(VisionPage())
        page.show()
        QApplication.processEvents()
        page.apply_config({}, {"enabled": False})
        QApplication.processEvents()

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
                "prefer_model_voice_translation": True,
            }
        )

        page = wizard.tts_page
        self.assertTrue(page.translation_enabled_cb.isChecked())
        self.assertTrue(page.prefer_model_voice_cb.isChecked())
        self.assertEqual(page.translate_target_combo.currentData(), "zh")
        self.assertIn("输出语言不受支持", page.translation_enabled_cb.text())
        self.assertFalse(hasattr(page, "translate_key"))

        tts_config = wizard.collect_config()["tts"]
        self.assertTrue(tts_config["translate_to_jp"])
        self.assertTrue(tts_config["prefer_model_voice_translation"])
        self.assertEqual(tts_config["translate_target_language"], "zh")
        self.assertNotIn("translate_api_key", tts_config)

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
