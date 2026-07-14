"""MeaPet 鍔熻兘 mixin锛堜粠 pet.py 鎷嗗嚭锛?""
from __future__ import annotations

import os
import shutil
import threading
import uuid

from PyQt5.QtCore import QTimer

from meapet.utils import debug_enabled, log_error, redact_text
from meapet.agent.base import (
    AgentTurnRequest,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.presentation import (
    AgentTurnPresentation,
    BeginBubble,
    CancelTurn,
    FailTurn,
    FinalizeBubble,
    FinishTurn,
    PlayAudio,
    RequestFormatRepair,
    ShowStatus,
    SubmitTTS,
    UpdateBubble,
)
from meapet.chat.engine import SYSTEM_PROMPT
from meapet.conversation.capabilities import build_agent_frontend_context
from meapet.conversation.output_protocol import (
    SegmentCompleted,
    SegmentStarted,
    SegmentTextDelta,
)
from meapet.conversation.timeline import ConversationKey
from meapet.conversation.types import (
    CompanionState,
    FrontendCapabilities,
    ReplySegment,
    normalize_voice_language,
)
from meapet.desktop import status_language
from meapet.desktop.workers import AgentChatWorker, ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox, set_awaiting_reply_state
from meapet.log import get_color_logger

log = get_color_logger("chat_flow")

# 涓茶闃熷垪锛氱‘淇濊蹇嗘搷浣滐紙鎽樿銆佹彁鍙栫瓑锛変笉浼氬苟鍙戞墽琛?
_memory_op_lock = threading.Lock()


def _log_private_text(label: str, text: str, *, suffix: str = "") -> None:
    """榛樿浠呰褰曟枃鏈暱搴︼紱鏄惧紡璋冭瘯鏃舵墠璁板綍姝ｆ枃銆?""
    value = str(text or "")
    tail = f" {suffix}" if suffix else ""
    if debug_enabled():
        log.debug(f"{label}: chars={len(value)}{tail}\n{value}")
    else:
        log.debug(f"{label}: chars={len(value)}{tail}")


class PetChatFlowMixin:
    def _refresh_conversation_key(self) -> ConversationKey:
        from meapet.conversation.orchestrator import ConversationOrchestrator

        llm = (getattr(self, "config", {}) or {}).get("llm") or {}
        mode = str(llm.get("mode") or "direct").strip().lower()
        if mode == "agent":
            agent = llm.get("agent") or {}
            key = ConversationKey(
                "agent",
                str(agent.get("kind") or "hermes"),
                str(agent.get("session_id") or "pending"),
            )
        else:
            direct = llm.get("direct") or {}
            key = ConversationKey(
                "direct",
                str(direct.get("provider") or llm.get("backend") or "ollama"),
                "local",
            )
        self._conversation_key = key
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None:
            orchestrator = ConversationOrchestrator(key)
            self._conversation_orchestrator = orchestrator
        else:
            orchestrator.activate(key)
        return key

    def _turn_context_is_current(self, context=None) -> bool:
        """旧宿主无编排器时保持兼容；真实桌面严格校验代次。"""
        if context is None:
            context = getattr(self, "_active_turn_context", None)
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None or context is None:
            return True
        return orchestrator.accepts(context)

    def _complete_turn_context(self, context=None) -> None:
        if context is None:
            context = getattr(self, "_active_turn_context", None)
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is not None and context is not None:
            orchestrator.complete(context)
        if getattr(self, "_active_turn_context", None) is context:
            self._active_turn_context = None

    def _invalidate_active_conversation(self) -> None:
        """取消当前请求并使已排队的网络、TTS 和音频回调失效。"""
        context = getattr(self, "_active_turn_context", None)
        timeline = getattr(self, "_conversation_timeline", None)
        if context is not None and timeline is not None:
            timeline.cancel_turn(
                context.conversation_key,
                context.turn_id,
            )
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is not None:
            orchestrator.invalidate()
        self._active_turn_context = None
        self._active_agent_turn_id = ""
        self._active_timeline_turn_id = ""
        self._agent_tts_workers = {}
        self._pending_chat_reply = None
        self._pending_chat_context = None
        set_awaiting_reply_state(self, False)

    def _timeline_start_turn(
        self,
        turn_id: str,
        *,
        source: str,
        user_text: str = "",
        context=None,
    ) -> None:
        timeline = getattr(self, "_conversation_timeline", None)
        if timeline is None:
            return
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        if key is None:
            key = self._refresh_conversation_key()
        timeline.start_turn(
            key,
            turn_id,
            source=source,
            user_text=user_text,
        )

    def _record_agent_timeline_event(self, event: object, context=None) -> None:
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        if timeline is None or key is None or not turn_id:
            return
        if isinstance(event, SegmentStarted):
            texts = getattr(self, "_timeline_segment_texts", None)
            if texts is None:
                texts = {}
                self._timeline_segment_texts = texts
            texts.setdefault(event.index, "")
        elif isinstance(event, SegmentTextDelta):
            texts = getattr(self, "_timeline_segment_texts", None)
            if texts is None:
                texts = {}
                self._timeline_segment_texts = texts
            texts[event.index] = texts.get(event.index, "") + event.delta
            timeline.update_segment_text(key, turn_id, event.index, texts[event.index])
        elif isinstance(event, SegmentCompleted):
            timeline.complete_segment(key, turn_id, event.segment)
        elif isinstance(event, ToolStatus):
            safe_text = str(event.safe_text or "").strip() or {
                "started": "姝ｅ湪澶勭悊",
                "running": "浠嶅湪澶勭悊",
                "succeeded": "澶勭悊瀹屾垚",
                "failed": "澶勭悊澶辫触",
            }.get(str(event.state or "").lower(), "鐘舵€佸凡鏇存柊")
            timeline.add_status(
                key,
                turn_id,
                state=event.state,
                safe_text=safe_text,
            )
        elif isinstance(event, TurnFailed):
            timeline.fail_turn(key, turn_id, event.safe_message)
        elif isinstance(event, TurnCancelled):
            timeline.cancel_turn(key, turn_id)

    def _bind_bubble_to_timeline(self, bubble, turn_id: str) -> None:
        signal = getattr(bubble, "activated", None)
        opener = getattr(self, "_show_timeline_turn", None)
        if signal is None or not callable(opener) or not turn_id:
            return
        try:
            signal.connect(lambda current=turn_id: opener(current))
        except (AttributeError, RuntimeError, TypeError):
            pass

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
            log.warning("[chat] 瀵硅瘽琚嫆缁濓細姝ｅ湪绛夊緟鍥炲涓?)
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            return
        self._record_interaction()
        _log_private_text("[input] 鏀跺埌鐢ㄦ埛杈撳叆", text)
        log.info("[input] 鎻愪氦娑堟伅锛屽噯澶囧洖澶?)
        self._show_bubble("鈥︹€︼紵", 1500)
        self._position_bubble()
        QTimer.singleShot(1200, lambda: self._do_chat(text))

    def _is_agent_mode(self) -> bool:
        llm = (getattr(self, "config", {}) or {}).get("llm") or {}
        return str(llm.get("mode") or "direct").strip().lower() == "agent"

    def _build_agent_frontend_context(self) -> dict:
        """生成本轮只读前端能力与角色状态摘要。"""
        from meapet.desktop.renderer import MOOD_TO_EXPRESSION

        tts = getattr(self, "tts", None)
        tts_enabled = bool(tts is not None and getattr(tts, "enabled", False))
        configured_tts = (getattr(self, "config", {}) or {}).get("tts") or {}
        languages = ()
        if tts is not None and hasattr(tts, "supported_languages"):
            try:
                languages = tuple(tts.supported_languages())
            except Exception as exc:
                log.warning(
                    f"[agent] 读取 TTS 语言能力失败: {type(exc).__name__}"
                )
        if not languages:
            voice_language = normalize_voice_language(
                getattr(tts, "voice_lang", "")
                or configured_tts.get("voice_lang")
                or ""
            )
            languages = (voice_language,) if voice_language else ()

        affection_level = ""
        memory = getattr(self, "memory", None)
        if memory is not None and hasattr(memory, "get_affection_tier"):
            try:
                tier = memory.get_affection_tier()
                if isinstance(tier, (tuple, list)) and len(tier) > 1:
                    affection_level = str(tier[1] or "")
            except Exception as exc:
                log.warning(f"[agent] 璇诲彇濂芥劅搴︽憳瑕佸け璐? {type(exc).__name__}")

        renderer = getattr(self, "renderer", None)
        current_mood = getattr(renderer, "_current_mood", "neutral")
        capabilities = FrontendCapabilities(
            renderer="live2d" if getattr(self, "_use_live2d", False) else "png",
            supported_moods=tuple(MOOD_TO_EXPRESSION),
            supported_motions=(),
            tts_enabled=tts_enabled,
            tts_languages=languages,
            streaming_text=True,
            multi_segment=True,
        )
        state = CompanionState(
            affection_level=affection_level,
            character_state=(
                "standby" if getattr(self, "_standby", False) else "active"
            ),
            current_mood=current_mood,
            busy=bool(getattr(self, "_awaiting_reply", False)),
        )
        return build_agent_frontend_context(capabilities, state)

    def _make_chat_worker(self, message: str):
        """按显式模式选择直连模型或 Agent worker。"""
        if getattr(self, "_conversation_key", None) is None:
            self._refresh_conversation_key()
        agent_mode = self._is_agent_mode()
        if agent_mode:
            adapter = getattr(self, "agent_adapter", None)
            if adapter is None:
                raise RuntimeError("Agent 鍚庣灏氭湭鍒濆鍖?)
            history = tuple(getattr(self, "_agent_history", ()) or ())
        else:
            adapter = getattr(self, "chat_engine", None)
            if adapter is None or not callable(getattr(adapter, "stream_turn", None)):
                raise RuntimeError("鐩磋繛妯″瀷鍚庣灏氭湭鍒濆鍖?)
            history = ()

        turn_id = f"meapet-{uuid.uuid4().hex}"
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None:
            self._refresh_conversation_key()
            orchestrator = self._conversation_orchestrator
        turn_context = orchestrator.begin_turn(turn_id)
        self._active_turn_context = turn_context
        self._active_timeline_turn_id = turn_id
        tts = getattr(self, "tts", None)
        tts_enabled = bool(tts is not None and getattr(tts, "enabled", False))
        bubble_config = (getattr(self, "config", {}) or {}).get(
            "bubble_duration_ms"
        ) or {}
        self._active_agent_turn_id = turn_id
        self._agent_turn_result = None
        self._agent_bubbles = {}
        self._agent_tts_workers = {}
        self._timeline_segment_texts = {}
        from meapet.desktop.renderer import MOOD_TO_EXPRESSION

        self._agent_presentation = AgentTurnPresentation(
            tts_enabled=tts_enabled,
            reply_min_duration_ms=int(bubble_config.get("reply", 3000)),
            supported_moods=tuple(MOOD_TO_EXPRESSION),
        )
        request = AgentTurnRequest(
            turn_id=turn_id,
            user_text=message,
            history=history,
            frontend_context=self._build_agent_frontend_context(),
            tts_enabled=tts_enabled,
            conversation_key=turn_context.conversation_key,
            generation_id=turn_context.generation_id,
        )
        self._timeline_start_turn(
            turn_id,
            source="user_reply",
            user_text=message,
            context=turn_context,
        )
        worker = AgentChatWorker(adapter, request)
        worker.turn_context = turn_context
        return worker

    def _do_chat(self, message: str):
        """执行 LLM 对话（后台线程）"""
        if self._awaiting_reply:
            log.warning("[chat] 瀵硅瘽琚嫆缁濓細姝ｅ湪绛夊緟鍥炲涓?)
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            return
        interrupt_control = getattr(self, "_interrupt_control_say", None)
        if callable(interrupt_control):
            interrupt_control()
        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        self._safe_set_mood("talking")
        self._last_user_msg = message
        _log_private_text("[chat] 鍙戦€佺粰 LLM", message)

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

        try:
            self._chat_worker = self._make_chat_worker(message)
            self._chat_worker.start()
        except Exception as exc:
            log.error(f"[chat] worker 启动失败: {type(exc).__name__}: {exc}")
            self._chat_worker = None
            if self._is_agent_mode():
                self._fail_agent_turn("Agent 鍚姩澶辫触锛岃妫€鏌ラ厤缃€?)
            else:
                self._on_chat_error(f"{type(exc).__name__}: {exc}")
            return
        # 轮询 timer：每 100ms 检查 worker 是否完成
        self._chat_poll = QTimer(self)
        self._chat_poll.timeout.connect(self._poll_chat)
        self._chat_poll.start(100)
        worker_name = type(self._chat_worker).__name__
        log.info(f"[chat] {worker_name} 已启动")

    def _poll_chat(self):
        """主线程轮询直连结果，或增量消费 Agent 事件。"""
        if not hasattr(self, '_chat_worker') or self._chat_worker is None:
            if hasattr(self, '_chat_poll') and self._chat_poll:
                self._chat_poll.stop()
            return
        worker = self._chat_worker
        context = getattr(worker, "turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            take_events = getattr(worker, "take_events", None)
            if callable(take_events):
                take_events()
            if getattr(worker, "done", False):
                worker.deleteLater()
                if getattr(self, "_chat_worker", None) is worker:
                    self._chat_worker = None
            log.info("[chat] 已丢弃非活动会话的迟到事件")
            return
        if callable(getattr(worker, "take_events", None)):
            self._poll_agent_chat(worker)
            return
        if not worker.done:
            return
        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
        result, error = worker.get_result()
        worker.deleteLater()
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

    def _poll_agent_chat(self, worker) -> None:
        """把后台 Agent 事件转成交给 Qt 主线程执行的呈现动作。"""
        context = getattr(worker, "turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            worker.take_events()
            if worker.done:
                worker.deleteLater()
                if getattr(self, "_chat_worker", None) is worker:
                    self._chat_worker = None
            return
        events = worker.take_events()
        presentation = getattr(self, "_agent_presentation", None)
        for event in events:
            self._record_agent_timeline_event(event, context)
            if isinstance(event, TurnCompleted):
                self._agent_turn_result = event.result
            if presentation is None:
                continue
            for action in presentation.consume(event):
                self._apply_agent_action(action, context=context)

        if not worker.done:
            return

        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()

        error = getattr(worker, "error", None)
        worker.deleteLater()
        if getattr(self, "_chat_worker", None) is worker:
            self._chat_worker = None

        if error and getattr(self, "_awaiting_reply", False):
            log.error("[chat] 事件流异常，已转为安全系统错误")
            backend_name = "Agent" if self._is_agent_mode() else "模型服务"
            self._fail_agent_turn(
                f"{backend_name}杩炴帴鎰忓涓柇锛岃绋嶅悗鍐嶈瘯銆?,
                context=context,
            )
            return

        # 姝ｅ父閫傞厤鍣ㄦ€讳細鍙戝嚭瀹屾垚銆佸け璐ユ垨鍙栨秷浜嬩欢銆傝嫢娴侀潤榛樼粨鏉燂紝涓嶈兘姘镐箙閿佷綇杈撳叆銆?
        if (
            getattr(self, "_awaiting_reply", False)
            and getattr(self, "_agent_turn_result", None) is None
            and not (getattr(self, "_agent_tts_workers", {}) or {})
        ):
            # --- Ollama 后端：跳过异常检测，视为正常完成 ---
            llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
            backend = str(llm_cfg.get("backend") or "").strip().lower()
            if backend == "ollama":
                log.info("[agent] Ollama 后端：事件流未产生 TurnCompleted，视为正常完成")
                # 构造一个空的 ParseResult 以避免 _finish_agent_turn 出错
                from meapet.conversation.output_protocol import ParseResult
                self._agent_turn_result = ParseResult((), (), True, "ollama")
                self._finish_agent_turn(
                    str(getattr(self, "_active_agent_turn_id", "") or ""),
                    context=context,
                )
                return
            backend_name = "Agent" if self._is_agent_mode() else "妯″瀷鏈嶅姟"
            self._fail_agent_turn(
                f"{backend_name}鏈繑鍥炲彲鐢ㄥ洖澶嶃€?,
                context=context,
            )

    def _agent_bubble(
        self,
        index: int,
        *,
        text: str = "",
        mood=None,
        context=None,
    ):
        bubbles = getattr(self, "_agent_bubbles", None)
        if bubbles is None:
            bubbles = {}
            self._agent_bubbles = bubbles
        bubble = bubbles.get(index)
        if bubble is not None:
            return bubble
        stack = getattr(self, "_bubble_stack", None)
        if stack is None:
            return None
        bubble = stack.begin_message(text, mood=mood)
        bubbles[index] = bubble
        self._bind_bubble_to_timeline(
            bubble,
            str(
                getattr(context, "turn_id", "")
                or getattr(self, "_active_agent_turn_id", "")
                or ""
            ),
        )
        self._position_bubble()
        return bubble

    def _apply_agent_actions(self, actions, *, context=None) -> None:
        for action in actions:
            self._apply_agent_action(action, context=context)

    def _apply_agent_action(self, action: object, *, context=None) -> None:
        """执行纯状态机动作；系统状态不进入角色历史、TTS 或情绪。"""
        if context is not None and not self._turn_context_is_current(context):
            return
        stack = getattr(self, "_bubble_stack", None)
        if isinstance(action, BeginBubble):
            self._agent_bubble(action.index, context=context)
            return
        if isinstance(action, UpdateBubble):
            bubble = self._agent_bubble(action.index, context=context)
            if bubble is not None and stack is not None:
                stack.update_message(bubble, action.text, mood=None)
                self._position_bubble()
            return
        if isinstance(action, FinalizeBubble):
            segment = action.segment
            bubble = self._agent_bubble(
                segment.index,
                text=segment.display_text,
                mood=segment.mood,
                context=context,
            )
            if bubble is not None and stack is not None:
                stack.finalize_message(
                    bubble,
                    segment.display_text,
                    duration_ms=action.duration_ms,
                    mood=segment.mood,
                )
                self._safe_set_mood(segment.mood)
                self._position_bubble()
            return
        if isinstance(action, SubmitTTS):
            self._submit_agent_tts(action.segment, context=context)
            return
        if isinstance(action, PlayAudio):
            self._play_audio(action.wav_path)
            QTimer.singleShot(
                max(0, int(action.duration_ms)),
                lambda index=action.index, current=context: (
                    self._on_agent_audio_finished(index, context=current)
                ),
            )
            return
        if isinstance(action, ShowStatus):
            safe_text = str(action.safe_text or "").strip() or {
                "started": "正在处理",
                "running": "仍在处理",
                "succeeded": "处理完成",
                "failed": "处理失败",
            }.get(str(action.state or "").lower(), "状态已更新")
            self._show_bubble(safe_text, 4500, mood=None)
            self._position_bubble()
            return
        if isinstance(action, RequestFormatRepair):
            # 适配器层随后负责一次格式修复；这里仅记录，不把协议细节暴露给用户。
            self._agent_format_repair_pending = True
            log.warning("[agent] 回复协议字段不完整，等待格式修复")
            return
        if isinstance(action, FinishTurn):
            self._finish_agent_turn(action.turn_id, context=context)
            return
        if isinstance(action, FailTurn):
            self._fail_agent_turn(action.safe_message, context=context)
            return
        if isinstance(action, CancelTurn):
            self._cancel_agent_turn(context=context)

    def _submit_agent_tts(self, segment, *, context=None) -> None:
        workers = getattr(self, "_agent_tts_workers", None)
        if workers is None:
            workers = {}
            self._agent_tts_workers = workers
        try:
            worker = TTSWorker(
                self.tts,
                segment.voice_text,
                mood=segment.mood,
                style=segment.tts_style,
                language=segment.voice_language,
            )
            worker.turn_context = context
            workers[segment.index] = worker
            worker.start()
            try:
                self._ensure_tts_poll()
            except (RuntimeError, TypeError) as exc:
                # 非 QWidget 的测试宿主可手动轮询；真实桌面对象不会走到这里。
                log.debug(f"[agent] TTS timer 暂未创建: {type(exc).__name__}")
        except Exception as exc:
            workers.pop(segment.index, None)
            log.error(
                f"[agent] 绗?{segment.index + 1} 娈?TTS 鍚姩澶辫触锛屽洖閫€鏂囧瓧: "
                f"{type(exc).__name__}"
            )
            presentation = getattr(self, "_agent_presentation", None)
            if presentation is not None:
                self._apply_agent_actions(
                    presentation.tts_ready(
                        segment.index,
                        "",
                        audio_duration_ms=0,
                    ),
                    context=context,
                )
    def _cleanup_after_turn(self, turn_id: str, context=None) -> None:
        """清理本轮对话状态，不执行记忆操作。"""
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        if timeline is not None and key is not None:
            timeline.finish_turn(key, turn_id)
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)
        log.info(f"[chat] 本轮呈现完成 (Ollama 空回复): turn={turn_id[:24]}")


    def _finish_agent_turn(self, turn_id: str, *, context=None) -> None:
        if context is not None and not self._turn_context_is_current(context):
            return
        result = getattr(self, "_agent_turn_result", None)
        segments = tuple(getattr(result, "segments", ()) or ())
        reply = "\n\n".join(
            segment.display_text
            for segment in sorted(segments, key=lambda item: item.index)
            if segment.display_text
        ).strip()
        # --- Ollama 后端：如果 result 为空或 segments 为空，跳过记忆操作 ---
        llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
        backend = str(llm_cfg.get("backend") or "").strip().lower()
        if backend == "ollama" and not reply:
            # 直接清理状态，不执行记忆操作
            self._cleanup_after_turn(turn_id, context)
            return
        user_text = str(getattr(self, "_last_user_msg", "") or "").strip()
        if self._is_agent_mode() and user_text and reply:
            history = list(getattr(self, "_agent_history", ()) or ())
            history.extend(
                (
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                )
            )
            agent_config = (
                ((getattr(self, "config", {}) or {}).get("llm") or {}).get("agent")
                or {}
            )
            try:
                history_turns = max(0, min(int(agent_config.get("history_turns", 5)), 50))
            except (TypeError, ValueError):
                history_turns = 5
            self._agent_history = history[-history_turns * 2:] if history_turns else []
        elif user_text and reply:
            mood = segments[0].mood if segments else "neutral"
            QTimer.singleShot(
                0,
                lambda current_reply=reply, current_mood=mood, user=user_text: (
                    self._do_memory_ops(current_reply, current_mood, user)
                ),
            )

        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        if timeline is not None and key is not None:
            timeline.finish_turn(key, turn_id)
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)
        log.info(f"[chat] 本轮呈现完成: turn={turn_id[:24]}")

    def _fail_agent_turn(self, safe_message: str, *, context=None) -> None:
        if context is not None and not self._turn_context_is_current(context):
            return
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, safe_message)
        self._agent_tts_workers = {}
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
        self._show_bubble(str(safe_message or "回复请求失败。"), 10000, mood=None)
        self._position_bubble()
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)

    def _cancel_agent_turn(self, *, context=None) -> None:
        if context is not None and not self._turn_context_is_current(context):
            return
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            timeline.cancel_turn(key, turn_id)
        self._agent_tts_workers = {}
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)

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
                # 闈?Ollama 鍚庣鎵嶉噸缃?system prompt
                llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
                backend = str(llm_cfg.get("backend") or "").strip().lower()
                if backend != "ollama":
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
        context = getattr(self, "_active_turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            return
        _log_private_text("[reply] LLM 鍥炲", reply, suffix=f"mood={mood}")
        log.info(f"[reply] 鏀跺埌鍥炲锛宮ood={mood}")
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
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            tts = getattr(self, "tts", None)
            voice_language = normalize_voice_language(
                getattr(tts, "voice_lang", "")
                or ((getattr(self, "config", {}) or {}).get("tts") or {}).get(
                    "voice_lang",
                    "",
                )
            ) or "zh"
            timeline.complete_segment(
                key,
                turn_id,
                ReplySegment(
                    index=0,
                    display_text=reply,
                    voice_text=voice_text,
                    voice_language=voice_language,
                    mood=detected,
                    tts_style=tts_style,
                ),
            )
        # 捕获本轮用户消息，记忆操作与 TTS 并行且由上游串行锁保护。
        user_msg = getattr(self, '_last_user_msg', '') or ''
        QTimer.singleShot(
            0,
            lambda: self._do_memory_ops(reply, detected, user_msg),
        )

        # 最终回复必须等音频文件真正生成后再显示；否则文字和声音会明显错位。
        # TTS 关闭或启动失败时仍立即显示文字，不能让回复永久卡住。
        self._pending_chat_reply = (reply, detected)
        self._pending_chat_context = context
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
            self._tts_worker.turn_context = context
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
            context = getattr(self._tts_worker, "turn_context", None)
            if context is not None and not self._turn_context_is_current(context):
                self._tts_worker = None
                self._pending_chat_reply = None
                self._pending_chat_context = None
                context = None
                result = None
            else:
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

        agent_workers = getattr(self, "_agent_tts_workers", None)
        if agent_workers:
            for index, worker in tuple(agent_workers.items()):
                if not worker.done:
                    continue
                context = getattr(worker, "turn_context", None)
                if context is not None and not self._turn_context_is_current(context):
                    agent_workers.pop(index, None)
                    continue
                try:
                    raw = worker.get_result()
                except Exception as exc:
                    log.error(
                        f"[agent] 绗?{index + 1} 娈?TTS 缁撴灉璇诲彇澶辫触: "
                        f"{type(exc).__name__}"
                    )
                    raw = None
                agent_workers.pop(index, None)
                value = str(raw or "")
                wav_path = value.rsplit("|", 1)[0] if "|" in value else value
                if not wav_path or not os.path.exists(wav_path):
                    wav_path = ""
                duration_ms = (
                    self._get_wav_duration_ms(wav_path) if wav_path else 0
                )
                presentation = getattr(self, "_agent_presentation", None)
                if presentation is not None:
                    self._apply_agent_actions(
                        presentation.tts_ready(
                            index,
                            wav_path,
                            audio_duration_ms=duration_ms,
                        ),
                        context=context,
                    )

        # 没有待处理的 worker 就停止
        if not any([
            getattr(self, '_tts_worker', None),
            getattr(self, '_speak_worker', None),
            getattr(self, '_watch_tts_worker', None),
            getattr(self, '_agent_tts_workers', None),
        ]):
            if hasattr(self, '_tts_poll') and self._tts_poll:
                self._tts_poll.stop()
                self._tts_poll.deleteLater()
                self._tts_poll = None

    def _on_agent_audio_finished(self, index: int, *, context=None) -> None:
        if context is not None and not self._turn_context_is_current(context):
            return
        presentation = getattr(self, "_agent_presentation", None)
        if presentation is None:
            return
        self._apply_agent_actions(
            presentation.audio_finished(index),
            context=context,
        )

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
        context = getattr(self, "_pending_chat_context", None)
        if context is not None and not self._turn_context_is_current(context):
            self._pending_chat_reply = None
            self._pending_chat_context = None
            return
        if pending is None:
            # 鍏煎鏃ц皟鐢細娌℃湁绛夊緟鏂囧瓧鏃讹紝浠嶅厑璁稿崟鐙挱鏀炬湁鏁堥煶棰戙€?
            if wav_path:
                self._play_audio(wav_path)
            return

        try:
            del self._pending_chat_reply
        except AttributeError:
            pass
        self._pending_chat_context = None

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

        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            timeline.finish_turn(key, turn_id)
        self._active_timeline_turn_id = ""
        self._complete_turn_context(context)

        if wav_path:
            self._play_audio(wav_path)

    def _on_tts_audio(self, raw: str | None):
        """TTS 完成后再显示最终气泡；失败时显示无声文字兜底。"""
        value = str(raw or "")
        wav_path = value.rsplit("|", 1)[0] if "|" in value else value
        if not wav_path or not os.path.exists(wav_path):
            log.warning(f"[audio] TTS 鏈敓鎴愭湁鏁堟枃浠讹紝鍥為€€鏂囧瓧: chars={len(value)}")
            if debug_enabled():
                log.debug(f"[audio] 无效 TTS 返回: {raw!r}")
            self._complete_pending_chat_reply()
            return
        self._complete_pending_chat_reply(wav_path)

    def _on_chat_error(self, err: str):
        context = getattr(self, "_active_turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            return
        _log_private_text("[chat] 閿欒", err)
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
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, "对话请求失败")
        self._active_timeline_turn_id = ""
        self._show_bubble(
            status_language.model_service_error(),
            10000,
            mood=None,
        )
        self._position_bubble()
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)

    def _on_chat_timeout(self):
        """ChatWorker 超时 — 强制终止线程并释放锁"""
        context = getattr(self, "_active_turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            return
        log.warning("[chat] ChatWorker 超时，释放锁")
        set_awaiting_reply_state(self, False)
        self._show_bubble(status_language.chat_timeout(), 3000)
        self._position_bubble()
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, status_language.chat_timeout())
        self._active_timeline_turn_id = ""
        if hasattr(self, '_chat_worker') and self._chat_worker:
            if self._chat_worker.isRunning():
                # quit() 协作取消 async 任务 / future
                # 用 terminate() 强制终止
                self._chat_worker.terminate()
                if not self._chat_worker.wait(2000):
                    log.warning("[chat] ChatWorker 无法终止")
            self._chat_worker.deleteLater()
            self._chat_worker = None
        self._complete_turn_context(context)

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
            # 缂撳瓨锛氱敤璇█鍓嶇紑缁熶竴鍛藉悕
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
        self._bind_bubble_to_timeline(
            getattr(self, "bubble", None),
            str(getattr(self, "_active_timeline_turn_id", "") or ""),
        )
        self._position_bubble()
