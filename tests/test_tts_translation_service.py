"""独立机器翻译服务的轮换、重试与语言校验。"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestTranslationService(unittest.TestCase):
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
        self.assertIn("import translators", launcher)


if __name__ == "__main__":
    unittest.main()
