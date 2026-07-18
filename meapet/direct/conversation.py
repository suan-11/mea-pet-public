"""把直连模型的 Canonical 流转换为 MeaPet 统一分段事件。"""

from __future__ import annotations

import time
from typing import AsyncIterator

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.prompts import (
    MAX_REPAIR_INPUT_CHARS,
    build_repair_instruction,
    frontend_context_json,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
)

# 用于从模型输出中提取最后一个 <MEAPET_SEGMENT> 块的局部正则
import re as _re
_SEGMENT_BLOCK_LOCAL_RE = _re.compile(
    r"<MEAPET_SEGMENT\s*>(.*?)</MEAPET_SEGMENT\s*>",
    _re.IGNORECASE | _re.DOTALL,
)
from meapet.direct.client import DirectProtocolError
from meapet.log import get_color_logger
from meapet.direct.types import (
    CanonicalChatRequest,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)


log = get_color_logger("direct_conversation")

# Direct 模式的紧凑输出格式说明（同 <MEAPET_SEGMENT> 协议，去掉了 Agent 特有内容）
_DIRECT_OUTPUT_INSTRUCTION = (
    "不要进行推理，直接给出回复。\n"
    "不要输出推理过程。回复必须使用以下格式：\n"
    "<MEAPET_SEGMENT>\n"
    "<DISPLAY>显示文本</DISPLAY>\n"
    "<META>{"
    '"voice_text":"朗读文本","voice_language":"BCP-47","mood":"情绪","tts_style":"表演方式"'
    "}</META>\n"
    "</MEAPET_SEGMENT>\n"
    "最后输出<MEAPET_DONE />。\n"
    "五个字段缺一不可。voice_language 必须与实际文本语言一致。"
)


class DirectConversationAdapter:
    """协调 MeaPet 本地角色/历史与供应商无关的流式协议客户端。"""

    def __init__(self, engine, protocol_client) -> None:
        self.engine = engine
        self.protocol_client = protocol_client
        self._cancelled_turns: set[str] = set()

    def _canonical_request(
        self,
        messages,
        *,
        max_tokens: int | None = None,
    ) -> CanonicalChatRequest:
        return CanonicalChatRequest(
            model=self.engine.model,
            messages=tuple(messages),
            temperature=self.engine.temperature,
            max_tokens=max_tokens or self.engine.max_tokens,
            stream=True,
        )

    async def cancel(self, turn_id: str) -> None:
        self._cancelled_turns.add(str(turn_id or "").strip())

    async def close(self) -> None:
        await self.protocol_client.close()

    async def _repair_result(
        self,
        *,
        request: AgentTurnRequest,
        malformed_output: str,
    ):
        repair_request = self._canonical_request(
            (
                {"role": "system", "content": build_repair_instruction(request)},
                {
                    "role": "user",
                    "content": malformed_output[:MAX_REPAIR_INPUT_CHARS],
                },
            )
        )
        parser = MeaPetOutputStreamParser()
        try:
            async for event in self.protocol_client.stream(repair_request):
                if request.turn_id in self._cancelled_turns:
                    return None
                if isinstance(event, TextDelta):
                    parser.feed(event.delta)
                elif isinstance(event, StreamDone):
                    break
        except DirectProtocolError:
            return None
        result = parser.close(tts_enabled=request.tts_enabled)
        if result.requires_repair(tts_enabled=request.tts_enabled):
            return None
        return result

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
        turn = str(request.turn_id or "")[:24]
        is_vision = bool(request.attachments)
        log.info(
            f"[direct] 回合开始 turn={turn} "
            f"user_chars={len(request.user_text or '')} "
            f"attachments={len(request.attachments or ())} "
            f"tts={bool(request.tts_enabled)} "
            f"vision={is_vision}"
        )
        if request.turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(request.turn_id)
            log.info(f"[direct] 回合已取消(开始前) turn={turn}")
            yield TurnCancelled(request.turn_id)
            return
        if not self.engine.available:
            log.error(f"[direct] 后端未就绪 turn={turn}")
            yield TurnFailed(
                request.turn_id,
                "backend_unavailable",
                "模型服务尚未就绪，请检查配置和运行状态。",
                True,
            )
            return

        prepared = False
        started = time.perf_counter()
        try:
            if not is_vision:
                messages = self.engine._prepare_direct_turn(request.user_text)
                prepared = True
            else:
                # 识图请求：不写入持久历史，从只读快照构建。
                with self.engine._history_lock:
                    messages = [dict(item) for item in self.engine.history]
                system = self.engine._build_vision_system_prompt(
                    request.user_text
                )
                messages[0] = {"role": "system", "content": system}
                messages.append(
                    {"role": "user", "content": request.user_text}
                )
                prepared = True

            log.info(
                f"[direct] 上下文已准备 turn={turn} messages={len(messages)} "
                f"history_tail_role={messages[-1].get('role') if messages else '-'}"
            )
            # 清理 assistant 消息中的旧格式残留（[mood] 行首或 <TTS> 标签），
            # 避免 "请用 <MEAPET_SEGMENT>" 指令与旧版 3 行历史打架。
            for i, msg in enumerate(messages):
                if msg.get("role") == "assistant":
                    raw = str(msg.get("content") or "")
                    if raw.startswith("[") or "<TTS>" in raw.upper():
                        from meapet.chat.engine import ChatEngine
                        display, _, _, _ = ChatEngine._parse_reply_payload(raw)
                        if display:
                            messages[i] = {"role": "assistant", "content": display}
                        else:
                            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                            messages[i] = {"role": "assistant", "content": lines[0] if lines else raw}
            if request.attachments:
                messages[-1] = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.user_text},
                        *(
                            attachment.canonical_part()
                            for attachment in request.attachments
                        ),
                    ],
                }
            system = str(messages[0].get("content") or "")
            messages[0] = {
                "role": "system",
                "content": (
                    f"{system}\n\n{_DIRECT_OUTPUT_INSTRUCTION}\n"
                    f"前端只读摘要：{frontend_context_json(request)}"
                ),
            }
            canonical = self._canonical_request(messages)
            parser = MeaPetOutputStreamParser()
            raw_chunks: list[str] = []
            completed_indices: set[int] = set()
            protocol_completed_emitted = False
            stream_done = False
            log.info(
                f"[direct] 开始流式调用 turn={turn} model={canonical.model} "
                f"max_tokens={canonical.max_tokens}"
            )

            async for event in self.protocol_client.stream(canonical):
                if request.turn_id in self._cancelled_turns:
                    self._cancelled_turns.discard(request.turn_id)
                    if not is_vision:
                        self.engine._rollback_direct_turn(request.user_text)
                    yield TurnCancelled(request.turn_id)
                    return
                if isinstance(event, TextDelta):
                    raw_chunks.append(event.delta)
                    for parsed_event in parser.feed(event.delta):
                        if isinstance(parsed_event, SegmentCompleted):
                            if parsed_event.segment.missing_required_fields:
                                continue
                            completed_indices.add(parsed_event.segment.index)
                        elif isinstance(parsed_event, ProtocolCompleted):
                            protocol_completed_emitted = True
                        yield parsed_event
                elif isinstance(event, ReasoningDelta):
                    # reasoning 仅存在于协议内部，禁止进入气泡、TTS 和时间线正文。
                    continue
                elif isinstance(event, StreamDone):
                    stream_done = True
                    break
            if not stream_done:
                raise DirectProtocolError("protocol", "模型流未正常结束。")

            raw_text = "".join(raw_chunks)
            if raw_text.strip():
                # 控制台默认可见：完整模型文本（非 reasoning）。
                log.info(
                    f"[direct] 模型返回文本 turn={turn} chars={len(raw_text)}\n{raw_text}"
                )
            else:
                log.info(f"[direct] 模型返回文本为空 turn={turn}")

            result = parser.close(tts_enabled=request.tts_enabled)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                # 模型经常先输出大量推理过程再输出格式回复，尝试剥离思考前置内容
                cleaned = raw_text
                blocks = list(_SEGMENT_BLOCK_LOCAL_RE.finditer(raw_text))
                if blocks:
                    tail_start = blocks[-1].start()
                    tail_end = len(raw_text)
                    done_pos = raw_text.rfind("<MEAPET_DONE")
                    if done_pos >= 0:
                        tail_end = done_pos + len("<MEAPET_DONE />")
                    else:
                        tail_end = blocks[-1].end()
                    cleaned = raw_text[tail_start:tail_end]
                if cleaned != raw_text:
                    new_parser = MeaPetOutputStreamParser()
                    new_parser.feed(cleaned)
                    new_result = new_parser.close(tts_enabled=request.tts_enabled)
                    if not new_result.requires_repair(tts_enabled=request.tts_enabled):
                        log.info(
                            f"[direct] 剥离推理前置后协议通过 turn={turn} "
                            f"original_chars={len(raw_text)} cleaned_chars={len(cleaned)}"
                        )
                        result = new_result
                        completed_indices = {
                            seg.index for seg in result.segments
                            if not seg.missing_required_fields
                        }
                if result.requires_repair(tts_enabled=request.tts_enabled):
                    log.warning(
                        f"[direct] 输出协议需修复 turn={turn} raw_chars={len(raw_text)}"
                    )
                    yield FormatRepairRequired(result)
                    repaired = await self._repair_result(
                        request=request,
                        malformed_output="".join(raw_chunks),
                    )
                    if request.turn_id in self._cancelled_turns:
                        self._cancelled_turns.discard(request.turn_id)
                        if not is_vision:
                            self.engine._rollback_direct_turn(request.user_text)
                        yield TurnCancelled(request.turn_id)
                        return
                    if repaired is not None:
                        result = repaired

            if not any(segment.display_text.strip() for segment in result.segments):
                if not is_vision:
                    self.engine._rollback_direct_turn(request.user_text)
                yield TurnFailed(
                    request.turn_id,
                    "protocol",
                    "模型没有返回可展示的回复。",
                )
                return

            for segment in result.segments:
                if segment.index not in completed_indices:
                    yield SegmentCompleted(segment)
                    completed_indices.add(segment.index)
            if result.done and not protocol_completed_emitted:
                yield ProtocolCompleted()
            if not is_vision:
                self.engine._commit_direct_turn(result)
            elapsed = time.perf_counter() - started
            display_chars = sum(
                len((seg.display_text or "").strip())
                for seg in result.segments
            )
            log.info(
                f"[direct] 回合完成 turn={turn} segments={len(result.segments)} "
                f"display_chars={display_chars} elapsed={elapsed:.2f}s"
            )
            yield TurnCompleted(request.turn_id, result)
        except DirectProtocolError as exc:
            if prepared and not is_vision:
                self.engine._rollback_direct_turn(request.user_text)
            elapsed = time.perf_counter() - started
            log.error(
                f"[direct] 回合失败 turn={turn} category={exc.category} "
                f"retryable={exc.retryable} elapsed={elapsed:.2f}s "
                f"msg={exc.safe_message}"
            )
            yield TurnFailed(
                request.turn_id,
                exc.category,
                exc.safe_message,
                exc.retryable,
            )
        except (ValueError, TypeError) as exc:
            if prepared and not is_vision:
                self.engine._rollback_direct_turn(request.user_text)
            elapsed = time.perf_counter() - started
            log.error(
                f"[direct] 配置错误 turn={turn} type={type(exc).__name__} "
                f"elapsed={elapsed:.2f}s"
            )
            yield TurnFailed(
                request.turn_id,
                "configuration",
                "模型配置不完整，请检查协议、地址和模型。",
            )
