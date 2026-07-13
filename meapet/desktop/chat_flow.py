"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import shutil
import threading

from PyQt5.QtCore import QTimer

from meapet.utils import debug_enabled, log_error, redact_text
from meapet.chat.engine import SYSTEM_PROMPT
from meapet.desktop import status_language
from meapet.desktop.workers import ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox, set_awaiting_reply_state
from meapet.log import get_color_logger

log = get_color_logger("chat_flow")

# 串行队列：确保记忆操作（摘要、提取等）不会并发执行
_memory_op_lock = threading.Lock()


def _log_private_text(label: str, text: str, *, suffix: str = "") -> None:
    """默认仅记录文本长度；显式调试时才记录正文。"""
    value = str(text or "")
    tail = f" {suffix}" if suffix else ""
    if debug_enabled():
        log.debug(f"{label}: chars={len(value)}{tail}\n{value}")
    else:
        log.debug(f"{label}: chars={len(value)}{tail}")


class PetChatFlowMixin:
    def _start_chat(self):
        """双击触发：在桌宠附近打开消息编辑器。"""
        log.info("[chat] 启动对话编辑器")
        clear_bubbles = getattr(self, "_clear_bubbles", None)
        if callable(clear_bubbles):
            clear_bubbles()
        else:
            bubble = getattr(self, "bubble", None)
            if bubble is not None:
                try:
                    bubble.hide()
                except RuntimeError:
                    pass

        self._chat_input = ChatInputBox(None)
        if getattr(self, "_awaiting_reply", False):
            self._chat_input.set_busy(True, status_language.thinking_busy())

        # 以编辑器实际尺寸居中，避免 UI 调整后仍依赖旧的硬编码宽度。
        input_x = self.pos().x() + (self.width() - self._chat_input.width()) // 2
        input_y = self.pos().y() - self._chat_input.height() - 20
        if input_y < 30:
            input_y = self.pos().y() + self.height() + 20

        self._chat_input.move(max(0, input_x), max(0, input_y))
        self._chat_input.text_submitted.connect(self._on_input_submit)
        self._chat_input.show()

    def _on_input_submit(self, text: str):
        """用户提交了输入"""
        if getattr(self, "_awaiting_reply", False):
            log.warning("[chat] 对话被拒绝：正在等待回复中")
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            return
        self._record_interaction()
        _log_private_text("[input] 收到用户输入", text)
        log.info("[input] 提交消息，准备回复")
        self._show_bubble("……？", 1500)
        self._position_bubble()
        QTimer.singleShot(1200, lambda: self._do_chat(text))

    def _do_chat(self, message: str):
        """执行 LLM 对话（后台线程）"""
        if self._awaiting_reply:
            log.warning("[chat] 对话被拒绝：正在等待回复中")
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            return
        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        self._safe_set_mood("talking")
        self._last_user_msg = message
        _log_private_text("[chat] 发送给 LLM", message)

        # 显示思考中提示
        self._show_bubble(
            status_language.thinking(),
            self.config["bubble_duration_ms"]["thinking"],
        )  # 0 = 持久显示
        self._position_bubble()

        # 停止旧 worker（防止泄漏）
        if hasattr(self, '_chat_worker') and self._chat_worker is not None:
            if self._chat_worker.isRunning():
                self._chat_worker.terminate()
                self._chat_worker.wait(1000)
            self._chat_worker.deleteLater()
        if hasattr(self, '_chat_poll'):
            self._chat_poll.stop()

        # 超时保护（匹配 Ollama 读取超时 120s + 缓冲）
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        self._chat_timeout = QTimer(self)
        self._chat_timeout.setSingleShot(True)
        self._chat_timeout.timeout.connect(self._on_chat_timeout)
        self._chat_timeout.start(130000)

        self._chat_worker = ChatWorker(self.chat_engine, message)
        self._chat_worker.start()
        # 轮询 timer：每 100ms 检查 worker 是否完成
        self._chat_poll = QTimer(self)
        self._chat_poll.timeout.connect(self._poll_chat)
        self._chat_poll.start(100)
        log.info("[chat] ChatWorker 已启动")

    def _poll_chat(self):
        """主线程轮询 ChatWorker 完成状态"""
        if not hasattr(self, '_chat_worker') or self._chat_worker is None:
            if hasattr(self, '_chat_poll') and self._chat_poll:
                self._chat_poll.stop()
            return
        if not self._chat_worker.done:
            return
        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
        result, error = self._chat_worker.get_result()
        self._chat_worker.deleteLater()
        if error:
            self._on_chat_error(error)
        elif result:
            reply, mood = result
            self._on_chat_done(reply, mood)
        else:
            # result 和 error 都为空（异常路径未捕获到）→ 释放锁，防止死锁
            log.warning("[chat] _poll_chat: 空结果，释放对话锁")
            set_awaiting_reply_state(self, False)
            if hasattr(self, '_chat_timeout') and self._chat_timeout:
                self._chat_timeout.stop()

    def _do_memory_ops(self, reply: str, mood: str, user_msg: str = ""):
        """记忆操作放后台线程执行，不阻塞主线程。通过串行锁避免竞态。"""
        t = threading.Thread(target=self._do_memory_ops_sync, args=(reply, mood, user_msg), daemon=True)
        t.start()

    def _do_memory_ops_sync(self, reply: str, mood: str, user_msg: str):
        if not user_msg:
            return
        with _memory_op_lock:
            try:
                engine = self.chat_engine
                if not engine or not engine.memory:
                    return
                engine.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
                engine.memory.add_chat("user", user_msg)
                engine.memory.add_chat("mea", reply, mood)
                n = len(user_msg or "")
                if n < 10:
                    delta = 1
                elif n < 50:
                    delta = 2
                else:
                    delta = 3
                upgrade_msg = engine.memory.add_affection(delta)
                full_system = SYSTEM_PROMPT + "\n\n" + engine.memory.build_context_prompt(current_query=user_msg)
                if upgrade_msg:
                    full_system += f"\n\n[内部：好感度升至{engine.memory.get_affection_tier()[1]}。请用稍暖的语气回应。]"
                engine.history[0] = {"role": "system", "content": full_system}
                engine.memory.mark_today_chatted()
                engine.memory.increment_message_counter()
                engine._extract_memories(user_msg, reply)
                engine._summarize_if_needed()
                engine.memory.store_chat_exchange(user_msg, reply)
            except Exception as e:
                log.error(f"[memory] 操作失败: {e}")

    def _on_chat_done(self, reply: str, mood: str):
        _log_private_text("[reply] LLM 回复", reply, suffix=f"mood={mood}")
        log.info(f"[reply] 收到回复，mood={mood}")
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        eng = getattr(self, "chat_engine", None)
        known_moods = getattr(eng, "_MOOD_TAGS", ())
        detected = mood if mood in known_moods else self._detect_mood(reply)
        # 气泡只显示中文；TTS 优先用模型附带的日语行（无则回退整段 reply，由 TTS 再翻译）
        voice_text = reply
        tts_style = ""
        try:
            if eng is not None and hasattr(eng, "take_voice_text"):
                jp = (eng.take_voice_text() or "").strip()
                if jp:
                    voice_text = jp
                    _log_private_text("[tts] TTS 使用模型日语行", jp)
        except Exception as e:
            log.error(f"[tts] 取日语行失败: {e}")
        try:
            if eng is not None and hasattr(eng, "take_tts_style"):
                tts_style = (eng.take_tts_style() or "").strip()
        except Exception as e:
            log.error(f"[tts] 取 TTS 风格失败: {e}")
        # 捕获本轮用户消息，记忆操作与 TTS 并行且由上游串行锁保护。
        user_msg = getattr(self, '_last_user_msg', '') or ''
        QTimer.singleShot(
            0,
            lambda: self._do_memory_ops(reply, detected, user_msg),
        )

        # 最终回复必须等音频文件真正生成后再显示；否则文字和声音会明显错位。
        # TTS 关闭或启动失败时仍立即显示文字，不能让回复永久卡住。
        self._pending_chat_reply = (reply, detected)
        tts = getattr(self, "tts", None)
        if tts is None or not bool(getattr(tts, "enabled", True)):
            self._complete_pending_chat_reply()
            return

        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        try:
            self._tts_worker = TTSWorker(
                tts,
                voice_text,
                mood=detected,
                style=tts_style,
            )
            self._tts_worker.start()
            self._ensure_tts_poll()
        except Exception as e:
            log.error(f"[tts] 语音合成启动失败，回退文字: {type(e).__name__}: {e}")
            self._tts_worker = None
            self._complete_pending_chat_reply()

    def _ensure_tts_poll(self):
        """确保 TTS 轮询 timer 在运行"""
        if not hasattr(self, '_tts_poll') or not self._tts_poll:
            self._tts_poll = QTimer(self)
            self._tts_poll.timeout.connect(self._poll_tts)
            self._tts_poll.start(100)

    def _poll_tts(self):
        """轮询所有 TTSWorker 完成状态"""
        if hasattr(self, '_tts_worker') and self._tts_worker and self._tts_worker.done:
            try:
                result = self._tts_worker.get_result()
            except Exception as e:
                log.error(f"[tts] 读取合成结果失败: {type(e).__name__}: {e}")
                result = None
            self._tts_worker = None
            # 空结果同样必须进入完成处理，以显示等待中的文字回复。
            self._on_tts_audio(result)
        if hasattr(self, '_speak_worker') and self._speak_worker and self._speak_worker.done:
            result = self._speak_worker.get_result()
            self._speak_worker = None
            if result:
                self._on_speak_audio_ready(result)
        if hasattr(self, '_watch_tts_worker') and self._watch_tts_worker and self._watch_tts_worker.done:
            result = self._watch_tts_worker.get_result()
            self._watch_tts_worker = None
            # 取走 _pending_reply 后立即删除，避免回调内部二次读取
            pending = getattr(self, '_pending_reply', None)
            if pending:
                reply, mood = pending
                if hasattr(self, '_pending_reply'):
                    del self._pending_reply
                self._on_watch_tts_and_show(result, reply, mood)
            else:
                self._on_watch_tts_and_show(result, None, None)


        # 没有待处理的 worker 就停止
        if not any([
            getattr(self, '_tts_worker', None),
            getattr(self, '_speak_worker', None),
            getattr(self, '_watch_tts_worker', None),
        ]):
            if hasattr(self, '_tts_poll') and self._tts_poll:
                self._tts_poll.stop()
                self._tts_poll.deleteLater()
                self._tts_poll = None

    def _detect_mood(self, text: str) -> str:
        """从回复文本推测情绪（替代后端 mood 检测）"""
        t = text.lower()
        if any(k in t for k in ["嘿嘿","好吃","开心","高兴","棒","哈哈","喜欢"]):
            return "happy"
        if any(k in t for k in ["烦","无聊","没兴趣","别吵","哼","切"]):
            return "annoyed"
        if any(k in t for k in ["哦？","咦","诶","真的？","意外"]):
            return "surprised"
        if any(k in t for k in ["有意思","有趣","让我看看","好奇"]):
            return "curious"
        if any(k in t for k in ["唉","难过","伤心","可惜"]):
            return "sad"
        if any(k in t for k in ["又没在","随便","……","脸红","害羞"]):
            return "shy"
        return "neutral"

    def _complete_pending_chat_reply(self, wav_path: str = "") -> None:
        """显示等待中的聊天回复，并在同一事件循环节拍开始播放音频。"""
        pending = getattr(self, "_pending_chat_reply", None)
        if pending is None:
            # 兼容旧调用：没有等待文字时，仍允许单独播放有效音频。
            if wav_path:
                self._play_audio(wav_path)
            return

        try:
            del self._pending_chat_reply
        except AttributeError:
            pass

        reply, mood = pending
        duration_ms = None
        config = getattr(self, "config", {}) or {}
        tts_config = config.get("tts") or {}
        bubble_config = config.get("bubble_duration_ms") or {}
        if wav_path and tts_config.get("sync_with_audio"):
            audio_ms = self._get_wav_duration_ms(wav_path)
            if audio_ms > 0:
                duration_ms = max(
                    audio_ms + 500,
                    int(bubble_config.get("reply", 3000)),
                )

        try:
            if duration_ms is None:
                self.show_reply(reply, mood)
            else:
                self.show_reply(reply, mood, duration_ms=duration_ms)
        except Exception as e:
            log.error(f"[chat] 显示等待回复失败: {type(e).__name__}: {e}")
        finally:
            set_awaiting_reply_state(self, False)

        if wav_path:
            self._play_audio(wav_path)

    def _on_tts_audio(self, raw: str | None):
        """TTS 完成后再显示最终气泡；失败时显示无声文字兜底。"""
        value = str(raw or "")
        wav_path = value.rsplit("|", 1)[0] if "|" in value else value
        if not wav_path or not os.path.exists(wav_path):
            log.warning(f"[audio] TTS 未生成有效文件，回退文字: chars={len(value)}")
            if debug_enabled():
                log.debug(f"[audio] 无效 TTS 返回: {raw!r}")
            self._complete_pending_chat_reply()
            return
        self._complete_pending_chat_reply(wav_path)

    def _on_chat_error(self, err: str):
        _log_private_text("[chat] 错误", err)
        error_summary = (
            redact_text(err)
            if debug_enabled()
            else f"error_chars={len(err or '')}"
        )
        log.error(f"[chat] 对话错误: {error_summary}")
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        log_error(
            "pet_chat",
            error_summary,
        )
        self.show_reply(
            f"{status_language.chat_error_prefix()}{err}",
            "annoyed",
            duration_ms=10000,
        )
        set_awaiting_reply_state(self, False)

    def _on_chat_timeout(self):
        """ChatWorker 超时 — 强制终止线程并释放锁"""
        log.warning("[chat] ChatWorker 超时，释放锁")
        set_awaiting_reply_state(self, False)
        self._show_bubble(status_language.chat_timeout(), 3000)
        self._position_bubble()
        if hasattr(self, '_chat_worker') and self._chat_worker:
            if self._chat_worker.isRunning():
                # quit() 协作取消 async 任务 / future
                # 用 terminate() 强制终止
                self._chat_worker.terminate()
                if not self._chat_worker.wait(2000):
                    log.warn("[chat] ChatWorker 无法终止")
            self._chat_worker.deleteLater()
            self._chat_worker = None

    def _speak_and_show(self, text: str, duration_ms: int, mood: str = "neutral"):
        """显示文字 + 后台合成语音播放（异常不抛出）"""
        try:
            self.show_reply(text, mood)
        except Exception as e:
            log.error(f"[speak] 显示文字失败: {type(e).__name__}: {e}")
        try:
            tts = getattr(self, "tts", None)
            if tts and getattr(tts, "enabled", False) and len((text or "").strip()) >= 2:
                self._current_speaking_text = text
                cached = None
                try:
                    cached = tts.get_cached(text)
                except Exception as e:
                    log.error(f"[speak] 缓存查询失败: {type(e).__name__}: {e}")
                if cached:
                    self._play_audio(cached)
                    return
                self._speak_worker = TTSWorker(tts, text, mood=mood)
                self._speak_worker.start()
                self._ensure_tts_poll()
        except Exception as e:
            log.error(f"[speak] 语音合成启动失败: {type(e).__name__}: {e}")

    def _on_speak_audio_ready(self, raw: str):
        """后台语音合成完成，播放并缓存"""
        wav_path = raw
        tts_lang = ""
        if "|" in raw:
            parts = raw.rsplit("|", 1)
            wav_path = parts[0]
            tts_lang = parts[1]
        if wav_path and os.path.exists(wav_path):
            # 缓存：用语言前缀统一命名
            if tts_lang:
                safe = self._safe_name(
                    self._current_speaking_text
                    if hasattr(self, "_current_speaking_text") else ""
                )
                if safe:
                    from meapet.paths import project_path
                    cache_dir = project_path("voice_cache")
                    os.makedirs(cache_dir, exist_ok=True)
                    cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                    try:
                        shutil.copy2(wav_path, cache_path)
                    except Exception:
                        pass
            self._play_audio(wav_path)

    def show_reply(self, text: str, mood: str = "neutral", duration_ms: int = None):
        if duration_ms is None:
            duration_ms = self.config["bubble_duration_ms"]["reply"]
        self._safe_set_mood(mood)
        self._show_bubble(text, max(duration_ms, 3000), mood=mood)
        self._position_bubble()
