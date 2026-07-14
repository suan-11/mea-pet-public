"""拆分后的回归单测：模块可导入、worker、配置读写、向导入口"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestPetWidgetsPure(unittest.TestCase):
    def test_wrap_text_breaks_by_width(self):
        from meapet.desktop.widgets import wrap_text

        text = "一二三四五六七八九十"
        out = wrap_text(text, width=5)
        self.assertEqual(out, "一二三四五\n六七八九十")

    def test_wrap_text_empty(self):
        from meapet.desktop.widgets import wrap_text

        self.assertEqual(wrap_text("", width=10), "")

    def test_wrap_text_shorter_than_width(self):
        from meapet.desktop.widgets import wrap_text

        self.assertEqual(wrap_text("喵", width=10), "喵")


class TestPetWorkers(unittest.TestCase):
    def test_chat_worker_returns_quick_chat_result(self):
        from meapet.desktop.workers import ChatWorker

        class FakeEngine:
            def quick_chat(self, message):
                return (f"回:{message}", "happy")

        w = ChatWorker(FakeEngine(), "你好")
        w.start()
        deadline = time.time() + 2.0
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        result, err = w.get_result()
        self.assertIsNone(err)
        self.assertEqual(result, ("回:你好", "happy"))

    def test_chat_worker_captures_error(self):
        from meapet.desktop.workers import ChatWorker

        class BoomEngine:
            def quick_chat(self, message):
                raise RuntimeError("network down")

        w = ChatWorker(BoomEngine(), "x")
        w.start()
        deadline = time.time() + 2.0
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        result, err = w.get_result()
        self.assertIsNone(result)
        self.assertIn("RuntimeError", err or "")

    def test_tts_worker_formats_wav_lang(self):
        from meapet.desktop.workers import TTSWorker

        class FakeTTS:
            def speak(self, text, mood="neutral"):
                return ("/tmp/a.wav", "zh")

        w = TTSWorker(FakeTTS(), "主人", mood="happy")
        w.start()
        deadline = time.time() + 2.0
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        result = w.get_result()
        self.assertEqual(result, "/tmp/a.wav|zh")

    def test_tts_worker_none_on_failure(self):
        from meapet.desktop.workers import TTSWorker

        class FakeTTS:
            def speak(self, text, mood="neutral"):
                return None

        w = TTSWorker(FakeTTS(), "x")
        w.start()
        deadline = time.time() + 2.0
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        self.assertIsNone(w.get_result())


class TestConfigStoreIO(unittest.TestCase):
    def test_load_save_roundtrip_temp(self):
        from meapet.config.store import load_config, save_config

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            raw = {
                "llm": {"backend": "ollama", "model": "qwen3.5:4b"},
                "display": {"size_factor": 1.25},
                "watcher": {"enabled": True, "allow_cloud": False},
            }
            save_config(raw, path)
            loaded = load_config(path)
            self.assertEqual(loaded["llm"]["backend"], "ollama")
            self.assertEqual(loaded["display"]["size_factor"], 1.25)
            self.assertTrue(loaded["watcher"]["enabled"])
            self.assertFalse(loaded["watcher"]["allow_cloud"])
            self.assertTrue(loaded["watcher"]["require_confirm"])
            self.assertIn("bubble_duration_ms", loaded)
            self.assertIn("interval", loaded["watcher"])
            self.assertNotIn("watcher_interval", loaded)

    def test_normalize_promotes_legacy_top_level_interval(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({
            "watcher": {"enabled": False},
            "watcher_interval": {"min_ms": 11, "max_ms": 22},
        })
        self.assertEqual(cfg["watcher"]["interval"]["min_ms"], 11)
        self.assertEqual(cfg["watcher"]["interval"]["max_ms"], 22)
        self.assertEqual(
            cfg["watcher_interval"],
            {"min_ms": 11, "max_ms": 22},
        )

    def test_save_config_writes_valid_json(self):
        from meapet.config.store import save_config

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "c.json")
            save_config({"llm": {"backend": "deepseek"}}, path)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(data["llm"]["backend"], "deepseek")
            self.assertIn("tts", data)


class TestWizardPackageImports(unittest.TestCase):
    def test_setup_wizard_facade_exports(self):
        import setup_wizard

        self.assertTrue(callable(setup_wizard.detect_platform))
        self.assertTrue(callable(setup_wizard.platform_checklist))
        self.assertTrue(callable(setup_wizard.main))
        info = setup_wizard.detect_platform()
        for key in ("os_key", "os_label", "arch", "display", "is_windows", "is_linux", "is_macos"):
            self.assertIn(key, info)

    def test_wizard_pages_reexport(self):
        import wizard.pages as pages
        from wizard.pages import BackendPage, EnvCheckPage, LLMPage, TTSPage, VisionPage

        for cls in (EnvCheckPage, LLMPage, BackendPage, TTSPage, VisionPage):
            self.assertTrue(isinstance(cls, type), msg=str(cls))
        self.assertFalse(hasattr(pages, "ApiKeyPage"))
        self.assertFalse(hasattr(pages, "SummaryPage"))

    def test_wizard_submodules_import(self):
        from wizard import platform_info, styles, env_utils, app

        self.assertTrue(hasattr(platform_info, "PLATFORM"))
        self.assertTrue(hasattr(styles, "COLOR_ACCENT"))
        self.assertTrue(hasattr(env_utils, "pip_install"))
        self.assertTrue(hasattr(app, "SetupWizard"))
        self.assertTrue(hasattr(app, "main"))


class TestSplitModuleSurfaces(unittest.TestCase):
    def test_pet_workers_export(self):
        import meapet.desktop.workers as pet_workers

        self.assertTrue(hasattr(pet_workers, "ChatWorker"))
        self.assertTrue(hasattr(pet_workers, "TTSWorker"))

    def test_pet_widgets_export(self):
        import meapet.desktop.widgets as pet_widgets

        self.assertTrue(hasattr(pet_widgets, "wrap_text"))
        self.assertTrue(hasattr(pet_widgets, "DialogueBox"))
        self.assertTrue(hasattr(pet_widgets, "SizeScaleDialog"))

    def test_pet_main_imports_split_modules(self):
        """不实例化 MeaPet（依赖 live2d），只检查源码仍引用拆分模块。"""
        entry = (ROOT / "pet.py").read_text(encoding="utf-8")
        self.assertIn("meapet.desktop.app", entry)
        text = (ROOT / "meapet" / "desktop" / "app.py").read_text(encoding="utf-8")
        self.assertIn("meapet.desktop.widgets", text)
        # workers 由 chat_flow / audio 等 mixin 引用；app 引用 chrome/render/splash
        self.assertIn("meapet.desktop.window_chrome", text)
        self.assertIn("meapet.desktop.render_host", text)
        self.assertIn("meapet.desktop.splash", text)
        self.assertIn("class MeaPet", text)
        self.assertIn("def main(", text)
        # workers 仍应可从包路径导入
        import meapet.desktop.workers as w
        self.assertTrue(hasattr(w, "ChatWorker"))

    def test_pet_import_optional_live2d(self):
        """有 live2d 则 import pet 入口模块；没有则 skip。"""
        try:
            import pet  # noqa: F401  # root entry
        except ModuleNotFoundError as e:
            if "live2d" in str(e).lower():
                self.skipTest(f"live2d not installed: {e}")
            raise
        # pet.py 只转发 main；MeaPet 在 meapet.desktop.app
        self.assertTrue(hasattr(pet, "main"))
        from meapet.desktop.app import MeaPet
        from meapet.desktop.workers import ChatWorker
        from meapet.desktop.widgets import DialogueBox
        self.assertTrue(MeaPet)
        self.assertTrue(ChatWorker)
        self.assertTrue(DialogueBox)


class TestWatcherDecisionStillWired(unittest.TestCase):
    def test_parse_decision_import_path(self):
        from meapet.watcher.screen import parse_decision

        speak, strategy, q = parse_decision("说\n轻松陪聊\n无")
        self.assertTrue(speak)
        self.assertEqual(strategy, "轻松陪聊")
        self.assertEqual(q, "")



class TestFurtherSplitSurfaces(unittest.TestCase):
    def test_pet_mixins_import(self):
        from meapet.desktop.audio import PetAudioMixin
        from meapet.desktop.watch_ctrl import PetWatcherMixin
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin
        for c in (PetAudioMixin, PetWatcherMixin, PetChatFlowMixin, PetInteractionMixin):
            self.assertTrue(isinstance(c, type))

    def test_tts_engine_mixins_import(self):
        from meapet.tts.service import MeaTTS
        from meapet.tts.common import LANG_TTS, MOOD_TO_REF
        from meapet.tts.engines.mimo import TtsMimoMixin
        from meapet.tts.engines.gsv import TtsGsvMixin
        from meapet.tts.engines.vits import TtsVitsMixin
        self.assertTrue(issubclass(MeaTTS, TtsMimoMixin))
        self.assertTrue(issubclass(MeaTTS, TtsGsvMixin))
        self.assertTrue(issubclass(MeaTTS, TtsVitsMixin))
        self.assertIn("日", LANG_TTS)
        self.assertIn("happy", MOOD_TO_REF)

    def test_wizard_tts_page_mixins(self):
        from wizard.page_tts import TTSPage
        from wizard.page_tts_gsv import TtsPageGsvMixin
        from wizard.page_tts_mimo import TtsPageMimoMixin
        from wizard.page_tts_vits import TtsPageVitsMixin
        self.assertTrue(issubclass(TTSPage, TtsPageGsvMixin))
        self.assertTrue(issubclass(TTSPage, TtsPageMimoMixin))
        self.assertTrue(issubclass(TTSPage, TtsPageVitsMixin))


class TestMixinMethodBinding(unittest.TestCase):
    def test_speak_and_show_is_instance_method(self):
        """防止再次把 _speak_and_show 标成 staticmethod 导致 self 错位。"""
        import inspect
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.audio import PetAudioMixin

        sig = inspect.signature(PetChatFlowMixin._speak_and_show)
        params = list(sig.parameters)
        self.assertEqual(params[0], "self")
        self.assertFalse(isinstance(
            inspect.getattr_static(PetChatFlowMixin, "_speak_and_show"),
            staticmethod,
        ))

        # duration helper is static and takes path only
        dur = inspect.getattr_static(PetAudioMixin, "_get_wav_duration_ms")
        self.assertTrue(isinstance(dur, staticmethod))


class TestRefactorRuntimeRegressions(unittest.TestCase):
    def test_chat_memory_ops_keep_working_after_module_split(self):
        from meapet.chat.engine import SYSTEM_PROMPT
        from meapet.desktop.chat_flow import PetChatFlowMixin

        class FakeMemory:
            def __init__(self):
                self.chats = []
                self.marked = False

            def add_chat(self, role, content, mood="neutral"):
                self.chats.append((role, content, mood))

            def add_affection(self, _delta):
                return None

            def build_context_prompt(self, current_query=""):
                return "测试上下文"

            def mark_today_chatted(self):
                self.marked = True

            def increment_message_counter(self):
                pass

        class FakeEngine:
            def __init__(self):
                self.memory = FakeMemory()
                self.history = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "请记住这句话"},
                ]
                self.extracted = None

            def _extract_memories(self, user_msg, reply):
                self.extracted = (user_msg, reply)

            def _summarize_if_needed(self):
                pass

        host = type("Host", (), {})()
        host.chat_engine = FakeEngine()

        PetChatFlowMixin._do_memory_ops_sync(host, "记住了喵", "happy", "请记住这句话")

        self.assertEqual(
            host.chat_engine.memory.chats,
            [
                ("user", "请记住这句话", "neutral"),
                ("mea", "记住了喵", "happy"),
            ],
        )
        self.assertTrue(host.chat_engine.memory.marked)
        self.assertEqual(host.chat_engine.extracted, ("请记住这句话", "记住了喵"))
        self.assertTrue(host.chat_engine.history[0]["content"].startswith(SYSTEM_PROMPT))

    def test_ready_tts_audio_is_copied_to_project_cache(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "generated.wav"
            source.write_bytes(b"RIFF" + b"\x00" * 40)

            class Host:
                _current_speaking_text = "缓存测试"

                def __init__(self):
                    self.played = []

                def _safe_name(self, _text):
                    return "cache_test"

                def _play_audio(self, path):
                    self.played.append(path)

            host = Host()
            with mock.patch("meapet.paths.PROJECT_ROOT", root):
                PetChatFlowMixin._on_speak_audio_ready(host, f"{source}|jp")

            cached = root / "voice_cache" / "jp_cache_test.wav"
            self.assertEqual(cached.read_bytes(), source.read_bytes())
            self.assertEqual(host.played, [str(source)])

    def test_vits_backend_invokes_existing_tool_script(self):
        from meapet.tts.engines.vits import TtsVitsMixin

        calls = []

        class Result:
            returncode = 0
            stderr = ""

        def fake_run(command, **_kwargs):
            calls.append(command)
            Path(command[command.index("--output") + 1]).write_bytes(
                b"RIFF" + b"\x00" * 40
            )
            return Result()

        host = type(
            "Host",
            (),
            {"_vits_python": sys.executable, "python_exe": sys.executable, "timeout": 1},
        )()
        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / "out.wav")
            with mock.patch("meapet.tts.engines.vits.subprocess.run", side_effect=fake_run):
                result = TtsVitsMixin._speak_vits(host, "こんにちは", output)

        self.assertEqual(result, (output, "jp"))
        self.assertEqual(
            Path(calls[0][1]).resolve(),
            (ROOT / "meapet" / "tools" / "vits_infer.py").resolve(),
        )
        self.assertTrue(Path(calls[0][1]).is_file())

    def test_vits_tool_imports_vits_core_utils(self):
        source = (ROOT / "meapet" / "tools" / "vits_infer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("\nimport utils\n", source)
        self.assertNotIn("import meapet.utils as utils", source)
        self.assertNotIn("from meapet.paths import project_root", source)

    def test_default_voice_cache_is_project_relative(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "voice_cache"
            cache.mkdir()
            expected = cache / "jp_缓存测试.wav"
            expected.write_bytes(b"RIFF" + b"\x00" * 40)
            host = type(
                "Host",
                (),
                {"_mimo_mode": False, "translate_enabled": True, "voice_lang": "jp"},
            )()
            with mock.patch("meapet.paths.PROJECT_ROOT", root):
                actual = MeaTTS.get_cached(host, "缓存测试")

        self.assertEqual(actual, str(expected))

    def test_wizard_vits_check_uses_project_models(self):
        from wizard.page_tts_vits import TtsPageVitsMixin

        class Status:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

            def setStyleSheet(self, _style):
                pass

        host = type("Host", (), {"vits_status": Status()})()
        TtsPageVitsMixin._check_vits(host)
        self.assertIn("VITS 模型就绪", host.vits_status.text)

    def test_wizard_main_initializes_palette_without_name_error(self):
        import wizard.app as wizard_app

        events = {}

        class FakeApplication:
            @staticmethod
            def setAttribute(*_args):
                pass

            def __init__(self, _argv):
                pass

            def setStyle(self, style):
                events["style"] = style

            def setPalette(self, palette):
                events["palette"] = palette

            def exec_(self):
                return 0

        class FakeWizard:
            def show(self):
                events["shown"] = True

        with (
            mock.patch.object(wizard_app, "QApplication", FakeApplication),
            mock.patch.object(wizard_app, "SetupWizard", FakeWizard),
            mock.patch.object(wizard_app.sys, "argv", ["setup_wizard.py"]),
            mock.patch.object(wizard_app.sys, "exit") as exit_mock,
        ):
            wizard_app.main()

        self.assertEqual(events["style"], "Fusion")
        self.assertIn("palette", events)
        self.assertTrue(events["shown"])
        exit_mock.assert_called_once_with(0)

    def test_vits_setup_error_callback_keeps_exception_message(self):
        from wizard.page_tts_vits import QMessageBox, QTimer, TtsPageVitsMixin

        callbacks = []

        class Control:
            def setEnabled(self, _value):
                pass

            def setText(self, _value):
                pass

            def setStyleSheet(self, _value):
                pass

            def text(self):
                return ""

        class Host:
            setup_vits_btn = Control()
            vits_python_input = Control()
            vits_status = Control()

            def __init__(self):
                self.completed = None

            def log(self, _message):
                pass

            def _ensure_vits_deps(self, _python, _log):
                pass

            def _on_vits_env_done(self, ok, message):
                self.completed = (ok, message)

        class CheckResult:
            returncode = 1
            stdout = ""
            stderr = "missing torch"

        def fake_run(command, **_kwargs):
            if "venv" in command:
                raise RuntimeError("venv boom")
            return CheckResult()

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        host = Host()
        with (
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(QTimer, "singleShot", side_effect=lambda _ms, cb: callbacks.append(cb)),
            mock.patch("subprocess.run", side_effect=fake_run),
            mock.patch("threading.Thread", ImmediateThread),
        ):
            TtsPageVitsMixin._setup_vits_env(host)

        callbacks[-1]()
        self.assertEqual(host.completed, (False, "venv boom"))

    def test_mimo_clone_fallback_uses_project_cache(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "voice_cache"
            cache.mkdir()
            expected = cache / "normal_reference.wav"
            expected.write_bytes(b"RIFF" + b"\x00" * 9000)

            class Host(TtsMimoMixin):
                mimo_clone_ref = ""
                mimo_clone_dir = ""
                voice_lang = "zh"
                ref_dir = str(root / "GPT-Sovits")

                def _get_ref_paths(self, _mood):
                    return None, None, None

            with mock.patch("meapet.paths.PROJECT_ROOT", root):
                actual = Host()._pick_clone_ref_wav("neutral")

        self.assertEqual(actual, str(expected))

    def test_mimo_clone_prefers_same_language_as_voice_lang(self):
        """voice_lang=zh 时不应选 jp_* 参考。"""
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "voice_cache"
            gsv = root / "GPT-Sovits" / "normal"
            cache.mkdir(parents=True)
            gsv.mkdir(parents=True)

            jp = cache / "jp_big_sample.wav"
            zh = gsv / "zh_normal.wav"
            # jp 更大，旧逻辑会误选它
            jp.write_bytes(b"RIFF" + b"\x00" * 200_000)
            zh.write_bytes(b"RIFF" + b"\x00" * 50_000)

            class Host(TtsMimoMixin):
                mimo_clone_ref = ""
                mimo_clone_dir = str(cache)
                voice_lang = "zh"
                ref_dir = str(root / "GPT-Sovits")

                def _get_ref_paths(self, _mood):
                    return str(zh), "你好", "中文"

            with mock.patch("meapet.paths.PROJECT_ROOT", root):
                actual = Host()._pick_clone_ref_wav("neutral")

        self.assertEqual(Path(actual).name, "zh_normal.wav")

    def test_mimo_clone_uri_accepts_only_documented_audio_formats(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for suffix, expected_prefix in (
                (".wav", "data:audio/wav;base64,"),
                (".mp3", "data:audio/mpeg;base64,"),
            ):
                supported = root / f"supported{suffix}"
                supported.write_bytes(b"documented-audio")
                uri = TtsMimoMixin()._build_clone_voice_uri(str(supported))
                self.assertIsNotNone(uri)
                self.assertTrue(uri.startswith(expected_prefix))

            for suffix in (".m4a", ".ogg", ".flac"):
                unsupported = root / f"unsupported{suffix}"
                unsupported.write_bytes(b"unsupported-audio")
                self.assertIsNone(
                    TtsMimoMixin()._build_clone_voice_uri(str(unsupported)),
                    suffix,
                )

    def test_mimo_clone_scan_ignores_undocumented_audio_formats(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "voice_cache"
            cache.mkdir()
            (cache / "reference.flac").write_bytes(b"fLaC" + b"\x00" * 9000)

            class Host(TtsMimoMixin):
                mimo_clone_ref = ""
                mimo_clone_dir = str(cache)
                voice_lang = "zh"
                ref_dir = str(root / "GPT-Sovits")

                def _get_ref_paths(self, _mood):
                    return None, None, None

            with mock.patch(
                "meapet.tts.engines.mimo.project_path",
                side_effect=lambda *parts: str(root.joinpath(*parts)),
            ):
                self.assertIsNone(Host()._pick_clone_ref_wav("neutral"))

    def test_mimo_clone_rejects_final_data_uri_over_10_mb(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            reference = Path(td) / "reference.wav"
            with reference.open("wb") as stream:
                # 编码后 Base64 已超过 10,000,000 字节，但原文件仍小于旧的 8 MiB 门槛。
                stream.truncate(7_500_003)

            uri = TtsMimoMixin()._build_clone_voice_uri(str(reference))
            self.assertTrue(uri is None, "编码后的 Data URI 超限时必须拒绝")

    def test_mimo_clone_uri_handles_cache_and_file_failures(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        with tempfile.TemporaryDirectory() as td:
            reference = Path(td) / "reference.wav"
            reference.write_bytes(b"RIFF" + b"\x00" * 64)

            host = TtsMimoMixin()
            cached_uri = host._build_clone_voice_uri(str(reference))
            with mock.patch("builtins.open", side_effect=AssertionError("cache miss")):
                self.assertEqual(
                    host._build_clone_voice_uri(str(reference)),
                    cached_uri,
                )

            with (
                mock.patch(
                    "meapet.tts.engines.mimo.os.path.isfile",
                    return_value=True,
                ),
                mock.patch(
                    "meapet.tts.engines.mimo.os.stat",
                    side_effect=OSError("stat failed"),
                ),
                mock.patch(
                    "meapet.tts.engines.mimo.os.path.getsize",
                    side_effect=OSError("size failed"),
                ),
            ):
                self.assertIsNone(
                    TtsMimoMixin()._build_clone_voice_uri(str(reference))
                )

            with mock.patch("builtins.open", side_effect=OSError("read failed")):
                self.assertIsNone(
                    TtsMimoMixin()._build_clone_voice_uri(str(reference))
                )

            with (
                mock.patch(
                    "meapet.tts.engines.mimo._MIMO_MAX_CLONE_VOICE_URI_BYTES",
                    30,
                ),
                mock.patch(
                    "meapet.tts.engines.mimo.os.path.getsize",
                    return_value=1,
                ),
                mock.patch(
                    "builtins.open",
                    mock.mock_open(read_data=b"0123456789"),
                ),
            ):
                self.assertIsNone(
                    TtsMimoMixin()._build_clone_voice_uri(str(reference))
                )

        self.assertIsNone(TtsMimoMixin()._build_clone_voice_uri(""))

    def test_mimo_voiceclone_stops_before_request_without_key_or_reference(self):
        import asyncio

        from meapet.tts.engines.mimo import TtsMimoMixin

        class Host(TtsMimoMixin):
            mimo_api_key = ""
            mimo_api_base = "https://api.xiaomimimo.com/v1"
            mimo_voice = "clone"
            mimo_model = "mimo-v2.5-tts-voiceclone"
            mimo_style = ""
            _mimo_voiceclone = True
            timeout = 60

        host = Host()
        self.assertEqual(
            asyncio.run(host._speak_mimo_async("你好", "unused.wav")),
            (None, ""),
        )

        host.mimo_api_key = "test-key-not-real"
        with mock.patch.object(host, "_pick_clone_ref_wav", return_value=None):
            self.assertEqual(
                asyncio.run(host._speak_mimo_async("你好", "unused.wav")),
                (None, ""),
            )

    def test_mimo_voiceclone_request_uses_single_documented_auth_header(self):
        import asyncio
        import base64

        from meapet.tts.engines.mimo import TtsMimoMixin

        class Response:
            status_code = 200
            text = "ok"

            @staticmethod
            def json():
                wav = b"RIFF" + b"\x00" * 48
                return {
                    "choices": [{
                        "message": {
                            "audio": {
                                "data": base64.b64encode(wav).decode("ascii")
                            }
                        }
                    }]
                }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference = root / "reference.wav"
            reference.write_bytes(b"RIFF" + b"\x00" * 64)
            output = root / "output.wav"

            class Host(TtsMimoMixin):
                mimo_api_key = "test-key-not-real"
                mimo_api_base = "https://api.xiaomimimo.com/v1"
                mimo_voice = "clone"
                mimo_model = "mimo-v2.5-tts-voiceclone"
                mimo_style = ""
                mimo_clone_ref = str(reference)
                mimo_clone_dir = ""
                voice_lang = "zh"
                ref_dir = ""
                _mimo_voiceclone = True
                _mimo_clone_voice_uri = None
                _mimo_clone_cache_key = None
                timeout = 60

            captured = {}

            async def fake_post(url, *, headers, json, timeout):
                captured.update(
                    url=url,
                    headers=headers,
                    payload=json,
                    timeout=timeout,
                )
                return Response()

            with mock.patch("meapet.http_async.post_json", side_effect=fake_post):
                result = asyncio.run(
                    Host()._speak_mimo_async(
                        "你好",
                        str(output),
                        lang_tag="zh",
                        style="保持参考音色。情绪：害羞。",
                    )
                )

        self.assertEqual(result, (str(output), "zh"))
        self.assertEqual(
            captured["headers"],
            {
                "Content-Type": "application/json",
                "api-key": "test-key-not-real",
            },
        )
        self.assertEqual(
            captured["url"],
            "https://api.xiaomimimo.com/v1/chat/completions",
        )
        self.assertEqual(
            captured["payload"]["model"],
            "mimo-v2.5-tts-voiceclone",
        )
        self.assertEqual(
            [message["role"] for message in captured["payload"]["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            captured["payload"]["messages"][0]["content"],
            "保持参考音色。情绪：害羞。",
        )
        self.assertTrue(
            captured["payload"]["audio"]["voice"].startswith(
                "data:audio/wav;base64,"
            )
        )
        self.assertFalse(captured["payload"]["stream"])

    def test_mimo_generated_style_preserves_fixed_style_as_guardrail(self):
        from meapet.tts.engines.mimo import TtsMimoMixin

        host = TtsMimoMixin()
        host.mimo_style = "始终保持参考音色，不改变说话人身份。"

        actual = host._mimo_style_for_mood(
            "shy",
            style="情绪：害羞。语速：稍慢。",
        )

        self.assertEqual(
            actual,
            "始终保持参考音色，不改变说话人身份。\n"
            "本句表演：情绪：害羞。语速：稍慢。",
        )

    def test_get_ref_paths_follows_voice_lang(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "normal"
            folder.mkdir()
            (folder / "jp_normal.wav").write_bytes(b"RIFF" + b"\x00" * 100)
            (folder / "jp_normal.txt").write_text("こんにちは", encoding="utf-8")
            (folder / "zh_normal.wav").write_bytes(b"RIFF" + b"\x00" * 100)
            (folder / "zh_normal.txt").write_text("你好", encoding="utf-8")

            class Host(TtsGsvMixin):
                ref_dir = str(root)
                voice_lang = "zh"

            wav, text, lang = Host()._get_ref_paths("neutral")
            self.assertTrue(str(wav).endswith("zh_normal.wav"))
            self.assertEqual(text, "你好")
            self.assertEqual(lang, "中文")

    def test_gsv_explicit_reference_audio_overrides_mood_directory(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference = root / "custom_reference.wav"
            reference.write_bytes(b"RIFF" + b"\x00" * 100)
            reference.with_suffix(".txt").write_text(
                "这是参考音频的文字",
                encoding="utf-8",
            )

            class Host(TtsGsvMixin):
                ref_dir = str(root / "automatic-references")
                voice_lang = "jp"
                gsv_ref_wav = str(reference)
                gsv_ref_lang = "zh"

            wav, text, lang = Host()._get_ref_paths("sad")

        self.assertEqual(wav, str(reference))
        self.assertEqual(text, "这是参考音频的文字")
        self.assertEqual(lang, "中文")

    def test_gsv_missing_explicit_reference_falls_back_to_mood_reference(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "normal"
            folder.mkdir()
            fallback = folder / "jp_normal.wav"
            fallback.write_bytes(b"RIFF" + b"\x00" * 100)
            (folder / "jp_normal.txt").write_text("こんにちは", encoding="utf-8")

            class Host(TtsGsvMixin):
                ref_dir = str(root)
                voice_lang = "jp"
                gsv_ref_wav = str(root / "missing.wav")
                gsv_ref_lang = "zh"

            wav, text, lang = Host()._get_ref_paths("neutral")

        self.assertEqual(wav, str(fallback))
        self.assertEqual(text, "こんにちは")
        self.assertEqual(lang, "日文")

    def test_wizard_clone_picker_starts_in_project_cache(self):
        from wizard.page_tts_mimo import QFileDialog, TtsPageMimoMixin

        seen = {}

        def fake_picker(*args):
            seen["directory"] = args[2]
            seen["filter"] = args[3]
            return "", ""

        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=fake_picker):
            TtsPageMimoMixin._browse_clone_ref(object())

        self.assertEqual(
            Path(seen["directory"]).resolve(),
            (ROOT / "voice_cache").resolve(),
        )
        self.assertEqual(seen["filter"], "Audio (*.wav *.mp3);;All (*.*)")

    def test_tts_log_and_status_background_stay_project_relative(self):
        import meapet.log as meapet_log
        from meapet.desktop.status_panel import BG_PATH

        with tempfile.TemporaryDirectory() as td:
            previous_cwd = os.getcwd()
            logger = None
            try:
                os.chdir(td)
                with mock.patch(
                    "meapet.log.logging.handlers.TimedRotatingFileHandler"
                ) as handler_factory:
                    logger = meapet_log.get_color_logger("tts-path-regression")
                log_path = Path(
                    handler_factory.call_args.kwargs["filename"]
                ).resolve()
            finally:
                os.chdir(previous_cwd)
                if logger is not None:
                    logger.handlers.clear()

            self.assertEqual(log_path.parent, (ROOT / "logs").resolve())

        self.assertEqual(Path(BG_PATH).resolve(), (ROOT / "ev312b.png").resolve())

    def test_vits_health_does_not_probe_gpt_sovits_dependencies(self):
        from meapet.tts.service import MeaTTS

        tts = MeaTTS({
            "tts": {
                "enabled": True,
                "engine": "vits",
                "vits_python": sys.executable,
                "translate_to_jp": False,
            }
        })
        with mock.patch(
            "meapet.tts.service.auto_install_gsv_deps",
            side_effect=AssertionError("VITS 不应检查 GPT-SoVITS 依赖"),
        ), mock.patch(
            "meapet.tts.service.is_model_artifact_ready",
            return_value=True,
        ):
            self.assertTrue(tts.health_check())

    def test_gsv_subprocess_runs_from_project_root(self):
        import json
        from meapet.tts.engines.gsv import TtsGsvMixin

        calls = []

        class Result:
            returncode = 0
            stdout = json.dumps({"ok": True, "duration": 1.0}).encode("utf-8")
            stderr = b""

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        host = type(
            "Host",
            (),
            {
                "python_exe": sys.executable,
                "infer_script": str(ROOT / "meapet" / "tools" / "gsv_infer.py"),
                "gpt_path": "gpt.ckpt",
                "sovits_path": "sovits.pth",
                "top_k": 15,
                "top_p": 0.8,
                "temperature": 0.6,
                "speed": 1.0,
                "sample_steps": 8,
                "timeout": 1,
            },
        )()
        with mock.patch("meapet.tts.engines.gsv.subprocess.run", side_effect=fake_run):
            result = TtsGsvMixin._speak_gsv(
                host,
                "こんにちは",
                "out.wav",
                "neutral",
                "ref.wav",
                "参考",
                "日文",
            )

        self.assertEqual(result, ("out.wav", "jp"))
        self.assertEqual(Path(calls[0][1]["cwd"]).resolve(), ROOT.resolve())

    def test_gsv_payload_separates_reference_and_synthesis_languages(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        calls = []

        class Result:
            returncode = 0
            stdout = json.dumps({"ok": True, "duration": 1.0}).encode("utf-8")
            stderr = b""

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        host = type(
            "Host",
            (),
            {
                "python_exe": sys.executable,
                "infer_script": str(ROOT / "meapet" / "tools" / "gsv_infer.py"),
                "gpt_path": "gpt.ckpt",
                "sovits_path": "sovits.pth",
                "top_k": 15,
                "top_p": 0.8,
                "temperature": 0.6,
                "speed": 1.0,
                "sample_steps": 8,
                "timeout": 1,
            },
        )()
        with mock.patch("meapet.tts.engines.gsv.subprocess.run", side_effect=fake_run):
            result = TtsGsvMixin._speak_gsv(
                host,
                "你好",
                "out.wav",
                "neutral",
                "ref.wav",
                "参考文本",
                "日文",
                text_lang="中文",
            )

        payload = json.loads(calls[0][1]["input"].decode("utf-8"))
        self.assertEqual(payload["prompt_language"], "日文")
        self.assertEqual(payload["text_language"], "中文")
        self.assertEqual(result, ("out.wav", "zh"))

    def test_gsv_reference_config_normalizes_path_and_language(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "tts": {
                    "gsv_ref_wav": "  ./refs/custom.wav  ",
                    "gsv_ref_lang": "中文",
                }
            }
        )

        self.assertEqual(config["tts"]["gsv_ref_wav"], "./refs/custom.wav")
        self.assertEqual(config["tts"]["gsv_ref_lang"], "zh")

    def test_wizard_tts_import_survives_stale_config_store_module(self):
        """更新代码后，运行中的桌宠仍可能缓存旧版配置模块。"""
        code = """
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import meapet.config.store as store

del store.normalize_gsv_ref_language
sys.modules.pop("wizard.page_tts", None)

from wizard.page_tts import TTSPage
print(TTSPage.__name__)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TTSPage", result.stdout)

    def test_redaction_masks_hyphenated_secrets(self):
        from meapet.utils import redact_text

        secret = "sk-proj-example_KEY-1234567890abcdef"
        redacted = redact_text(f"request failed for {secret}")
        self.assertNotIn(secret, redacted)
        self.assertIn("…", redacted)

if __name__ == "__main__":
    unittest.main()
