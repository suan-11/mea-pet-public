"""配置中心的 direct/Agent 互斥配置与截图范围。"""

from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication, QLabel, QMessageBox
from PyQt5.QtTest import QSignalSpy


class TestWizardConversationConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.app import SetupWizard

        self._config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_dir.cleanup)
        template = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )
        self.wizard = SetupWizard(
            config_path=Path(self._config_dir.name) / "profile.json",
            initial_config=template,
        )
        self.wizard._load_timer.stop()
        self.wizard.llm_page._status_timer.stop()
        self.wizard.env_page._check_timer.stop()
        for timer in self.wizard.tts_page._startup_timers:
            timer.stop()

    def tearDown(self):
        self.wizard.close()
        self.wizard.deleteLater()
        QApplication.processEvents()

    def test_direct_mode_saves_actual_protocol_endpoint_model_and_limits(self):
        page = self.wizard.llm_page
        self.wizard.backend_page.direct_radio.setChecked(True)
        page.set_backend("deepseek")
        page.set_protocol("openai_responses")
        page.endpoint_input.setText("https://models.example.test/v1")
        page.model_input.setText("custom-reply-model")
        page.temperature_input.setValue(0.35)
        page.max_tokens_input.setValue(2048)
        page.direct_api_key_input.setText("$CUSTOM_MODEL_KEY")

        config = self.wizard.collect_config()

        self.assertEqual(config["llm"]["mode"], "direct")
        self.assertEqual(
            config["llm"]["direct"],
            {
                "provider": "deepseek",
                "protocol": "openai_responses",
                "api_base": "https://models.example.test/v1",
                "host": "",
                "model": "custom-reply-model",
                "api_key": "$CUSTOM_MODEL_KEY",
                "temperature": 0.35,
                "max_tokens": 2048,
            },
        )
        self.assertEqual(config["llm"]["api_base"], "https://models.example.test/v1")
        self.assertEqual(config["llm"]["model"], "custom-reply-model")

    def test_collect_preserves_fields_not_owned_by_the_wizard(self):
        self.wizard._existing_config = {
            "live2d": {
                "enabled": False,
                "scale": 0.42,
                "model_dir": "D:/custom/live2d",
                "custom_live2d_key": "keep-live2d",
            },
            "display": {
                "scale": 0.73,
                "fps": 17,
                "size_factor": 1.4,
                "font_scale": 1.0,
                "reduced_motion": False,
                "custom_display_key": "keep-display",
            },
            "character": {
                "name": "自定义角色",
                "default_outfit": "99",
                "default_direction": "B",
            },
            "sprite_dir": "D:/custom/sprites",
            "plugin_config": {"enabled": True, "value": 42},
            "tts": {
                "engine": "gpt_sovits",
                "enabled": False,
                "sync_with_audio": True,
                "custom_tts_key": "keep-tts",
            },
            "vision": {
                "mode": "disabled",
                "custom_vision_key": "keep-vision",
            },
            "watcher": {
                "enabled": False,
                "custom_watcher_key": "keep-watcher",
            },
        }
        self.wizard.font_scale_slider.setValue(125)
        self.wizard.reduced_motion_cb.setChecked(True)

        config = self.wizard.collect_config()

        self.assertEqual(config["live2d"]["enabled"], False)
        self.assertEqual(config["live2d"]["scale"], 0.42)
        self.assertEqual(
            config["live2d"]["custom_live2d_key"],
            "keep-live2d",
        )
        self.assertEqual(config["display"]["scale"], 0.73)
        self.assertEqual(config["display"]["fps"], 17)
        self.assertEqual(config["display"]["size_factor"], 1.4)
        self.assertEqual(config["display"]["font_scale"], 1.25)
        self.assertTrue(config["display"]["reduced_motion"])
        self.assertEqual(config["character"]["name"], "自定义角色")
        self.assertEqual(config["sprite_dir"], "D:/custom/sprites")
        self.assertEqual(
            config["plugin_config"],
            {"enabled": True, "value": 42},
        )
        self.assertTrue(config["tts"]["sync_with_audio"])
        self.assertEqual(config["tts"]["custom_tts_key"], "keep-tts")
        self.assertEqual(
            config["vision"]["custom_vision_key"],
            "keep-vision",
        )
        self.assertEqual(
            config["watcher"]["custom_watcher_key"],
            "keep-watcher",
        )

    def test_agent_validation_continues_through_tts_and_vision(self):
        backend = self.wizard.backend_page
        backend.agent_radio.setChecked(True)
        backend.agent_base_url.setText("https://agent.example.test")
        self.wizard.tts_page.enable_cb.setChecked(True)
        self.wizard.tts_page.set_engine("mimo")
        self.wizard.tts_page.mimo_api_key_input.clear()
        vision = self.wizard.vision_page
        vision.enable_cb.setChecked(True)
        vision.mode_combo.setCurrentIndex(vision.mode_combo.findData("relay"))

        issues = self.wizard._configuration_issues()

        self.assertIn("MiMo TTS API Key", issues[self.wizard.TAB_VOICE])
        self.assertIn(
            "Agent 模式须由 Agent 直接读图",
            issues[self.wizard.TAB_VISION],
        )

    def test_direct_api_key_has_one_editable_source(self):
        page = self.wizard.llm_page
        self.wizard.backend_page.direct_radio.setChecked(True)
        page.set_backend("deepseek")
        page.direct_api_key_input.setText("single-source-key")

        config = self.wizard.collect_config()

        self.assertEqual(
            config["llm"]["direct"]["api_key"],
            "single-source-key",
        )
        self.assertEqual(config["llm"]["api_key"], "single-source-key")

    def test_provider_switch_restores_each_provider_draft(self):
        page = self.wizard.llm_page
        page.set_backend("deepseek")
        page.endpoint_input.setText("https://private.deepseek.test/v1")
        page.model_input.setText("private-deepseek-model")
        page.direct_api_key_input.setText("deepseek-draft-key")

        page.set_backend("mimo")
        self.assertEqual(page.endpoint_input.text(), "https://api.xiaomimimo.com/v1")
        self.assertEqual(page.model_input.text(), "mimo-v2.5")
        self.assertEqual(page.direct_api_key_input.text(), "")

        page.endpoint_input.setText("https://private.mimo.test/v1")
        page.direct_api_key_input.setText("mimo-draft-key")
        page.set_backend("deepseek")

        self.assertEqual(
            page.endpoint_input.text(),
            "https://private.deepseek.test/v1",
        )
        self.assertEqual(page.model_input.text(), "private-deepseek-model")
        self.assertEqual(page.direct_api_key_input.text(), "deepseek-draft-key")

    def test_agent_mode_preserves_direct_profile_and_collects_control_listener(self):
        self.wizard._existing_config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "mimo",
                    "protocol": "openai_chat",
                    "api_base": "https://saved.example/v1",
                    "host": "",
                    "model": "saved-model",
                    "api_key": "$SAVED_KEY",
                    "temperature": 0.2,
                    "max_tokens": 900,
                },
            }
        }
        page = self.wizard.backend_page
        page.agent_radio.setChecked(True)
        page.set_agent_kind("hermes")
        page.agent_base_url.setText("http://192.168.50.20:8642")
        page.agent_auth_token.setText("$HERMES_API_SERVER_KEY")
        page.agent_session_id.setText("session-a")
        page.agent_session_key.setText("memory-a")
        page.agent_history_turns.setValue(5)
        page.timeline_turns.setValue(9)
        page.control_enabled.setChecked(True)
        page.control_listen_host.setText("192.168.50.10")
        page.control_allowed_ip.setText("192.168.50.20")
        page.control_port.setValue(8765)
        page.control_auth_token.setText("$MEAPET_CONTROL_TOKEN")
        page.control_allow_http.setChecked(True)

        config = self.wizard.collect_config()

        self.assertEqual(config["llm"]["mode"], "agent")
        self.assertEqual(config["llm"]["agent"]["kind"], "hermes")
        self.assertEqual(
            config["llm"]["agent"]["base_url"],
            "http://192.168.50.20:8642",
        )
        self.assertEqual(config["llm"]["agent"]["history_turns"], 5)
        self.assertEqual(config["ui"]["timeline_turns"], 9)
        self.assertEqual(config["llm"]["direct"]["model"], "saved-model")
        self.assertEqual(
            config["agent_control"],
            {
                "enabled": True,
                "listen_host": "192.168.50.10",
                "port": 8765,
                "allowed_agent_ip": "192.168.50.20",
                "auth_token": "$MEAPET_CONTROL_TOKEN",
                "allow_insecure_http": True,
                "cert_file": "",
                "key_file": "",
                "ca_file": "",
            },
        )
        self.assertTrue(page.insecure_http_warning.isVisibleTo(page))

    def test_loading_agent_config_restores_mode_without_overwriting_inactive_direct(self):
        config = {
            "llm": {
                "mode": "agent",
                "backend": "hermes",
                "direct": {
                    "provider": "ollama",
                    "protocol": "ollama_chat",
                    "api_base": "",
                    "host": "http://10.0.0.2:11434",
                    "model": "local-model",
                    "api_key": "",
                    "temperature": 0.6,
                    "max_tokens": 700,
                },
                "agent": {
                    "kind": "hermes",
                    "base_url": "https://agent.example.test",
                    "auth_token": "$HERMES_API_SERVER_KEY",
                    "session_id": "resume-me",
                    "session_key": "memory-me",
                    "history_turns": 7,
                    "tls": {"verify": True, "ca_file": "agent-ca.pem"},
                },
            },
            "agent_control": {
                "enabled": False,
                "listen_host": "127.0.0.1",
                "port": 9000,
                "allowed_agent_ip": "127.0.0.1",
                "auth_token": "saved-control-token-value-long-enough",
                "allow_insecure_http": False,
                "cert_file": "server.pem",
                "key_file": "server-key.pem",
                "ca_file": "client-ca.pem",
            },
            "ui": {"timeline_turns": 7},
        }

        self.wizard.apply_conversation_config(config)
        collected = self.wizard.collect_config()

        self.assertTrue(self.wizard.backend_page.agent_radio.isChecked())
        self.assertEqual(collected["llm"]["agent"]["session_id"], "resume-me")
        self.assertEqual(collected["llm"]["direct"]["model"], "local-model")
        self.assertEqual(collected["agent_control"]["port"], 9000)
        self.assertEqual(self.wizard.backend_page.timeline_turns.value(), 7)
        self.assertEqual(collected["ui"]["timeline_turns"], 7)

    def test_openclaw_remote_plaintext_ws_requires_explicit_visible_opt_in(self):
        page = self.wizard.backend_page
        page.agent_radio.setChecked(True)
        page.set_agent_kind("openclaw")
        page.agent_base_url.setText("ws://192.168.50.20:18789")
        page.agent_auth_token.setText("$OPENCLAW_GATEWAY_TOKEN")

        self.assertFalse(page.agent_allow_insecure_ws.isChecked())
        self.assertFalse(page.insecure_ws_warning.isVisibleTo(page))

        page.agent_allow_insecure_ws.setChecked(True)
        config = self.wizard.collect_config()

        self.assertTrue(config["llm"]["agent"]["allow_insecure_ws"])
        self.assertTrue(page.insecure_ws_warning.isVisibleTo(page))

        self.wizard.apply_conversation_config(config)
        self.assertTrue(page.agent_allow_insecure_ws.isChecked())

    def test_control_token_can_be_revealed_copied_and_regenerated_before_save(self):
        from PyQt5.QtWidgets import QLineEdit

        page = self.wizard.backend_page
        page.control_auth_token.setText("old-control-token-value-long-enough")

        page._toggle_control_token_visibility()
        self.assertEqual(page.control_auth_token.echoMode(), QLineEdit.Normal)
        page._copy_control_token()
        self.assertEqual(
            QApplication.clipboard().text(),
            "old-control-token-value-long-enough",
        )
        page._regenerate_control_token()

        regenerated = page.control_auth_token.text()
        self.assertNotEqual(regenerated, "old-control-token-value-long-enough")
        self.assertGreaterEqual(len(regenerated), 43)

    def test_saving_emits_normalized_config_for_the_running_desktop(self):
        spy = QSignalSpy(self.wizard.config_saved)
        payload = {
            "llm": {
                "mode": "agent",
                "agent": {
                    "kind": "hermes",
                    "base_url": "http://127.0.0.1:8642",
                },
            }
        }

        with (
            unittest.mock.patch.object(
                self.wizard,
                "collect_config",
                return_value=payload,
            ),
            unittest.mock.patch.object(
                self.wizard,
                "_configuration_issues",
                return_value={index: [] for index in range(4)},
            ),
            unittest.mock.patch("wizard.app.os.path.isfile", return_value=False),
            unittest.mock.patch("meapet.config.store.save_config"),
            unittest.mock.patch("wizard.app.QMessageBox.information"),
        ):
            self.wizard._save()

        self.assertEqual(len(spy), 1)
        emitted = spy[0][0]
        self.assertEqual(emitted["llm"]["mode"], "agent")
        self.assertIn("direct", emitted["llm"])

    def test_incomplete_configuration_requires_explicit_save_confirmation(self):
        self.wizard.backend_page.agent_radio.setChecked(True)
        self.wizard.backend_page.agent_base_url.clear()

        with (
            unittest.mock.patch("wizard.app.os.path.isfile", return_value=False),
            unittest.mock.patch(
                "wizard.app.QMessageBox.question",
                return_value=QMessageBox.Cancel,
            ) as question,
            unittest.mock.patch("meapet.config.store.save_config") as save,
            unittest.mock.patch("wizard.app.QMessageBox.information"),
        ):
            self.wizard._save()

        question.assert_called_once()
        save.assert_not_called()

    def test_custom_config_path_is_loaded_and_used_for_save(self):
        from wizard.app import SetupWizard

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(
                '{"display":{"font_scale":1.3},'
                '"llm":{"mode":"direct","backend":"custom",'
                '"direct":{"provider":"custom","protocol":"openai_chat",'
                '"api_base":"https://profile.example/v1","host":"",'
                '"model":"profile-model","api_key":"",'
                '"temperature":0.4,"max_tokens":700}}}',
                encoding="utf-8",
            )
            wizard = SetupWizard(config_path=str(path))
            self.addCleanup(wizard.deleteLater)
            wizard._load_timer.stop()
            wizard.llm_page._status_timer.stop()
            wizard.env_page._check_timer.stop()
            for timer in wizard.tts_page._startup_timers:
                timer.stop()
            wizard._load_existing_config()

            self.assertEqual(wizard.config_path, str(path))
            self.assertEqual(wizard.font_scale_slider.value(), 130)
            self.assertEqual(wizard.llm_page.model_input.text(), "profile-model")

            with (
                unittest.mock.patch("meapet.config.store.save_config") as save,
                unittest.mock.patch("wizard.app.QMessageBox.information"),
            ):
                wizard._save()

            self.assertEqual(save.call_args.args[1], str(path))

    def test_save_uses_latest_disk_values_for_fields_outside_the_ui(self):
        from wizard.app import SetupWizard

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(
                '{"plugin_config":{"revision":1},'
                '"llm":{"mode":"direct","backend":"ollama",'
                '"direct":{"provider":"ollama",'
                '"protocol":"ollama_chat",'
                '"host":"http://127.0.0.1:11434",'
                '"api_base":"","model":"qwen3.5:4b",'
                '"api_key":"","temperature":0.7,"max_tokens":512}},'
                '"tts":{"enabled":false,"engine":"gpt_sovits"}}',
                encoding="utf-8",
            )
            wizard = SetupWizard(config_path=str(path))
            self.addCleanup(wizard.deleteLater)
            wizard._load_timer.stop()
            wizard.llm_page._status_timer.stop()
            wizard.env_page._check_timer.stop()
            for timer in wizard.tts_page._startup_timers:
                timer.stop()
            wizard._load_existing_config()

            path.write_text(
                '{"plugin_config":{"revision":2,"external":true},'
                '"live2d":{"enabled":false,"scale":0.41},'
                '"llm":{"mode":"direct","backend":"ollama",'
                '"direct":{"provider":"ollama",'
                '"protocol":"ollama_chat",'
                '"host":"http://127.0.0.1:11434",'
                '"api_base":"","model":"qwen3.5:4b",'
                '"api_key":"","temperature":0.7,"max_tokens":512}},'
                '"tts":{"enabled":false,"engine":"gpt_sovits"}}',
                encoding="utf-8",
            )

            with (
                unittest.mock.patch.object(
                    wizard,
                    "_configuration_issues",
                    return_value={index: [] for index in range(4)},
                ),
                unittest.mock.patch("meapet.config.store.save_config") as save,
                unittest.mock.patch("wizard.app.QMessageBox.information"),
            ):
                wizard._save()

            saved = save.call_args.args[0]
            self.assertEqual(
                saved["plugin_config"],
                {"revision": 2, "external": True},
            )
            self.assertFalse(saved["live2d"]["enabled"])
            self.assertEqual(saved["live2d"]["scale"], 0.41)

    def test_dirty_window_confirms_before_discarding_changes(self):
        self.wizard.show()
        QApplication.processEvents()
        self.assertFalse(self.wizard.is_dirty)
        self.wizard.llm_page.model_input.setText("unsaved-model")
        self.assertTrue(self.wizard.is_dirty)
        event = SimpleNamespace(
            accept=unittest.mock.Mock(),
            ignore=unittest.mock.Mock(),
        )

        with unittest.mock.patch(
            "wizard.app.QMessageBox.question",
            return_value=QMessageBox.Cancel,
        ) as question:
            self.wizard.closeEvent(event)

        question.assert_called_once()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        self.wizard._dirty = False

    def test_required_environment_failure_marks_environment_tab(self):
        required = next(
            name
            for name, _hint, is_required in self.wizard.env_page._checklist
            if is_required
        )

        self.wizard.env_page._set_item_status(required, False, "缺失")
        QApplication.processEvents()
        self.assertFalse(
            self.wizard.tabs.tabIcon(self.wizard.TAB_ENV).isNull()
        )

        self.wizard.env_page._set_item_status(required, True, "就绪")
        QApplication.processEvents()
        self.assertTrue(
            self.wizard.tabs.tabIcon(self.wizard.TAB_ENV).isNull()
        )

    def test_display_copy_distinguishes_live_and_restart_settings(self):
        copy = " ".join(
            label.text()
            for label in self.wizard.display_page.findChildren(QLabel)
        )
        self.assertIn("桌宠重启后应用", copy)
        self.assertIn("减少动画保存后立即应用", copy)

    def test_desktop_opens_wizard_with_active_config_path_and_values(self):
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        signal = SimpleNamespace(connect=unittest.mock.Mock())
        opened = SimpleNamespace(config_saved=signal, show=unittest.mock.Mock())
        host = SimpleNamespace(
            _config_path="D:/profiles/meapet.json",
            config={"llm": {"mode": "agent"}},
            _apply_runtime_config=unittest.mock.Mock(),
            _show_bubble=unittest.mock.Mock(),
        )

        with unittest.mock.patch(
            "wizard.app.SetupWizard",
            return_value=opened,
        ) as factory:
            PetWindowChromeMixin._reopen_setup_wizard(host)

        factory.assert_called_once_with(
            config_path="D:/profiles/meapet.json",
            initial_config=host.config,
        )
        signal.connect.assert_called_once_with(host._apply_runtime_config)
        opened.show.assert_called_once_with()


class TestWizardCaptureScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_screen_observer_scope_is_chosen_per_confirmation_not_persisted(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        watcher = page.collect("ollama", {})["watcher"]
        self.assertNotIn("capture", watcher)
        self.assertFalse(hasattr(page, "capture_scope_combo"))

    def test_vision_modes_are_explicit_and_inherit_never_saves_relay_backend(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(
            [page.mode_combo.itemData(i) for i in range(page.mode_combo.count())],
            ["disabled", "inherit", "relay"],
        )

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("inherit"))
        page.main_model_vision_cb.setChecked(True)
        inherited = page.collect(
            "custom",
            {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                },
            },
        )

        self.assertEqual(inherited["vision"]["mode"], "inherit")
        self.assertTrue(inherited["vision"]["main_model_supports_images"])
        self.assertEqual(inherited["vision"]["backend"], "")

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("relay"))
        page.backend_combo.setCurrentIndex(page.backend_combo.findData("ollama"))
        relayed = page.collect("custom", {"mode": "direct"})
        self.assertEqual(relayed["vision"]["mode"], "relay")
        self.assertEqual(relayed["vision"]["backend"], "ollama")

    def test_apply_config_restores_inherit_capability_without_enabling_relay_fields(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        page.apply_config(
            {
                "mode": "inherit",
                "main_model_supports_images": True,
                "backend": "mimo",
            },
            {"enabled": True},
        )

        self.assertEqual(page.mode_combo.currentData(), "inherit")
        self.assertTrue(page.main_model_vision_cb.isChecked())
        self.assertTrue(page.backend_combo.isHidden())


if __name__ == "__main__":
    unittest.main()
