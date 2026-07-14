"""
梅尔桌宠 - 屏幕观察模块 v4
一次多模态：截图 → 单次模型调用（说/不说 + 中日双语对白）→ TTS
（旧 parse_decision 仍保留供单测/兼容）

支持切换视觉模型（config.json → vision.model）
可用模型：minicpm-v (5.5G, 快) / qwen2.5vl:7b (6GB, 稍慢) MiMo V2.5（云端，超快）

设计参考：Sakura（Rvosy/sakura）的主动搭话 prompt 架构
"""
import io
import traceback
from PyQt5.QtCore import QThread, pyqtSignal

from meapet.utils import debug_enabled, redact_text
from meapet.log import get_color_logger
from meapet.watcher.capture import capture_screen_image

log = get_color_logger("watcher")

# Windows GBK 兼容 — 由 pet.py 统一调用 ensure_utf8_stdout()
# 各模块不重复包装 stdout，避免多次 TextIOWrapper 后旧 wrapper GC 时关闭底层 buffer


# ========================
# 阶段提示（用于 progress 信号）
# ========================
STAGE_CAPTURE = "偷偷看一眼…"
STAGE_SUMMARY = "看清楚在干嘛…"
STAGE_DECISION = "要不要开口呢…"
STAGE_ROAST = "想好吐槽了喵…"
STAGE_SEARCH = "让我查一下…"
STAGE_SILENT = "算了，没什么好说的"
STAGE_ERROR = "唔…没看清喵"


def parse_decision(decision: str) -> tuple:
    """
    解析策略评估输出。
    返回 (should_speak: bool, strategy: str, search_query: str)
    纯函数，便于单测。
    """
    strategy = "毒舌吐槽"
    search_query = ""
    should_speak = False
    if not decision:
        return should_speak, strategy, search_query

    lines = [l.strip() for l in decision.split("\n") if l.strip()]
    if not lines:
        return should_speak, strategy, search_query

    first_line = lines[0].replace("第1行：", "").replace("第1行:", "").strip()
    if first_line == "说" or first_line.startswith("说"):
        should_speak = True
    if should_speak and len(lines) >= 2:
        s = lines[1].replace("第2行：", "").replace("第2行:", "").strip()
        if "关心" in s or "进度" in s:
            strategy = "关心进度"
        elif "陪聊" in s or "轻松" in s:
            strategy = "轻松陪聊"
        elif "吃醋" in s:
            strategy = "轻微吃醋"
        elif "好奇" in s or "询问" in s:
            strategy = "好奇询问"
        elif "毒舌" in s or "吐槽" in s:
            strategy = "毒舌吐槽"
    if should_speak and len(lines) >= 3:
        sq = lines[2].replace("第3行：", "").replace("第3行:", "").strip()
        if sq and sq not in ("无", "不需要", "否", "不需要搜索", "-"):
            search_query = sq
    return should_speak, strategy, search_query


def parse_watch_output(raw: str) -> tuple:
    log.debug(f"[parse] input chars={len(raw)}")
    """
    解析一次多模态偷看输出。
    返回 (should_speak, display_zh, voice_jp, mood, strategy_hint)
    """
    text = (raw or "").strip()
    if not text:
        return False, "", "", "neutral", ""

    # 去掉可能的代码块
    if "```" in text:
        import re as _re
        text = _re.sub(r"```[\s\S]*?```", "", text)
        text = text.replace("```", "").strip()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False, "", "", "neutral", ""

    first = lines[0].replace("第1行：", "").replace("第1行:", "").strip()
    # 兼容旧决策：只有「说/不说」
    if first.startswith("不说") or first in ("否", "沉默", "算了"):
        return False, "", "", "neutral", ""
    if not (first == "说" or first.startswith("说")):
        # 模型直接吐对白：尝试当「说」
        speak = True
        body_lines = lines
    else:
        speak = True
        body_lines = lines[1:]

    if not speak:
        return False, "", "", "neutral", ""
    if not body_lines:
        return False, "", "", "neutral", ""

    # 复用对话侧双语解析
    try:
        from meapet.chat.engine import ChatEngine
        bundle = "\n".join(body_lines)
        display, mood, voice = ChatEngine._parse_reply_bundle(bundle)
    except Exception:
        display = body_lines[0]
        mood = "neutral"
        voice = body_lines[1] if len(body_lines) > 1 else ""
        if display.startswith("[") and "]" in display:
            tag = display[1:display.find("]")].lower()
            display = display[display.find("]") + 1:].strip()
            mood = tag or "neutral"

    display = (display or "").strip()
    voice = (voice or "").strip()
    if not display:
        return False, "", "", "neutral", ""
    return True, display, voice, mood or "neutral", ""

# ========================
# 场景摘要 prompt
# ========================
SUMMARY_PROMPT = "用一句话（不超过30字）描述这个屏幕截图的内容。只需描述画面上有什么软件、什么内容、用户在做什么。不要评价，不要吐槽。"


# ========================
# 策略评估 prompt（融合冷落感知 + Web 搜索决策）
# ========================
DECISION_PROMPT = """你是梅尔，一只毒舌猫娘。你正在犹豫要不要主动对主人开口说话。

【冷落状态】
距离上次互动已过 {idle_minutes} 分钟。
- 如果 >10分钟：主人可能忙或冷落你了，可以主动搭话
- 如果 >30分钟：主人好久没理你了，轻微表达在意
- 如果 <3分钟：刚说过话，除非有重要的事否则不说

【屏幕内容摘要】
{summary}

请严格按以下格式回复（每行一条，不加前缀标签）：
第1行：说 或 不说
第2行（说时）：策略名（毒舌吐槽/关心进度/轻松陪聊/轻微吃醋/好奇询问）
第3行（说时，可选）：需要搜索的内容（如果屏幕上有你不认识的名词/角色/作品，写出你想搜索的关键词。不需要搜索就写「无」）

场景策略：
- 代码/IDE/终端 → 关心进度（说你看到的具体文件或语言）
- 网页浏览/社交媒体 → 毒舌吐槽（点破摸鱼）
- 视频/游戏/娱乐 → 轻松陪聊（聊画面内容）
- 二次元角色图 → 轻微吃醋（如果不认识角色可以请求搜索）
- 文档/笔记/学习 → 好奇询问
- 聊天窗口 → 轻松陪聊或关心进度
- 桌面空闲/锁屏/黑屏 → 不说

重要：宁说错不沉默。只要能看到有意义的内容，优先选一个策略开口。
如果画面里有你不认识的作品名/角色名/专有名词，写在第3行请求搜索。
不要泛说「休息」「喝水」「辛苦了」——说具体看到的。

回复："""



# ========================
# 一次多模态：决策 + 中日双语最终对白
# ========================
UNIFIED_WATCH_PROMPT = """你是梅尔，《霞流宝石心》的毒舌猫娘（茶发褐瞳144cm，冷淡傲娇）。
你刚偷看了主人屏幕，要决定要不要开口。

【冷落状态】
距离上次互动约 {idle_minutes} 分钟。
- >10分钟：可以主动搭话
- >30分钟：可带一点在意，但不抱怨
- <3分钟：刚说过话，除非画面很值得说，否则不说

【输出格式（严格）】
第1行：说 或 不说
若第1行是「不说」：不要再输出任何内容。
若第1行是「说」：
第2行：中文对白。行首可带 [情绪] 标签（happy/annoyed/curious/melancholy/shy/neutral 等）；≤40字；句尾加「喵」；可加一个括号小动作；禁 Markdown/引号/前缀解释。
第3行：日语对白。与中文同义，自然口语，句尾可用 にゃ；只写日语，不要罗马音、不要中文、不要前缀。

【策略参考】
- 代码/IDE/终端 → 关心进度或毒舌
- 网页/摸鱼 → 毒舌点破
- 视频/游戏 → 轻松陪聊
- 二次元图 → 轻微吃醋
- 桌面空闲/锁屏/黑屏 → 不说
宁说错不沉默：能看到有意义内容就说具体画面，不要空泛「休息/喝水」。

直接按格式输出："""

RELAY_OBSERVATION_PROMPT = """你是视觉观察器，不是角色聊天模型。
只描述截图中可见的事实，不评价、不向用户说话、不猜测不可见内容。
严格返回一个 JSON 对象，禁止 Markdown，字段为：
{"summary":"不超过800字的画面摘要","application":"主要应用或空字符串","activity":"coding/reading/chatting/gaming/video/idle/unknown","notable_text":["最多10条必要的短文本"],"sensitive":false}
如可见密码、密钥、私聊、邮件或身份信息，sensitive 设为 true，且 notable_text 不要转录具体秘密。"""


class ScreenWatcher(QThread):
    """一次多模态偷看：冷落感知 + 中日双语对白"""

    result_ready = pyqtSignal(str, str)  # (回复文本, 情绪)
    error = pyqtSignal(str)
    silent = pyqtSignal()
    progress = pyqtSignal(str)
    search_request = pyqtSignal(str)  # 请求 Web 搜索（关键词）

    def __init__(self, ollama_host: str = "http://127.0.0.1:11434",
                 vision_model: str = "minicpm-v",
                 chat_model: str = "qwen2.5:7b",
                 idle_minutes: float = 0,
                 # MiMo 后端参数
                 backend: str = "ollama",
                 api_base: str = "",
                 api_key: str = "",
                 mimo_model: str = "mimo-v2.5",
                 mode: str = "relay",
                 capture_scope: str = "full_screen",
                 capture_region: dict | None = None,
                 capture_application: str = ""):
        super().__init__()
        self.host = ollama_host
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.idle_minutes = idle_minutes
        self.backend = backend
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key
        self.mimo_model = mimo_model
        self.mode = str(mode or "disabled").strip().lower()
        self.capture_scope = str(capture_scope or "full_screen").strip().lower()
        self.capture_region = (
            dict(capture_region) if isinstance(capture_region, dict) else None
        )
        self.capture_application = str(capture_application or "").strip()
        self._stop = False
        self.last_voice_text = ""
        self.last_voice_language = ""
        self.last_tts_style = ""
        self._reply_adapter = None
        self._frontend_context = {}
        self._tts_enabled = False

    def configure_reply(
        self,
        adapter,
        *,
        frontend_context: dict | None = None,
        tts_enabled: bool = False,
    ) -> None:
        """在主线程为本轮截图写入回复后端与只读上下文快照。"""
        self._reply_adapter = adapter
        self._frontend_context = dict(frontend_context or {})
        self._tts_enabled = bool(tts_enabled)

    def set_idle_minutes(self, minutes: float):
        """外部更新冷落时长"""
        self.idle_minutes = minutes

    def stop(self, timeout_ms: int = 3000) -> bool:
        """请求线程停止，并报告是否已在超时时间内退出。"""
        self._stop = True
        if self.isRunning():
            return bool(self.wait(timeout_ms))
        return True

    def prepare_start(self) -> bool:
        """在重新启动前复位停止标志；仍在运行时拒绝启动。"""
        if self.isRunning():
            return False
        self._stop = False
        return True

    def _capture_image(self):
        """与手动/MCP 路径复用同一个范围截图后端。"""
        return capture_screen_image(
            scope=self.capture_scope,
            region=self.capture_region,
            application=self.capture_application,
        ).image



    @staticmethod
    def _mimo_extract_text(message: dict) -> str:
        content = (message or {}).get("content") or ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(part))
            content = "".join(parts)
        content = str(content).strip()
        if content:
            return content
        # thinking 开启且 content 空时，弱兜底从 reasoning 取尾
        reasoning = ((message or {}).get("reasoning_content") or "").strip()
        if not reasoning:
            return ""
        for sep in ("最终答案", "最终回复", "Final answer", "final answer", "回复：", "回复:"):
            if sep in reasoning:
                tail = reasoning.split(sep)[-1].strip(" :：\n")
                if tail:
                    return tail[:800]
        lines = [ln.strip() for ln in reasoning.splitlines() if ln.strip()]
        return "\n".join(lines[-6:])[:800] if lines else ""

    def _http_post(self, url: str, *, headers=None, json=None, timeout=90):
        """所有网络统一走 httpx 异步客户端（在后台 loop 上执行）。"""
        from meapet.async_runtime import run
        from meapet.http_async import post_json
        return run(
            post_json(url, headers=headers or {}, json=json, timeout=float(timeout)),
            timeout=float(timeout) + 30,
        )

    def _request_visual_observation(self, image_base64: str) -> str:
        """仅 relay 模式调用：独立视觉模型只产生观察 JSON。"""
        if self.backend == "mimo":
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["api-key"] = self.api_key
            response = self._http_post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json={
                    "model": self.mimo_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "只输出用户要求的视觉观察 JSON。",
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": RELAY_OBSERVATION_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": (
                                            "data:image/jpeg;base64,"
                                            f"{image_base64}"
                                        )
                                    },
                                },
                            ],
                        },
                    ],
                    "max_tokens": 1200,
                    "max_completion_tokens": 1200,
                    "temperature": 0.1,
                    "stream": False,
                },
                timeout=600,
            )
            if response.status_code != 200:
                raise RuntimeError(f"vision relay HTTP {response.status_code}")
            message = (
                (response.json().get("choices") or [{}])[0].get("message")
                or {}
            )
            return self._mimo_extract_text(message)

        response = self._http_post(
            f"{self.host}/api/generate",
            json={
                "model": self.vision_model,
                "prompt": RELAY_OBSERVATION_PROMPT,
                "images": [image_base64],
                "stream": False,
                "options": {"num_predict": 600, "temperature": 0.1},
            },
            timeout=600,
        )
        if response.status_code != 200:
            raise RuntimeError(f"vision relay HTTP {response.status_code}")
        return str(response.json().get("response") or "").strip()

    def _emit_vision_reply(self, reply) -> None:
        if reply.silent:
            self.progress.emit(STAGE_SILENT)
            self.silent.emit()
            return
        segments = tuple(reply.segments)
        if not segments:
            raise RuntimeError("vision reply has no segments")
        display = "\n".join(
            segment.display_text for segment in segments if segment.display_text
        ).strip()
        languages = {
            segment.voice_language
            for segment in segments
            if segment.voice_language
        }
        if len(languages) == 1:
            self.last_voice_language = next(iter(languages))
            self.last_voice_text = " ".join(
                segment.voice_text
                for segment in segments
                if segment.voice_text
            ).strip()
        else:
            # 旧 watcher 呈现一次只播放一条音频；多语段不强行错配。
            self.last_voice_language = ""
            self.last_voice_text = ""
            log.warning("[watcher] 多语段回复跳过合并语音")
        self.last_tts_style = " ".join(
            segment.tts_style
            for segment in segments
            if segment.tts_style
        ).strip()[:400]
        mood = segments[0].mood or "neutral"
        self.progress.emit(STAGE_ROAST)
        self.result_ready.emit(display, mood)

    def run(self):
        """截图后按 inherit/relay 路由，最终统一交给主回复后端。"""
        try:
            import base64

            from meapet.agent.base import ImageAttachment
            from meapet.async_runtime import run as run_async
            from meapet.vision.coordinator import VisionCoordinator
            from meapet.vision.observation import parse_vision_observation

            self.last_voice_text = ""
            self.last_voice_language = ""
            self.last_tts_style = ""
            if self.mode == "disabled":
                self.silent.emit()
                return
            if self._reply_adapter is None:
                raise RuntimeError("主回复后端未就绪")
            if self._stop:
                return

            self.progress.emit(STAGE_CAPTURE)
            image = self._capture_image()
            log.info(
                f"[screenshot] captured in memory: size={image.size}, mode={image.mode}"
            )
            ratio = 1280 / max(1, image.width)
            if ratio < 1.0:
                img = image.resize((320, int(image.height * ratio)))
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format="JPEG", quality=50)
            b64 = base64.b64encode(buf.getvalue()).decode()
            log.info(f"[screenshot] encoded base64 length={len(b64)}")

            if self._stop:
                return

            coordinator = VisionCoordinator(self._reply_adapter)
            self.progress.emit(STAGE_SUMMARY)
            if self.mode == "inherit":
                attachment = ImageAttachment(
                    image=image,
                    base64=b64,
                    mime_type="image/jpeg",
                )
                operation = coordinator.inherit(
                    attachment,
                    idle_minutes=self.idle_minutes,
                    frontend_context=self._frontend_context,
                    tts_enabled=self._tts_enabled,
                )
            elif self.mode == "relay":
                raw_observation = self._request_visual_observation(b64)
                observation = parse_vision_observation(raw_observation)
                if observation is None:
                    raise RuntimeError("视觉模型未返回可用观察")
                operation = coordinator.relay(
                    observation,
                    idle_minutes=self.idle_minutes,
                    frontend_context=self._frontend_context,
                    tts_enabled=self._tts_enabled,
                )
            else:
                raise RuntimeError(f"不支持的视觉模式: {self.mode}")

            reply = run_async(operation, timeout=660)
            if self._stop:
                return
            self._emit_vision_reply(reply)
        except Exception as exc:
            self.progress.emit(STAGE_ERROR)
            log.error(f"[run] exception: {type(exc).__name__}: {exc}")
            if debug_enabled():
                log.debug(f"[run] traceback:\n{traceback.format_exc()}")
            safe_message = getattr(exc, "safe_message", "") or str(exc)
            self.error.emit(safe_message)

    # ---- 搜索回传接口 ----
    _search_pending = True
    _search_result = ""

    def set_search_result(self, result: str):
        """外部（pet.py）回传 Web 搜索结果"""
        self._search_pending = False
        self._search_result = result

    def _mimo_chat(self, messages: list, max_tokens: int = 2048, temperature: float = 0.3, timeout: int = 180) -> str:
        """调用 MiMo 聊天补全 API，返回 content 文本（httpx 异步客户端）"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            # 与 TTS 对齐：部分网关只认 api-key
            if self.api_key:
                headers["api-key"] = self.api_key
            log.info(f"[mimo_chat] sending {len(messages)} messages, max_tokens={max_tokens}")
            resp = self._http_post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json={
                    "model": self.mimo_model,
                    "messages": messages,
                    "max_tokens": max(int(max_tokens or 0), 2048),
                    "max_completion_tokens": max(int(max_tokens or 0), 2048),
                    "temperature": temperature,
                    "thinking": {"type": "enabled"},
                },
                timeout=timeout,
            )
            log.info(f"[mimo_chat] response status={resp.status_code}")
            if resp.status_code == 200:
                msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
                text = self._mimo_extract_text(msg)
                log.info(f"[mimo_chat] extracted text length={len(text)}")
                rc = (msg.get("reasoning_content") or "").strip()
                log.info(
                    f"[mimo_chat] content_len={len(text)} "
                    f"reasoning_len={len(rc)}"
                )
                return text
            body = (resp.text or "").replace("\n", " ").strip()
            log.warning(
                f"[mimo_chat] HTTP {resp.status_code} "
                f"model={self.mimo_model} body_len={len(body)}"
            )
            log.debug(f"[mimo_chat] error body: {body[:500]}")
            return ""
        except Exception as e:
            log.error(f"[mimo_chat] error: {type(e).__name__}: {e!r}")
            return ""

    def _guess_mood(self, text: str, strategy: str = "", idle_minutes: float = 0) -> str:
        if idle_minutes > 30:
            return "melancholy"
        if strategy == '毒舌吐槽':
            return "annoyed"
        if strategy == '关心进度':
            return "curious"
        if strategy == '轻微吃醋':
            return "melancholy"
        if strategy == '轻松陪聊':
            return "happy"
        if strategy == '好奇询问':
            return "curious"
        if any(w in text for w in ["傻", "笨", "垃圾", "烂"]):
            return "annoyed"
        if any(w in text for w in ["摸鱼", "偷懒"]):
            return "curious"
        if any(w in text for w in ["哼", "……", "懒得"]):
            return "melancholy"
        return "neutral"
