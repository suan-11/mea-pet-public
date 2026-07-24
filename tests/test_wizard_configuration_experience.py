"""配置中心本轮交互修复的回归契约（OpenAI 兼容版）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QMessageBox,
)


class WizardConfigurationExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self._widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QApplication.processEvents()

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    @staticmethod
    def _stop_startup_work(wizard) -> None:
        wizard.env_page._check_timer.stop()
        wizard._load_timer.stop()
        for timer in wizard.tts_page._startup_timers:
            timer.stop()

    # ------------------------------------------------------------------
    # 所有下拉框忽略滚轮
    # ------------------------------------------------------------------
    def test_all_wizard_combo_boxes_ignore_wheel_changes(self) -> None:
        from wizard.app import SetupWizard
        from wizard.widgets import WheelSafeComboBox

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)

        combos = wizard.findChildren(QComboBox)
        self.assertGreaterEqual(len(combos), 8)
        self.assertTrue(all(isinstance(combo, WheelSafeComboBox) for combo in combos))

        combo = wizard.tts_page.backend_combo
        combo.setCurrentIndex(0)
        event = Mock()
        combo.wheelEvent(event)
        self.assertEqual(combo.currentIndex(), 0)
        event.ignore.assert_called_once_with()

    # ------------------------------------------------------------------
    # 模型选择器可编辑并记住文本
    # ------------------------------------------------------------------
    def test_model_selector_is_editable_and_remembers_text(self) -> None:
        from wizard.page_llm import LLMPage

        page = self._track(LLMPage())
        self.assertTrue(page.model_combo.isEditable())
        page.model_combo.setEditText("custom-model-v1")
        self.assertEqual(page.model_combo.currentText(), "custom-model-v1")

    # ------------------------------------------------------------------
    # 模型限额有说明且默认为 4096
    # ------------------------------------------------------------------
    def test_model_limits_are_explained_and_default_to_4096(self) -> None:
        from meapet.config.store import normalize_config
        from wizard.page_llm import LLMPage

        page = self._track(LLMPage())
        self.assertEqual(page.max_tokens_input.value(), 4096)
        copy = " ".join(label.text() for label in page.findChildren(QLabel))
        self.assertIn("随机性", copy)
        self.assertIn("最大回复长度", copy)

    # ------------------------------------------------------------------
    # 现有配置在构造函数返回前已加载
    # ------------------------------------------------------------------
    def test_existing_config_is_loaded_before_constructor_returns(self) -> None:
        from wizard.app import SetupWizard

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "display": {"font_scale": 1.3},
                        "llm": {
                            "mode": "direct",
                            "direct": {
                                "provider": "custom",
                                "protocol": "openai_chat",
                                "api_base": "https://example.test/v1",
                                "model": "saved-model",
                            },
                        },
                        "tts": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            wizard = self._track(SetupWizard(config_path=path))
            self._stop_startup_work(wizard)

            self.assertFalse(wizard._load_timer.isActive())
            self.assertEqual(wizard.font_scale_slider.value(), 130)
            self.assertEqual(wizard.llm_page.model_combo.currentText(), "saved-model")
            self.assertEqual(
                wizard._existing_config["display"]["font_scale"],
                1.3,
            )

            wizard.font_scale_slider.setValue(125)
            with patch.object(
                wizard,
                "_configuration_issues",
                return_value={index: [] for index in range(4)},
            ), patch(
                "wizard.app.styled_message_box",
                return_value=QMessageBox.Ok,
            ):
                wizard._save()

            reopened = self._track(SetupWizard(config_path=path))
            self._stop_startup_work(reopened)
            self.assertEqual(reopened.font_scale_slider.value(), 125)

    # ------------------------------------------------------------------
    # 环境检测在 UI 线程外派发
    # ------------------------------------------------------------------
    def test_environment_startup_checks_are_dispatched_off_ui_thread(self) -> None:
        from wizard.page_env import EnvCheckPage

        env = self._track(EnvCheckPage())
        env._check_timer.stop()
        with patch.object(env, "_run_checks_impl") as checks, patch(
            "wizard.page_env.threading.Thread"
        ) as thread:
            env._run_checks()
        checks.assert_not_called()
        thread.assert_called_once()
        thread.return_value.start.assert_called_once_with()

    # ------------------------------------------------------------------
    # Python 3.13 是有效运行时
    # ------------------------------------------------------------------
    def test_python_313_is_a_valid_core_runtime_with_a_local_vits_advisory(self) -> None:
        from wizard.platform_info import (
            PYTHON_CHECK_NAME,
            platform_checklist,
            python_runtime_compatibility,
        )

        ok, status = python_runtime_compatibility(
            SimpleNamespace(major=3, minor=13, micro=3)
        )

        self.assertTrue(ok)
        self.assertIn("3.13.3", status)
        self.assertIn("VITS", status)
        self.assertEqual(PYTHON_CHECK_NAME, "Python 3.10+")
        names = [name for name, _hint, _required in platform_checklist()]
        self.assertIn(PYTHON_CHECK_NAME, names)
        self.assertNotIn("Python 3.10–3.12", names)

        too_old, old_status = python_runtime_compatibility(
            SimpleNamespace(major=3, minor=9, micro=19)
        )
        self.assertFalse(too_old)
        self.assertIn("3.10+", old_status)

    # ------------------------------------------------------------------
    # SpinBox 使用暗色主题和可访问高度
    # ------------------------------------------------------------------
    def test_wizard_spin_boxes_use_the_dark_theme_and_accessible_height(self) -> None:
        from meapet.ui_theme import MIN_TARGET_SIZE
        from wizard.app import SetupWizard
        from wizard.styles import WIZARD_STYLESHEET

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)
        spin_boxes = wizard.findChildren(QAbstractSpinBox)

        self.assertGreaterEqual(len(spin_boxes), 5)
        self.assertTrue(
            all(widget.minimumHeight() >= MIN_TARGET_SIZE for widget in spin_boxes)
        )
        for selector in (
            "QSpinBox,",
            "QDoubleSpinBox",
            "QSpinBox::up-button",
            "QSpinBox::down-button",
            "QDoubleSpinBox::up-button",
            "QDoubleSpinBox::down-button",
            "QSpinBox::up-arrow",
            "QSpinBox::down-arrow",
            "QComboBox::down-arrow",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, WIZARD_STYLESHEET)

    # ------------------------------------------------------------------
    # GSV 探测不在页面打开时调度
    # ------------------------------------------------------------------
    def test_slow_gsv_probe_is_not_scheduled_when_page_opens(self) -> None:
        from wizard.page_tts import TTSPage

        with patch.object(TTSPage, "_check_gsv") as check:
            page = self._track(TTSPage())
        check.assert_not_called()
        self.assertEqual(page._startup_timers, [])

    # ------------------------------------------------------------------
    # Vision 页无虚假高级开关或持久化范围表单
    # ------------------------------------------------------------------
    def test_vision_page_has_no_fake_advanced_toggle_or_persistent_scope_form(self) -> None:
        from wizard.page_vision import VisionPage

        page = self._track(VisionPage())
        self.assertFalse(hasattr(page, "advanced_toggle"))
        self.assertFalse(hasattr(page, "capture_scope_combo"))

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("inherit"))
        self.assertFalse(page.advanced_frame.isHidden())
        page.mode_combo.setCurrentIndex(page.mode_combo.findData("disabled"))
        self.assertTrue(page.advanced_frame.isHidden())

    # ------------------------------------------------------------------
    # 每个模型请求区都暴露连接测试
    # ------------------------------------------------------------------
    def test_every_model_request_area_exposes_a_connection_test(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)

        controls = (
            (wizard.llm_page.test_connection_btn, wizard.llm_page.connection_status),
            (
                wizard.backend_page.test_agent_connection_btn,
                wizard.backend_page.agent_connection_status,
            ),
            (wizard.tts_page.test_connection_btn, wizard.tts_page.connection_status),
            (
                wizard.vision_page.test_connection_btn,
                wizard.vision_page.connection_status,
            ),
        )
        for button, status in controls:
            with self.subTest(button=button.accessibleName()):
                self.assertTrue(button.text())
                self.assertTrue(button.accessibleName())
                self.assertTrue(status.accessibleName())

    # ------------------------------------------------------------------
    # 连接测试报告进度和结果且不阻塞 UI
    # ------------------------------------------------------------------
    def test_connection_test_reports_progress_and_result_without_blocking_ui(self) -> None:
        from wizard.app import SetupWizard
        from wizard.connection_test import ConnectionResult

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)
        future = Future()
        button = wizard.llm_page.test_connection_btn
        status = wizard.llm_page.connection_status

        def submit(coro):
            coro.close()
            return future

        with patch("meapet.async_runtime.submit", side_effect=submit):
            wizard._start_connection_test("direct", button, status)

        self.assertFalse(button.isEnabled())
        self.assertIn("正在测试", status.text())
        self.assertTrue(wizard._connection_test_jobs["direct"][1].isActive())

        future.set_result(ConnectionResult(True, "回复模型连接正常。"))
        wizard._poll_connection_test("direct")
        self.assertTrue(button.isEnabled())
        self.assertEqual(status.text(), "回复模型连接正常。")
        self.assertEqual(status.property("status"), "success")


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    """连接探测测试（OpenAI 兼容）。"""

    async def test_direct_probe_uses_real_protocol_shape_with_a_small_reply(self) -> None:
        from meapet.direct.types import TextDelta
        from wizard.connection_test import probe_connection

        captured = {}

        class Client:
            async def stream(self, request):
                captured["request"] = request
                yield TextDelta("OK")

            async def close(self):
                captured["closed"] = True

        config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "api_base": "https://models.example.test/v1",
                    "model": "reply-model",
                    "api_key": "secret",
                    "temperature": 0.7,
                    "max_tokens": 99999,
                },
            }
        }
        with patch(
            "meapet.direct.client.DirectProtocolClient",
            return_value=Client(),
        ):
            result = await probe_connection("direct", config)

        self.assertTrue(result.ok, result.message)
        self.assertLessEqual(captured["request"].max_tokens, 32)
        self.assertTrue(captured["closed"])

    async def test_vision_probe_uses_a_synthetic_image_not_a_screenshot(self) -> None:
        from meapet.direct.types import TextDelta
        from wizard.connection_test import probe_connection

        captured = {}

        class Client:
            async def stream(self, request):
                captured["request"] = request
                yield TextDelta("OK")

            async def close(self):
                pass

        config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "mimo",
                    "protocol": "openai_chat",
                    "api_base": "https://api.example.test/v1",
                    "model": "mimo-v2.5",
                    "api_key": "secret",
                },
            },
            "vision": {
                "mode": "inherit",
                "main_model_supports_images": True,
            },
        }
        with patch(
            "meapet.direct.client.DirectProtocolClient",
            return_value=Client(),
        ):
            result = await probe_connection("vision", config)

        self.assertTrue(result.ok, result.message)
        content = captured["request"].messages[-1]["content"]
        image = next(part for part in content if part["type"] == "image")
        self.assertEqual(image["media_type"], "image/png")
        self.assertGreater(len(image["data"]), 20)

    async def test_agent_probe_uses_openai_compatible_adapter(self) -> None:
        """Agent 探测现在通过 OpenAI 兼容适配器完成。"""
        from wizard.connection_test import probe_connection

        adapter = Mock()
        # OpenAIAdapter 使用 chat_stream 而非 probe
        adapter.chat_stream = unittest.mock.AsyncMock(return_value=Mock())
        adapter.chat_stream.return_value.__aiter__ = Mock(
            return_value=iter([])
        )
        adapter.close = unittest.mock.AsyncMock()
        config = {
            "llm": {
                "mode": "agent",
                "agent": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "secret",
                    "model": "gpt-4o-mini",
                    "timeout_seconds": 30,
                },
            }
        }
        with patch(
            "meapet.agent.factory.create_agent_adapter_from_config",
            return_value=adapter,
        ):
            result = await probe_connection("agent", config)

        self.assertTrue(result.ok, result.message)
        adapter.close.assert_awaited_once_with()

    async def test_tts_probe_synthesizes_a_short_sample(self) -> None:
        from wizard.connection_test import probe_connection

        tts = Mock()
        tts.enabled = True
        tts.speak_async = unittest.mock.AsyncMock(
            return_value=("/tmp/connection-test.wav", "zh")
        )
        config = {"tts": {"enabled": True, "engine": "mimo"}}
        with patch("meapet.tts.service.MeaTTS", return_value=tts):
            result = await probe_connection("tts", config)

        self.assertTrue(result.ok, result.message)
        tts.speak_async.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
