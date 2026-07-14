"""独立机器翻译服务的轮换、重试与语言校验。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class TestTranslationService(unittest.TestCase):
    def test_tts_startup_does_not_load_translation_backend(self):
        from meapet.tts.service import MeaTTS
        from meapet.tts.translation import TranslationService

        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
            TranslationService,
            "_load_translate_func",
            side_effect=RuntimeError("translation package import reached network"),
        ) as loader:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "output_dir": output_dir,
                    }
                }
            )

        loader.assert_not_called()
        self.assertIsInstance(tts.translation_service, TranslationService)

    def test_lazy_translation_import_failure_is_isolated(self):
        from meapet.tts.translation import TranslationService

        with mock.patch(
            "meapet.tts.translation.importlib.util.find_spec",
            return_value=object(),
        ), mock.patch.object(
            TranslationService,
            "_load_translate_func",
            side_effect=RuntimeError("temporary translators bootstrap failure"),
        ) as loader:
            service = TranslationService()
            self.assertTrue(service.available)
            self.assertEqual(service.translate("你好", "zh", "jp"), "")

        loader.assert_called_once_with()
        self.assertFalse(service.available)

    def test_translation_backend_uses_a_fixed_region_without_geo_request(self):
        from meapet.tts.translation import TranslationService

        translate = mock.Mock(return_value="こんにちは")
        fake_package = SimpleNamespace(translate_text=translate)
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.dict(
            sys.modules,
            {"translators": fake_package},
        ):
            os.environ.pop("translators_default_region", None)
            loaded = TranslationService._load_translate_func()
            selected_region = os.environ.get("translators_default_region")

        self.assertIs(loaded, translate)
        self.assertEqual(selected_region, "CN")

    def test_rotates_across_at_most_three_non_llm_services(self):
        from meapet.tts.translation import TranslationService

        calls = []

        def translate(text, **kwargs):
            calls.append((text, kwargs))
            if kwargs["translator"] in {"alibaba", "iflytek"}:
                raise RuntimeError("temporary provider failure")
            return "こんにちは"

        service = TranslationService(translate_func=translate)
        result = service.translate("你好", "zh", "jp")

        self.assertEqual(result, "こんにちは")
        self.assertEqual(
            [kwargs["translator"] for _text, kwargs in calls],
            ["alibaba", "iflytek", "sogou"],
        )
        self.assertEqual(service.last_successful_provider, "sogou")
        self.assertNotIn("model", calls[0][1])
        self.assertNotIn("messages", calls[0][1])

    def test_last_successful_provider_is_prioritized_on_next_request(self):
        from meapet.tts.translation import TranslationService

        calls = []
        failures_remaining = 2

        def translate(_text, **kwargs):
            nonlocal failures_remaining
            calls.append(kwargs["translator"])
            if failures_remaining:
                failures_remaining -= 1
                raise RuntimeError("temporary provider failure")
            return "こんにちは"

        service = TranslationService(translate_func=translate)
        self.assertEqual(service.translate("你好", "zh", "jp"), "こんにちは")
        calls.clear()
        self.assertEqual(service.translate("再见", "zh", "jp"), "こんにちは")
        self.assertEqual(calls, ["sogou"])

    def test_failed_batch_rotates_start_for_the_next_request(self):
        from meapet.tts.translation import TranslationService

        calls = []

        def translate(_text, **kwargs):
            provider = kwargs["translator"]
            calls.append(provider)
            if provider != "bing":
                raise RuntimeError("temporary provider failure")
            return "こんにちは"

        service = TranslationService(translate_func=translate)
        self.assertEqual(service.translate("第一轮", "zh", "jp"), "")
        self.assertEqual(calls, ["alibaba", "iflytek", "sogou"])

        calls.clear()
        self.assertEqual(service.translate("第二轮", "zh", "jp"), "こんにちは")
        self.assertEqual(calls, ["bing"])

    def test_confirmed_wrong_language_counts_as_a_failed_attempt(self):
        from meapet.tts.translation import TranslationService

        calls = []

        def translate(_text, **kwargs):
            calls.append(kwargs["translator"])
            if kwargs["translator"] == "alibaba":
                return "还是中文呀"
            return "こんにちは"

        service = TranslationService(translate_func=translate)
        self.assertEqual(service.translate("你好", "zh", "jp"), "こんにちは")
        self.assertEqual(calls, ["alibaba", "iflytek"])

    def test_ambiguous_translation_result_is_accepted(self):
        from meapet.tts.translation import TranslationService

        calls = []

        def translate(_text, **kwargs):
            calls.append(kwargs["translator"])
            return "東京"

        service = TranslationService(translate_func=translate)
        self.assertEqual(service.translate("东京", "zh", "jp"), "東京")
        self.assertEqual(calls, ["alibaba"])

    def test_no_provider_failure_escapes_and_no_more_than_three_calls_are_made(self):
        from meapet.tts.translation import TranslationService

        calls = []

        def translate(_text, **kwargs):
            calls.append(kwargs["translator"])
            raise RuntimeError("all unavailable")

        service = TranslationService(translate_func=translate)
        self.assertEqual(service.translate("你好", "zh", "jp"), "")
        self.assertEqual(len(calls), 3)


class TestTranslationDependencyPackaging(unittest.TestCase):
    def test_default_launcher_installs_and_checks_translation_component(self):
        requirements = (ROOT / "linux_requirements.txt").read_text(encoding="utf-8")
        launcher = (ROOT / "启动桌宠.bat").read_text(encoding="utf-8")

        self.assertIn("translators>=6.0.4,<7", requirements)
        self.assertIn("find_spec('translators')", launcher)
        self.assertNotIn("; import translators", launcher)


if __name__ == "__main__":
    unittest.main()
