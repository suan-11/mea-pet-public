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
    build_output_instruction,
    build_repair_instruction,
    frontend_context_json,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
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
        log.info(
            f"[direct] 回合开始 turn={turn} "
            f"user_chars={len(request.user_text or '')} "
            f"attachments={len(request.attachments or ())} "
            f"tts={bool(request.tts_enabled)}"
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
            messages = self.engine._prepare_direct_turn(request.user_text)
            prepared = True
            log.info(
                f"[direct] 上下文已准备 turn={turn} messages={len(messages)} "
                f"history_tail_role={messages[-1].get('role') if messages else '-'}"
            )
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
                    f"{system}\n\n{build_output_instruction(request)}\n"
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
                    self.engine._rollback_direct_turn(request.user_text)
                    yield TurnCancelled(request.turn_id)
                    return
                if repaired is not None:
                    result = repaired

            if not any(segment.display_text.strip() for segment in result.segments):
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
            if prepared:
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
            if prepared:
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
