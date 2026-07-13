"""聊天 / TTS 任务：投递到后台 asyncio loop（不再为每个请求 new Thread）。"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from queue import Empty, Queue
from typing import Optional

from meapet.async_runtime import submit
from meapet.chat.engine import ChatEngine
from meapet.tts.service import MeaTTS
from meapet.utils import log_error, safe_print


class ChatWorker:
    """异步对话任务 — 兼容原 Thread worker 轮询接口（done / get_result / start）。"""

    def __init__(self, engine: ChatEngine, message: str):
        self.engine = engine
        self.message = message
        self._future: Optional[Future] = None
        self._done = False
        self._result = None
        self._error = None

    def start(self):
        self._done = False
        self._result = None
        self._error = None
        self._future = submit(self._run())

        def _done_cb(fut: Future):
            try:
                self._result = fut.result()
            except Exception as e:
                self._error = f"{type(e).__name__}: {e}"
                log_error("ChatWorker", self._error)
            self._done = True

        self._future.add_done_callback(_done_cb)

    async def _run(self):
        if hasattr(self.engine, "quick_chat_async"):
            reply, mood = await self.engine.quick_chat_async(self.message)
        else:
            reply, mood = await asyncio.to_thread(self.engine.quick_chat, self.message)
        return (reply, mood)

    @property
    def done(self):
        return self._done

    def get_result(self):
        return self._result, self._error

    def isRunning(self):
        return self._future is not None and not self._future.done()

    def terminate(self):
        try:
            if self.engine is not None and hasattr(self.engine, "cancel"):
                self.engine.cancel()
        except Exception:
            pass
        try:
            if self._future is not None and not self._future.done():
                self._future.cancel()
        except Exception:
            pass

    def wait(self, timeout_ms=1000):
        if self._future is not None:
            try:
                self._future.result(timeout=timeout_ms / 1000)
            except Exception:
                pass

    def deleteLater(self):
        self._future = None


class AgentChatWorker:
    """消费 Agent async event stream，并让 GUI 定时器增量取走事件。"""

    def __init__(self, adapter, request):
        self.adapter = adapter
        self.request = request
        self._future: Optional[Future] = None
        self._events: Queue = Queue()
        self._done = False
        self._error = None

    def start(self):
        self._done = False
        self._error = None
        self._future = submit(self._run())

        def _done_cb(fut: Future):
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                log_error("AgentChatWorker", self._error)
            self._done = True

        self._future.add_done_callback(_done_cb)

    async def _run(self):
        async for event in self.adapter.stream_turn(self.request):
            self._events.put(event)

    def take_events(self):
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return tuple(events)

    @property
    def done(self):
        return self._done

    @property
    def error(self):
        return self._error

    def isRunning(self):
        return self._future is not None and not self._future.done()

    def terminate(self):
        try:
            submit(self.adapter.cancel(self.request.turn_id))
        except Exception:
            pass
        try:
            if self._future is not None and not self._future.done():
                self._future.cancel()
        except Exception:
            pass

    def wait(self, timeout_ms=1000):
        if self._future is not None:
            try:
                self._future.result(timeout=timeout_ms / 1000)
            except Exception:
                pass

    def deleteLater(self):
        self._future = None


class TTSWorker:
    """异步 TTS 任务 — 兼容原轮询接口。"""

    def __init__(
        self,
        tts: MeaTTS,
        text: str,
        mood: str = "neutral",
        style: str = "",
        language: str = "",
    ):
        self.tts = tts
        self.text = text
        self.mood = mood
        self.style = style
        self.language = str(language or "").strip()
        self._future: Optional[Future] = None
        self._done = False
        self._result = None

    def start(self):
        self._done = False
        self._result = None
        self._future = submit(self._run())

        def _done_cb(fut: Future):
            try:
                self._result = fut.result()
            except Exception as e:
                log_error("TTSWorker", f"{type(e).__name__}: {e}")
                safe_print(f"[TTSWorker] {e}")
                self._result = None
            self._done = True

        self._future.add_done_callback(_done_cb)

    async def _run(self):
        if hasattr(self.tts, "speak_async"):
            if self.language:
                result = await self.tts.speak_async(
                    self.text,
                    mood=self.mood,
                    style=self.style,
                    language=self.language,
                )
            elif self.style:
                result = await self.tts.speak_async(
                    self.text,
                    mood=self.mood,
                    style=self.style,
                )
            else:
                result = await self.tts.speak_async(self.text, mood=self.mood)
        else:
            if self.language:
                args = (self.text, self.mood, self.style, self.language)
            elif self.style:
                args = (self.text, self.mood, self.style)
            else:
                args = (self.text, self.mood)
            result = await asyncio.to_thread(self.tts.speak, *args)
        if result and result[0]:
            wav, lang = result
            return f"{wav}|{lang}"
        return None

    @property
    def done(self):
        return self._done

    def get_result(self):
        return self._result

    def isRunning(self):
        return self._future is not None and not self._future.done()

    def wait(self, timeout_ms=1000):
        if self._future is not None:
            try:
                self._future.result(timeout=timeout_ms / 1000)
            except Exception:
                pass

    def deleteLater(self):
        self._future = None
