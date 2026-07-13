"""Companion MCP 与 Qt 桌面主线程之间的呈现和隐私桥。"""

from __future__ import annotations

import base64
import io
import os
import threading

from PyQt5.QtCore import QTimer

from meapet.agent.base import TurnCompleted
from meapet.agent.presentation import (
    AgentTurnPresentation,
    BeginBubble,
    CancelTurn,
    FailTurn,
    FinalizeBubble,
    FinishTurn,
    PlayAudio,
    SubmitTTS,
    UpdateBubble,
)
from meapet.config.store import resolve_resource_path, resolve_secret
from meapet.control.broker import CompanionControlBroker
from meapet.control.transport import (
    CompanionMcpRuntime,
    ControlServerConfig,
    ensure_control_token,
)
from meapet.conversation.output_protocol import ParseResult, SegmentCompleted
from meapet.desktop.dialogs import confirm_cloud_vision
from meapet.desktop.workers import TTSWorker
from meapet.log import get_color_logger
from meapet.watcher.capture import CaptureError, CapturedImage, capture_screen_image


log = get_color_logger("control_bridge")


def encode_control_capture(captured: CapturedImage) -> dict:
    """把已批准的截图编码到内存；返回值不包含本地路径。"""
    buffer = io.BytesIO()
    captured.image.save(buffer, format="PNG")
    return {
        "status": "approved",
        "image": {
            "mime_type": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
        "metadata": dict(captured.metadata),
    }


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(result, maximum))


class PetControlBridgeMixin:
    """仅在活动 Agent 模式中启动 MCP，并把命令投递到 Qt 主线程。"""

    def _init_control(self) -> None:
        self._control_broker = None
        self._control_runtime = None
        self._control_poll_timer = None
        self._control_say_active = False
        self._control_presentation = None
        self._control_tts_workers = {}
        self._control_bubbles = {}

        config = getattr(self, "config", {}) or {}
        llm = config.get("llm") or {}
        control = config.get("agent_control") or {}
        if (
            str(llm.get("mode") or "direct").strip().lower() != "agent"
            or not bool(control.get("enabled", False))
        ):
            return

        raw_token = str(control.get("auth_token") or "").strip()
        token = resolve_secret(raw_token, ("MEAPET_CONTROL_TOKEN",))
        if not token and not raw_token:
            token = ensure_control_token(control)
            save = getattr(self, "_save_config", None)
            if callable(save):
                save()
        if not token:
            raise ValueError("Agent 主动控制 token 未配置或环境变量不可用")

        state_builder = getattr(self, "_build_agent_frontend_context", None)
        state = state_builder() if callable(state_builder) else {}
        self._control_broker = CompanionControlBroker(
            state=state,
            max_say_queue=_bounded_int(
                control.get("max_say_queue"), 8, 1, 100
            ),
            say_ttl_seconds=_bounded_int(
                control.get("say_ttl_seconds"), 120, 1, 3600
            ),
            capture_timeout_seconds=_bounded_int(
                control.get("capture_timeout_seconds"), 60, 5, 300
            ),
        )
        server_config = ControlServerConfig(
            listen_host=control.get("listen_host", "127.0.0.1"),
            allowed_agent_ip=control.get("allowed_agent_ip", "127.0.0.1"),
            port=control.get("port", 8765),
            auth_token=token,
            allow_insecure_http=control.get("allow_insecure_http", False),
            cert_file=resolve_resource_path(control.get("cert_file", "")),
            key_file=resolve_resource_path(control.get("key_file", "")),
            ca_file=resolve_resource_path(control.get("ca_file", "")),
            max_request_bytes=_bounded_int(
                control.get("max_request_bytes"), 1_048_576, 1024, 16 * 1024 * 1024
            ),
            rate_limit_per_minute=_bounded_int(
                control.get("rate_limit_per_minute"), 60, 1, 10_000
            ),
        )
        self._control_runtime = CompanionMcpRuntime(
            self._control_broker,
            server_config,
        )
        self._control_runtime.start()

        self._control_poll_timer = QTimer(self)
        self._control_poll_timer.timeout.connect(self._poll_control)
        self._control_poll_timer.start(100)
        log.info(
            "[control] Companion MCP 已启动: "
            f"endpoint={server_config.endpoint} agent_ip={server_config.allowed_agent_ip}"
        )

    def _poll_control(self) -> None:
        """在 Qt 主线程消费远端命令；网络线程从不直接触碰窗口。"""
        broker = getattr(self, "_control_broker", None)
        if broker is None:
            return

        state_builder = getattr(self, "_build_agent_frontend_context", None)
        if callable(state_builder):
            try:
                broker.update_state(state_builder())
            except Exception as exc:
                log.warning(f"[control] 状态摘要更新失败: {type(exc).__name__}")

        user_busy = bool(getattr(self, "_awaiting_reply", False))
        broker.set_user_busy(user_busy)

        for command in broker.take_expressions():
            if command.mood:
                setter = getattr(self, "_safe_set_mood", None)
                if callable(setter):
                    setter(command.mood)
            if command.motion:
                play_motion = getattr(self, "_play_motion", None)
                if callable(play_motion):
                    play_motion(command.motion)

        for request in broker.take_capture_requests():
            if not self._confirm_control_capture(request):
                broker.resolve_capture(
                    request.capture_id,
                    {"status": "rejected", "code": "user_denied"},
                )
                continue
            self._capture_for_control(request)

        self._poll_control_tts()

        if user_busy or bool(getattr(self, "_control_say_active", False)):
            return
        command = broker.take_ready_say()
        if command is not None:
            self._start_control_say(command)

    def _confirm_control_capture(self, request) -> bool:
        scope_names = {
            "full_screen": "全部屏幕",
            "region": "指定区域",
            "application": "指定应用窗口",
        }
        detail = scope_names.get(request.scope, request.scope)
        if request.scope == "region" and request.region:
            detail += (
                f"（{request.region['width']}×{request.region['height']}，"
                f"位置 {request.region['x']},{request.region['y']}）"
            )
        elif request.scope == "application" and request.application:
            detail += f"（{request.application[:80]}）"
        message = (
            "已配置的 Agent 请求读取一次桌面截图。\n\n"
            f"本次范围：{detail}\n"
            "批准后才会采集，并直接在内存中发送给 Agent；MeaPet 不会为此落盘。\n"
            "授权仅本次有效，超时将自动拒绝。"
        )
        return confirm_cloud_vision(
            self,
            title="允许 Agent 本次截图？",
            message=message,
            timeout_seconds=15,
            accept_text="允许本次截图",
        )

    def _capture_for_control(self, request) -> None:
        broker = getattr(self, "_control_broker", None)
        if broker is None:
            return

        def capture() -> None:
            try:
                captured = capture_screen_image(
                    scope=request.scope,
                    region=request.region,
                    application=request.application,
                )
                result = encode_control_capture(captured)
            except CaptureError as exc:
                result = {"status": "error", "code": exc.code}
            except Exception as exc:
                log.error(f"[control] 截图失败: {type(exc).__name__}")
                result = {"status": "error", "code": "capture_failed"}
            broker.resolve_capture(request.capture_id, result)

        threading.Thread(
            target=capture,
            name="MeaPetControlCapture",
            daemon=True,
        ).start()

    def _control_bubble(self, index: int, *, text: str = "", mood=None):
        bubbles = getattr(self, "_control_bubbles", None)
        if bubbles is None:
            bubbles = {}
            self._control_bubbles = bubbles
        bubble = bubbles.get(index)
        if bubble is not None:
            return bubble
        stack = getattr(self, "_bubble_stack", None)
        if stack is None:
            return None
        bubble = stack.begin_message(text, mood=mood)
        bubbles[index] = bubble
        position = getattr(self, "_position_bubble", None)
        if callable(position):
            position()
        return bubble

    def _start_control_say(self, command) -> None:
        tts = getattr(self, "tts", None)
        tts_enabled = bool(tts is not None and getattr(tts, "enabled", False))
        bubble_config = (getattr(self, "config", {}) or {}).get(
            "bubble_duration_ms"
        ) or {}
        self._control_say_active = True
        self._control_bubbles = {}
        self._control_tts_workers = {}
        self._control_presentation = AgentTurnPresentation(
            tts_enabled=tts_enabled,
            reply_min_duration_ms=_bounded_int(
                bubble_config.get("reply"), 3000, 0, 300_000
            ),
        )
        result = ParseResult(
            segments=tuple(command.segments),
            issues=(),
            done=True,
            source_format="meapet",
        )
        for segment in result.segments:
            self._apply_control_actions(
                self._control_presentation.consume(SegmentCompleted(segment))
            )
        self._apply_control_actions(
            self._control_presentation.consume(
                TurnCompleted(command.queue_id, result)
            )
        )

    def _apply_control_actions(self, actions) -> None:
        for action in actions:
            self._apply_control_action(action)

    def _apply_control_action(self, action: object) -> None:
        stack = getattr(self, "_bubble_stack", None)
        if isinstance(action, BeginBubble):
            self._control_bubble(action.index)
            return
        if isinstance(action, UpdateBubble):
            bubble = self._control_bubble(action.index)
            if bubble is not None and stack is not None:
                stack.update_message(bubble, action.text, mood=None)
            return
        if isinstance(action, FinalizeBubble):
            segment = action.segment
            bubble = self._control_bubble(
                segment.index,
                text=segment.display_text,
                mood=segment.mood,
            )
            if bubble is not None and stack is not None:
                stack.finalize_message(
                    bubble,
                    segment.display_text,
                    duration_ms=action.duration_ms,
                    mood=segment.mood,
                )
                setter = getattr(self, "_safe_set_mood", None)
                if callable(setter):
                    setter(segment.mood)
                position = getattr(self, "_position_bubble", None)
                if callable(position):
                    position()
            return
        if isinstance(action, SubmitTTS):
            self._submit_control_tts(action.segment)
            return
        if isinstance(action, PlayAudio):
            play = getattr(self, "_play_audio", None)
            if callable(play):
                play(action.wav_path)
            QTimer.singleShot(
                max(0, int(action.duration_ms)),
                lambda index=action.index: self._on_control_audio_finished(index),
            )
            return
        if isinstance(action, FinishTurn):
            self._control_say_active = False
            self._control_presentation = None
            self._control_tts_workers = {}
            return
        if isinstance(action, (FailTurn, CancelTurn)):
            self._interrupt_control_say()

    def _submit_control_tts(self, segment) -> None:
        workers = getattr(self, "_control_tts_workers", None)
        if workers is None:
            workers = {}
            self._control_tts_workers = workers
        try:
            worker = TTSWorker(
                self.tts,
                segment.voice_text,
                mood=segment.mood,
                style=segment.tts_style,
                language=segment.voice_language,
            )
            workers[segment.index] = worker
            worker.start()
        except Exception as exc:
            workers.pop(segment.index, None)
            log.error(
                f"[control] 第 {segment.index + 1} 段 TTS 启动失败，回退文字: "
                f"{type(exc).__name__}"
            )
            presentation = getattr(self, "_control_presentation", None)
            if presentation is not None:
                self._apply_control_actions(
                    presentation.tts_ready(
                        segment.index,
                        "",
                        audio_duration_ms=0,
                    )
                )

    def _poll_control_tts(self) -> None:
        workers = getattr(self, "_control_tts_workers", None)
        if not workers:
            return
        for index, worker in tuple(workers.items()):
            if not worker.done:
                continue
            try:
                raw = worker.get_result()
            except Exception as exc:
                log.error(
                    f"[control] 第 {index + 1} 段 TTS 结果读取失败: "
                    f"{type(exc).__name__}"
                )
                raw = None
            workers.pop(index, None)
            value = str(raw or "")
            wav_path = value.rsplit("|", 1)[0] if "|" in value else value
            if not wav_path or not os.path.exists(wav_path):
                wav_path = ""
            duration = getattr(self, "_get_wav_duration_ms", None)
            duration_ms = duration(wav_path) if wav_path and callable(duration) else 0
            presentation = getattr(self, "_control_presentation", None)
            if presentation is not None:
                self._apply_control_actions(
                    presentation.tts_ready(
                        index,
                        wav_path,
                        audio_duration_ms=duration_ms,
                    )
                )

    def _on_control_audio_finished(self, index: int) -> None:
        presentation = getattr(self, "_control_presentation", None)
        if presentation is not None:
            self._apply_control_actions(presentation.audio_finished(index))

    def _interrupt_control_say(self) -> None:
        """用户对话优先；忽略仍在后台完成的主动 TTS。"""
        self._control_say_active = False
        self._control_presentation = None
        self._control_tts_workers = {}

    def _stop_control(self) -> None:
        timer = getattr(self, "_control_poll_timer", None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._control_poll_timer = None
        self._interrupt_control_say()

        broker = getattr(self, "_control_broker", None)
        if broker is not None:
            for request in broker.take_capture_requests():
                broker.resolve_capture(
                    request.capture_id,
                    {"status": "cancelled", "code": "server_stopped"},
                )

        runtime = getattr(self, "_control_runtime", None)
        if runtime is not None:
            try:
                runtime.stop()
            except Exception as exc:
                log.warning(f"[control] 服务停止失败: {type(exc).__name__}")
        self._control_runtime = None
        self._control_broker = None
