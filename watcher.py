"""
梅尔桌宠 - 屏幕观察模块 v3
三层决策：截图 → 场景摘要 → 策略评估（含搜索/冷落感知） → 回复

支持切换视觉模型（config.json → vision.model）
可用模型：minicpm-v (5.5G, 快) / qwen2.5vl:7b (6GB, 稍慢)

设计参考：Sakura（Rvosy/sakura）的主动搭话 prompt 架构
"""
import sys
import os
import time
import io
from typing import Optional, Tuple
from PIL import ImageGrab
from PyQt5.QtCore import QThread, pyqtSignal


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


class ScreenWatcher(QThread):
    """三层决策 + 冷落感知 + Web 搜索"""

    result_ready = pyqtSignal(str, str)  # (回复文本, 情绪)
    error = pyqtSignal(str)
    silent = pyqtSignal()
    progress = pyqtSignal(str)
    search_request = pyqtSignal(str)  # 请求 Web 搜索（关键词）

    def __init__(self, ollama_host: str = "http://127.0.0.1:11434",
                 vision_model: str = "minicpm-v",
                 chat_model: str = "qwen2.5:7b",
                 idle_minutes: float = 0):
        super().__init__()
        self.host = ollama_host
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.idle_minutes = idle_minutes
        self._stop = False

    def set_idle_minutes(self, minutes: float):
        """外部更新冷落时长"""
        self.idle_minutes = minutes

    def stop(self):
        self._stop = True
        if self.isRunning():
            self.wait(3000)

    def run(self):
        try:
            import requests, base64, re

            # ========== Step 1: 截屏 ==========
            if self._stop:
                return
            self.progress.emit(STAGE_CAPTURE)
            img = ImageGrab.grab()
            ratio = 320 / img.width
            if ratio < 1.0:
                img = img.resize((320, int(img.height * ratio)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            b64 = base64.b64encode(buf.getvalue()).decode()

            # ========== Step 2: 场景摘要 ==========
            if self._stop:
                return
            self.progress.emit(STAGE_SUMMARY)
            resp = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.vision_model,
                    "prompt": SUMMARY_PROMPT,
                    "images": [b64],
                    "stream": False,
                    "options": {"num_predict": 50, "temperature": 0.3},
                },
                timeout=90,
            )
            if self._stop:
                return
            print(f"[watcher] Step2 status: {resp.status_code}, response[:200]: {resp.text[:200]}")
            if resp.status_code != 200:
                self.progress.emit(STAGE_ERROR)
                self.error.emit(f"摘要失败: {resp.status_code}")
                return
            summary = resp.json().get("response", "").strip()
            if not summary:
                summary = "（画面内容无法识别）"

            # ========== Step 3: 策略评估（含冷落感知） ==========
            if self._stop:
                return
            self.progress.emit(STAGE_DECISION)
            decision_prompt = DECISION_PROMPT.format(
                idle_minutes=int(self.idle_minutes),
                summary=summary,
            )
            resp = requests.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": [
                        {"role": "system", "content": "你是决策助手。严格按格式回复。"},
                        {"role": "user", "content": decision_prompt},
                    ],
                    "stream": False,
                    "keep_alive": "2m",
                    "options": {"num_predict": 100, "temperature": 0.3},
                },
                timeout=180,
            )
            if self._stop:
                return
            print(f"[watcher] Step3 status: {resp.status_code}, response[:200]: {resp.text[:200]}")
            decision = ""
            strategy = "毒舌吐槽"
            search_query = ""
            should_speak = False
            if resp.status_code == 200:
                decision = resp.json().get("message", {}).get("content", "").strip()

            # 解析决策
            lines = [l.strip() for l in decision.split('\n') if l.strip()]
            if lines:
                first_line = lines[0].replace('第1行：', '').replace('第1行:', '').strip()
                if first_line == '说' or first_line.startswith('说'):
                    should_speak = True
                if should_speak and len(lines) >= 2:
                    s = lines[1].replace('第2行：', '').replace('第2行:', '').strip()
                    if '关心' in s or '进度' in s: strategy = '关心进度'
                    elif '陪聊' in s or '轻松' in s: strategy = '轻松陪聊'
                    elif '吃醋' in s: strategy = '轻微吃醋'
                    elif '好奇' in s or '询问' in s: strategy = '好奇询问'
                    elif '毒舌' in s or '吐槽' in s: strategy = '毒舌吐槽'
                # 第3行：搜索关键词
                if should_speak and len(lines) >= 3:
                    sq = lines[2].replace('第3行：', '').replace('第3行:', '').strip()
                    if sq and sq not in ('无', '不需要', '否', '不需要搜索', '-'):
                        search_query = sq

            if not should_speak:
                self.progress.emit(STAGE_SILENT)
                self.silent.emit()
                return

            # ========== Step 3.5: Web 搜索（如果需要） ==========
            search_result = ""
            if search_query:
                self.progress.emit(STAGE_SEARCH)
                self.search_request.emit(search_query)
                # 实际搜索由 pet.py 执行并通过 set_search_result 回传
                # 这里等待最多 5 秒
                waited = 0
                while self._search_pending and waited < 50:
                    if self._stop: return
                    time.sleep(0.1)
                    waited += 1
                if self._search_result:
                    search_result = self._search_result
                    self._search_result = ""
                    self._search_pending = True  # 重置标记

            # ========== Step 4: 生成回复 ==========
            if self._stop:
                return
            self.progress.emit(STAGE_ROAST)
            attitude = {
                '毒舌吐槽': '带刺的冷淡傲娇，点破主人摸鱼或犯傻',
                '关心进度': '带一点冷淡的关心，不肉麻，点出具体在做什么',
                '轻松陪聊': '轻松但不热络，保持傲娇，围绕看到的画面内容',
                '轻微吃醋': '带一点酸味但不指责，傲娇地表达在意',
                '好奇询问': '冷淡但真实的好奇，问看到的画面内容',
            }.get(strategy, '冷淡傲娇')

            # 拼接搜索上下文
            search_ctx = f"\n\n关于画面内容的搜索结果：\n{search_result}" if search_result else ""
            # 冷落上下文
            idle_ctx = ""
            if self.idle_minutes > 30:
                idle_ctx = f"\n主人已经{int(self.idle_minutes)}分钟没理你了，语气里可以带一点点落寞但不抱怨。"
            elif self.idle_minutes > 10:
                idle_ctx = f"\n主人{int(self.idle_minutes)}分钟没理你了，可以主动搭话。"

            final_prompt = f"""你是梅尔·艾什礼佩克，毒舌猫娘，144cm，天才但冷淡。
屏幕内容：{summary}
策略：{strategy}，语气：{attitude}{idle_ctx}{search_ctx}

请一句话（不超过40字）直接对主人说话。基于画面真实内容，结尾加「喵」。末尾加一个括号可爱小动作。不要前缀引号Markdown。"""

            resp = requests.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": [
                        {"role": "system", "content": "你是梅尔，猫娘。回复简短自然。"},
                        {"role": "user", "content": final_prompt},
                    ],
                    "stream": False,
                    "keep_alive": "2m",
                    "options": {"num_predict": 100, "temperature": 0.85},
                },
                timeout=180,
            )
            if self._stop:
                return
            print(f"[watcher] Step4 status: {resp.status_code}, response[:200]: {resp.text[:200]}")
            if resp.status_code != 200:
                self.progress.emit(STAGE_ERROR)
                self.error.emit(f"回复失败: {resp.status_code}")
                return

            text = resp.json().get("message", {}).get("content", "").strip()
            if not text:
                text = "……没什么好说的喵。（尾巴轻轻晃了晃）"

            # 后处理
            text = re.sub(r'```[\s\S]*?```', '', text)
            text = re.sub(r'```', '', text)
            text = re.sub(r'^[关于对看][^：]*[：:：]\s*', '', text)
            text = re.sub(r'^(屏幕上|看到|画面里|桌面|这个画面)\S*\s*', '', text)
            text = re.sub(r'^(哦|嗯|呃|啊|好吧|好的)[，,]\s*', '', text)
            text = text.strip()
            if not text:
                text = "……没什么好说的喵。（尾巴轻轻晃了晃）"

            mood = self._guess_mood(text, strategy, self.idle_minutes)
            self.result_ready.emit(text, mood)

        except Exception as e:
            self.progress.emit(STAGE_ERROR)
            self.error.emit(str(e))

    # ---- 搜索回传接口 ----
    _search_pending = True
    _search_result = ""

    def set_search_result(self, result: str):
        """外部（pet.py）回传 Web 搜索结果"""
        self._search_pending = False
        self._search_result = result

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
