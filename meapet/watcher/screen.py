"""
梅尔桌宠 - 屏幕观察模块 v4
一次多模态：截图 → 单次模型调用（说/不说 + 中日双语对白）→ TTS
（旧 parse_decision 仍保留供单测/兼容）

支持切换视觉模型（config.json → vision.model）
可用模型：minicpm-v (5.5G, 快) / qwen2.5vl:7b (6GB, 稍慢) MiMo V2.5（云端，超快）

设计参考：Sakura（Rvosy/sakura）的主动搭话 prompt 架构
"""
import io
from PIL import ImageGrab
from PyQt5.QtCore import QThread, pyqtSignal

from meapet.utils import debug_enabled, redact_text


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


def _debug_log(message: str) -> None:
    """仅在显式调试模式输出可能包含对话或响应体的内容。"""
    if debug_enabled():
        print(redact_text(message))

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
    _debug_log(f"[parse_watch_output] input raw chars={len(raw)}")
    
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
                 mimo_model: str = "mimo-v2.5"):
        super().__init__()
        self.host = ollama_host
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.idle_minutes = idle_minutes
        self.backend = backend
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key
        self.mimo_model = mimo_model
        self._stop = False
        self.last_voice_text = ""

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

    def run(self):
        """截屏后一次模型调用：说/不说 + 中日双语对白。"""
        try:
            import base64
            import re

            self.last_voice_text = ""
            
            print(f"[watcher] thread started, idle_minutes={self.idle_minutes}, backend={self.backend}")
            # ========== 1) 截屏 ==========
            if self._stop:
                return
            self.progress.emit(STAGE_CAPTURE)
            img = ImageGrab.grab()
            print(f"[watcher] screenshot captured: size={img.size}, mode={img.mode}")
            
            import os
            save_dir = "screenshots"                     # 可改为配置项
            os.makedirs(save_dir, exist_ok=True)
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(save_dir, f"screenshot_{timestamp}.png")
            img.save(save_path)
            print(f"[watcher] screenshot saved to {save_path}")
            
            
            ratio = 320 / img.width
            if ratio < 1.0:
                img = img.resize((320, int(img.height * ratio)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            b64 = base64.b64encode(buf.getvalue()).decode()
            print(f"[watcher] image encoded to base64, length={len(b64)}")

            if self._stop:
                return
            self.progress.emit(STAGE_SUMMARY)

            prompt = UNIFIED_WATCH_PROMPT.format(idle_minutes=int(self.idle_minutes))
            raw = ""
            
            print(f"[watcher] calling model: backend={self.backend}, model={self.vision_model if self.backend=='ollama' else self.mimo_model}, prompt_len={len(prompt)}")
            
            

            # ========== 2) 一次多模态 ==========
            if self.backend == "mimo":
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                
                print(f"[watcher] MiMo request: url={self.api_base}/chat/completions, model={self.mimo_model}, max_tokens=2048")
                
                if self.api_key:
                    headers["api-key"] = self.api_key
                resp = self._http_post(
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.mimo_model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是梅尔。严格按用户要求的行格式输出，不要解释。",
                            },
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{b64}"
                                        },
                                    },
                                ],
                            },
                        ],
                        "max_tokens": 2048,
                        "max_completion_tokens": 2048,
                        "temperature": 0.7,
                        "thinking": {"type": "enabled"},
                    },
                    timeout=600,
                )
                
                print(f"[watcher] MiMo response: status={resp.status_code}, elapsed=...")
                
                if self._stop:
                    return
                if resp.status_code == 200:
                    msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
                    rc = (msg.get("reasoning_content") or "").strip()
                    raw = self._mimo_extract_text(msg)
                    print(
                        f"[watcher] one-shot MiMo status=200 "
                        f"content_len={len(raw)} reasoning_len={len(rc)} "
                        f"model={self.mimo_model}"
                    )
                    if rc and not (msg.get("content") or "").strip():
                        print("[watcher] one-shot 使用 reasoning 尾部兜底")
                else:
                    body = (resp.text or "").replace("\n", " ").strip()
                    print(
                        f"[watcher] one-shot MiMo status={resp.status_code} "
                        f"model={self.mimo_model} body_len={len(body)}"
                    )
                    _debug_log(f"[watcher] one-shot error body: {body[:500]}")
                    self.progress.emit(STAGE_ERROR)
                    self.error.emit(f"偷看失败: HTTP {resp.status_code}")
                    return
            else:
                # Ollama 视觉：一张图一次 generate
                resp = self._http_post(
                    f"{self.host}/api/generate",
                    json={
                        "model": self.vision_model,
                        "prompt": prompt,
                        "images": [b64],
                        "stream": False,
                        "options": {"num_predict": 200, "temperature": 0.7},
                    },
                    timeout=600,
                )
                if self._stop:
                    return
                print(f"[watcher] one-shot Ollama status={resp.status_code}")
                if resp.status_code != 200:
                    self.progress.emit(STAGE_ERROR)
                    self.error.emit(f"偷看失败: {resp.status_code}")
                    return
                raw = (resp.json().get("response") or "").strip()
                print(f"[watcher] RAW MODEL OUTPUT ({len(raw)} chars):")
                print(raw)
                
                print(f"[watcher] raw response received, chars={len(raw)}")
                _debug_log(f"[watcher] raw response first 300 chars: {raw[:300]!r}")

            print(f"[watcher] one-shot response chars={len(raw or '')}")
            _debug_log(f"[watcher] one-shot raw: {(raw or '')[:200]!r}")
            should_speak, display, voice, mood, _hint = parse_watch_output(raw)
            print(f"[watcher] parse result: should_speak={should_speak}, mood={mood}, display_len={len(display)}, voice_len={len(voice)}")

            if not should_speak:
                self.progress.emit(STAGE_SILENT)
                print(f"[watcher] decided to stay silent (idle_minutes={self.idle_minutes})")
                self.silent.emit()
                return

            # 轻清洗显示文本
            display = re.sub(r'["\'「」『』`]', '', display or '')
            display = re.sub(r'```', '', display).strip()
            if not display:
                display = "……没什么好说的喵。（尾巴轻轻晃了晃）"
                mood = mood or "neutral"

            # 日语给 TTS；没有则 TTS 侧再回退翻译
            self.last_voice_text = voice or ""
            if voice:
                print(f"[watcher] bilingual voice chars={len(voice)}")
                _debug_log(f"[watcher] bilingual voice_jp={voice[:40]!r}")
            else:
                print("[watcher] 无日语行，TTS 将回退翻译/原文")

            # 情绪兜底
            if not mood or mood == "neutral":
                mood = self._guess_mood(display, "", self.idle_minutes)

            self.progress.emit(STAGE_ROAST)
            self.result_ready.emit(display, mood)

        except Exception as e:
            self.progress.emit(STAGE_ERROR)
            import traceback
            print(f"[watcher] exception in run(): {type(e).__name__}: {e}")
            _debug_log(traceback.format_exc())
            self.error.emit(str(e))

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
            print(f"[watcher] _mimo_chat: sending {len(messages)} messages, max_tokens={max_tokens}")
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
            print(f"[watcher] _mimo_chat response: status={resp.status_code}")
            if resp.status_code == 200:
                msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
                text = self._mimo_extract_text(msg)
                print(f"[watcher] _mimo_chat extracted text length={len(text)}")
                rc = (msg.get("reasoning_content") or "").strip()
                print(
                    f"[watcher] MiMo chat content_len={len(text)} "
                    f"reasoning_len={len(rc)}"
                )
                return text
            body = (resp.text or "").replace("\n", " ").strip()
            print(
                f"[watcher] MiMo chat HTTP {resp.status_code} "
                f"model={self.mimo_model} body_len={len(body)}"
            )
            _debug_log(f"[watcher] MiMo chat error body: {body[:500]}")
            return ""
        except Exception as e:
            print(f"[watcher] MiMo chat error: {type(e).__name__}")
            _debug_log(f"[watcher] MiMo chat exception: {e!r}")
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


if __name__ == "__main__":
    w = ScreenWatcher(idle_minutes=5)
    w.progress.connect(lambda s: print(f"[{s}]"))
    w.result_ready.connect(lambda t, m: print(f"梅尔 [{m}]: {t}"))
    w.silent.connect(lambda: print("(不说话)"))
    w.error.connect(lambda e: print(f"ERROR: {e}"))
    w.search_request.connect(lambda q: print(f"[搜索请求: {q}]"))
    w.start()
    w.wait()
