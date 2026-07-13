"""Mixin 组合契约：方法绑定、跨 mixin 调用链、交互异常不抛出"""
from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestMixinBinding(unittest.TestCase):
    def test_no_staticmethod_with_self_param(self):
        from meapet.desktop.audio import PetAudioMixin
        from meapet.desktop.watch_ctrl import PetWatcherMixin
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        for cls in (PetAudioMixin, PetWatcherMixin, PetChatFlowMixin, PetInteractionMixin):
            for name, obj in cls.__dict__.items():
                if not isinstance(obj, staticmethod):
                    continue
                params = list(inspect.signature(obj.__func__).parameters)
                self.assertNotEqual(
                    params[:1],
                    ["self"],
                    msg=f"{cls.__name__}.{name} is staticmethod but first param is self",
                )

    def test_speak_and_show_is_instance_method(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin

        self.assertFalse(
            isinstance(
                inspect.getattr_static(PetChatFlowMixin, "_speak_and_show"),
                staticmethod,
            )
        )
        params = list(inspect.signature(PetChatFlowMixin._speak_and_show).parameters)
        self.assertEqual(params[0], "self")

    def test_get_wav_duration_is_staticmethod_without_self(self):
        from meapet.desktop.audio import PetAudioMixin

        obj = inspect.getattr_static(PetAudioMixin, "_get_wav_duration_ms")
        self.assertTrue(isinstance(obj, staticmethod))
        params = list(inspect.signature(obj.__func__).parameters)
        self.assertNotEqual(params[:1], ["self"])


class _FakeBubble:
    def __init__(self):
        self.texts = []

    def show_text(self, text, duration_ms=0, mood=None, **kwargs):
        self.texts.append((text, duration_ms, mood))


class _FakeTTS:
    enabled = True

    def get_cached(self, text):
        return None


class _Composite:
    """手动拼出与 MeaPet 相同的跨 mixin 能力（不启 Qt 主窗）"""

    def __init__(self):
        from meapet.desktop.audio import PetAudioMixin
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        # 绑定 mixin 方法到本实例（与多继承解析一致的子集）
        self._mix_audio = PetAudioMixin
        self._mix_chat = PetChatFlowMixin
        self._mix_inter = PetInteractionMixin

        self.config = {
            "bubble_duration_ms": {
                "default": 1000,
                "reply": 1000,
                "interaction": 1000,
                "watch": 1000,
                "thinking": 0,
            },
            "tts": {"sync_with_audio": False},
        }
        self.tts = _FakeTTS()
        self.bubble = _FakeBubble()
        self._last_interaction_time = 0
        self._safe_moods = []
        self._played = []
        self._workers = []

    # --- wire methods like MRO would ---
    def _safe_set_mood(self, mood):
        self._safe_moods.append(mood)

    def _play_audio(self, path):
        self._played.append(path)

    def _position_bubble(self):
        pass

    def _ensure_tts_poll(self):
        pass

    def show_reply(self, text, mood="neutral", duration_ms=None):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        return PetChatFlowMixin.show_reply(self, text, mood, duration_ms)

    def _show_bubble(self, text, duration_ms=None, mood=None):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._show_bubble(self, text, duration_ms, mood=mood)

    def _speak_and_show(self, text, duration_ms, mood="neutral"):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        return PetChatFlowMixin._speak_and_show(self, text, duration_ms, mood)

    def _interaction_speak(self, text, duration_ms, mood):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._interaction_speak(self, text, duration_ms, mood)

    def _on_head_patted(self):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._on_head_patted(self)

    def _get_cached_interaction(self, text, lang):
        return None

    def _record_interaction(self):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._record_interaction(self)

    def _safe_name(self, text):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._safe_name(self, text)


class TestCrossMixinCallChain(unittest.TestCase):
    def test_interaction_speak_calls_speak_and_show_with_real_self(self):
        c = _Composite()
        seen = {}

        def fake_speak_and_show(self, text, duration_ms, mood="neutral"):
            seen["self_type"] = type(self).__name__
            seen["has_tts"] = hasattr(self, "tts") and not isinstance(self, str)
            seen["text"] = text
            seen["mood"] = mood
            # also exercise show path
            self.show_reply(text, mood)

        with mock.patch.object(_Composite, "_speak_and_show", fake_speak_and_show):
            c._interaction_speak("别摸了……", 1000, "annoyed")

        self.assertEqual(seen.get("self_type"), "_Composite")
        self.assertTrue(seen.get("has_tts"))
        self.assertEqual(seen.get("text"), "别摸了……")
        self.assertTrue(c.bubble.texts)

    def test_head_patted_does_not_raise_when_tts_pipeline_breaks(self):
        c = _Composite()

        def boom(self, text, duration_ms, mood="neutral"):
            raise RuntimeError("tts pipeline broken")

        with mock.patch.object(_Composite, "_speak_and_show", boom):
            # should not raise
            c._on_head_patted()
        # mood attempted
        self.assertTrue(c._safe_moods)
        # text still shown via fallback in _interaction_speak
        self.assertTrue(c.bubble.texts)

    def test_speak_and_show_tolerates_missing_tts(self):
        c = _Composite()
        c.tts = None
        c._speak_and_show("你好喵", 1000, "happy")
        self.assertTrue(c.bubble.texts)

    def test_speak_and_show_rejects_string_self_pattern(self):
        """文档化错误形态：若误标 staticmethod，self 会变成 str。"""
        from meapet.desktop.chat_flow import PetChatFlowMixin

        # 直接按错误调用方式应能被我们的防护挡住或至少不认为 str 有 tts
        # 正确绑定：
        c = _Composite()
        self.assertTrue(hasattr(c, "tts"))
        self.assertFalse(isinstance(c, str))
        # 函数参数名第一位是 self
        params = list(inspect.signature(PetChatFlowMixin._speak_and_show).parameters)
        self.assertEqual(params[0], "self")


class TestFormattedChatToTtsFlow(unittest.TestCase):
    def test_chat_reply_waits_for_audio_before_showing_bubble(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        captured = {"events": []}

        class Engine:
            _MOOD_TAGS = {"neutral", "shy"}

            @staticmethod
            def take_voice_text():
                return "べ、別に待ってないにゃ"

            @staticmethod
            def take_tts_style():
                return "保持参考音色。情绪：害羞。"

        class FakeWorker:
            def __init__(self, tts, text, mood="neutral", style=""):
                captured.update(tts=tts, text=text, mood=mood, style=style)
                self.done = False

            def start(self):
                captured["started"] = True

        class FakeTTS:
            enabled = True

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = FakeTTS()
            _awaiting_reply = True
            config = {
                "bubble_duration_ms": {"reply": 1000},
                "tts": {"sync_with_audio": True},
            }

            @staticmethod
            def _detect_mood(_text):
                raise AssertionError("模型 mood 有效时不应重新猜测")

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                captured.update(
                    display=text,
                    display_mood=mood,
                    display_duration_ms=duration_ms,
                )
                captured["events"].append("bubble")

            @staticmethod
            def _get_wav_duration_ms(_path):
                return 1200

            @staticmethod
            def _play_audio(path):
                captured["played"] = path
                captured["events"].append("audio")

            @staticmethod
            def _ensure_tts_poll():
                captured["polling"] = True

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(chat_flow, "TTSWorker", FakeWorker),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "才没有等你回来喵",
                "shy",
            )

        self.assertNotIn("display", captured)
        self.assertTrue(host._awaiting_reply)
        self.assertEqual(captured["text"], "べ、別に待ってないにゃ")
        self.assertEqual(captured["mood"], "shy")
        self.assertEqual(captured["style"], "保持参考音色。情绪：害羞。")
        self.assertTrue(captured["started"])
        self.assertTrue(captured["polling"])

        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "reply.wav"
            wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
            chat_flow.PetChatFlowMixin._on_tts_audio(
                host,
                f"{wav_path}|jp",
            )

        self.assertEqual(captured["display"], "才没有等你回来喵")
        self.assertEqual(captured["display_mood"], "shy")
        self.assertEqual(captured["display_duration_ms"], 1700)
        self.assertEqual(captured["events"], ["bubble", "audio"])
        self.assertFalse(host._awaiting_reply)

    def test_chat_reply_falls_back_to_text_when_tts_returns_no_audio(self):
        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class DoneWorker:
            done = True

            @staticmethod
            def get_result():
                return None

        class Host(chat_flow.PetChatFlowMixin):
            _awaiting_reply = True
            _pending_chat_reply = ("语音失败也要显示喵", "neutral")
            _tts_worker = DoneWorker()
            config = {
                "bubble_duration_ms": {"reply": 3000},
                "tts": {"sync_with_audio": False},
            }

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

        host = Host()
        chat_flow.PetChatFlowMixin._poll_tts(host)

        self.assertEqual(displayed, [("语音失败也要显示喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)

    def test_chat_reply_shows_immediately_when_tts_is_disabled(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class Engine:
            _MOOD_TAGS = {"neutral"}

            @staticmethod
            def take_voice_text():
                return ""

            @staticmethod
            def take_tts_style():
                return ""

        class DisabledTTS:
            enabled = False

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = DisabledTTS()
            _awaiting_reply = True

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

            @staticmethod
            def _detect_mood(_text):
                return "neutral"

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(
                chat_flow,
                "TTSWorker",
                side_effect=AssertionError("TTS 关闭时不应创建 worker"),
            ),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "这次只显示文字喵",
                "neutral",
            )

        self.assertEqual(displayed, [("这次只显示文字喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)

    def test_chat_reply_falls_back_when_tts_worker_cannot_start(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class Engine:
            _MOOD_TAGS = {"neutral"}

            @staticmethod
            def take_voice_text():
                return ""

            @staticmethod
            def take_tts_style():
                return ""

        class EnabledTTS:
            enabled = True

        class BrokenWorker:
            def __init__(self, *_args, **_kwargs):
                pass

            @staticmethod
            def start():
                raise RuntimeError("worker start failed")

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = EnabledTTS()
            _awaiting_reply = True

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

            @staticmethod
            def _detect_mood(_text):
                return "neutral"

            @staticmethod
            def _ensure_tts_poll():
                raise AssertionError("启动失败时不应轮询")

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(chat_flow, "TTSWorker", BrokenWorker),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "启动失败也要显示喵",
                "neutral",
            )

        self.assertEqual(displayed, [("启动失败也要显示喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)


class TestRequiredSurfaceOnMeaPetSource(unittest.TestCase):
    def test_meapet_inherits_all_mixins(self):
        text = (ROOT / "meapet" / "desktop" / "app.py").read_text(encoding="utf-8")
        self.assertIn("PetAudioMixin", text)
        self.assertIn("PetWatcherMixin", text)
        self.assertIn("PetChatFlowMixin", text)
        self.assertIn("PetInteractionMixin", text)
        self.assertIn("PetWindowChromeMixin", text)
        self.assertIn("PetRenderHostMixin", text)
        self.assertIn("PetConfigBridgeMixin", text)
        self.assertIn("class MeaPet(", text)

    def test_interaction_depends_on_chat_flow_method(self):
        """interaction 调用 _speak_and_show，必须由 chat_flow 提供且非 static。"""
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        self.assertTrue(hasattr(PetChatFlowMixin, "_speak_and_show"))
        self.assertTrue(hasattr(PetInteractionMixin, "_interaction_speak"))
        src = inspect.getsource(PetInteractionMixin._interaction_speak)
        self.assertIn("_speak_and_show", src)


if __name__ == "__main__":
    unittest.main()
