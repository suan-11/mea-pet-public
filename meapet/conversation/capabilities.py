"""构造发送给 Agent 的最小、只读前端上下文。"""

from __future__ import annotations

from .types import CompanionState, FrontendCapabilities


def build_agent_frontend_context(
    capabilities: FrontendCapabilities,
    state: CompanionState,
) -> dict:
    return {
        "frontend_capabilities": capabilities.to_dict(),
        "companion_state": state.to_dict(),
    }
