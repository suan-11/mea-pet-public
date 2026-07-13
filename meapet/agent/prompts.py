"""Agent 后端共用的 MeaPet 前端输出约束。"""

from __future__ import annotations

import json

from meapet.agent.base import AgentTurnRequest


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

MAX_REPAIR_INPUT_CHARS = 65536


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
        f"{OUTPUT_INSTRUCTION}\n"
        f"前端只读摘要：{frontend_context_json(request)}\n\n"
        f"用户当前请求：\n{request.user_text}"
    )
