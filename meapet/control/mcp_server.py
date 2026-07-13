"""标准 MCP Streamable HTTP 工具表面（网络安全封装在 runner）。"""

from __future__ import annotations

from typing import Any

from .broker import CompanionControlBroker


def build_companion_mcp(broker: CompanionControlBroker):
    """构造只包含四个命名空间工具的 FastMCP 服务器。"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - 由可选依赖环境触发
        raise RuntimeError(
            "Companion MCP 需要安装 mcp>=1.27,<2"
        ) from exc

    server = FastMCP(
        "MeaPet Companion",
        instructions=(
            "受限桌宠前端控制；仅用于说话、表情、只读状态和逐次确认截图。"
        ),
        stateless_http=True,
        json_response=True,
    )

    @server.tool(name="meapet.say", structured_output=True)
    async def meapet_say(
        segments: list[dict[str, Any]],
        request_id: str = "",
    ) -> dict[str, Any]:
        """排队一到多个完整回复分段；不会抢占用户正在等待的回复。"""
        return await broker.say(segments, request_id=request_id)

    @server.tool(name="meapet.express", structured_output=True)
    async def meapet_express(
        mood: str = "",
        motion: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """请求当前前端明确支持的情绪或动作，不做值回退。"""
        return await broker.express(
            mood=mood,
            motion=motion,
            request_id=request_id,
        )

    @server.tool(name="meapet.get_state", structured_output=True)
    async def meapet_get_state() -> dict[str, Any]:
        """读取不含路径、密钥、记忆和全文的前端能力与状态摘要。"""
        return await broker.get_state()

    @server.tool(name="meapet.capture_screen", structured_output=True)
    async def meapet_capture_screen(
        scope: str = "full_screen",
        region: dict[str, int] | None = None,
        application: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """请求一次本机确认后的截图；授权不复用，截图不落盘。"""
        return await broker.capture_screen(
            scope=scope,
            region=region,
            application=application,
            request_id=request_id,
        )

    return server
