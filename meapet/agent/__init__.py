"""外部 Agent 运行时适配器。"""

from .base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from .openai_adapter import (
    OpenAIAdapter,
    OpenAIConfig,
    OpenAICapabilities,
)
from .factory import create_agent_adapter_from_config
from .presentation import AgentTurnPresentation

__all__ = [
    "AgentTurnRequest",
    "AgentTurnPresentation",
    "FormatRepairRequired",
    "OpenAIAdapter",
    "OpenAIConfig",
    "OpenAICapabilities",
    "ToolStatus",
    "TurnCancelled",
    "TurnCompleted",
    "TurnFailed",
    "create_agent_adapter_from_config",
]
