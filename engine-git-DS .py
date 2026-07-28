"""
梅尔桌宠 - LLM 对话模块
统一使用 OpenAI 兼容接口，不再区分后端类型。
"""
import json
import sys
import random
import re
import socket  # noqa: F401
import threading
from typing import TYPE_CHECKING, Dict, List, Tuple

from meapet.log import get_color_logger
from meapet.utils import debug_enabled, redact_mapping, redact_text

log = get_color_logger("chat")

if TYPE_CHECKING:
    from meapet.memory.db import MeaMemory


def _safe_print(*args, **kwargs):
    """GUI 安全版 print，并对常见凭据格式自动脱敏。"""
    try:
        text = " ".join(str(arg) for arg in args)
        print(redact_text(text), **kwargs)
    except (ValueError, OSError):
        pass


# ========================
# 角色设定
# ========================
PERSONA_PROMPT = """你是梅尔，《霞流宝石心》游戏中的猫娘天才。茶发褐瞳144cm，面无表情。
性格：毒舌冷淡、学术狂热、嘴硬心软。
说话：句尾加「喵」；输出不能超过80字；害羞时转移话题；开心偶尔「嘿嘿」。
知识：全科全能。信条「知道越多越不可怕」。
对主人：亲密但毒舌，称「主人」。"""

_LEGACY_OUTPUT_PROMPT = """格式（严格）：
1) 首行：中文对白。行首可带 [情绪] 标签，如 [happy]……；禁感叹号/卖萌/长篇大论；问啥答啥。
2) 第二行：日语对白，语义与中文一致，自然口语，句尾可用 にゃ。只写日语，不要罗马音、不要中文、不要解释。
3) 第三行：内部 TTS 表演元数据，严格输出单行 <TTS>{JSON}</TTS>，不要使用 Markdown。
4) 禁止输出第四行；不要写「中文：」「日语：」这类前缀。
TTS JSON 必须且只能包含：
- emotion: 必须与首行情绪一致，可选 neutral/happy/surprised/curious/sad/shy/annoyed/melancholy/intrigued/wistful/teary/embarrassed
- pace: slow/slightly_slow/normal/slightly_fast/fast
- energy: low/medium/high
- volume: soft/normal/loud
- delivery: 不超过60字，只描述本句的停顿、重音、气息和表演方式，不重复对白，不改变说话人身份
示例：
[annoyed]别摸了喵
触るなにゃ
<TTS>{"emotion":"annoyed","pace":"normal","energy":"medium","volume":"soft","delivery":"前半句短促，后半句收轻，句尾带一点嘴硬"}</TTS>
"""

SYSTEM_PROMPT = f"{PERSONA_PROMPT}\n{_LEGACY_OUTPUT_PROMPT}"


_TTS_METADATA_RE = re.compile(
    r"<TTS>\s*(\{[^\r\n]*\})\s*</TTS>",
    re.IGNORECASE,
)
_TTS_DELIVERY_MAX_CHARS = 60


class ChatEngine:
    """统一对话引擎 + 记忆/养成系统。"""

    _UNSUPPORTED_DIRECT_BACKENDS = frozenset({"openclaw", "hermes"})

    def __init__(
        self,
        backend: str = "custom",
        protocol: str = "",
        host: str = "http://127.0.0.1:11434",
        model: str = "gpt-4o-mini",
        api_key: str = "",
        api_base: str = "",
        temperature: float = 0.7,
        memory: "MeaMemory" = None,
        bridge_url: str = "http://127.0.0.1:18888",
        max_tokens: int = 4096,
        direct_client=None,
        extra_headers: dict | None = None,
        timeout_seconds: float = 0.0,
        proxy: str = "",
        thinking: dict | None = None,
    ):
        self.backend = "custom"
        self.host = host
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.extra_headers = {
            str(k): str(v) for k, v in (extra_headers or {}).items()
        }
        self.proxy = str(proxy or "").strip()
        self.thinking = dict(thinking or {})
        try:
            self.timeout_seconds = float(timeout_seconds or 0)
        except (TypeError, ValueError):
            self.timeout_seconds = 0.0

        if not protocol:
            from meapet.config.store import infer_direct_protocol
            protocol = infer_direct_protocol(
                "custom",
                api_base=self.api_base,
                host=self.host,
            )
        self.protocol = str(protocol).strip().lower() or "openai_chat"
        try:
            self.max_tokens = max(1, int(max_tokens))
        except (TypeError, ValueError):
            self.max_tokens = 4096
        self.bridge_url = bridge_url.rstrip("/")
        self.memory = memory

        raw_backend = str(backend or "custom").strip().lower() or "custom"
        if raw_backend in self._UNSUPPORTED_DIRECT_BACKENDS:
            self.available = False
            _safe_print(
                f"⚠ {raw_backend} 直连后端未实现，请将 llm.mode 设为 agent",
                flush=True,
            )
        else:
            self.available = True
            _safe_print(f"✓ 模型服务已配置: {self.model}", flush=True)
        self._backend_ready = True

        self.history: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._history_lock = threading.Lock()
        self._cancelled = False
        self._direct_client = direct_client
        self._direct_adapter = None

    def cancel(self):
        self._cancelled = True

    def _debug_dump(self, label: str, payload, limit: int = 2000) -> None:
        if not debug_enabled():
            return
        try:
            text = payload if isinstance(payload, str) else repr(payload)
        except Exception:
            return
        try:
            bound = max(0, int(limit))
        except (TypeError, ValueError):
            bound = 2000
        _safe_print(f"[debug] {label}: {text[:bound]}", flush=True)

    def _deferred_check(self) -> None:
        protocol = str(getattr(self, "protocol", "") or "").strip().lower()
        if protocol != "ollama_chat":
            return
        try:
            from meapet.async_runtime import run as _arun
            base = (self.api_base or self.host or "http://127.0.0.1:11434").rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3].rstrip("/")
            resp = _arun(self._get_json(f"{base}/api/tags", timeout=3), timeout=5)
            if getattr(resp, "status_code", 0) == 200:
                self.available = True
        except Exception:
            pass

    def _direct_base_url(self) -> str:
        base = (self.api_base or self.host or "http://127.0.0.1:11434").rstrip("/")
        return base

    def _get_direct_adapter(self):
        if self._direct_adapter is not None:
            return self._direct_adapter
        from meapet.direct.client import DirectProtocolClient, DirectProtocolConfig
        from meapet.direct.conversation import DirectConversationAdapter

        client = self._direct_client
        if client is None:
            client = DirectProtocolClient(
                DirectProtocolConfig(
                    protocol=self.protocol or "openai_chat",
                    base_url=self._direct_base_url(),
                    api_key=self.api_key,
                    timeout_seconds=(
                        self.timeout_seconds
                        if self.timeout_seconds > 0
                        else max(300.0, (self.max_tokens / 1000) * 60.0)
                    ),
                    extra_headers=self.extra_headers,
                    proxy=self.proxy,
                    thinking=self.thinking,
                )
            )
            self._direct_client = client
        self._direct_adapter = DirectConversationAdapter(self, client)
        return self._direct_adapter

    async def stream_turn(self, request):
        adapter = self._get_direct_adapter()
        async for event in adapter.stream_turn(request):
            yield event

    async def cancel_turn(self, turn_id: str) -> None:
        await self._get_direct_adapter().cancel(turn_id)

    async def close(self) -> None:
        if self._direct_adapter is not None:
            await self._direct_adapter.close()

    def _prepare_direct_turn(self, message: str) -> List[Dict[str, str]]:
        with self._history_lock:
            self.history.append({"role": "user", "content": str(message or "")})
            if len(self.history) > 16:
                saved_system = self.history[0]
                self.history = [saved_system] + self.history[-14:]

            system = PERSONA_PROMPT
            if self.memory:
                context = self.memory.build_context_prompt(current_query=message)
                if context:
                    system += "\n\n" + context
            self.history[0] = {"role": "system", "content": system}
            snapshot = [dict(item) for item in self.history]

        return snapshot

    def _build_vision_system_prompt(self, message: str) -> str:
        system = PERSONA_PROMPT
        if self.memory:
            context = self.memory.build_context_prompt(current_query=message)
            if context:
                system += "\n\n" + context
        return system

    def _rollback_direct_turn(self, message: str) -> None:
        with self._history_lock:
            if (
                self.history
                and self.history[-1].get("role") == "user"
                and self.history[-1].get("content") == str(message or "")
            ):
                self.history.pop()

    def _commit_direct_turn(self, result, raw_text: str = "") -> None:
        """
        存储完整协议块到历史。
        优先使用原始输出中的 <MEAPET_SEGMENT> 块；
        若没有 raw_text，则回退为 display_text 拼接（兼容旧调用）。
        """
        reply = ""
        if raw_text and raw_text.strip():
            import re
            pattern = re.compile(
                r"<MEAPET_SEGMENT\s*>.*?</MEAPET_SEGMENT\s*>",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(raw_text)
            if match:
                reply = match.group(0).strip()
                log.info(f"[_commit] 提取到完整协议，长度={len(reply)}")
            else:
                log.warning(f"[_commit] 未找到完整协议，放弃存储（raw_text 长度={len(raw_text)}）")
                return
        else:
            # 无原始文本时，从 result 提取 display_text 作为兼容兜底
            reply = "\n".join(
                segment.display_text.strip()
                for segment in result.segments
                if segment.display_text.strip()
            )
            if not reply:
                return

        if not reply:
            return
        with self._history_lock:
            self.history.append({"role": "assistant", "content": reply})
            if self.history and str(self.history[0].get("content", "")).startswith("你是梅尔"):
                self.history[0] = {"role": "system", "content": PERSONA_PROMPT}

    async def _post_json(self, url: str, *, headers=None, json_body=None, timeout=30):
        from meapet.http_async import post_json
        to = timeout
        if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
            to = float(timeout[1])
        return await post_json(url, headers=headers, json=json_body, timeout=to)

    async def _get_json(self, url: str, timeout=5):
        from meapet.http_async import get_json
        return await get_json(url, timeout=float(timeout))


    _MOOD_TAGS = {
        "neutral", "happy", "surprised", "curious",
        "sad", "shy", "annoyed", "melancholy",
        "intrigued", "wistful", "teary", "embarrassed",
    }
    _TTS_EMOTION_LABELS = {
        "neutral": "自然平静",
        "happy": "开心",
        "surprised": "惊讶",
        "curious": "好奇",
        "sad": "难过",
        "shy": "害羞",
        "annoyed": "不耐烦",
        "melancholy": "忧郁",
        "intrigued": "感兴趣",
        "wistful": "若有所思",
        "teary": "带哭腔",
        "embarrassed": "尴尬",
    }
    _TTS_PACE_LABELS = {
        "slow": "慢",
        "slightly_slow": "稍慢",
        "normal": "适中",
        "slightly_fast": "稍快",
        "fast": "快",
    }
    _TTS_ENERGY_LABELS = {
        "low": "偏低",
        "medium": "适中",
        "high": "偏高",
    }
    _TTS_VOLUME_LABELS = {
        "soft": "轻柔",
        "normal": "自然",
        "loud": "响亮",
    }

    @staticmethod
    def _has_japanese_kana(text: str) -> bool:
        return any(
            "\u3040" <= c <= "\u30ff" or "\u31f0" <= c <= "\u31ff"
            for c in (text or "")
        )

    @classmethod
    def _looks_like_japanese_line(cls, text: str) -> bool:
        s = (text or "").strip()
        if not s:
            return False
        if not cls._has_japanese_kana(s):
            return False
        latin = sum(1 for c in s if ("a" <= c.lower() <= "z"))
        return latin <= max(2, len(s) // 8)

    @classmethod
    def _render_tts_style(cls, raw_json: str) -> str:
        try:
            payload = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""

        parts = [
            "保持参考音色，不改变说话人身份。"
            "以下内容仅控制表演，不得改变、添加或复述目标文本。"
        ]
        has_valid_field = False
        enum_fields = (
            ("emotion", cls._TTS_EMOTION_LABELS, "情绪"),
            ("pace", cls._TTS_PACE_LABELS, "语速"),
            ("energy", cls._TTS_ENERGY_LABELS, "能量"),
            ("volume", cls._TTS_VOLUME_LABELS, "音量"),
        )
        for field, labels, title in enum_fields:
            value = payload.get(field)
            if not isinstance(value, str):
                continue
            label = labels.get(value.strip().lower())
            if not label:
                continue
            parts.append(f"{title}：{label}。")
            has_valid_field = True

        delivery = payload.get("delivery")
        if isinstance(delivery, str):
            delivery = re.sub(r"[\x00-\x1f\x7f<>]", " ", delivery)
            delivery = redact_text(delivery)
            delivery = " ".join(delivery.split())[:_TTS_DELIVERY_MAX_CHARS]
            delivery = delivery.rstrip("。；; ")
            if delivery:
                parts.append(f"表演细节：{delivery}。")
                has_valid_field = True

        return "".join(parts) if has_valid_field else ""

    @classmethod
    def _extract_tts_style(cls, reply: str) -> Tuple[str, str]:
        source = reply or ""
        style = ""
        for match in _TTS_METADATA_RE.finditer(source):
            if not style:
                style = cls._render_tts_style(match.group(1))

        source = _TTS_METADATA_RE.sub("", source)
        visible_lines = [
            line
            for line in source.splitlines()
            if "<tts" not in line.lower() and "</tts" not in line.lower()
        ]
        return "\n".join(visible_lines).strip(), style

    def _parse_mood(self, reply: str) -> Tuple[str, str]:
        display, mood, voice, style = self._parse_reply_payload(reply)
        self._last_voice_text = voice
        self._last_tts_style = style
        return display, mood

    @classmethod
    def _parse_reply_bundle(cls, reply: str) -> Tuple[str, str, str]:
        display, mood, voice, _style = cls._parse_reply_payload(reply)
        return display, mood, voice

    @classmethod
    def _parse_reply_payload(cls, reply: str) -> Tuple[str, str, str, str]:
        raw, tts_style = cls._extract_tts_style(reply)
        mood = "neutral"
        if not raw:
            return "", mood, "", tts_style

        if raw.startswith("["):
            close = raw.find("]")
            if close > 0:
                tag = raw[1:close].lower().strip()
                if tag in cls._MOOD_TAGS:
                    mood = tag
                raw = raw[close + 1:].lstrip()

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return "", mood, "", tts_style

        def _strip_label(s: str) -> str:
            for pref in (
                "中文：", "中文:", "日语：", "日语:", "日本語：", "日本語:",
                "JP：", "JP:", "ZH：", "ZH:", "ja:", "JA:",
            ):
                if s.startswith(pref):
                    return s[len(pref):].strip()
            return s

        lines = [_strip_label(x) for x in lines]
        display = lines[0]
        if display.startswith("["):
            close = display.find("]")
            if close > 0:
                tag = display[1:close].lower().strip()
                if tag in cls._MOOD_TAGS:
                    mood = tag
                display = display[close + 1:].strip()

        voice = ""
        if len(lines) >= 2 and cls._looks_like_japanese_line(lines[1]):
            voice = lines[1]
        elif len(lines) == 1 and cls._looks_like_japanese_line(display) and not any(
            "\u4e00" <= c <= "\u9fff" for c in display
        ):
            voice = display

        return display, mood, voice, tts_style

    def take_voice_text(self) -> str:
        v = getattr(self, "_last_voice_text", "") or ""
        self._last_voice_text = ""
        return v

    def take_tts_style(self) -> str:
        style = getattr(self, "_last_tts_style", "") or ""
        self._last_tts_style = ""
        return style

    def chat(self, message: str) -> Tuple[str, str]:
        with self._history_lock:
            self.history.append({"role": "user", "content": message})

            if self.memory:
                ctx = self.memory.build_context_prompt(current_query=message)
                log.debug(f"[Chat] 获取记忆上下文，长度={len(ctx)}，为：{ctx}")
                full_system = SYSTEM_PROMPT + "\n\n" + ctx
                self.history[0] = {"role": "system", "content": full_system}
                log.debug(f"[Chat] 注入记忆上下文，prompt 长度={len(full_system)}")

            if len(self.history) > 16:
                saved_system = self.history[0]
                self.history = [saved_system] + self.history[-14:]

            if not self.available:
                _safe_print(f"[DEBUG] chat() 拦截: available=False", flush=True)
                self.history.pop()
                if self.memory:
                    self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
                return self._fallback_reply(), "neutral"

            messages_snapshot = list(self.history)

        try:
            reply = self._dispatch_chat(messages_snapshot)
            reply = reply.strip()
            reply, mood = self._parse_mood(reply)

            with self._history_lock:
                self.history.append({"role": "assistant", "content": reply})

            if self.memory:
                with self._history_lock:
                    self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}

                self.memory.add_chat("user", message)
                self.memory.add_chat("mea", reply, mood)

                n = len(message or "")
                if n < 10:
                    delta = 1
                elif n < 50:
                    delta = 2
                else:
                    delta = 3

                upgrade_msg = self.memory.add_affection(delta)
                full_system = SYSTEM_PROMPT + "\n\n" + self.memory.build_context_prompt(current_query=message)
                if upgrade_msg:
                    full_system += f"\n\n[内部：好感度升至{self.memory.get_affection_tier()[1]}。请用稍暖的语气回应。]"
                with self._history_lock:
                    self.history[0] = {"role": "system", "content": full_system}
                log.debug(f"[Chat] 好感更新后重新注入记忆上下文，prompt 长度={len(full_system)}")
                self.memory.mark_today_chatted()
                self.memory.increment_message_counter()
                self._extract_memories(message, reply)
                self._summarize_if_needed()
                self.memory.store_chat_exchange(message, reply)

            return reply, mood

        except Exception as e:
            with self._history_lock:
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()
                if self.memory:
                    self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
            _safe_print(f"Chat error: {type(e).__name__}", flush=True)
            self._debug_dump("chat exception", e)
            return self._fallback_reply(), "neutral"

    def quick_chat(self, message: str) -> Tuple[str, str]:
        try:
            from meapet.async_runtime import run as _arun
            return _arun(self.quick_chat_async(message), timeout=130)
        except Exception as e:
            _safe_print(f"[chat] quick_chat failed: {type(e).__name__}", flush=True)
            self._debug_dump("quick_chat exception", e)
            return self._fallback_reply(), "neutral"

    async def _dispatch_chat_async(self, messages: List[Dict[str, str]]) -> str:
        protocol = str(getattr(self, "protocol", "") or "").strip().lower()
        if protocol == "ollama_chat":
            return await self._chat_ollama_async(messages)
        from meapet.config.store import detect_endpoint_family

        family = detect_endpoint_family(self.api_base, self.host)
        if family == "mimo":
            return await self._chat_mimo_async(messages)
        return await self._chat_openai_async(messages)

    @staticmethod
    def _mimo_message_text(message: dict) -> str:
        content = (message or {}).get("content") or ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(part))
            content = "".join(parts)
        return str(content).strip()

    @staticmethod
    def _mimo_content_from_reasoning(reasoning: str) -> str:
        text = (reasoning or "").strip()
        if not text:
            return ""
        for sep in ("最终答案", "最终回复", "Final answer", "final answer", "回复：", "回复:"):
            if sep in text:
                tail = text.split(sep)[-1].strip(" :：\n")
                if tail:
                    return tail[:500]
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        tail_lines = lines[-4:]
        joined = "\n".join(tail_lines).strip()
        think_marks = ("首先", "我需要", "让我", "分析", "思考", "step", "because")
        if sum(1 for m in think_marks if m.lower() in joined.lower()) >= 2 and len(joined) > 80:
            return ""
        return joined[:500]

    async def _chat_openai_async(self, messages: List[Dict[str, str]] = None) -> str:
        import time as _time
        msgs = messages if messages is not None else self.history
        t0 = _time.time()
        base_url = (self.api_base or self.host or "http://127.0.0.1:11434").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        _safe_print(
            f"[chat] OpenAI 请求(async): model={self.model} messages={len(msgs)} "
            f"chars={sum(len(str(m.get('content', ''))) for m in msgs)}",
            flush=True,
        )
        try:
            resp = await self._post_json(
                url,
                headers=headers,
                json_body=body,
                timeout=(5, 120),
            )
        except Exception as e:
            _safe_print(f"[chat] OpenAI async 异常: {type(e).__name__}", flush=True)
            self._debug_dump("OpenAI exception", e)
            return self._fallback_reply()

        elapsed = _time.time() - t0
        _safe_print(
            f"[chat] OpenAI 响应: status={resp.status_code} elapsed={elapsed:.1f}s "
            f"model={self.model}",
            flush=True,
        )
        if resp.status_code != 200:
            _safe_print(f"[chat] OpenAI HTTP {resp.status_code} → 本地兜底句", flush=True)
            self._debug_dump("OpenAI error body", getattr(resp, "text", ""), limit=2000)
            return self._fallback_reply()

        try:
            data = resp.json()
        except Exception as e:
            _safe_print(f"[chat] OpenAI JSON 解析失败: {type(e).__name__}", flush=True)
            return self._fallback_reply()

        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = self._mimo_message_text(message)
        reasoning = (message.get("reasoning_content") or "").strip()
        if not content:
            if reasoning:
                content = self._mimo_content_from_reasoning(reasoning)
                if content:
                    _safe_print(
                        f"[chat] content 空，从 reasoning 尾部提取 len={len(content)}",
                        flush=True,
                    )
            if not content:
                _safe_print("[chat] 空 content → 本地兜底句", flush=True)
                return self._fallback_reply()
        return content

    _MIN_COMPLETION_TOKENS = 320

    def _completion_token_budget(self) -> int:
        try:
            configured = int(getattr(self, "max_tokens", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        return max(configured, self._MIN_COMPLETION_TOKENS)

    async def _chat_openai_compatible_async(
        self,
        messages: List[Dict[str, str]],
        *,
        default_base: str,
        label: str,
    ) -> str:
        import time as _time

        msgs = messages if messages is not None else self.history
        t0 = _time.time()
        base_url = (getattr(self, "api_base", "") or default_base).rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        api_key = getattr(self, "api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": getattr(self, "temperature", 0.7),
            "max_tokens": self._completion_token_budget(),
        }
        _safe_print(
            f"[chat] {label} 请求(async): model={self.model} messages={len(msgs)}",
            flush=True,
        )
        try:
            resp = await self._post_json(
                url,
                headers=headers,
                json_body=body,
                timeout=(5, 120),
            )
        except Exception as e:
            _safe_print(f"[chat] {label} async 异常: {type(e).__name__}", flush=True)
            self._debug_dump(f"{label} exception", e)
            return self._fallback_reply()

        elapsed = _time.time() - t0
        _safe_print(
            f"[chat] {label} 响应: status={resp.status_code} elapsed={elapsed:.1f}s",
            flush=True,
        )
        if resp.status_code != 200:
            _safe_print(f"[chat] {label} HTTP {resp.status_code} → 本地兜底句", flush=True)
            self._debug_dump(f"{label} error body", getattr(resp, "text", ""), limit=2000)
            return self._fallback_reply()
        try:
            data = resp.json()
        except Exception as e:
            _safe_print(f"[chat] {label} JSON 解析失败: {type(e).__name__}", flush=True)
            return self._fallback_reply()
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = self._mimo_message_text(message)
        if not content:
            reasoning = (message.get("reasoning_content") or "").strip()
            if reasoning:
                content = self._mimo_content_from_reasoning(reasoning)
        if not content:
            _safe_print(f"[chat] {label} 空 content → 本地兜底句", flush=True)
            return self._fallback_reply()
        return content

    async def _chat_mimo_async(self, messages: List[Dict[str, str]] = None) -> str:
        return await self._chat_openai_compatible_async(
            messages,
            default_base="https://api.xiaomimimo.com/v1",
            label="MiMo",
        )

    async def _chat_ollama_async(self, messages: List[Dict[str, str]] = None) -> str:
        msgs = messages if messages is not None else self.history
        host = (getattr(self, "host", "") or "http://127.0.0.1:11434").rstrip("/")
        url = f"{host}/api/chat"
        body = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "options": {
                "temperature": getattr(self, "temperature", 0.7),
                "num_predict": self._completion_token_budget(),
            },
        }
        try:
            resp = await self._post_json(
                url,
                headers={"Content-Type": "application/json"},
                json_body=body,
                timeout=(5, 120),
            )
        except Exception as e:
            _safe_print(f"[chat] Ollama async 异常: {type(e).__name__}", flush=True)
            self._debug_dump("Ollama exception", e)
            return self._fallback_reply()
        if resp.status_code != 200:
            _safe_print(f"[chat] Ollama HTTP {resp.status_code} → 本地兜底句", flush=True)
            self._debug_dump("Ollama error body", getattr(resp, "text", ""), limit=2000)
            return self._fallback_reply()
        try:
            data = resp.json()
        except Exception:
            return self._fallback_reply()
        content = str(((data.get("message") or {}).get("content")) or "").strip()
        if not content:
            return self._fallback_reply()
        return content

    async def quick_chat_async(self, message: str) -> Tuple[str, str]:
        self._cancelled = False
        with self._history_lock:
            self.history.append({"role": "user", "content": message})

            if self.memory:
                ctx = self.memory.build_context_prompt(current_query=message)
                log.debug(f"[Chat] 获取记忆上下文，长度={len(ctx)}，为：{ctx}")
                full_system = SYSTEM_PROMPT + "\n\n" + ctx
                self.history[0] = {"role": "system", "content": full_system}
                log.debug(f"[Chat] 注入记忆上下文，prompt 长度={len(full_system)}")

            if len(self.history) > 16:
                saved_system = self.history[0]
                self.history = [saved_system] + self.history[-14:]
            if not self.available:
                self.history.pop()
                return self._fallback_reply(), "neutral"
            messages_snapshot = list(self.history)
        try:
            if self._cancelled:
                return self._fallback_reply(), "neutral"
            reply = await self._dispatch_chat_async(messages_snapshot)
            reply = (reply or "").strip()
            reply, mood = self._parse_mood(reply)
            with self._history_lock:
                self.history.append({"role": "assistant", "content": reply})
            return reply, mood
        except Exception as e:
            with self._history_lock:
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()
            _safe_print(f"Chat error: {type(e).__name__}", flush=True)
            self._debug_dump("quick_chat_async exception", e)
            return self._fallback_reply(), "neutral"

    def _dispatch_chat(self, messages: List[Dict[str, str]]) -> str:
        try:
            from meapet.async_runtime import run as _arun
            return _arun(self._dispatch_chat_async(messages), timeout=130)
        except Exception as e:
            _safe_print(f"[chat] dispatch async failed: {type(e).__name__}", flush=True)
            self._debug_dump("dispatch exception", e)
            return self._fallback_reply()

    def _extract_memories(self, user_msg: str, mea_reply: str):
        if not self.memory:
            return
        if not hasattr(self, '_mem_extract_count'):
            self._mem_extract_count = 0
        self._mem_extract_count += 1

        quick_trigger = any(kw in user_msg for kw in ["记住", "记下", "别忘了", "提醒我"])
        if not quick_trigger and self._mem_extract_count < 3:
            return
        self._mem_extract_count = 0

        recent = self.memory.get_recent_chats(6)
        if len(recent) < 4:
            return

        context_lines = []
        for c in recent:
            role = "主人" if c["role"] == "user" else "梅尔"
            context_lines.append(f"{role}：{c['content']}")
        context = "\n".join(context_lines)

        extract_prompt = f"""分析以下对话，提取值得长期记住的信息。
只提取非敏感事实（如：姓名昵称、兴趣偏好、计划约定、重要事件）。
严禁提取：密码、密钥、token、验证码、银行卡号、身份证号、住址门牌、私密健康信息。
不要提取闲聊、问候、寒暄。
如果没有值得长期记忆的内容，或内容涉及敏感凭据，回复「无」。

对话：
{context}

值得记住的信息（每条一行，用「- 」开头）："""

        sensitive_kw = (
            "密码", "口令", "密钥", "token", "api_key", "apikey", "secret",
            "验证码", "银行卡", "信用卡", "身份证", "社保", "私钥", "sk-",
        )

        try:
            result = self._send_extract_request(extract_prompt)
            if not result:
                return
            for line in result.split("\n"):
                line = line.strip()
                if line.startswith("-") or line.startswith("·"):
                    content = line.lstrip("-· ").strip()
                    if not content or content == "无" or len(content) <= 3:
                        continue
                    low = content.lower()
                    if any(k in low for k in sensitive_kw):
                        _safe_print("[memory] 跳过疑似敏感记忆", flush=True)
                        continue
                    self.memory.add_memory(content, importance=5)
                    _safe_print(f"[memory] 已提取记忆 chars={len(content)}")
                    self._debug_dump("memory extracted", content, limit=1000)
        except Exception as e:
            _safe_print(f"[memory] 提取失败: {type(e).__name__}")
            self._debug_dump("memory extraction exception", e)

    def _send_extract_request(self, prompt: str) -> str:
        from meapet.async_runtime import run as _arun
        from meapet.http_async import post_json

        base_url = (self.api_base or self.host or "http://127.0.0.1:11434").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        extract_messages = [
            {"role": "system", "content": "你是一个信息提取助手。从对话中提取值得长期记住的事实，每行一条用「- 」开头。如果没有值得记的内容回复「无」。"},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = _arun(
                post_json(
                    url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": extract_messages,
                        "temperature": 0.2,
                        "max_tokens": 200,
                    },
                    timeout=30.0,
                ),
                timeout=45,
            )
            if resp.status_code != 200:
                return ""
            choices = resp.json().get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            return (msg.get("content") or "").strip()
        except Exception:
            return ""

    def _summarize_if_needed(self):
        if not self.memory:
            return
        if not self.memory.check_summarization_trigger():
            return
        try:
            chats, ids = self.memory.prepare_summarization_context()
            if not chats or not ids:
                return
            context_lines = []
            for c in chats:
                role = "主人" if c["role"] == "user" else "梅尔"
                context_lines.append(f"{role}：{c['content']}")
            context = "\n".join(context_lines)
            prompt = (
                "请用一句话概括以下对话的核心内容（不超过50字）。只输出概括，不要前缀。\n\n"
                f"对话：\n{context}"
            )
            result = self._send_extract_request(prompt)
            if result and result.strip() not in ("", "无", "无。"):
                self.memory.store_summary(result.strip(), ids)
                _safe_print(f"[summary] 已生成对话摘要: {result[:60]}...", flush=True)
        except Exception as e:
            _safe_print(f"[summary] 摘要生成失败: {type(e).__name__}: {e}", flush=True)

    def _fallback_reply(self) -> str:
        fallbacks = [
            "……干嘛喵。",
            "哼，无聊喵。",
            "……不想说话喵。",
            "有事吗喵。",
            "……喵。",
            "别烦我喵。",
            "（嗅嗅）……什么味喵。",
            "啊……好困喵。",
        ]
        return random.choice(fallbacks)

    def clear_history(self):
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]


def create_engine_from_config(config: dict, memory: "MeaMemory" = None) -> ChatEngine:
    """从配置创建引擎：llm.direct 显式 profile 是运行时真源，旧顶层字段仅兜底。"""
    from meapet.config.store import infer_direct_protocol, resolve_direct_api_key

    llm_cfg = config.get("llm", {})
    direct = (
        llm_cfg.get("direct")
        if isinstance(llm_cfg.get("direct"), dict)
        else {}
    )
    api_key = resolve_direct_api_key(llm_cfg)

    model = direct.get("model") or llm_cfg.get("model") or "gpt-4o-mini"
    api_base = direct.get("api_base") or llm_cfg.get("api_base") or ""
    host = direct.get("host") or llm_cfg.get("host") or "http://127.0.0.1:11434"
    protocol = str(direct.get("protocol") or "").strip().lower()
    if not protocol:
        protocol = infer_direct_protocol(
            "custom",
            api_base=api_base,
            host=host,
        )

    _safe_print(
        f"[DEBUG] create_engine_from_config: backend=custom, host={host}, "
        f"api_base={api_base}, model={model}, protocol={protocol}",
        flush=True,
    )
    return ChatEngine(
        backend="custom",
        protocol=protocol,
        host=host,
        model=model,
        api_key=api_key,
        api_base=api_base,
        temperature=direct.get("temperature", llm_cfg.get("temperature", 0.7)),
        memory=memory,
        bridge_url=llm_cfg.get("bridge_url", "http://127.0.0.1:18888"),
        max_tokens=direct.get("max_tokens", llm_cfg.get("max_tokens", 4096)),
        extra_headers=(
            direct.get("headers") if isinstance(direct.get("headers"), dict) else {}
        ),
        timeout_seconds=direct.get("timeout_seconds", 0),
        proxy=direct.get("proxy", ""),
        thinking=(
            direct.get("thinking") if isinstance(direct.get("thinking"), dict) else {}
        ),
    )


if __name__ == "__main__":
    engine = ChatEngine()
    _safe_print(f"Model: {engine.model}, Available: {engine.available}")
    _safe_print("=== 梅尔对话测试 ===")
    for msg in ["你好呀", "你最喜欢吃什么"]:
        reply, mood = engine.chat(msg)
        _safe_print(f"\n你: {msg}")
        _safe_print(f"梅尔 [{mood}]: {reply}")