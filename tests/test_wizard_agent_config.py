"""配置中心的 direct/Agent 互斥配置与截图范围。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication


class TestWizardConversationConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.app import SetupWizard

        self.wizard = SetupWizard()
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
        self.wizard.key_page_ds.key_input.setText("$CUSTOM_MODEL_KEY")

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
        }

        self.wizard.apply_conversation_config(config)
        collected = self.wizard.collect_config()

        self.assertTrue(self.wizard.backend_page.agent_radio.isChecked())
        self.assertEqual(collected["llm"]["agent"]["session_id"], "resume-me")
        self.assertEqual(collected["llm"]["direct"]["model"], "local-model")
        self.assertEqual(collected["agent_control"]["port"], 9000)

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


class TestWizardCaptureScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_screen_observer_collects_application_and_region_scope(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        page.capture_scope_combo.setCurrentIndex(
            page.capture_scope_combo.findData("application")
        )
        page.capture_application_input.setText("Visual Studio Code")

        application = page.collect("ollama", {})["watcher"]["capture"]
        self.assertEqual(
            application,
            {
                "scope": "application",
                "region": None,
                "application": "Visual Studio Code",
            },
        )

        page.capture_scope_combo.setCurrentIndex(
            page.capture_scope_combo.findData("region")
        )
        page.capture_x.setValue(-120)
        page.capture_y.setValue(40)
        page.capture_width.setValue(1280)
        page.capture_height.setValue(720)
        region = page.collect("ollama", {})["watcher"]["capture"]
        self.assertEqual(
            region,
            {
                "scope": "region",
                "region": {
                    "x": -120,
                    "y": 40,
                    "width": 1280,
                    "height": 720,
                },
                "application": "",
            },
        )


if __name__ == "__main__":
    unittest.main()
