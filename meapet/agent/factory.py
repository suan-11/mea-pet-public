"""从统一配置构造 OpenAI 兼容 Agent 适配器。"""

from __future__ import annotations

import os

from meapet.agent.openai_adapter import (
    OpenAIAdapter,
    OpenAIConfig,
    DEFAULT_OPENAI_BASE_URL,
)


def _resolve_secret(value: str, env_keys: tuple[str, ...]) -> str:
    """解析密钥：如果是 $VAR 格式则从环境变量读取，否则直接返回。"""
    if not value:
        for key in env_keys:
            val = os.environ.get(key)
            if val:
                return val
        return ""
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def _positive_float(value: object, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _positive_int(value: object, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def create_agent_adapter_from_config(config: dict) -> OpenAIAdapter:
    """根据 llm.agent 配置构造一个 OpenAI 兼容适配器。"""
    llm = config.setdefault("llm", {})
    agent_cfg = llm.setdefault("agent", {})

    base_url = (
        str(agent_cfg.get("base_url") or "").strip()
        or str(llm.get("api_base") or "").strip()
        or str(llm.get("host") or "").strip()
        or DEFAULT_OPENAI_BASE_URL
    )

    raw_key = str(agent_cfg.get("api_key") or llm.get("api_key") or "").strip()
    api_key = _resolve_secret(raw_key, ("OPENAI_API_KEY", "MEAPET_API_KEY"))

    model = (
        str(agent_cfg.get("model") or "").strip()
        or str(llm.get("model") or "").strip()
        or str(llm.get("direct", {}).get("model") or "").strip()
        or "gpt-4o-mini"
    )

    temperature = _positive_float(
        agent_cfg.get("temperature") or llm.get("temperature"), 0.7
    )
    max_tokens = _positive_int(
        agent_cfg.get("max_tokens") or llm.get("max_tokens"), 4096
    )
    timeout = _positive_float(
        agent_cfg.get("timeout_seconds"), 120.0
    )

    tls = agent_cfg.get("tls") if isinstance(agent_cfg.get("tls"), dict) else {}
    verify_tls = bool(tls.get("verify", True))

    return OpenAIAdapter(
        OpenAIConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout,
            verify_tls=verify_tls,
        )
    )

