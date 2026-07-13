"""从统一配置构造显式 Agent 适配器。"""

from __future__ import annotations

import secrets
import uuid

from meapet.agent.hermes import HermesAdapter, HermesConfig
from meapet.config.store import resolve_secret


_AGENT_TOKEN_ENV = ("HERMES_API_SERVER_KEY", "MEAPET_AGENT_TOKEN")


def _positive_float(value: object, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def create_agent_adapter_from_config(config: dict):
    """构造当前唯一启用的 Agent 后端，并补齐持久会话标识。"""
    llm = config.setdefault("llm", {})
    agent = llm.setdefault("agent", {})
    kind = str(agent.get("kind") or "hermes").strip().lower()

    if kind not in {"hermes", "openclaw"}:
        raise ValueError(f"不支持的 Agent 类型: {kind or '<empty>'}")

    session_id = str(agent.get("session_id") or "").strip()
    session_key = str(agent.get("session_key") or "").strip()
    if not session_id:
        session_id = f"meapet-{uuid.uuid4().hex}"
        agent["session_id"] = session_id
    if not session_key:
        session_key = secrets.token_urlsafe(32)
        agent["session_key"] = session_key

    tls = agent.get("tls") if isinstance(agent.get("tls"), dict) else {}
    auth_token = resolve_secret(
        str(agent.get("auth_token") or ""),
        _AGENT_TOKEN_ENV,
    )
    if kind == "openclaw":
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig

        return OpenClawAdapter(
            OpenClawConfig(
                base_url=str(agent.get("base_url") or "ws://127.0.0.1:18789"),
                auth_token=auth_token,
                session_id=session_id,
                session_key=session_key,
                timeout_seconds=_positive_float(
                    agent.get("timeout_seconds"),
                    120.0,
                ),
                verify_tls=bool(tls.get("verify", True)),
                ca_file=str(tls.get("ca_file") or ""),
                allow_insecure_ws=bool(agent.get("allow_insecure_ws", False)),
                identity_path=str(agent.get("identity_path") or ""),
            )
        )

    return HermesAdapter(
        HermesConfig(
            base_url=str(agent.get("base_url") or "http://127.0.0.1:8642"),
            auth_token=auth_token,
            model=str(agent.get("model") or "hermes-agent"),
            session_id=session_id,
            session_key=session_key,
            history_turns=agent.get("history_turns", 5),
            timeout_seconds=_positive_float(
                agent.get("timeout_seconds"),
                120.0,
            ),
            verify_tls=bool(tls.get("verify", True)),
            ca_file=str(tls.get("ca_file") or ""),
        )
    )
