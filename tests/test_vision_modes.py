"""视觉路由必须显式区分继承、独立中转与关闭。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestVisionRoutePolicy(unittest.TestCase):
    def test_inherit_requires_a_known_or_explicit_main_model_capability(self):
        from meapet.vision.policy import resolve_vision_route

        mimo = resolve_vision_route(
            {"mode": "inherit"},
            {
                "mode": "direct",
                "direct": {
                    "provider": "mimo",
                    "protocol": "openai_chat",
                    "model": "mimo-v2.5",
                },
            },
        )
        custom = resolve_vision_route(
            {"mode": "inherit"},
            {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "model": "private-model",
                },
            },
        )
        explicit = resolve_vision_route(
            {"mode": "inherit", "main_model_supports_images": True},
            {
                "mode": "direct",
                "direct": {"provider": "custom", "model": "private-model"},
            },
        )

        self.assertTrue(mimo.available)
        self.assertFalse(custom.available)
        self.assertEqual(custom.reason, "main_model_vision_not_confirmed")
        self.assertTrue(explicit.available)

    def test_agent_never_uses_a_separate_relay_model(self):
        from meapet.vision.policy import resolve_vision_route

        relay = resolve_vision_route(
            {"mode": "relay", "backend": "mimo"},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )
        inherit_unknown = resolve_vision_route(
            {"mode": "inherit"},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )
        inherit_confirmed = resolve_vision_route(
            {"mode": "inherit", "main_model_supports_images": True},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )

        self.assertFalse(relay.available)
        self.assertEqual(relay.reason, "agent_relay_forbidden")
        self.assertFalse(inherit_unknown.available)
        self.assertTrue(inherit_confirmed.available)

    def test_disabled_is_always_available_and_does_not_probe_models(self):
        from meapet.vision.policy import resolve_vision_route

        route = resolve_vision_route(
            {"mode": "disabled"},
            {"mode": "agent", "agent": {}},
        )

        self.assertTrue(route.available)
        self.assertEqual(route.mode, "disabled")


class TestVisionConfigMigration(unittest.TestCase):
    def test_legacy_active_watcher_migrates_to_relay_without_assuming_vision(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {"backend": "ollama", "enabled": True},
                "watcher": {"enabled": True},
            }
        )

        self.assertEqual(config["vision"]["mode"], "relay")
        self.assertTrue(config["watcher"]["enabled"])

    def test_disabled_mode_forces_watcher_off(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {"mode": "disabled"},
                "watcher": {"enabled": True},
            }
        )

        self.assertFalse(config["vision"]["enabled"])
        self.assertFalse(config["watcher"]["enabled"])

    def test_explicit_inherit_mode_and_capability_are_preserved(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {
                    "mode": "inherit",
                    "main_model_supports_images": True,
                }
            }
        )

        self.assertEqual(config["vision"]["mode"], "inherit")
        self.assertTrue(config["vision"]["enabled"])
        self.assertTrue(config["vision"]["main_model_supports_images"])


if __name__ == "__main__":
    unittest.main()
