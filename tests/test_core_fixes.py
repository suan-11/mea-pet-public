"""核心修复的轻量单测（标准库 unittest，无额外依赖）"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestConfigSettingsJson(unittest.TestCase):
    def test_example_config_is_unified(self):
        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIn("llm", data)
        self.assertIn("backend", data["llm"])
        self.assertIn("watcher", data)
        self.assertIn("bubble_duration_ms", data)
        self.assertIn("display", data)
        self.assertIn("size_factor", data["display"])
        self.assertIn("interval", data["watcher"])
        self.assertFalse(data["watcher"].get("enabled", True))
        self.assertFalse(data["watcher"].get("allow_cloud", True))


class TestRedactSecrets(unittest.TestCase):
    def test_mask_and_redact_text(self):
        from meapet.utils import mask_secret, redact_text, redact_mapping

        key = "sk-b6de2e45cef14655855425362c2704c1"
        masked = mask_secret(key)
        self.assertNotIn(key, masked)
        self.assertIn("…", masked)

        raw = f"Authorization: Bearer {key} used"
        red = redact_text(raw)
        self.assertNotIn(key, red)
        self.assertIn("Bearer", red)

        cfg = {"llm": {"api_key": key, "model": "deepseek-v4-flash"}, "ok": 1}
        red_cfg = redact_mapping(cfg)
        self.assertEqual(red_cfg["ok"], 1)
        self.assertNotEqual(red_cfg["llm"]["api_key"], key)
        self.assertEqual(cfg["llm"]["api_key"], key)  # 不修改原对象


class TestWatcherConfigMerge(unittest.TestCase):
    def test_unified_normalize(self):
        from meapet.config.store import normalize_config
        from meapet.utils import cloud_vision_allowed

        cfg = normalize_config({
            "watcher": {"enabled": True, "allow_cloud": False, "interval": {"min_ms": 1000, "max_ms": 2000}},
            "display": {"size_factor": 1.2},
        })
        self.assertTrue(cfg["watcher"]["enabled"])
        self.assertFalse(cfg["watcher"]["allow_cloud"])
        self.assertTrue(cfg["watcher"]["require_confirm"])
        self.assertEqual(cfg["watcher"]["interval"]["min_ms"], 1000)
        self.assertIn("bubble_duration_ms", cfg)
        self.assertEqual(cfg["display"]["size_factor"], 1.2)

        self.assertTrue(cloud_vision_allowed(cfg, is_cloud_backend=False))
        self.assertFalse(cloud_vision_allowed(cfg, is_cloud_backend=True))
        cfg["watcher"]["allow_cloud"] = True
        self.assertTrue(cloud_vision_allowed(cfg, is_cloud_backend=True))


class TestParseDecision(unittest.TestCase):
    def test_speak_with_strategy_and_search(self):
        from meapet.watcher.screen import parse_decision

        decision = "说\n关心进度\nReact hooks\n"
        speak, strategy, query = parse_decision(decision)
        self.assertTrue(speak)
        self.assertEqual(strategy, "关心进度")
        self.assertEqual(query, "React hooks")

    def test_silent(self):
        from meapet.watcher.screen import parse_decision

        speak, strategy, query = parse_decision("不说\n")
        self.assertFalse(speak)
        self.assertEqual(query, "")

    def test_no_search_token(self):
        from meapet.watcher.screen import parse_decision

        speak, strategy, query = parse_decision("说\n毒舌吐槽\n无")
        self.assertTrue(speak)
        self.assertEqual(strategy, "毒舌吐槽")
        self.assertEqual(query, "")


class TestCloudGateLogic(unittest.TestCase):
    def test_cloud_blocked_without_allow(self):
        from meapet.utils import cloud_vision_allowed

        settings = {"watcher": {"enabled": True, "allow_cloud": False}}
        self.assertFalse(cloud_vision_allowed(settings, True))

    def test_local_backend_not_blocked(self):
        from meapet.utils import cloud_vision_allowed

        settings = {"watcher": {"enabled": True, "allow_cloud": False}}
        self.assertTrue(cloud_vision_allowed(settings, False))


class TestAudioCacheCleanup(unittest.TestCase):
    def test_removes_old_and_overflow(self):
        from meapet.utils import cleanup_audio_cache

        with tempfile.TemporaryDirectory() as td:
            for i in range(5):
                path = os.path.join(td, f"mea_{i}.wav")
                with open(path, "wb") as f:
                    f.write(b"RIFF" + b"\x00" * 40)
                os.utime(path, (time.time() - 3600 * (i + 1), time.time() - 3600 * (i + 1)))
            old = os.path.join(td, "mea_old.wav")
            with open(old, "wb") as f:
                f.write(b"RIFF" + b"\x00" * 40)
            os.utime(old, (time.time() - 3600 * 100, time.time() - 3600 * 100))

            stats = cleanup_audio_cache(td, max_files=3, max_age_hours=48.0, prefix="mea_")
            self.assertGreaterEqual(stats["removed"], 1)
            remaining = [n for n in os.listdir(td) if n.endswith(".wav")]
            self.assertLessEqual(len(remaining), 3)
            self.assertNotIn("mea_old.wav", remaining)


class TestAffectionBounds(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Path(self._tmpdir.name) / "t.db"
        import meapet.memory.db as mem
        self.mem_mod = mem
        self._old = mem.DB_PATH
        mem.DB_PATH = str(self.db)
        self.m = mem.MeaMemory()

    def tearDown(self):
        try:
            self.m.close()
        except Exception:
            pass
        self.mem_mod.DB_PATH = self._old
        self._tmpdir.cleanup()

    def test_min_is_zero(self):
        self.assertEqual(self.mem_mod.AFFECTION_MIN, 0)

    def test_default_affection_not_locked_at_90(self):
        aff = self.m.get_affection()
        self.assertLess(aff, 90)
        self.assertGreaterEqual(aff, 0)

    def test_add_affection_no_deadlock(self):
        before = self.m.get_affection()
        self.m.add_affection(3)
        after = self.m.get_affection()
        self.assertGreaterEqual(after, before)
        self.assertLessEqual(after, self.mem_mod.AFFECTION_MAX)

    def test_daily_cap(self):
        for _ in range(20):
            self.m.add_affection(5)
        aff = self.m.get_affection()
        self.assertLessEqual(aff, self.mem_mod.AFFECTION_MAX)
        before = aff
        self.m.add_affection(5)
        self.assertEqual(self.m.get_affection(), before)


class TestChatBackendInit(unittest.TestCase):
    def test_mimo_available_with_key(self):
        from meapet.chat.engine import ChatEngine
        eng = ChatEngine(backend="mimo", api_key="test-key", api_base="https://example.com", model="m")
        self.assertTrue(eng.available)
        self.assertTrue(eng._backend_ready)

    def test_mimo_unavailable_without_key(self):
        from meapet.chat.engine import ChatEngine
        eng = ChatEngine(backend="mimo", api_key="", api_base="https://example.com")
        self.assertFalse(eng.available)
        self.assertTrue(eng._backend_ready)

    def test_deepseek_available_with_key(self):
        from meapet.chat.engine import ChatEngine
        eng = ChatEngine(backend="deepseek", api_key="sk-test", model="deepseek-v4-flash")
        self.assertTrue(eng.available)


# 会污染密钥解析的环境变量（测试必须隔离）
_SECRET_ENV_KEYS = (
    "MIMO_API_KEY",
    "XIAOMIMIMO_API_KEY",
    "DEEPSEEK_API_KEY",
    "MEAPET_API_KEY",
    "OPENAI_API_KEY",
    "TRANSLATE_API_KEY",
)


def _cleared_secret_env():
    """清除环境中的 API Key，避免真实密钥污染单测。"""
    return mock.patch.dict(os.environ, {k: "" for k in _SECRET_ENV_KEYS}, clear=False)


class TestMimoTTS(unittest.TestCase):
    def test_mimo_engine_health_needs_key(self):
        from meapet.tts.service import MeaTTS
        with _cleared_secret_env():
            tts = MeaTTS({
                "tts": {
                    "enabled": True,
                    "engine": "mimo",
                    "api_key": "",
                    "api_base": "https://api.xiaomimimo.com/v1",
                }
            })
            self.assertTrue(tts._mimo_mode)
            self.assertFalse(tts.health_check())

    def test_mimo_engine_health_ok_with_key(self):
        from meapet.tts.service import MeaTTS
        with _cleared_secret_env():
            tts = MeaTTS({
                "llm": {
                    "backend": "mimo",
                    "api_key": "sk-test-not-a-real-key",
                    "api_base": "https://api.xiaomimimo.com/v1",
                },
                "tts": {"enabled": True, "engine": "mimo", "voice": "冰糖"},
            })
            self.assertTrue(tts._mimo_mode)
            self.assertEqual(tts.mimo_api_key, "sk-test-not-a-real-key")
            self.assertTrue(tts.health_check())

    def test_mimo_speak_writes_wav_from_mock(self):
        import base64
        from meapet.tts.service import MeaTTS

        pcm = b"\x00\x00" * 50
        data_size = len(pcm)
        riff_size = 36 + data_size
        wav_bytes = (
            b"RIFF" + riff_size.to_bytes(4, "little") + b"WAVE"
            b"fmt " + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + (1).to_bytes(2, "little")
            + (24000).to_bytes(4, "little")
            + (48000).to_bytes(4, "little")
            + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data" + data_size.to_bytes(4, "little")
            + pcm
        )
        fake = {
            "choices": [{
                "message": {
                    "audio": {"data": base64.b64encode(wav_bytes).decode("ascii")}
                }
            }]
        }

        class _Resp:
            status_code = 200
            text = "ok"
            def json(self):
                return fake

        with tempfile.TemporaryDirectory() as td:
            with _cleared_secret_env():
                tts = MeaTTS({
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "sk-test-not-a-real-key",
                        "api_base": "https://api.xiaomimimo.com/v1",
                        "output_dir": td,
                        "translate_to_jp": False,
                        "voice_lang": "zh",
                    }
                })
            async def _fake_post(url, headers=None, json=None, timeout=None):
                return _Resp()

            with mock.patch("meapet.http_async.post_json", side_effect=_fake_post) as post:
                result = tts.speak("主人你好喵", mood="happy")
            self.assertIsNotNone(result)
            wav_path, lang = result
            self.assertEqual(lang, "zh")
            self.assertTrue(Path(wav_path).is_file())
            self.assertGreater(Path(wav_path).stat().st_size, 32)
            self.assertTrue(post.called)

    def test_mimo_speak_async_passes_model_generated_style(self):
        import asyncio

        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            with _cleared_secret_env():
                tts = MeaTTS({
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "api_base": "https://api.xiaomimimo.com/v1",
                        "output_dir": td,
                        "translate_to_jp": False,
                        "voice_lang": "zh",
                    }
                })

            with mock.patch.object(
                tts,
                "_speak_mimo_async",
                new_callable=mock.AsyncMock,
                return_value=(str(Path(td) / "styled.wav"), "zh"),
            ) as speak:
                result = asyncio.run(
                    tts.speak_async(
                        "欢迎回来",
                        mood="shy",
                        style="保持参考音色。情绪：害羞。",
                    )
                )

        self.assertEqual(result[1], "zh")
        self.assertEqual(speak.await_args.kwargs["mood"], "shy")
        self.assertEqual(
            speak.await_args.kwargs["style"],
            "保持参考音色。情绪：害羞。",
        )


class TestPlatformDetect(unittest.TestCase):
    def test_detect_platform_fields(self):
        try:
            from setup_wizard import detect_platform, platform_checklist
        except Exception as e:
            self.skipTest(f"setup_wizard import failed: {e}")
        info = detect_platform()
        for key in ("os_key", "os_label", "arch", "display", "is_windows", "is_linux", "is_macos"):
            self.assertIn(key, info)
        names = [n for n, _, _ in platform_checklist()]
        self.assertIn("Python 3.10+", names)
        self.assertIn("requests", names)
        if info["is_windows"]:
            self.assertIn("pywin32", names)
        else:
            self.assertNotIn("pywin32", names)



class TestConfigStoreSecrets(unittest.TestCase):
    def test_env_overrides_file(self):
        import os
        from meapet.config.store import resolve_llm_api_key, resolve_secret, scrub_secrets

        os.environ["DEEPSEEK_API_KEY"] = "sk-env-test-key-12345678"
        try:
            key = resolve_llm_api_key({"backend": "deepseek", "api_key": "sk-file-should-not-win-xxxx"})
            self.assertEqual(key, "sk-env-test-key-12345678")
            # placeholder
            key2 = resolve_secret("$ENV", ("DEEPSEEK_API_KEY",))
            self.assertEqual(key2, "sk-env-test-key-12345678")
            key3 = resolve_secret("${DEEPSEEK_API_KEY}", ())
            self.assertEqual(key3, "sk-env-test-key-12345678")
            scrubbed = scrub_secrets({"llm": {"api_key": "sk-x"}, "tts": {"api_key": "a", "translate_api_key": "b"}, "vision": {"api_key": "c"}})
            self.assertEqual(scrubbed["llm"]["api_key"], "")
            self.assertEqual(scrubbed["tts"]["api_key"], "")
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_file_used_when_no_env(self):
        import os
        from meapet.config.store import resolve_llm_api_key
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("MEAPET_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        key = resolve_llm_api_key({"backend": "deepseek", "api_key": "sk-only-in-file-abcdef"})
        self.assertEqual(key, "sk-only-in-file-abcdef")



if __name__ == "__main__":
    unittest.main()
