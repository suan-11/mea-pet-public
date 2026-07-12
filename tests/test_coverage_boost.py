"""提高可测核心模块覆盖率：memory / chat / config_store / utils / watcher / tts helpers"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestUtilsMore(unittest.TestCase):
    def test_mask_secret_edge_cases(self):
        from meapet.utils import mask_secret

        self.assertEqual(mask_secret(None), "")
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret("abcd"), "***")
        self.assertEqual(mask_secret("abcdefgh"), "***")  # len == keep*2
        m = mask_secret("abcdefghij", keep=3)
        self.assertIn("…", m)
        self.assertTrue(m.startswith("abc"))

    def test_normalize_watcher_fields(self):
        from meapet.utils import normalize_watcher

        w = normalize_watcher({
            "enabled": 1,
            "allow_cloud": 0,
            "interval": {"min_ms": 500, "max_ms": 100},
        })
        self.assertTrue(w["enabled"])
        self.assertFalse(w["allow_cloud"])
        self.assertIn("min_ms", w["interval"])

    def test_log_error_redacts_and_writes(self):
        from meapet.utils import log_error

        with tempfile.TemporaryDirectory() as td:
            log_error("ctx", "token sk-abcdefghijklmnop leaked", log_dir=td)
            path = Path(td) / "chat_errors.log"
            self.assertTrue(path.is_file())
            body = path.read_text(encoding="utf-8")
            self.assertIn("[ctx]", body)
            self.assertNotIn("sk-abcdefghijklmnop", body)

    def test_redact_mapping_depth_and_list(self):
        from meapet.utils import redact_mapping

        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": 1}}}}}}}}}}
        out = redact_mapping(deep)
        s = json.dumps(out)
        self.assertTrue("max-depth" in s or "j" in s)

        arr = redact_mapping([{"api_key": "sk-1234567890abcdef"}, "plain"])
        self.assertNotEqual(arr[0]["api_key"], "sk-1234567890abcdef")
        self.assertEqual(arr[1], "plain")

    def test_cleanup_skips_non_prefix_and_missing_dir(self):
        from meapet.utils import cleanup_audio_cache

        self.assertEqual(cleanup_audio_cache("/no/such/dir"), {"removed": 0, "kept": 0})
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "other.wav"
            other.write_bytes(b"RIFF" + b"\x00" * 40)
            stats = cleanup_audio_cache(td, max_files=1, max_age_hours=48, prefix="mea_")
            self.assertEqual(stats["removed"], 0)
            self.assertTrue(other.exists())


class TestConfigStoreMore(unittest.TestCase):
    def test_resolve_secret_placeholders(self):
        from meapet.config.store import resolve_secret

        os.environ["MEAPET_TEST_KEY"] = "from-env-value"
        try:
            self.assertEqual(resolve_secret("$ENV", ("MEAPET_TEST_KEY",)), "from-env-value")
            self.assertEqual(resolve_secret("${MEAPET_TEST_KEY}", ()), "from-env-value")
            self.assertEqual(resolve_secret("$MEAPET_TEST_KEY", ()), "from-env-value")
            self.assertEqual(
                resolve_secret("file-key", ("MEAPET_TEST_KEY",)),
                "from-env-value",
            )
        finally:
            os.environ.pop("MEAPET_TEST_KEY", None)

    def test_resolve_keys_by_backend(self):
        from meapet.config.store import (
            resolve_llm_api_key,
            resolve_tts_api_key,
            resolve_translate_api_key,
            resolve_vision_api_key,
        )

        for k in ("DEEPSEEK_API_KEY", "MIMO_API_KEY", "MEAPET_API_KEY", "TRANSLATE_API_KEY"):
            os.environ.pop(k, None)

        self.assertEqual(
            resolve_llm_api_key({"backend": "deepseek", "api_key": "sk-file-ds"}),
            "sk-file-ds",
        )
        self.assertEqual(
            resolve_tts_api_key(
                {"api_key": ""},
                {"backend": "mimo", "api_key": "sk-from-llm"},
            ),
            "sk-from-llm",
        )
        self.assertEqual(
            resolve_translate_api_key({}, {"backend": "deepseek", "api_key": "sk-tr"}),
            "sk-tr",
        )
        self.assertEqual(
            resolve_vision_api_key({"backend": "mimo", "api_key": "sk-vis"}, {}),
            "sk-vis",
        )

    def test_scrub_and_secret_status(self):
        from meapet.config.store import scrub_secrets, secret_status

        cfg = {
            "llm": {"backend": "deepseek", "api_key": "sk-llm-key-xxxx"},
            "tts": {"api_key": "sk-tts", "translate_api_key": "sk-tr"},
            "vision": {"api_key": "sk-vis"},
        }
        scrubbed = scrub_secrets(cfg)
        self.assertEqual(scrubbed["llm"]["api_key"], "")
        self.assertEqual(cfg["llm"]["api_key"], "sk-llm-key-xxxx")

        st = secret_status(cfg)
        self.assertEqual(st["llm"], "file")
        self.assertTrue(st["llm_preview"])

    def test_load_json_bad_and_normalize(self):
        from meapet.config.store import load_json, normalize_config

        self.assertEqual(load_json("/nope.json", {"a": 1}), {"a": 1})
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "b.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_json(str(bad), {"x": 2}), {"x": 2})

        cfg = normalize_config({"tts": {"engine": "mimo"}})
        self.assertIn("sync_with_audio", cfg["tts"])
        self.assertIn("size_factor", cfg["display"])
        self.assertFalse(cfg["watcher"]["enabled"])


class TestMemoryMore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import meapet.memory.db as mem

        self.mem_mod = mem
        self._old = mem.DB_PATH
        mem.DB_PATH = str(Path(self._tmpdir.name) / "m.db")
        self.m = mem.MeaMemory()

    def tearDown(self):
        try:
            self.m.close()
        except Exception:
            pass
        self.mem_mod.DB_PATH = self._old
        self._tmpdir.cleanup()

    def test_chat_history_and_counts(self):
        self.m.add_chat("user", "你好呀")
        self.m.add_chat("mea", "嗯喵", mood="happy")
        recent = self.m.get_recent_chats(10)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["role"], "user")
        self.assertGreaterEqual(self.m.get_total_chats(), 1)
        self.assertGreaterEqual(self.m.get_recent_chat_count(hours=24), 1)

    def test_memory_and_master_info(self):
        self.m.add_memory("主人喜欢猫", importance=5)
        mems = self.m.get_important_memories(3)
        self.assertIn("主人喜欢猫", mems)
        self.m.set_master_info("hobby", "coding")
        self.assertEqual(self.m.get_master_info("hobby"), "coding")
        all_info = self.m.get_all_master_info()
        self.assertEqual(all_info.get("hobby"), "coding")

    def test_events_and_context_prompt(self):
        self.m.add_event("milestone", "第一次对话", {"n": 1})
        events = self.m.get_recent_events(5)
        self.assertTrue(any(e["event_type"] == "milestone" for e in events))
        self.m.add_chat("user", "测试上下文")
        self.m.add_chat("mea", "收到喵")
        self.m.add_memory("记得主人", importance=4)
        ctx = self.m.build_context_prompt()
        self.assertIn("好感度", ctx)
        self.assertIn("记得主人", ctx)

    def test_mood_and_today_chat(self):
        self.m.set_mood("开心")
        self.assertEqual(self.m.get_mood(), "开心")
        before = self.m.get_today_chat_count()
        self.m.mark_today_chatted()
        self.assertEqual(self.m.get_today_chat_count(), before + 1)

    def test_daily_maintenance_and_reset(self):
        self.m.add_chat("user", "将被重置")
        self.m.add_memory("temp")
        self.m.daily_maintenance()
        self.m.reset_all()
        self.assertEqual(self.m.get_recent_chats(10), [])
        self.assertEqual(self.m.get_important_memories(10), [])
        self.assertGreaterEqual(self.m.get_affection(), 0)

    def test_affection_tier(self):
        tier = self.m.get_affection_tier()
        self.assertEqual(len(tier), 3)
        self.assertIsInstance(tier[1], str)


class TestChatEngineMore(unittest.TestCase):
    def test_parse_mood_known_and_unknown(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k")
        text, mood = eng._parse_mood("[happy]你好喵")
        self.assertEqual(mood, "happy")
        self.assertEqual(text, "你好喵")

        text2, mood2 = eng._parse_mood("[not_a_mood]嗨")
        self.assertEqual(mood2, "neutral")
        self.assertEqual(text2, "嗨")

        text3, mood3 = eng._parse_mood("无标签")
        self.assertEqual(mood3, "neutral")
        self.assertEqual(text3, "无标签")

    def test_parse_formatted_tts_metadata_without_displaying_or_speaking_it(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k")
        raw = (
            "[shy]才没有特意等你回来喵\n"
            "べ、別にあなたの帰りを待ってたわけじゃないにゃ\n"
            '<TTS>{"emotion":"shy","pace":"slightly_slow",'
            '"energy":"low","volume":"soft",'
            '"delivery":"前半句嘴硬，后半句明显放轻，句尾略作停顿"}</TTS>'
        )

        display, mood = eng._parse_mood(raw)

        self.assertEqual(display, "才没有特意等你回来喵")
        self.assertEqual(mood, "shy")
        self.assertEqual(
            eng.take_voice_text(),
            "べ、別にあなたの帰りを待ってたわけじゃないにゃ",
        )
        style = eng.take_tts_style()
        self.assertIn("保持参考音色", style)
        self.assertIn("情绪：害羞", style)
        self.assertIn("语速：稍慢", style)
        self.assertIn("句尾略作停顿", style)
        self.assertNotIn("<TTS>", display)
        self.assertEqual(eng.take_tts_style(), "")

    def test_malformed_tts_metadata_is_hidden_and_falls_back(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k")
        display, mood = eng._parse_mood(
            "[happy]欢迎回来喵\nおかえりにゃ\n<TTS>{not-json}</TTS>"
        )
        self.assertEqual((display, mood), ("欢迎回来喵", "happy"))
        self.assertEqual(eng.take_voice_text(), "おかえりにゃ")
        self.assertEqual(eng.take_tts_style(), "")

        display2, _ = eng._parse_mood(
            "[happy]又见面了喵\nまた会ったにゃ\n<TTS>broken"
        )
        self.assertEqual(display2, "又见面了喵")
        self.assertNotIn("TTS", display2)

    def test_tts_metadata_uses_whitelist_and_limits_free_text(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k")
        metadata = json.dumps(
            {
                "emotion": "root",
                "pace": "fast",
                "energy": "MAX",
                "volume": "soft",
                "delivery": (
                    " 轻声 <script> sk-sensitive-test-key-1234567890 "
                    + "啊" * 150
                ),
                "api_key": "must-not-pass-through",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        eng._parse_mood(f"[neutral]测试喵\nテストにゃ\n<TTS>{metadata}</TTS>")
        style = eng.take_tts_style()

        self.assertIn("语速：快", style)
        self.assertIn("音量：轻柔", style)
        self.assertNotIn("root", style)
        self.assertNotIn("MAX", style)
        self.assertNotIn("must-not-pass-through", style)
        self.assertNotIn("sk-sensitive-test-key-1234567890", style)
        self.assertNotIn("<", style)
        self.assertNotIn(">", style)
        self.assertLessEqual(style.count("啊"), 100)

    def test_system_prompt_requires_internal_tts_metadata_line(self):
        from meapet.chat.engine import SYSTEM_PROMPT

        self.assertIn("<TTS>", SYSTEM_PROMPT)
        self.assertIn('"emotion"', SYSTEM_PROMPT)
        self.assertNotIn("禁止输出第三行", SYSTEM_PROMPT)

    def test_fallback_and_clear_history(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="")
        fb = eng._fallback_reply()
        self.assertTrue(isinstance(fb, str) and len(fb) > 0)
        eng.history.append({"role": "user", "content": "x"})
        eng.clear_history()
        self.assertEqual(len(eng.history), 1)
        self.assertEqual(eng.history[0]["role"], "system")

    def test_create_engine_from_config_uses_resolver(self):
        from meapet.chat.engine import create_engine_from_config

        os.environ.pop("DEEPSEEK_API_KEY", None)
        eng = create_engine_from_config({
            "llm": {
                "backend": "deepseek",
                "api_key": "sk-from-config-file",
                "model": "deepseek-v4-flash",
                "api_base": "https://api.deepseek.com",
            }
        })
        self.assertEqual(eng.backend, "deepseek")
        self.assertEqual(eng.api_key, "sk-from-config-file")
        self.assertTrue(eng.available)

    def test_quick_chat_unavailable_fallback(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="")
        self.assertFalse(eng.available)
        reply, mood = eng.quick_chat("hi")
        self.assertEqual(mood, "neutral")
        self.assertTrue(reply)

    def test_quick_chat_with_mock_dispatch(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k", model="m")
        eng.available = True
        eng._backend_ready = True
        async def _fake(messages):
            return "[curious]模拟回复喵"
        with mock.patch.object(eng, "_dispatch_chat_async", side_effect=_fake):
            reply, mood = eng.quick_chat("测试")
        self.assertEqual(mood, "curious")
        self.assertEqual(reply, "模拟回复喵")
        roles = [h["role"] for h in eng.history]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_quick_chat_keeps_tts_metadata_out_of_history(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k", model="m")
        eng.available = True

        async def _fake(_messages):
            return (
                "[happy]欢迎回来喵\nおかえりにゃ\n"
                '<TTS>{"emotion":"happy","pace":"fast",'
                '"energy":"medium","volume":"normal",'
                '"delivery":"句尾轻微上扬"}</TTS>'
            )

        with mock.patch.object(eng, "_dispatch_chat_async", side_effect=_fake):
            reply, mood = eng.quick_chat("回来了")

        self.assertEqual((reply, mood), ("欢迎回来喵", "happy"))
        self.assertNotIn("<TTS>", eng.history[-1]["content"])
        self.assertIn("句尾轻微上扬", eng.take_tts_style())

    def test_chat_with_memory_mocked_api(self):
        from meapet.chat.engine import ChatEngine
        import meapet.memory.db as mem

        tmp = tempfile.TemporaryDirectory()
        old = mem.DB_PATH
        mem.DB_PATH = str(Path(tmp.name) / "c.db")
        try:
            m = mem.MeaMemory()
            eng = ChatEngine(backend="deepseek", api_key="k", memory=m)
            eng.available = True
            # chat() still sync path uses _dispatch_chat
            with mock.patch.object(eng, "_dispatch_chat", return_value="[happy]记住了喵"):
                with mock.patch.object(eng, "_extract_memories"):
                    reply, mood = eng.chat("我叫小明")
            self.assertEqual(mood, "happy")
            self.assertIn("记住了", reply)
            self.assertGreaterEqual(len(m.get_recent_chats(5)), 2)
            m.close()
        finally:
            mem.DB_PATH = old
            tmp.cleanup()

    def test_dispatch_routes(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="ollama")
        eng.available = True

        async def fo(messages=None):
            return "o"
        async def fd(messages=None):
            return "d"
        async def fm(messages=None):
            return "m"

        with mock.patch.object(eng, "_chat_ollama_async", side_effect=fo):
            self.assertEqual(eng._dispatch_chat([]), "o")
        eng.backend = "deepseek"
        with mock.patch.object(eng, "_chat_deepseek_async", side_effect=fd):
            self.assertEqual(eng._dispatch_chat([]), "d")
        eng.backend = "mimo"
        with mock.patch.object(eng, "_chat_mimo_async", side_effect=fm):
            self.assertEqual(eng._dispatch_chat([]), "m")
        eng.backend = "unknown"
        out = eng._dispatch_chat([])
        self.assertTrue(isinstance(out, str))



class TestWatcherMore(unittest.TestCase):
    def test_parse_decision_variants(self):
        from meapet.watcher.screen import parse_decision

        speak, strategy, q = parse_decision("第1行：说\n第2行：轻微吃醋\n第3行：无")
        self.assertTrue(speak)
        self.assertEqual(strategy, "轻微吃醋")
        self.assertEqual(q, "")

        speak2, strategy2, q2 = parse_decision("")
        self.assertFalse(speak2)

        speak3, strategy3, q3 = parse_decision("说\n好奇询问\n原神角色")
        self.assertTrue(speak3)
        self.assertEqual(strategy3, "好奇询问")
        self.assertEqual(q3, "原神角色")

    def test_guess_mood(self):
        from meapet.watcher.screen import ScreenWatcher

        w = ScreenWatcher()
        self.assertEqual(w._guess_mood("x", "毒舌吐槽", 0), "annoyed")
        self.assertEqual(w._guess_mood("x", "关心进度", 0), "curious")
        self.assertEqual(w._guess_mood("x", "轻微吃醋", 0), "melancholy")
        self.assertEqual(w._guess_mood("x", "轻松陪聊", 0), "happy")
        self.assertEqual(w._guess_mood("x", "好奇询问", 0), "curious")
        self.assertEqual(w._guess_mood("x", "", 40), "melancholy")
        self.assertEqual(w._guess_mood("真傻啊", "", 0), "annoyed")
        self.assertEqual(w._guess_mood("又在摸鱼", "", 0), "curious")


class TestTtsHelpers(unittest.TestCase):
    def test_get_import_name(self):
        from meapet.tts.service import _get_import_name

        self.assertEqual(_get_import_name("PyYAML"), "yaml")
        self.assertEqual(_get_import_name("split-lang"), "split_lang")
        self.assertEqual(_get_import_name("numpy<2.0"), "numpy")
        self.assertEqual(_get_import_name("requests"), "requests")

    def test_mimo_mode_flags(self):
        from meapet.tts.service import MeaTTS

        tts = MeaTTS({
            "tts": {
                "enabled": True,
                "engine": "mimo",
                "api_key": "k",
                "api_base": "https://example.com/v1",
                "translate_to_jp": False,
            }
        })
        self.assertTrue(tts._mimo_mode)
        self.assertFalse(tts._vits_mode)


class TestConfigChecker(unittest.TestCase):
    def test_check_config_lines(self):
        from meapet.config.checker import check_config_lines

        self.assertFalse(check_config_lines("/no/such/config.json"))
        with tempfile.TemporaryDirectory() as td:
            short = Path(td) / "s.json"
            short.write_text("{\n}\n", encoding="utf-8")
            self.assertTrue(check_config_lines(str(short)))
            longp = Path(td) / "l.json"
            longp.write_text("\n".join(["x"] * 12), encoding="utf-8")
            self.assertFalse(check_config_lines(str(longp)))



class TestChatHttpMocks(unittest.TestCase):
    def test_chat_deepseek_success(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k", api_base="https://api.example.com", model="deepseek-v4-flash")
        eng.available = True

        class Resp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "[happy]好的喵"}}]}

        async def fake_post(url, headers=None, json_body=None, timeout=30):
            return Resp()
        with mock.patch.object(eng, "_post_json", side_effect=fake_post):
            out = eng._dispatch_chat([{"role": "user", "content": "hi"}])
        self.assertIn("好的喵", out)

    def test_chat_mimo_success(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="mimo", api_key="k", api_base="https://api.example.com", model="m")
        eng.available = True

        class Resp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "米莫回复"}}]}

        async def fake_post(url, headers=None, json_body=None, timeout=30):
            return Resp()
        with mock.patch.object(eng, "_post_json", side_effect=fake_post):
            eng.backend = "mimo"
            out = eng._dispatch_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "米莫回复")

    def test_chat_ollama_success(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="ollama", host="http://127.0.0.1:11434", model="qwen")
        eng.available = True

        class Resp:
            status_code = 200
            def json(self):
                return {"message": {"content": "本地回复"}}

        async def fake_post(url, headers=None, json_body=None, timeout=30):
            return Resp()
        with mock.patch.object(eng, "_post_json", side_effect=fake_post):
            eng.backend = "ollama"
            out = eng._dispatch_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "本地回复")

    def test_chat_http_error_fallback_paths(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k", api_base="https://api.example.com")
        eng.available = True

        class Bad:
            status_code = 500
            text = "err"
            def json(self):
                return {}

        async def fake_post(url, headers=None, json_body=None, timeout=30):
            return Bad()
        with mock.patch.object(eng, "_post_json", side_effect=fake_post):
            out = eng._dispatch_chat([{"role": "user", "content": "hi"}])
        # implementation may return fallback string
        self.assertTrue(isinstance(out, str))

    def test_cancel_sets_flag(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(backend="deepseek", api_key="k")
        eng.cancel()
        self.assertTrue(eng._cancelled)



class TestUtilsPrintAndWatcherNormalize(unittest.TestCase):
    def test_safe_print_redacts(self):
        from meapet.utils import safe_print
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            safe_print("Authorization: Bearer sk-abcdefghijklmnop")
        s = buf.getvalue()
        self.assertNotIn("sk-abcdefghijklmnop", s)

    def test_cloud_vision_allowed_with_config_shape(self):
        from meapet.utils import cloud_vision_allowed
        self.assertTrue(cloud_vision_allowed({"watcher": {}}, False))
        self.assertFalse(cloud_vision_allowed({"watcher": {"allow_cloud": False}}, True))
        self.assertTrue(cloud_vision_allowed({"watcher": {"allow_cloud": True}}, True))


class TestMemoryEdge(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import meapet.memory.db as mem
        self.mem_mod = mem
        self._old = mem.DB_PATH
        mem.DB_PATH = str(Path(self._tmpdir.name) / "e.db")
        self.m = mem.MeaMemory()

    def tearDown(self):
        try:
            self.m.close()
        except Exception:
            pass
        self.mem_mod.DB_PATH = self._old
        self._tmpdir.cleanup()

    def test_get_missing_master_info(self):
        self.assertIsNone(self.m.get_master_info("nope"))

    def test_add_affection_zero_when_at_cap(self):
        # fill daily cap
        for _ in range(30):
            self.m.add_affection(5)
        before = self.m.get_affection()
        self.assertIsNone(self.m.add_affection(5))
        self.assertEqual(self.m.get_affection(), before)


class TestWatcherSetSearch(unittest.TestCase):
    def test_set_search_result(self):
        from meapet.watcher.screen import ScreenWatcher
        w = ScreenWatcher()
        w.set_search_result("搜索结果")
        self.assertEqual(w._search_result, "搜索结果")
        self.assertFalse(w._search_pending)

if __name__ == "__main__":
    unittest.main()
