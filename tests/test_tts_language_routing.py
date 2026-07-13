"""按回复段语言路由 TTS 与固定参考音频。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestReferenceAudioConfig(unittest.TestCase):
    def test_normalizes_language_map_and_migrates_legacy_single_reference(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "tts": {
                    "gsv_ref_wav": " ./legacy-ja.wav ",
                    "gsv_ref_lang": "日语",
                    "reference_audios": {
                        "zh-CN": {"path": " ./refs/zh.wav ", "text": "你好"},
                        "en": " ./refs/en.wav ",
                    },
                }
            }
        )

        refs = config["tts"]["reference_audios"]
        self.assertEqual(refs["zh"], {"path": "./refs/zh.wav", "text": "你好"})
        self.assertEqual(refs["en"], {"path": "./refs/en.wav", "text": ""})
        self.assertEqual(
            refs["jp"],
            {"path": "./legacy-ja.wav", "text": ""},
        )

    def test_normalizes_translation_target_and_supported_languages(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "tts": {
                    "translate_to_jp": True,
                    "translate_target_language": "ja-JP",
                    "supported_languages": ["zh-CN", "jp", "zh"],
                }
            }
        )

        self.assertTrue(config["tts"]["translate_to_jp"])
        self.assertEqual(config["tts"]["translate_target_language"], "jp")
        self.assertEqual(config["tts"]["supported_languages"], ["zh", "jp"])


class TestGsvReferenceRouting(unittest.TestCase):
    def test_explicit_segment_language_selects_its_fixed_reference(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            zh = root / "zh.wav"
            jp = root / "jp.wav"
            zh.write_bytes(b"RIFF" + b"\x00" * 64)
            jp.write_bytes(b"RIFF" + b"\x00" * 64)

            class Host(TtsGsvMixin):
                ref_dir = str(root / "automatic")
                voice_lang = "jp"
                gsv_ref_wav = ""
                gsv_ref_lang = "jp"
                reference_audios = {
                    "zh": {"path": str(zh), "text": "中文参考"},
                    "jp": {"path": str(jp), "text": "日本語の参照"},
                }

            host = Host()
            zh_result = host._get_ref_paths("neutral", voice_language="zh-CN")
            jp_result = host._get_ref_paths("neutral", voice_language="ja")

        self.assertEqual(zh_result, (str(zh), "中文参考", "中文"))
        self.assertEqual(jp_result, (str(jp), "日本語の参照", "日文"))

    def test_explicit_segment_language_never_falls_back_to_another_language(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "normal"
            folder.mkdir()
            (folder / "jp_normal.wav").write_bytes(b"RIFF" + b"\x00" * 64)
            (folder / "jp_normal.txt").write_text("こんにちは", encoding="utf-8")

            class Host(TtsGsvMixin):
                ref_dir = str(root)
                voice_lang = "jp"
                gsv_ref_wav = ""
                gsv_ref_lang = "jp"
                reference_audios = {}

            result = Host()._get_ref_paths(
                "neutral",
                voice_language="zh-CN",
            )

        self.assertEqual(result, (None, None, None))


class TestTtsLanguagePolicy(unittest.TestCase):
    def test_uses_original_text_when_output_language_is_supported(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "zh-CN",
            supported_languages=("ja", "zh"),
            translation_enabled=True,
            translation_api_configured=True,
            preferred_translation_language="ja",
        )

        self.assertEqual(plan.action, "direct")
        self.assertEqual(plan.requested_language, "zh")
        self.assertEqual(plan.synthesis_language, "zh")
        self.assertFalse(plan.requires_translation)

    def test_translates_only_to_an_available_preferred_language(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr-FR",
            supported_languages=("zh-CN", "jp"),
            translation_enabled=True,
            translation_api_configured=True,
            preferred_translation_language="ja-JP",
        )

        self.assertEqual(plan.action, "translate")
        self.assertEqual(plan.requested_language, "fr")
        self.assertEqual(plan.synthesis_language, "jp")
        self.assertTrue(plan.requires_translation)

    def test_skips_voice_when_translation_api_is_not_configured(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr",
            supported_languages=("jp",),
            translation_enabled=True,
            translation_api_configured=False,
            preferred_translation_language="jp",
        )

        self.assertEqual(plan.action, "skip")
        self.assertEqual(plan.synthesis_language, "")
        self.assertIn("translation_api_unavailable", plan.reason)

    def test_uses_first_available_language_if_preference_is_unavailable(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr",
            supported_languages=("zh-CN", "en-US"),
            translation_enabled=True,
            translation_api_configured=True,
            preferred_translation_language="jp",
        )

        self.assertEqual(plan.action, "translate")
        self.assertEqual(plan.synthesis_language, "zh")


class TestTtsWorkerLanguage(unittest.TestCase):
    def test_worker_forwards_segment_language_without_mutating_global_language(self):
        from meapet.desktop.workers import TTSWorker

        captured = {}

        class TTS:
            voice_lang = "jp"

            async def speak_async(
                self,
                text,
                mood="neutral",
                style="",
                language="",
            ):
                captured.update(
                    text=text,
                    mood=mood,
                    style=style,
                    language=language,
                    global_language=self.voice_lang,
                )
                return ("/tmp/language.wav", "zh")

        worker = TTSWorker(
            TTS(),
            "你好",
            mood="happy",
            style="轻声",
            language="zh-CN",
        )
        result = asyncio.run(worker._run())

        self.assertEqual(result, "/tmp/language.wav|zh")
        self.assertEqual(captured["language"], "zh-CN")
        self.assertEqual(captured["global_language"], "jp")


class TestMeaTtsLanguageOverride(unittest.TestCase):
    def test_translation_endpoint_rejects_unsafe_base_urls(self):
        from meapet.tts.service import MeaTTS

        tts = MeaTTS.__new__(MeaTTS)
        for base_url in (
            "file:///tmp/private.txt",
            "https://user:password@example.test/v1",
            "https://example.test/v1?token=secret",
            "https://example.test/v1#fragment",
        ):
            with self.subTest(base_url=base_url):
                tts.translate_api_base = base_url
                with self.assertRaisesRegex(ValueError, "translation API"):
                    tts._translation_endpoint()

        tts.translate_api_base = "https://example.test/v1"
        self.assertEqual(
            tts._translation_endpoint(),
            "https://example.test/v1/chat/completions",
        )

    def test_mimo_async_uses_per_call_language_instead_of_configured_default(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(
                os.environ,
                {
                    "MIMO_API_KEY": "",
                    "XIAOMIMIMO_API_KEY": "",
                    "MEAPET_API_KEY": "",
                },
                clear=False,
            ):
                tts = MeaTTS(
                    {
                        "tts": {
                            "enabled": True,
                            "engine": "mimo",
                            "api_key": "test-key-not-real",
                            "voice_lang": "jp",
                            "translate_to_jp": True,
                            "output_dir": td,
                        }
                    }
                )

            with mock.patch.object(
                tts,
                "_speak_mimo_async",
                new_callable=mock.AsyncMock,
                return_value=(str(Path(td) / "zh.wav"), "zh"),
            ) as speak:
                result = asyncio.run(
                    tts.speak_async("你好", language="zh-CN")
                )

        self.assertEqual(result[1], "zh")
        self.assertEqual(speak.await_args.kwargs["lang_tag"], "zh")
        self.assertEqual(speak.await_args.kwargs["voice_language"], "zh")

    def test_unsupported_language_is_translated_before_synthesis(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "voice_lang": "jp",
                        "supported_languages": ["jp"],
                        "translate_to_jp": True,
                        "translate_api_key": "translate-key-not-real",
                        "output_dir": td,
                    }
                }
            )

            with (
                mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new_callable=mock.AsyncMock,
                    return_value="bonjour の翻訳",
                ) as translate,
                mock.patch.object(
                    tts,
                    "_speak_mimo_async",
                    new_callable=mock.AsyncMock,
                    return_value=(str(Path(td) / "jp.wav"), "jp"),
                ) as speak,
            ):
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr-FR")
                )

        self.assertEqual(result[1], "jp")
        translate.assert_awaited_once_with("bonjour", "fr", "jp")
        self.assertEqual(speak.await_args.args[0], "bonjour の翻訳")
        self.assertEqual(speak.await_args.kwargs["lang_tag"], "jp")

    def test_translation_failure_skips_audio_instead_of_synthesizing_original(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "voice_lang": "jp",
                        "supported_languages": ["jp"],
                        "translate_to_jp": True,
                        "translate_api_key": "translate-key-not-real",
                        "output_dir": td,
                    }
                }
            )

            with (
                mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new_callable=mock.AsyncMock,
                    return_value="",
                ),
                mock.patch.object(
                    tts,
                    "_speak_mimo_async",
                    new_callable=mock.AsyncMock,
                ) as speak,
            ):
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr")
                )

        self.assertIsNone(result)
        speak.assert_not_awaited()

    def test_missing_translation_api_skips_unsupported_language(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(
                os.environ,
                {
                    "TRANSLATE_API_KEY": "",
                    "DEEPSEEK_API_KEY": "",
                    "MEAPET_API_KEY": "",
                },
                clear=False,
            ):
                tts = MeaTTS(
                    {
                        "tts": {
                            "enabled": True,
                            "engine": "mimo",
                            "api_key": "test-key-not-real",
                            "voice_lang": "jp",
                            "supported_languages": ["jp"],
                            "translate_to_jp": True,
                            "translate_api_key": "",
                            "output_dir": td,
                        }
                    }
                )

            with mock.patch.object(
                tts,
                "_speak_mimo_async",
                new_callable=mock.AsyncMock,
            ) as speak:
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr")
                )

        self.assertIsNone(result)
        speak.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
