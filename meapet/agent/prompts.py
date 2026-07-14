"""Agent 后端共用的 MeaPet 前端输出约束。"""

from __future__ import annotations

import json
from typing import Mapping

from meapet.agent.base import AgentTurnRequest
from meapet.config.normalizers import canonical_tts_language


OUTPUT_INSTRUCTION = """你仍使用 Agent 已有的人设、记忆、模型和工具；以下内容只约束桌宠前端输出格式。
最终回复必须由一到多个以下分段组成，禁止 Markdown 代码围栏：
<MEAPET_SEGMENT>
<DISPLAY>给用户看的本段文字</DISPLAY>
<META>{"voice_text":"本段朗读文本","voice_language":"BCP-47语言码","mood":"前端支持的情绪","tts_style":"本段语音表演方式，可为空字符串"}</META>
</MEAPET_SEGMENT>
全部分段后输出 <MEAPET_DONE />。
display_text、voice_text、voice_language、mood、tts_style 都是必需字段；不要输出推理、工具参数或工具结果。"""

REPAIR_INSTRUCTION = """你是一个纯格式转换器。只转换用户提供的畸形回复，不回答或继续原任务，不调用任何工具，不补充事实。
保留原回复的含义与语言，将其转换为一到多个下列分段，禁止 Markdown 代码围栏：
<MEAPET_SEGMENT>
<DISPLAY>给用户看的本段文字</DISPLAY>
<META>{"voice_text":"本段朗读文本","voice_language":"BCP-47语言码","mood":"neutral","tts_style":""}</META>
</MEAPET_SEGMENT>
全部分段后输出 <MEAPET_DONE />。五个 META/DISPLAY 字段都必须存在。"""

VOICE_TRANSLATION_INSTRUCTION = """【朗读语言（已开启：优先模型输出目标语朗读）】
- DISPLAY / display_text：给用户阅读的语言（通常是中文）。
- 若前端只读摘要中 prefer_model_voice_translation=true，且给出了 voice_target_language：
  - voice_language 必须使用该目标语对应的 BCP-47（例如 ja / ja-JP、en、zh-CN）。
  - voice_text 必须是该语言的完整朗读稿，语义与 DISPLAY 等价，不得增删事实。
  - 禁止出现“voice_language 标为日语/英语，但 voice_text 仍是中文”的情况。
- 若你无法产出合格的目标语朗读：把 voice_language 标成与 DISPLAY 相同的语言（如 zh-CN），
  voice_text 使用与 DISPLAY 相同语言的文本，由前端非 LLM 机器翻译回落处理。"""

MAX_REPAIR_INPUT_CHARS = 65536


def _frontend_caps(request: AgentTurnRequest) -> Mapping[str, object]:
    context = request.frontend_context if isinstance(request.frontend_context, Mapping) else {}
    caps = context.get("frontend_capabilities")
    return caps if isinstance(caps, Mapping) else {}


def should_request_model_voice_translation(request: AgentTurnRequest) -> bool:
    """是否在提示词中要求模型直接产出目标语 voice_text。"""
    if not bool(getattr(request, "tts_enabled", False)):
        return False
    caps = _frontend_caps(request)
    if not bool(caps.get("prefer_model_voice_translation", False)):
        return False
    if not bool(caps.get("translation_api_available", False)):
        return False
    target = canonical_tts_language(caps.get("voice_target_language") or "")
    return bool(target)


def build_output_instruction(request: AgentTurnRequest | None = None) -> str:
    """按前端能力动态拼接输出协议提示词。"""
    if request is None or not should_request_model_voice_translation(request):
        return OUTPUT_INSTRUCTION
    return f"{OUTPUT_INSTRUCTION}\n{VOICE_TRANSLATION_INSTRUCTION}"


def build_repair_instruction(request: AgentTurnRequest | None = None) -> str:
    if request is None or not should_request_model_voice_translation(request):
        return REPAIR_INSTRUCTION
    return f"{REPAIR_INSTRUCTION}\n{VOICE_TRANSLATION_INSTRUCTION}"


def frontend_context_json(request: AgentTurnRequest) -> str:
    """生成稳定、紧凑的只读前端能力摘要。"""
    return json.dumps(
        request.frontend_context,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def gateway_user_message(request: AgentTurnRequest) -> str:
    """为只有单一 message 字段的 Agent Gateway 组合当前轮输入。"""
    return (
        f"{build_output_instruction(request)}\n"
        f"前端只读摘要：{frontend_context_json(request)}\n\n"
        f"用户当前请求：\n{request.user_text}"
    )
