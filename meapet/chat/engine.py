"""
梅尔桌宠 - LLM 对话模块
支持多种后端：Ollama、DeepSeek API
"""
import json
import sys
import random
import re
import socket  # noqa: F401  # 必须在 PyQt 之前导入（避免 QtNetwork hook 冲突）
import threading
from typing import TYPE_CHECKING, Dict, List, Tuple

from meapet.log import get_color_logger
from meapet.utils import debug_enabled, redact_mapping, redact_text

log = get_color_logger("chat")

if TYPE_CHECKING:
    from meapet.memory.db import MeaMemory

# Windows GBK 兼容 — 由 pet.py 统一调用 ensure_utf8_stdout()
# 各模块不重复包装 stdout，避免多次 TextIOWrapper 后旧 wrapper GC 时关闭底层 buffer


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
SYSTEM_PROMPT = """你是梅尔，《霞流宝石心》游戏中的猫娘天才。茶发褐瞳144cm，面无表情。
性格：毒舌冷淡、学术狂热、嘴硬心软。
说话：句尾加「喵」；极简20-40字；解释≤80字；害羞时转移话题；开心偶尔「嘿嘿」。
知识：全科全能。信条「知道越多越不可怕」。
对主人：亲密但毒舌，称「主人」。
格式（严格）：
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


_TTS_METADATA_RE = re.compile(
    r"<TTS>\s*(\{[^\r\n]*\})\s*</TTS>",
    re.IGNORECASE,
)
_TTS_DELIVERY_MAX_CHARS = 60


class ChatEngine:
    """多后端对话引擎 + 记忆/养成系统"""

    def __init__(
        self,
        backend: str = "ollama",
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen3.5:4b",
        api_key: str = "",
        api_base: str = "",
        temperature: float = 0.7,
        memory: "MeaMemory" = None,
        bridge_url: str = "http://127.0.0.1:18888",
    ):
        self.backend = backend
        self.host = host
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.bridge_url = bridge_url.rstrip("/")
        self.available = False
        self.memory = memory  # MeaMemory 实例

        self.history: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._history_lock = threading.Lock()
        self._cancelled = False

        self._backend_ready = False
        # 云端 API 同步就绪；Ollama 后台探测，避免阻塞启动
        if self.backend == "ollama":
            self._deferred_check()
        elif self.backend == "deepseek":
            if self.api_key:
                self.available = True
                _safe_print(f"✓ DeepSeek API configured: {self.model}", flush=True)
            else:
                _safe_print("⚠ DeepSeek API: no key", flush=True)
            self._backend_ready = True
        elif self.backend == "mimo":
            if self.api_key:
                self.available = True
                _safe_print(f"✓ MiMo API configured: {self.model}", flush=True)
            else:
                _safe_print("⚠ MiMo API: no key", flush=True)
            self._backend_ready = True
        elif self.backend == "openclaw":
            self._backend_ready = True
            _safe_print("⚠ OpenClaw 后端尚未实现，已标记为不可用", flush=True)
        else:
            _safe_print(f"⚠ Unknown backend: {self.backend}", flush=True)
            self._backend_ready = True

    def cancel(self):
        """协作式取消：标记取消位（httpx 请求在超时后结束）。"""
        self._cancelled = True


    async def _post_json(self, url: str, *, headers=None, json_body=None, timeout=30):
        """真异步 HTTP（httpx）。"""
        from meapet.http_async import post_json
        # httpx timeout: float seconds
        to = timeout
        if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
            to = float(timeout[1])
        return await post_json(url, headers=headers, json=json_body, timeout=to)

    async def _get_json(self, url: str, timeout=5):
        from meapet.http_async import get_json
        return await get_json(url, timeout=float(timeout))

    def _deferred_check(self):
        """线程内检测 Ollama（不阻塞 __init__）"""
        t = threading.Thread(target=self._check_backend, daemon=True)
        t.start()

    def _check_ready(self) -> bool:
        """检查后端是否已检测完成（供外部调用）"""
        return self._backend_ready

    def _check_backend(self):
        """检查 Ollama 是否可用（后台线程；HTTP 走 httpx）"""
        try:
            if self.backend != "ollama":
                return
            from meapet.async_runtime import run as _arun
            from meapet.http_async import get_json
            resp = _arun(get_json(f"{self.host}/api/tags", timeout=5.0), timeout=10)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                # 精确匹配或带 tag 前缀匹配（qwen2.5:7b / qwen2.5:7b-instruct）
                if self.model in models or any(m.startswith(self.model.split(":")[0]) and self.model in m for m in models):
                    self.available = True
                    _safe_print(f"✓ Ollama: {self.model}", flush=True)
                else:
                    chat_models = [m for m in models
                                   if any(t in m for t in ["qwen", "deepseek",
                                    "minicpm", "llama", "mistral", "gemma"])]
                    if chat_models:
                        self.model = chat_models[0]
                        self.available = True
                        _safe_print(f"✓ Ollama: using {self.model}", flush=True)
                    else:
                        _safe_print("⚠ Ollama: no chat model found", flush=True)
        except Exception as e:
            msg = str(e).lower()
            name = type(e).__name__
            if "timeout" in msg or "Timeout" in name:
                _safe_print(f"⚠ Ollama 超时，请确认已启动: {self.host}", flush=True)
            elif "connect" in msg or "Connection" in name:
                _safe_print(f"⚠ Ollama 未连接: {self.host}", flush=True)
            else:
                _safe_print(f"⚠ Ollama 检测异常: {e}", flush=True)
        finally:
            self._backend_ready = True


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
        # 允许少量汉字/标点；若含大量拉丁字母则不像日语对白
        latin = sum(1 for c in s if ("a" <= c.lower() <= "z"))
        return latin <= max(2, len(s) // 8)

    @classmethod
    def _render_tts_style(cls, raw_json: str) -> str:
        """校验模型 TTS JSON，并渲染成可发送给 MiMo 的自然语言指令。"""
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
        """从保留的 <TTS> 行提取指令，并确保元数据不会进入显示或朗读文本。"""
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
        """兼容旧接口：返回 (中文显示文本, mood)，其余字段写入一次性侧通道。"""
        display, mood, voice, style = self._parse_reply_payload(reply)
        self._last_voice_text = voice
        self._last_tts_style = style
        return display, mood

    @classmethod
    def _parse_reply_bundle(cls, reply: str) -> Tuple[str, str, str]:
        """兼容旧接口：返回 (display_zh, mood, voice_jp_or_empty)。"""
        display, mood, voice, _style = cls._parse_reply_payload(reply)
        return display, mood, voice

    @classmethod
    def _parse_reply_payload(cls, reply: str) -> Tuple[str, str, str, str]:
        """
        解析模型回复：
        - 第1行：中文（可带 [mood]）
        - 第2行：日语（可选，供 TTS）
        - 第3行：内部 <TTS>{JSON}</TTS>（可选，不显示、不朗读）
        返回 (display_zh, mood, voice_jp_or_empty, tts_style)
        """
        raw, tts_style = cls._extract_tts_style(reply)
        mood = "neutral"
        if not raw:
            return "", mood, "", tts_style

        # 先抽首行 mood 标签（可能在整段开头）；未知标签也剥离，保持旧行为
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

        # 去掉可能的「中文：」「日语：」前缀
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
        # 首行若仍带 [mood]
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
            # 整段只有日语时：显示与语音都用它
            voice = display

        return display, mood, voice, tts_style

    def take_voice_text(self) -> str:
        """取出最近一次解析到的日语对白（供 TTS），取后清空。"""
        v = getattr(self, "_last_voice_text", "") or ""
        self._last_voice_text = ""
        return v

    def take_tts_style(self) -> str:
        """取出最近一次解析到的动态 TTS 指令，取后清空。"""
        style = getattr(self, "_last_tts_style", "") or ""
        self._last_tts_style = ""
        return style

    def chat(self, message: str) -> Tuple[str, str]:
        """发送消息，返回 (回复文本, 情绪标签)"""
        with self._history_lock:
            self.history.append({"role": "user", "content": message})

            # ========== 注入养成记忆上下文（语义检索） ==========
            if self.memory:
                ctx = self.memory.build_context_prompt(current_query=message)
                full_system = SYSTEM_PROMPT + "\n\n" + ctx
                self.history[0] = {"role": "system", "content": full_system}
                log.debug(f"[Chat] 注入记忆上下文，prompt 长度={len(full_system)}，记忆内容={ctx}")

            # 保持历史不超 8 条（减少上下文长度，加速推理）
            if len(self.history) > 8:
                saved_system = self.history[0]
                self.history = [saved_system] + self.history[-6:]

            if not self.available:
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

            # ========== 记录到记忆系统 ==========
            if self.memory:
                with self._history_lock:
                    self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}

                self.memory.add_chat("user", message)
                self.memory.add_chat("mea", reply, mood)

                # 好感度按消息长度：短1 / 中2 / 长3（受每日上限约束）
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
        """同步入口：内部走 quick_chat_async + httpx。"""
        try:
            from meapet.async_runtime import run as _arun
            return _arun(self.quick_chat_async(message), timeout=130)
        except Exception as e:
            _safe_print(f"[chat] quick_chat failed: {type(e).__name__}", flush=True)
            self._debug_dump("quick_chat exception", e)
            return self._fallback_reply(), "neutral"

    async def _dispatch_chat_async(self, messages: List[Dict[str, str]]) -> str:
        if self.backend == "ollama":
            return await self._chat_ollama_async(messages)
        if self.backend == "deepseek":
            return await self._chat_deepseek_async(messages)
        if self.backend == "mimo":
            return await self._chat_mimo_async(messages)
        return self._fallback_reply()


    @staticmethod
    def _auto_start_ollama() -> None:
        """Best-effort start of local Ollama when health check fails."""
        import subprocess
        import time as _time
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            _time.sleep(1.5)
        except Exception:
            pass

    async def _chat_ollama_async(self, messages: List[Dict[str, str]] = None) -> str:
        import time as _time
        msgs = messages if messages is not None else self.history
        t0 = _time.time()
        total_chars = sum(len(m.get("content", "")) for m in msgs)
        _safe_print(f"[chat] 发送请求(async): model={self.model} messages={len(msgs)} 总字符≈{total_chars}", flush=True)
        try:
            resp = await self._post_json(
                f"{self.host}/api/chat",
                json_body={
                    "model": self.model,
                    "messages": msgs,
                    "stream": False,
                    "keep_alive": "30s",
                    "think": False,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": 320,
                        "num_ctx": 8192,
                        "top_p": 0.85,
                        "repeat_penalty": 1.1,
                    },
                },
                timeout=(5, 120),
            )
        except Exception as e:
            _safe_print(f"[chat] Ollama async 异常: {type(e).__name__}", flush=True)
            self._debug_dump("Ollama exception", e)
            return self._fallback_reply()
        t1 = _time.time()
        _safe_print(f"[chat] Ollama 响应耗时: {t1-t0:.1f}s  status={resp.status_code}", flush=True)
        if resp.status_code != 200:
            return self._fallback_reply()
        content = resp.json().get("message", {}).get("content", "")
        if not content or not content.strip():
            return self._fallback_reply()
        return content

    async def _chat_deepseek_async(self, messages: List[Dict[str, str]] = None) -> str:
        import time as _time
        msgs = messages if messages is not None else self.history
        t0 = _time.time()
        _safe_print(f"[chat] DeepSeek 请求(async): model={self.model} messages={len(msgs)}", flush=True)
        try:
            resp = await self._post_json(
                f"{self.api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "model": self.model,
                    "messages": msgs,
                    "temperature": self.temperature,
                    "max_tokens": 320,
                },
                timeout=30,
            )
        except Exception as e:
            _safe_print(f"[chat] DeepSeek async 异常: {type(e).__name__}", flush=True)
            self._debug_dump("DeepSeek exception", e)
            return self._fallback_reply()
        _safe_print(f"[chat] DeepSeek 响应耗时: {_time.time()-t0:.1f}s  status={resp.status_code}", flush=True)
        if resp.status_code != 200:
            _safe_print(f"[chat] DeepSeek 错误: status={resp.status_code}", flush=True)
            self._debug_dump("DeepSeek error body", getattr(resp, "text", ""), limit=2000)
            return self._fallback_reply()
        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content or not content.strip():
            return self._fallback_reply()
        return content



    @staticmethod
    def _redact_secret(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        if len(s) <= 10:
            return "*" * len(s)
        return f"{s[:6]}...{s[-4:]}(len={len(s)})"

    def _debug_dump(self, title: str, obj, *, limit: int = 8000) -> None:
        """仅在显式调试模式打印载荷；始终对凭据字段脱敏。"""
        if not debug_enabled():
            return
        import json as _json
        import traceback as _tb
        try:
            if isinstance(obj, BaseException):
                text = redact_text(
                    "".join(_tb.format_exception(type(obj), obj, obj.__traceback__))
                )
            elif isinstance(obj, (dict, list, tuple)):
                text = _json.dumps(
                    redact_mapping(obj),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            else:
                text = redact_text(str(obj))
        except Exception as e:
            text = f"<dump failed: {e!r}> str={obj!r}"
        if len(text) > limit:
            text = text[:limit] + f"\n...[truncated total={len(text)}]"
        _safe_print(f"[chat] ===== {title} =====", flush=True)
        for line in text.splitlines() or [""]:
            _safe_print(f"[chat] {line}", flush=True)
        _safe_print(f"[chat] ===== /{title} =====", flush=True)

    @staticmethod
    def _mimo_message_text(message: dict) -> str:
        """从 MiMo message 提取可见 content（兼容 list 分段）。"""
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
        """content 为空时，尝试从 reasoning 末尾捞最终对白（弱兜底）。"""
        text = (reasoning or "").strip()
        if not text:
            return ""
        # 常见分隔：最终答案 / Final answer / 回复：
        for sep in ("最终答案", "最终回复", "Final answer", "final answer", "回复：", "回复:"):
            if sep in text:
                tail = text.split(sep)[-1].strip(" :：\n")
                if tail:
                    return tail[:500]
        # 否则取最后非空几行（避免整段思考）
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        tail_lines = lines[-4:]
        joined = "\n".join(tail_lines).strip()
        # 太像思考过程则放弃
        think_marks = ("首先", "我需要", "让我", "分析", "思考", "step", "because")
        if sum(1 for m in think_marks if m.lower() in joined.lower()) >= 2 and len(joined) > 80:
            return ""
        return joined[:500]

    async def _chat_mimo_async(self, messages: List[Dict[str, str]] = None) -> str:
        import time as _time
        msgs = messages if messages is not None else self.history
        t0 = _time.time()
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            # 开启深度思考；token 要够 reasoning + 最终 content
            "max_tokens": 4096,
            "max_completion_tokens": 4096,
            "thinking": {"type": "enabled"},
        }
        # 载荷仅在 MEAPET_DEBUG=1 时输出，且密钥字段会脱敏。
        safe_headers = {
            "Authorization": f"Bearer {self._redact_secret(self.api_key)}",
            "api-key": self._redact_secret(self.api_key),
            "Content-Type": "application/json",
        }
        _safe_print(
            f"[chat] MiMo 请求(async): model={self.model} messages={len(msgs)} "
            f"chars={sum(len(str(m.get('content', ''))) for m in msgs)}",
            flush=True,
        )
        self._debug_dump("MiMo REQUEST headers", safe_headers)
        self._debug_dump("MiMo REQUEST body", body, limit=12000)
        try:
            resp = await self._post_json(
                url,
                headers=headers,
                json_body=body,
                timeout=120,
            )
        except Exception as e:
            _safe_print(
                f"[chat] MiMo async 异常: type={type(e).__name__}",
                flush=True,
            )
            self._debug_dump("MiMo EXCEPTION traceback", e, limit=12000)
            return self._fallback_reply()

        elapsed = _time.time() - t0
        status = getattr(resp, "status_code", None)
        resp_text = ""
        try:
            resp_text = resp.text or ""
        except Exception as e:
            resp_text = f"<read resp.text failed: {e!r}>"
        resp_headers = {}
        try:
            resp_headers = dict(getattr(resp, "headers", {}) or {})
        except Exception:
            resp_headers = {}
        _safe_print(
            f"[chat] MiMo 响应: status={status} elapsed={elapsed:.1f}s "
            f"model={self.model} body_len={len(resp_text)}",
            flush=True,
        )
        self._debug_dump("MiMo RESPONSE headers", resp_headers, limit=4000)
        self._debug_dump("MiMo RESPONSE body", resp_text, limit=12000)

        if status != 200:
            _safe_print(
                f"[chat] MiMo HTTP {status} → 本地兜底句（不是其它云端模型）",
                flush=True,
            )
            return self._fallback_reply()

        try:
            data = resp.json()
        except Exception as e:
            _safe_print(f"[chat] MiMo JSON 解析失败: {type(e).__name__}", flush=True)
            self._debug_dump("MiMo RESPONSE raw (json fail)", resp_text, limit=12000)
            return self._fallback_reply()

        self._debug_dump("MiMo RESPONSE json", data, limit=12000)
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = self._mimo_message_text(message)
        reasoning = (message.get("reasoning_content") or "").strip()
        _safe_print(
            f"[chat] MiMo parsed content_len={len(content)} reasoning_len={len(reasoning)} "
            f"finish={(data.get('choices') or [{}])[0].get('finish_reason')}",
            flush=True,
        )
        if not content:
            if reasoning:
                content = self._mimo_content_from_reasoning(reasoning)
                if content:
                    _safe_print(
                        f"[chat] MiMo content 空，从 reasoning 尾部提取 len={len(content)}",
                        flush=True,
                    )
                    self._debug_dump("MiMo extracted from reasoning", content, limit=2000)
            if not content:
                _safe_print("[chat] MiMo 空 content → 本地兜底句", flush=True)
                return self._fallback_reply()
        else:
            self._debug_dump("MiMo final content", content, limit=2000)
        return content

    async def quick_chat_async(self, message: str) -> Tuple[str, str]:
        """async 版 quick_chat：历史更新仍加锁，HTTP 走 asyncio。"""
        self._cancelled = False
        with self._history_lock:
            self.history.append({"role": "user", "content": message})

            # ========== 注入养成记忆上下文（语义检索） ==========
            if self.memory:
                ctx = self.memory.build_context_prompt(current_query=message)
                full_system = SYSTEM_PROMPT + "\n\n" + ctx
                self.history[0] = {"role": "system", "content": full_system}
                log.debug(f"[Chat] 注入记忆上下文，prompt 长度={len(full_system)}，记忆内容={ctx}")

            if len(self.history) > 8:
                saved_system = self.history[0]
                self.history = [saved_system] + self.history[-6:]
            if not self.available:
                self.history.pop()
                return self._fallback_reply(), "neutral"
            messages_snapshot = list(self.history)
        try:
            # HTTP：_dispatch_chat_async → httpx
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
        """同步入口：内部仍走异步 HTTP（httpx），保证网络全异步。"""
        try:
            from meapet.async_runtime import run as _arun
            return _arun(self._dispatch_chat_async(messages), timeout=130)
        except Exception as e:
            _safe_print(f"[chat] dispatch async failed: {type(e).__name__}", flush=True)
            self._debug_dump("dispatch exception", e)
            return self._fallback_reply()

    def _extract_memories(self, user_msg: str, mea_reply: str):
        """从对话中提取值得长期记住的信息（类似 OpenClaw memory promotion）
        每 3 条用户消息触发一次，用 LLM 做轻量提取。"""
        if not self.memory:
            return
        # 用内存计数器
        if not hasattr(self, '_mem_extract_count'):
            self._mem_extract_count = 0
        self._mem_extract_count += 1

        # 快速触发：用户消息含「记住」关键词时立即提取本轮
        quick_trigger = any(kw in user_msg for kw in ["记住", "记下", "别忘了", "提醒我"])
        if not quick_trigger and self._mem_extract_count < 3:
            return
        self._mem_extract_count = 0

        # 取最近 6 条对话作为上下文
        recent = self.memory.get_recent_chats(6)
        if len(recent) < 4:
            return

        # 构建提取 prompt
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
        """根据后端类型发送记忆提取请求，返回响应文本（空字符串表示失败）。"""
        from meapet.async_runtime import run as _arun
        from meapet.http_async import post_json

        if self.backend == "ollama":
            resp = _arun(
                post_json(
                    f"{self.host}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.2,
                            "num_predict": 200,
                        },
                    },
                    timeout=30.0,
                ),
                timeout=45,
            )
            if resp.status_code != 200:
                return ""
            return resp.json().get("response", "")

        elif self.backend in ("deepseek", "mimo"):
            extract_messages = [
                {"role": "system", "content": "你是一个信息提取助手。从对话中提取值得长期记住的事实，每行一条用「- 」开头。如果没有值得记的内容回复「无」。仅提取非敏感事实。"},
                {"role": "user", "content": prompt},
            ]
            resp = _arun(
                post_json(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
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

        _safe_print(f"[memory] 后端 {self.backend} 不支持记忆提取", flush=True)
        return ""

    def _summarize_if_needed(self):
        """检查摘要触发器，调用 LLM 压缩旧对话为记忆片段"""
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
    """从配置文件创建引擎（api_key：环境变量优先于 config.json）"""
    from meapet.config.store import resolve_llm_api_key

    llm_cfg = config.get("llm", {})
    backend = llm_cfg.get("backend", "ollama")
    api_key = resolve_llm_api_key(llm_cfg)

    model = llm_cfg.get("model", "qwen3.5:4b")
    api_base = llm_cfg.get("api_base", "https://api.deepseek.com")
    if (backend or "").lower() == "mimo":
        try:
            from meapet.config.store import normalize_mimo_model_id
            model = normalize_mimo_model_id(model, for_vision=False)
        except Exception:
            # 官方 API id，不是 HF 仓库名
            if not model or model in ("mimo",) or str(model).startswith("XiaomiMiMo/"):
                model = "mimo-v2.5"
        if not api_base:
            api_base = "https://api.xiaomimimo.com/v1"
    return ChatEngine(
        backend=backend,
        host=llm_cfg.get("host", "http://127.0.0.1:11434"),
        model=model,
        api_key=api_key,
        api_base=api_base,
        temperature=llm_cfg.get("temperature", 0.7),
        memory=memory,
        bridge_url=llm_cfg.get("bridge_url", "http://127.0.0.1:18888"),
    )


if __name__ == "__main__":
    engine = ChatEngine()
    _safe_print(f"Backend: {engine.backend}, Model: {engine.model}, Available: {engine.available}")
    _safe_print("=== 梅尔对话测试 ===")
    for msg in ["你好呀", "你最喜欢吃什么"]:
        reply, mood = engine.chat(msg)
        _safe_print(f"\n你: {msg}")
        _safe_print(f"梅尔 [{mood}]: {reply}")
