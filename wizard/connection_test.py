"""配置中心使用的轻量连接探测；所有网络与推理均在后台执行。"""

from __future__ import annotations

import copy
from contextlib import suppress
from dataclasses import dataclass

from meapet.agent.factory import create_agent_adapter_from_config
from meapet.config.store import (
    normalize_config,
    normalize_mimo_model_id,
    resolve_direct_api_key,
    resolve_vision_api_base,
    resolve_vision_api_key,
    resolve_vision_backend,
    resolve_vision_host,
)
from meapet.direct.client import DirectProtocolClient, DirectProtocolConfig
from meapet.direct.types import CanonicalChatRequest, StreamDone, TextDelta
from meapet.tts.service import MeaTTS


# 1×1 PNG；识图测试从不截取用户桌面。
_TEST_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    message: str


def _safe_failure(exc: BaseException) -> ConnectionResult:
    detail = " ".join(str(exc or "").split())[:180]
    if not detail:
        detail = type(exc).__name__
    return ConnectionResult(False, f"连接失败：{detail}")


def _profile_base_url(profile: dict) -> str:
    protocol = str(profile.get("protocol") or "openai_chat").strip().lower()
    if protocol == "ollama_chat":
        return str(profile.get("host") or "http://127.0.0.1:11434").strip()
    return str(profile.get("api_base") or "").strip()


async def _probe_direct_profile(
    profile: dict,
    *,
    with_image: bool = False,
) -> ConnectionResult:
    client = None
    received = False
    stream = None
    try:
        protocol = str(profile.get("protocol") or "openai_chat").strip().lower()
        tls = profile.get("tls") if isinstance(profile.get("tls"), dict) else {}
        client = DirectProtocolClient(
            DirectProtocolConfig(
                protocol=protocol,
                base_url=_profile_base_url(profile),
                api_key=str(profile.get("api_key") or ""),
                timeout_seconds=25.0,
                verify_tls=bool(tls.get("verify", True)),
                ca_file=str(tls.get("ca_file") or ""),
            )
        )
        if with_image:
            content: object = [
                {"type": "text", "text": "描述这张连接测试图，只回复 OK。"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "data": _TEST_PNG_BASE64,
                },
            ]
        else:
            content = "这是连接测试，只回复 OK。"
        request = CanonicalChatRequest(
            model=str(profile.get("model") or ""),
            messages=({"role": "user", "content": content},),
            temperature=0.0,
            # 探测不沿用用户的大上限，避免不必要的耗时和费用。
            max_tokens=32,
            stream=True,
        )
        stream = client.stream(request)
        async for event in stream:
            if isinstance(event, TextDelta) and event.delta:
                received = True
                break
            if isinstance(event, StreamDone):
                received = True
                break
        if not received:
            return ConnectionResult(False, "接口已连接，但没有返回可识别的模型响应。")
        target = "识图模型" if with_image else "回复模型"
        return ConnectionResult(True, f"{target}连接正常。")
    except Exception as exc:
        return _safe_failure(exc)
    finally:
        if stream is not None:
            close_stream = getattr(stream, "aclose", None)
            if callable(close_stream):
                with suppress(Exception):
                    await close_stream()
        if client is not None:
            with suppress(Exception):
                await client.close()


async def _probe_direct(config: dict) -> ConnectionResult:
    llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    profile = copy.deepcopy(
        llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
    )
    profile["api_key"] = resolve_direct_api_key(llm)
    return await _probe_direct_profile(profile)


async def _probe_agent(config: dict) -> ConnectionResult:
    adapter = None
    try:
        adapter = create_agent_adapter_from_config(copy.deepcopy(config))
        await adapter.probe()
        return ConnectionResult(True, "Agent 握手与能力检查正常。")
    except Exception as exc:
        return _safe_failure(exc)
    finally:
        if adapter is not None:
            close = getattr(adapter, "close", None)
            if callable(close):
                with suppress(Exception):
                    await close()


async def _probe_tts(config: dict) -> ConnectionResult:
    try:
        tts = MeaTTS(copy.deepcopy(config))
        if not tts.enabled:
            return ConnectionResult(False, "语音已关闭，请先启用语音。")
        tts_cfg = config.get("tts") if isinstance(config.get("tts"), dict) else {}
        language = str(tts_cfg.get("voice_lang") or "zh")
        result = await tts.speak_async(
            "连接测试",
            mood="neutral",
            language=language,
        )
        if not result or not result[0]:
            return ConnectionResult(False, "语音引擎未生成测试音频，请检查密钥、模型和路径。")
        return ConnectionResult(True, "语音引擎连接正常，测试音频已生成。")
    except Exception as exc:
        return _safe_failure(exc)


async def _probe_vision(config: dict) -> ConnectionResult:
    llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    vision = (
        config.get("vision") if isinstance(config.get("vision"), dict) else {}
    )
    mode = str(vision.get("mode") or "disabled").strip().lower()
    if mode == "disabled":
        return ConnectionResult(False, "识图已关闭，请先选择视觉链路。")
    if mode == "inherit":
        if str(llm.get("mode") or "direct").lower() == "agent":
            result = await _probe_agent(config)
            if result.ok:
                return ConnectionResult(
                    True,
                    "Agent 连接正常；图片输入能力仍以 Agent 实际实现为准。",
                )
            return result
        profile = copy.deepcopy(
            llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
        )
        profile["api_key"] = resolve_direct_api_key(llm)
        return await _probe_direct_profile(profile, with_image=True)

    backend = resolve_vision_backend(vision, llm)
    if backend == "mimo":
        profile = {
            "protocol": "openai_chat",
            "api_base": resolve_vision_api_base(vision, llm),
            "model": normalize_mimo_model_id(
                str(vision.get("model") or "mimo-v2.5"),
                for_vision=True,
            ),
            "api_key": resolve_vision_api_key(vision, llm),
        }
    else:
        profile = {
            "protocol": "ollama_chat",
            "host": resolve_vision_host(vision, llm),
            "model": str(vision.get("model") or "qwen3.5:4b"),
            "api_key": "",
        }
    return await _probe_direct_profile(profile, with_image=True)


async def probe_connection(target: str, config: dict) -> ConnectionResult:
    """探测指定配置区域；调用方负责把协程提交到后台事件循环。"""
    normalized_target = str(target or "").strip().lower()
    normalized = normalize_config(copy.deepcopy(config or {}))
    probes = {
        "direct": _probe_direct,
        "agent": _probe_agent,
        "tts": _probe_tts,
        "vision": _probe_vision,
    }
    probe = probes.get(normalized_target)
    if probe is None:
        return ConnectionResult(False, "未知的连接测试类型。")
    return await probe(normalized)
