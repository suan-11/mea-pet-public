"""
梅尔桌宠 - 记忆与养成系统
基于 SQLite 存储对话历史、好感度、心情状态
实现类似"养成游戏"的持久化记忆
"""
import sqlite3
import json
import os
import time
import random
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mea_memory.db")


# ========================
# 好感度系统配置
# ========================
AFFECTION_GAIN_PER_CHAT = 1          # 每次对话 +1
AFFECTION_MAX = 100
AFFECTION_MIN = 90
AFFECTION_DAILY_CAP = 15            # 每天好感度获取上限

# 好感度等级
AFFECTION_TIERS = [
    (0,   "陌生人",    "……你是谁？别靠近我喵。"),
    (10,  "认识",      "嗯，记得你。有事快说喵。"),
    (30,  "熟人",      "又来啦。真是闲得慌喵。"),
    (50,  "朋友",      "哼，才不是特意等你的喵。"),
    (70,  "好朋友",    "……其实，和你聊天也不算太讨厌喵。"),
    (85,  "亲密",      "你来的话，我……稍微有点开心喵。"),
    (95,  "挚友",      "你是我少数不讨厌的人类喵。"),
]

# 心情状态（持久化）
MOODS = ["平静", "开心", "忧郁", "烦躁", "困倦", "期待"]


class MeaMemory:
    """梅尔的持久化记忆系统"""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_tables()
        self._ensure_defaults()

    # ── 私有：加锁执行 ──
    def _write(self, func, *args, **kwargs):
        """带锁的数据库写操作"""
        with self._lock:
            return func(*args, **kwargs)

    def _init_tables(self):
        """建表"""
        c = self.conn.cursor()

        # 对话历史
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,           -- 'user' / 'mea'
                content TEXT NOT NULL,
                mood TEXT DEFAULT 'neutral',  -- 梅尔当时的情绪
                timestamp REAL NOT NULL
            )
        """)

        # 状态表（单行）
        c.execute("""
            CREATE TABLE IF NOT EXISTS mea_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # 主人信息
        c.execute("""
            CREATE TABLE IF NOT EXISTS master_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated REAL NOT NULL
            )
        """)

        # 事件日志（里程碑）
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,     -- 'affection_up' / 'milestone' / 'first_chat' / 'gift' / 'nickname'
                description TEXT NOT NULL,
                data TEXT DEFAULT '{}',
                timestamp REAL NOT NULL
            )
        """)

        # 记忆片段（梅尔记得的重要事情）
        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,         -- 简短记忆内容
                importance INTEGER DEFAULT 1,  -- 1-5 重要性
                source TEXT DEFAULT '',        -- 来源对话id
                created REAL NOT NULL,
                last_recalled REAL
            )
        """)

        self.conn.commit()

    def _ensure_defaults(self):
        """初始化默认状态"""
        c = self.conn.cursor()

        defaults = {
            "affection": "90",             # 好感度（初始90=亲密）
            "mood": "平静",                # 当前心情
            "mood_updated": str(time.time()),
            "last_chat": str(time.time()), # 最后对话时间
            "total_chats": "0",            # 总对话次数
            "total_days": "0",             # 相识天数（非连续）
            "first_met": str(time.time()), # 初次见面时间
            "nickname": "",                # 主人给梅尔的昵称
            "master_name": "主人",         # 梅尔怎么叫主人
        }

        for key, val in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO mea_state (key, value) VALUES (?, ?)",
                (key, val)
            )

        self.conn.commit()

    # ========================
    # 状态读写
    # ========================
    def _get_state(self, key: str, default: str = "") -> str:
        c = self.conn.cursor()
        c.execute("SELECT value FROM mea_state WHERE key = ?", (key,))
        row = c.fetchone()
        return row["value"] if row else default

    def _set_state(self, key: str, value: str):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO mea_state (key, value) VALUES (?, ?)",
                (key, value)
            )
            self.conn.commit()

    def get_affection(self) -> int:
        return int(self._get_state("affection", "5"))

    def get_affection_tier(self) -> Tuple[int, str, str]:
        """返回 (等级值, 等级名, 等级描述)"""
        aff = self.get_affection()
        tier = AFFECTION_TIERS[0]
        for t in AFFECTION_TIERS:
            if aff >= t[0]:
                tier = t
        return tier

    def add_affection(self, delta: int = 1):
        """增加好感度（受每日上限约束）"""
        today = datetime.now().strftime("%Y-%m-%d")
        gained_key = f"affection_gained_{today}"
        gained_today = int(self._get_state(gained_key, "0"))
        if gained_today >= AFFECTION_DAILY_CAP:
            return None  # 今日已达上限

        actual = min(delta, AFFECTION_DAILY_CAP - gained_today)
        current = self.get_affection()
        new = max(AFFECTION_MIN, min(AFFECTION_MAX, current + actual))
        old_tier = self.get_affection_tier()[0]

        if new != current:
            with self._lock:
                self._set_state("affection", str(new))
                self._set_state(gained_key, str(gained_today + actual))

            # 检查升级
            new_tier = self._get_tier_for(new)
            if new_tier[0] > old_tier:
                self.add_event(
                    "milestone",
                    f"好感度升级：{new_tier[1]}（{current}→{new}）"
                )
                return new_tier[2]  # 返回升级台词
        return None

    def _get_tier_for(self, affection: int) -> Tuple[int, str, str]:
        tier = AFFECTION_TIERS[0]
        for t in AFFECTION_TIERS:
            if affection >= t[0]:
                tier = t
        return tier

    def get_mood(self) -> str:
        return self._get_state("mood", "平静")

    def set_mood(self, mood: str):
        with self._lock:
            self._set_state("mood", mood)
            self._set_state("mood_updated", str(time.time()))

    def get_last_chat_time(self) -> float:
        return float(self._get_state("last_chat", "0"))

    def get_total_chats(self) -> int:
        return int(self._get_state("total_chats", "0"))

    def get_total_days(self) -> int:
        return int(self._get_state("total_days", "0"))

    def get_first_met(self) -> float:
        return float(self._get_state("first_met", str(time.time())))

    def get_master_name(self) -> str:
        return self._get_state("master_name", "主人")

    def get_nickname(self) -> str:
        return self._get_state("nickname", "")

    # ========================
    # 对话历史
    # ========================
    def add_chat(self, role: str, content: str, mood: str = "neutral"):
        """记录一条对话"""
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO chat_history (role, content, mood, timestamp) VALUES (?, ?, ?, ?)",
                (role, content, mood, time.time())
            )
            self.conn.commit()

        # 更新统计（_set_state 自带锁）
        self._set_state("last_chat", str(time.time()))
        if role == "mea":
            total = self.get_total_chats()
            self._set_state("total_chats", str(total + 1))

        # 检查新的一天
        first_met = self.get_first_met()
        days = int((time.time() - first_met) / 86400) + 1
        old_days = self.get_total_days()
        if days > old_days:
            self._set_state("total_days", str(days))

    def get_recent_chats(self, limit: int = 20) -> List[Dict]:
        """获取最近的对话记录（用于 LLM 上下文）"""
        c = self.conn.cursor()
        c.execute(
            "SELECT role, content, mood, timestamp FROM chat_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = c.fetchall()
        # 反转回时间顺序
        result = []
        for r in reversed(rows):
            result.append({
                "role": r["role"],
                "content": r["content"],
                "mood": r["mood"],
            })
        return result

    def get_recent_chat_count(self, hours: float = 24) -> int:
        """获取最近N小时内的对话数"""
        c = self.conn.cursor()
        since = time.time() - hours * 3600
        c.execute(
            "SELECT COUNT(*) FROM chat_history WHERE timestamp >= ?",
            (since,)
        )
        row = c.fetchone()
        return row[0] if row else 0

    # ========================
    # 事件日志
    # ========================
    def add_event(self, event_type: str, description: str, data: dict = None):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO events (event_type, description, data, timestamp) VALUES (?, ?, ?, ?)",
                (event_type, description, json.dumps(data or {}), time.time())
            )
            self.conn.commit()

    def get_recent_events(self, limit: int = 10) -> List[Dict]:
        c = self.conn.cursor()
        c.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in c.fetchall()]

    # ========================
    # 记忆片段
    # ========================
    def add_memory(self, content: str, importance: int = 1, source: str = ""):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO memories (content, importance, source, created, last_recalled) "
                "VALUES (?, ?, ?, ?, ?)",
                (content, importance, source, time.time(), time.time())
            )
            self.conn.commit()

    def get_important_memories(self, limit: int = 10) -> List[str]:
        """获取最重要的记忆"""
        c = self.conn.cursor()
        c.execute(
            "SELECT content FROM memories ORDER BY importance DESC, last_recalled DESC LIMIT ?",
            (limit,)
        )
        return [r["content"] for r in c.fetchall()]

    # ========================
    # 主人信息
    # ========================
    def set_master_info(self, key: str, value: str):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO master_info (key, value, updated) VALUES (?, ?, ?)",
                (key, value, time.time())
            )
            self.conn.commit()

    def get_master_info(self, key: str) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT value FROM master_info WHERE key = ?", (key,))
        row = c.fetchone()
        return row["value"] if row else None

    def get_all_master_info(self) -> Dict[str, str]:
        c = self.conn.cursor()
        c.execute("SELECT key, value FROM master_info")
        return {r["key"]: r["value"] for r in c.fetchall()}

    # ========================
    # 综合：LLM 上下文构建
    # ========================
    def build_context_prompt(self) -> str:
        """构建注入 system prompt 的记忆上下文"""
        aff = self.get_affection()
        tier = self.get_affection_tier()
        mood = self.get_mood()
        total_chats = self.get_total_chats()
        days = self.get_total_days()
        master_name = self.get_master_name()
        nickname = self.get_nickname()

        lines = []
        lines.append("## 与主人的关系")
        lines.append(f"- 好感度：{aff}/100（{tier[1]}）")
        lines.append(f"- 关系描述：{tier[2]}")
        lines.append(f"- 当前心情：{mood}")
        if nickname:
            lines.append(f"- 梅尔对你的昵称：{nickname}")
        lines.append(f"- 相识天数：{days}天，共对话{total_chats}次")

        # 重要记忆
        important = self.get_important_memories(5)
        if important:
            lines.append("")
            lines.append("## 你记得的重要事情")
            for m in important:
                lines.append(f"- {m}")

        # 最近对话
        recent = self.get_recent_chats(6)
        if recent:
            lines.append("")
            lines.append("## 最近对话（参考语境）")
            for chat in recent:
                if chat["role"] == "user":
                    lines.append(f"主人（{master_name}）：{chat['content'][:60]}")
                else:
                    lines.append(f"你：{chat['content'][:60]}")

        return "\n".join(lines)

    # ========================
    # 每日维护（桌宠启动时调用）
    # ========================
    def daily_maintenance(self):
        """桌宠启动时执行日常维护：心情刷新等"""
        # 心情随机（如果超过4小时没有互动）
        mood_updated = float(self._get_state("mood_updated", "0"))
        if time.time() - mood_updated > 14400:  # 4小时
            if random.random() < 0.3:
                new_mood = random.choice(["平静", "困倦", "烦躁", "期待"])
                self.set_mood(new_mood)

        # 记录今天有没有聊过
        today = datetime.now().strftime("%Y-%m-%d")
        today_key = f"chatted_{today}"
        if self._get_state(today_key) == "":
            self._set_state(today_key, "0")  # 今天还没聊过

    def mark_today_chatted(self):
        today = datetime.now().strftime("%Y-%m-%d")
        today_key = f"chatted_{today}"
        count = int(self._get_state(today_key, "0"))
        self._set_state(today_key, str(count + 1))

    def get_today_chat_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return int(self._get_state(f"chatted_{today}", "0"))

    def close(self):
        self.conn.close()

    # ========================
    # 重置
    # ========================
    def reset_all(self):
        """重置所有数据：记忆、好感度、聊天记录、统计"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM chat_history")
            c.execute("DELETE FROM mea_state")
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM memories")
            self.conn.commit()
        self._ensure_defaults()
