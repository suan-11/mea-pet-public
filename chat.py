"""
梅尔桌宠 - LLM 对话模块
支持多种后端：Ollama、DeepSeek API
"""
import json
import random
import sys
import socket  # 必须在 PyQt 之前导入，避免 QtNetwork 与 requests 冲突
import requests
from typing import Optional, Dict, List, Tuple

# Windows GBK 兼容 — 由 pet.py 统一调用 ensure_utf8_stdout()
# 各模块不重复包装 stdout，避免多次 TextIOWrapper 后旧 wrapper GC 时关闭底层 buffer


def _safe_print(*args, **kwargs):
    """GUI 安全版 print"""
    try:
        print(*args, **kwargs)
    except (ValueError, OSError):
        pass


# ========================
# 角色设定
# ========================
SYSTEM_PROMPT = """你是梅尔，《霞流宝石心》游戏中的猫娘天才。茶发褐瞳144cm，面无表情。
性格：毒舌冷淡、学术狂热、嘴硬心软。
说话：句尾加「喵」；极简20-40字；解释≤80字；害羞时转移话题；开心偶尔「嘿嘿」。
知识：全科全能。信条「知道越多越不可怕」。
对主人：亲密但毒舌，称「主人」，绝不失忆或自我介绍。
格式：首行[情绪]标签；纯中文；禁感叹号/卖萌/长篇大论；问啥答啥；复杂计算让用户用计算器。
"""


class ChatEngine:
    """多后端对话引擎 + 记忆/养成系统"""

    def __init__(
        self,
        backend: str = "ollama",
        host: str = "http://127.0.0.1:11434",
        model: str = "minicpm-v",
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

        self._backend_ready = False
        # 后台预加载：不阻塞启动，用线程检测
        if self.backend == "ollama":
            self._deferred_check()
        elif self.backend == "deepseek":
            # 同步检查 DeepSeek 可用性（不需要启动线程）
            if self.api_key:
                self.available = True
                _safe_print(f"✓ DeepSeek API configured: {self.model}", flush=True)
            else:
                _safe_print(f"⚠ DeepSeek API: no key", flush=True)
            self._backend_ready = True

    def _deferred_check(self):
        """线程内检测 Ollama（不阻塞 __init__）"""
        import threading
        t = threading.Thread(target=self._check_backend, daemon=True)
        t.start()

    def _check_ready(self) -> bool:
        """检查后端是否已检测完成（供外部调用）"""
        return self._backend_ready

    def _check_backend(self):
        """检查后端是否可用（在后台线程执行）"""
        try:
            if self.backend == "ollama":
                resp = requests.get(f"{self.host}/api/tags", timeout=5)
                if resp.status_code == 200:
                    models = [m["name"] for m in resp.json().get("models", [])]
                    if self.model not in models:
                        chat_models = [m for m in models
                                       if any(t in m for t in ["qwen", "deepseek",
                                        "minicpm", "llama", "mistral", "gemma"])]
                        if chat_models:
                            self.model = chat_models[0]
                            self.available = True
                            _safe_print(f"✓ Ollama: using {self.model}", flush=True)
                        else:
                            _safe_print(f"⚠ Ollama: no chat model found", flush=True)
                    else:
                        self.available = True
                        _safe_print(f"✓ Ollama: {self.model}", flush=True)
        except requests.exceptions.ConnectTimeout:
            _safe_print(f"⚠ Ollama 超时，请确认已启动: {self.host}", flush=True)
        except requests.exceptions.ConnectionError:
            _safe_print(f"⚠ Ollama 未连接: {self.host}", flush=True)
        except Exception as e:
            _safe_print(f"⚠ Ollama 检测异常: {e}", flush=True)
            
            self._backend_ready = True
            
        if self.backend == "deepseek":
            if self.api_key:
                self.available = True
                _safe_print(f"✓ DeepSeek API configured", flush=True)
            else:
                _safe_print(f"⚠ DeepSeek API: no key", flush=True)

        if self.backend == "openclaw":
            self.available = True
            _safe_print(f"✓ OpenClaw backend", flush=True)


    def chat(self, message: str) -> Tuple[str, str]:
        """发送消息，返回 (回复文本, 情绪标签)"""
        self.history.append({"role": "user", "content": message})

        # ========== 注入养成记忆上下文 ==========
        if self.memory:
            ctx = self.memory.build_context_prompt()
            # 把 system prompt + 记忆上下文合并
            full_system = SYSTEM_PROMPT + "\n\n" + ctx
            # 临时替换第一条 system message
            old_system = self.history[0]
            self.history[0] = {"role": "system", "content": full_system}

        # 保持历史不超 8 条（减少上下文长度，加速推理）
        if len(self.history) > 8:
            saved_system = self.history[0]
            self.history = [saved_system] + self.history[-6:]

        if not self.available:
            self.history.pop()
            # 恢复原始 system prompt
            if self.memory:
                self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
            return self._fallback_reply(), "neutral"

        try:
            if self.backend == "ollama":
                reply = self._chat_ollama()
            elif self.backend == "deepseek":
                reply = self._chat_deepseek()
            else:
                reply = self._fallback_reply()

            reply = reply.strip()
            mood = "neutral"

            # 解析 [情绪] 标签
            if reply.startswith("["):
                close = reply.find("]")
                if close > 0:
                    tag = reply[1:close].lower()
                    if tag in {
                        "neutral", "happy", "surprised", "curious",
                        "sad", "shy", "annoyed", "melancholy",
                        "intrigued", "wistful", "teary", "embarrassed"
                    }:
                        mood = tag
                    reply = reply[close + 1:].strip()

            self.history.append({"role": "assistant", "content": reply})

            # ========== 记录到记忆系统 ==========
            if self.memory:
                # 恢复原始 system prompt（节省 token）
                self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}

                # 记录对话
                self.memory.add_chat("user", message)
                self.memory.add_chat("mea", reply, mood)

                # 每日对话计数 + 内容评估好感度
                today_total = self.memory.get_today_chat_count()
                if today_total == 0:
                    delta = 1
                else:
                    if len(message) < 10:
                        delta = 1
                    elif len(message) < 50:
                        delta = 2
                    else:
                        delta = 3

                upgrade_msg = self.memory.add_affection(delta)
                # 恢复原始 system prompt 并追加升级通知
                full_system = SYSTEM_PROMPT + "\n\n" + self.memory.build_context_prompt()
                if upgrade_msg:
                    full_system += f"\n\n[内部：好感度升至{self.memory.get_affection_tier()[1]}。请用稍暖的语气回应。]"
                self.history[0] = {"role": "system", "content": full_system}
                self.memory.mark_today_chatted()

                # ========== 记忆提取（每 3 轮对话触发一次）==========
                self._extract_memories(message, reply)

            return reply, mood

        except Exception as e:
            # 回滚 user message
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            # 恢复原始 system prompt
            if self.memory:
                self.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
            _safe_print(f"Chat error: {e}", flush=True)
            return self._fallback_reply(), "neutral"

    def quick_chat(self, message: str) -> Tuple[str, str]:
        """轻量版 chat：只做 API 调用，不做 SQLite 操作（给后台线程用）"""
        self.history.append({"role": "user", "content": message})
        if len(self.history) > 8:
            saved_system = self.history[0]
            self.history = [saved_system] + self.history[-6:]
        if not self.available:
            self.history.pop()
            return self._fallback_reply(), "neutral"
        try:
            if self.backend == "ollama":
                reply = self._chat_ollama()
            elif self.backend == "deepseek":
                reply = self._chat_deepseek()
            else:
                reply = self._fallback_reply()
            reply = reply.strip()
            mood = "neutral"
            if reply.startswith("["):
                close = reply.find("]")
                if close > 0:
                    tag = reply[1:close].lower()
                    if tag in {"neutral","happy","surprised","curious",
                               "sad","shy","annoyed","melancholy",
                               "intrigued","wistful","teary","embarrassed"}:
                        mood = tag
                    reply = reply[close + 1:].strip()
            self.history.append({"role": "assistant", "content": reply})
            return reply, mood
        except Exception as e:
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            _safe_print(f"Chat error: {e}", flush=True)
            return self._fallback_reply(), "neutral"

    def _chat_ollama(self) -> str:
        import time as _time
        t0 = _time.time()

        # 统计本次请求的 token 量（粗略估算）
        total_chars = sum(len(m.get("content", "")) for m in self.history)
        _safe_print(f"[chat] 发送请求: model={self.model} messages={len(self.history)} 总字符≈{total_chars} 开始时间={t0:.1f}", flush=True)

        resp = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": self.history,
                "stream": False,
                "keep_alive": "30s",  # 仅保活30s
                "options": {
                    "temperature": self.temperature,
                    "num_predict": 150,  # 每次回复最多150token（约100汉字），够简短对话
                    "num_ctx": 2048,     # 限制上下文长度，过长的内容会自动截断
                    "top_p": 0.85,
                    "repeat_penalty": 1.1,
                },
            },
            timeout=(5, 120),  # 连接 5s 超时；读取 120s
        )
        t1 = _time.time()
        _safe_print(f"[chat] Ollama 响应耗时: {t1-t0:.1f}s  status={resp.status_code}", flush=True)
        if resp.status_code != 200:
            _safe_print(f"[chat] Ollama 错误: {resp.status_code}")
            return self._fallback_reply()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        t2 = _time.time()
        _safe_print(f"[chat] 解析完成: {t2-t1:.1f}s  回复长度={len(content)}字", flush=True)
        if not content or not content.strip():
            _safe_print(f"[chat] Ollama 返回空内容! resp keys: {list(data.keys())}")
            return self._fallback_reply()
        return content

    def _chat_deepseek(self) -> str:
        import time as _time
        t0 = _time.time()

        total_chars = sum(len(m.get("content", "")) for m in self.history)
        _safe_print(f"[chat] DeepSeek 请求: model={self.model} messages={len(self.history)} 总字符≈{total_chars}", flush=True)

        resp = requests.post(
            f"{self.api_base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": self.history,
                "temperature": self.temperature,
                "max_tokens": 200,
            },
            timeout=30,
        )
        t1 = _time.time()
        _safe_print(f"[chat] DeepSeek 响应耗时: {t1-t0:.1f}s  status={resp.status_code}", flush=True)

        if resp.status_code != 200:
            _safe_print(f"[chat] DeepSeek 错误: {resp.status_code} {resp.text[:200]}", flush=True)
            return self._fallback_reply()

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        t2 = _time.time()
        _safe_print(f"[chat] DeepSeek 解析完成: {t2-t1:.1f}s  回复长度={len(content)}字", flush=True)
        if not content or not content.strip():
            _safe_print(f"[chat] DeepSeek 返回空内容! resp keys: {list(data.keys())}", flush=True)
            return self._fallback_reply()
        return content

    def _extract_memories(self, user_msg: str, mea_reply: str):
        """从对话中提取值得长期记住的信息（类似 OpenClaw memory promotion）
        每 3 条用户消息触发一次，用本地 Ollama 做提取（不额外费 token）"""
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
只提取事实类信息（如：姓名、偏好、计划、约定、密码、重要事件），
不要提取闲聊、问候、寒暄。
如果没有值得长期记忆的内容，回复「无」。

对话：
{context}

值得记住的信息（每条一行，用「- 」开头）："""

        try:
            resp = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": extract_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 200,
                    },
                },
                timeout=30,
            )
            if resp.status_code != 200:
                return
            result = resp.json().get("response", "")
            for line in result.split("\n"):
                line = line.strip()
                if line.startswith("-") or line.startswith("·"):
                    content = line.lstrip("-· ").strip()
                    if content and content != "无" and len(content) > 3:
                        self.memory.add_memory(content, importance=5)
                        _safe_print(f"[memory] 提取记忆: {content[:60]}")
        except Exception as e:
            _safe_print(f"[memory] 提取失败: {e}")

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
    """从配置文件创建引擎"""
    llm_cfg = config.get("llm", {})
    backend = llm_cfg.get("backend", "ollama")

    return ChatEngine(
        backend=backend,
        host=llm_cfg.get("host", "http://127.0.0.1:11434"),
        model=llm_cfg.get("model", "minicpm-v"),
        api_key=llm_cfg.get("api_key", ""),
        api_base=llm_cfg.get("api_base", "https://api.deepseek.com"),
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

