"""与具体模型、Agent 和桌面控件解耦的统一对话契约。"""

from .capabilities import build_agent_frontend_context
from .output_protocol import MeaPetOutputStreamParser, parse_reply_output
from .types import CompanionState, FrontendCapabilities, ReplySegment

__all__ = [
    "CompanionState",
    "FrontendCapabilities",
    "MeaPetOutputStreamParser",
    "ReplySegment",
    "build_agent_frontend_context",
    "parse_reply_output",
]
