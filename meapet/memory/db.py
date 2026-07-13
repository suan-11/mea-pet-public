"""
梅尔桌宠 - 记忆与养成系统 v2
语义检索 · 生命周期管理 · 对话摘要 · CRUD · JSON 导入导出
"""
import sqlite3
import json
import os
import math
import time
import random
import threading
import hashlib
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from meapet.log import get_color_logger

log = get_color_logger("memory")

from meapet.paths import project_path

DB_PATH = project_path("mea_memory.db")
SCHEMA_VERSION = 2
VECTOR_DIM = 1024

# ========================
# 好感度系统配置
# ========================
AFFECTION_GAIN_PER_CHAT = 1
AFFECTION_MAX = 100
AFFECTION_MIN = 0
AFFECTION_DAILY_CAP = 15

AFFECTION_TIERS = [
    (0,   "陌生人",    "……你是谁？别靠近我喵。"),
    (10,  "认识",      "嗯，记得你。有事快说喵。"),
    (30,  "熟人",      "又来啦。真是闲得慌喵。"),
    (50,  "朋友",      "哼，才不是特意等你的喵。"),
    (70,  "好朋友",    "……其实，和你聊天也不算太讨厌喵。"),
    (85,  "亲密",      "你来的话，我……稍微有点开心喵。"),
    (95,  "挚友",      "你是我少数不讨厌的人类喵。"),
]

MOODS = ["平静", "开心", "忧郁", "烦躁", "困倦", "期待"]

# ========================
# 生命周期配置
# ========================
DECAY_DAYS = 7
DECAY_FLOOR_FACT = 1
DECAY_FLOOR_SUMMARY = 2
CONSOLIDATION_SIMILARITY = 0.85
PRUNE_DAYS = 30
PRUNE_IMPORTANCE_FLOOR = 1
SUMMARIZE_EVERY_N = 20
SUMMARY_CHAT_LIMIT = 20


def _trigram_hash(tri: str) -> int:
    h = 0
    for c in tri:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h % VECTOR_DIM


def _compute_embedding(text: str) -> List[Tuple[int, float]]:
    if not text:
        return []
    freq = {}
    padded = "  " + text + "  "
    for i in range(len(padded) - 2):
        tri = padded[i:i + 3]
        idx = _trigram_hash(tri)
        freq[idx] = freq.get(idx, 0) + 1
    if not freq:
        return []
    norm = math.sqrt(sum(v * v for v in freq.values()))
    if norm == 0:
        return []
    return sorted((k, v / norm) for k, v in freq.items())


def _cosine_similarity_sparse(
    a: List[Tuple[int, float]],
    b: List[Tuple[int, float]],
) -> float:
    if not a or not b:
        return 0.0
    i = j = 0
    dot = 0.0
    while i < len(a) and j < len(b):
        if a[i][0] < b[j][0]:
            i += 1
        elif a[i][0] > b[j][0]:
            j += 1
        else:
            dot += a[i][1] * b[j][1]
            i += 1
            j += 1
    return dot


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class MeaMemory:
    """梅尔的持久化记忆系统 v2"""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error:
            pass
        self._lock = threading.RLock()
        self._init_tables()
        self._migrate_schema()
        self._ensure_defaults()

    # ── 私有：加锁执行 ──
    def _write(self, func, *args, **kwargs):
        with self._lock:
            return func(*args, **kwargs)

    def _init_tables(self):
        """建表"""
        c = self.conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS memory_schema_version (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mood TEXT DEFAULT 'neutral',
                timestamp REAL NOT NULL,
                summarized INTEGER DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS mea_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS master_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated REAL NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                data TEXT DEFAULT '{}',
                timestamp REAL NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 1,
                source TEXT DEFAULT '',
                created REAL NOT NULL,
                last_recalled REAL,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                memory_type TEXT DEFAULT 'fact',
                embedding TEXT DEFAULT '',
                decay_factor REAL DEFAULT 1.0,
                updated REAL DEFAULT 0,
                access_count INTEGER DEFAULT 1,
                source_ids TEXT DEFAULT '[]'
            )
        """)

        self.conn.commit()
        log.debug("[DB] 数据表初始化完成")

    def _migrate_schema(self):
        """安全迁移：新增列或表结构变更"""
        c = self.conn.cursor()
        c.execute("SELECT version FROM memory_schema_version ORDER BY version DESC LIMIT 1")
        row = c.fetchone()
        current_version = row["version"] if row else 0

        if current_version >= SCHEMA_VERSION:
            return
        log.debug(f"[DB] 开始数据库迁移：{current_version} → {SCHEMA_VERSION}")

        # 迁移 1: 为旧 memories 表补充新列
        if current_version < 1:
            _add_cols = {
                "tags": "TEXT DEFAULT '[]'",
                "metadata": "TEXT DEFAULT '{}'",
                "memory_type": "TEXT DEFAULT 'fact'",
                "embedding": "TEXT DEFAULT ''",
                "decay_factor": "REAL DEFAULT 1.0",
                "updated": "REAL DEFAULT 0",
                "access_count": "INTEGER DEFAULT 1",
                "source_ids": "TEXT DEFAULT '[]'",
            }
            for col, dtype in _add_cols.items():
                try:
                    c.execute(f"ALTER TABLE memories ADD COLUMN {col} {dtype}")
                except sqlite3.OperationalError:
                    pass

        # 迁移 2: 为旧 chat_history 补充 summarized 列
        if current_version < 2:
            try:
                c.execute("ALTER TABLE chat_history ADD COLUMN summarized INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

        # 为没有 embedding 的记忆计算 embedding
        c.execute("SELECT id, content, embedding FROM memories WHERE embedding IS NULL OR embedding = ''")
        rows = c.fetchall()
        for row in rows:
            emb = _compute_embedding(row["content"])
            emb_json = json.dumps(emb, ensure_ascii=False)
            c.execute("UPDATE memories SET embedding = ? WHERE id = ?", (emb_json, row["id"]))
        if rows:
            log.debug(f"[DB] 迁移中补齐了 {len(rows)} 条记忆的 embedding")

        # 更新 schema version
        c.execute(
            "INSERT OR REPLACE INTO memory_schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, time.time()),
        )
        self.conn.commit()
        log.debug(f"[DB] 数据库迁移完成 → v{SCHEMA_VERSION}")

    def _ensure_defaults(self):
        """初始化默认状态"""
        c = self.conn.cursor()
        defaults = {
            "affection": "5",
            "mood": "平静",
            "mood_updated": str(time.time()),
            "last_chat": str(time.time()),
            "total_chats": "0",
            "total_days": "0",
            "first_met": str(time.time()),
            "nickname": "",
            "master_name": "主人",
            "messages_since_summary": "0",
        }
        for key, val in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO mea_state (key, value) VALUES (?, ?)",
                (key, val),
            )
        self.conn.commit()
        log.debug("[DB] 默认状态初始化完成")

    # ========================
    # 状态读写
    # ========================
    def _get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT value FROM mea_state WHERE key = ?", (key,))
            row = c.fetchone()
            return row["value"] if row else default

    def _set_state(self, key: str, value: str):
        with self._lock:
            self._set_state_unlocked(key, value)

    def _set_state_unlocked(self, key: str, value: str):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO mea_state (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()
        log.debug(f"[DB] 写入状态 {key}={value}")

    def get_affection(self) -> int:
        return int(self._get_state("affection", "5"))

    def get_affection_tier(self) -> Tuple[int, str, str]:
        aff = self.get_affection()
        tier = AFFECTION_TIERS[0]
        for t in AFFECTION_TIERS:
            if aff >= t[0]:
                tier = t
        return tier

    def add_affection(self, delta: int = 1):
        today = datetime.now().strftime("%Y-%m-%d")
        gained_key = f"affection_gained_{today}"
        with self._lock:
            gained_today = int(self._get_state(gained_key, "0"))
            if gained_today >= AFFECTION_DAILY_CAP:
                log.debug(f"[DB] 好感度已达每日上限，跳过")
                return None
            actual = min(delta, AFFECTION_DAILY_CAP - gained_today)
            current = int(self._get_state("affection", "5"))
            new = max(AFFECTION_MIN, min(AFFECTION_MAX, current + actual))
            old_tier = self._get_tier_for(current)[0]
            if new == current:
                return None
            self._set_state_unlocked("affection", str(new))
            self._set_state_unlocked(gained_key, str(gained_today + actual))
            new_tier = self._get_tier_for(new)
            upgraded = new_tier[0] > old_tier
            upgrade_line = new_tier[2] if upgraded else None
            log.debug(f"[DB] 好感度变更 {current}→{new}，今日已获得 {gained_today + actual}/{AFFECTION_DAILY_CAP}")
        if upgraded:
            self.add_event("milestone", f"好感度升级：{new_tier[1]}（{current}→{new}）")
            return upgrade_line
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
            old = self._get_state("mood", "平静")
            self._set_state_unlocked("mood", mood)
            self._set_state_unlocked("mood_updated", str(time.time()))
            log.debug(f"[DB] 心情变更 {old}→{mood}")

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
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO chat_history (role, content, mood, timestamp) VALUES (?, ?, ?, ?)",
                (role, content, mood, time.time()),
            )
            self.conn.commit()
        self._set_state("last_chat", str(time.time()))
        if role == "mea":
            total = self.get_total_chats()
            self._set_state("total_chats", str(total + 1))
        first_met = self.get_first_met()
        days = int((time.time() - first_met) / 86400) + 1
        old_days = self.get_total_days()
        if days > old_days:
            self._set_state("total_days", str(days))
        log.debug(f"[DB] 添加聊天记录 role={role} len={len(content)}")

    def get_recent_chats(self, limit: int = 20, exclude_summarized: bool = True) -> List[Dict]:
        with self._lock:
            c = self.conn.cursor()
            if exclude_summarized:
                c.execute(
                    "SELECT role, content, mood, timestamp FROM chat_history "
                    "WHERE summarized = 0 ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            else:
                c.execute(
                    "SELECT role, content, mood, timestamp FROM chat_history "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = c.fetchall()
        result = []
        for r in reversed(rows):
            result.append({
                "role": r["role"],
                "content": r["content"],
                "mood": r["mood"],
            })
        return result

    def get_recent_chat_count(self, hours: float = 24) -> int:
        with self._lock:
            c = self.conn.cursor()
            since = time.time() - hours * 3600
            c.execute("SELECT COUNT(*) FROM chat_history WHERE timestamp >= ?", (since,))
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
                (event_type, description, json.dumps(data or {}, ensure_ascii=False), time.time()),
            )
            self.conn.commit()
        log.debug(f"[DB] 添加事件 {event_type}: {description}")

    def get_recent_events(self, limit: int = 10) -> List[Dict]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in c.fetchall()]

    # ========================
    # 记忆 CRUD
    # ========================
    def create_memory(
        self,
        content: str,
        importance: int = 1,
        memory_type: str = "fact",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_ids: Optional[List[int]] = None,
        decay_factor: float = 1.0,
    ) -> int:
        emb = _compute_embedding(content)
        now = time.time()
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                """INSERT INTO memories
                   (content, importance, memory_type, tags, metadata, embedding,
                    decay_factor, created, updated, last_recalled, access_count, source_ids)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    content,
                    importance,
                    memory_type,
                    json.dumps(tags or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps(emb, ensure_ascii=False),
                    decay_factor,
                    now,
                    now,
                    now,
                    1,
                    json.dumps(source_ids or [], ensure_ascii=False),
                ),
            )
            self.conn.commit()
            log.debug(f"[DB] 创建记忆 id={c.lastrowid} type={memory_type} imp={importance}")
            return c.lastrowid

    def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = c.fetchone()
            if not row:
                log.debug(f"[DB] 获取记忆 id={memory_id} → 未找到")
                return None
            d = self._row_to_memory_dict(row)
            log.debug(f"[DB] 获取记忆 id={memory_id} → {d.get('memory_type', '?')} imp={d.get('importance')}")
            return d

    def _row_to_memory_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for field in ("tags", "metadata", "embedding", "source_ids"):
            if isinstance(d.get(field), str) and d[field]:
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def update_memory(self, memory_id: int, **kwargs) -> bool:
        allowed = {
            "content", "importance", "memory_type", "tags",
            "metadata", "decay_factor", "source_ids",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated"] = time.time()
        if "content" in updates:
            updates["embedding"] = json.dumps(
                _compute_embedding(updates["content"]), ensure_ascii=False,
            )
        # Serialize complex fields
        for field in ("tags", "metadata", "source_ids"):
            if field in updates and not isinstance(updates[field], str):
                updates[field] = json.dumps(updates[field], ensure_ascii=False)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [memory_id]
        with self._lock:
            c = self.conn.cursor()
            c.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
            self.conn.commit()
            ok = c.rowcount > 0
            log.debug(f"[DB] 更新记忆 id={memory_id} → {'成功' if ok else '未找到'} fields={list(updates.keys())}")
            return ok

    def delete_memory(self, memory_id: int) -> bool:
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self.conn.commit()
            ok = c.rowcount > 0
            log.debug(f"[DB] 删除记忆 id={memory_id} → {'成功' if ok else '未找到'}")
            return ok

    def list_memories(
        self,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created",
        memory_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        include_archived: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        params = []
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        if tags:
            placeholders = " OR ".join("tags LIKE ?" for _ in tags)
            conditions.append(f"({placeholders})")
            params.extend([f'%"{t}"%' for t in tags])
        if sort_by not in ("created", "updated", "importance", "last_recalled"):
            sort_by = "created"
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        count_sql = f"SELECT COUNT(*) FROM memories{where}"
        with self._lock:
            c = self.conn.cursor()
            c.execute(count_sql, params)
            total = c.fetchone()[0]
            offset = (page - 1) * page_size
            c.execute(
                f"SELECT * FROM memories{where} ORDER BY {sort_by} DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            )
            rows = [self._row_to_memory_dict(r) for r in c.fetchall()]
        log.debug(f"[DB] 列出记忆 page={page} size={page_size} total={total} 返回={len(rows)}条")
        return rows, total

    def get_memories_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM memories WHERE tags LIKE ? ORDER BY importance DESC", (f'%"{tag}"%',))
            return [self._row_to_memory_dict(r) for r in c.fetchall()]

    # ========================
    # 旧 API 兼容（委托给新方法）
    # ========================
    def add_memory(self, content: str, importance: int = 1, source: str = ""):
        self.create_memory(
            content=content,
            importance=importance,
            memory_type="fact",
            metadata={"source": source} if source else None,
        )
        log.debug(f"[DB] add_memory（旧API）content='{content[:40]}' imp={importance}")

    def get_important_memories(self, limit: int = 10) -> List[str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT content FROM memories ORDER BY importance DESC, last_recalled DESC LIMIT ?",
                (limit,),
            )
            rows = [r["content"] for r in c.fetchall()]
        log.debug(f"[DB] 获取重要记忆：返回 {len(rows)} 条")
        return rows

    # ========================
    # 主人信息
    # ========================
    def set_master_info(self, key: str, value: str):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO master_info (key, value, updated) VALUES (?, ?, ?)",
                (key, value, time.time()),
            )
            self.conn.commit()
        log.debug(f"[DB] 设置主人信息 {key}={value}")

    def get_master_info(self, key: str) -> Optional[str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT value FROM master_info WHERE key = ?", (key,))
            row = c.fetchone()
            return row["value"] if row else None

    def get_all_master_info(self) -> Dict[str, str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT key, value FROM master_info")
            return {r["key"]: r["value"] for r in c.fetchall()}

    # ========================
    # 语义检索
    # ========================
    def search_memories(
        self,
        query: str,
        limit: int = 7,
        memory_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        query_emb = _compute_embedding(query)
        conditions = []
        params = []
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        if tags:
            placeholders = " OR ".join("tags LIKE ?" for _ in tags)
            conditions.append(f"({placeholders})")
            params.extend([f'%"{t}"%' for t in tags])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._lock:
            c = self.conn.cursor()
            c.execute(f"SELECT * FROM memories{where}", params)
            rows = [self._row_to_memory_dict(r) for r in c.fetchall()]

        if not query_emb or not rows:
            rows.sort(key=lambda r: (r["importance"], r.get("last_recalled") or 0), reverse=True)
            return rows[:limit]

        scored = []
        for r in rows:
            stored_emb = r.get("embedding") or []
            if isinstance(stored_emb, str):
                try:
                    stored_emb = json.loads(stored_emb) if stored_emb else []
                except (json.JSONDecodeError, TypeError):
                    stored_emb = []
            sim = _cosine_similarity_sparse(query_emb, stored_emb)
            days_since = (time.time() - (r.get("last_recalled") or r["created"])) / 86400
            recency_bonus = max(0.3, 1.0 - days_since * 0.02)
            score = sim * (r["importance"] / 5.0) * r.get("decay_factor", 1.0) * recency_bonus
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = [r for _, r in scored[:limit]]
        log.debug(f"[DB] 语义检索 query='{query[:40]}' → {len(result)}条 (共{len(rows)}候选)")
        return result

    # ========================
    # 生命周期管理
    # ========================
    def lifecycle_maintenance(self):
        now = time.time()
        log.debug("[DB] 开始生命周期维护")

        # 1. 重要性衰减
        decay_count = 0
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT id, importance, memory_type, last_recalled, decay_factor FROM memories")
            for row in c.fetchall():
                lr = row["last_recalled"] or 0
                days_since = (now - lr) / 86400
                if days_since >= DECAY_DAYS and row["decay_factor"] > 0:
                    ticks = int(days_since / DECAY_DAYS)
                    if ticks > 0:
                        floor = DECAY_FLOOR_SUMMARY if row["memory_type"] == "summary" else DECAY_FLOOR_FACT
                        new_imp = max(floor, row["importance"] - ticks)
                        if new_imp != row["importance"]:
                            c.execute("UPDATE memories SET importance = ?, updated = ? WHERE id = ?",
                                      (new_imp, now, row["id"]))
                            decay_count += 1
        if decay_count:
            log.debug(f"[DB] 生命周期：{decay_count} 条记忆重要性衰减")

        # 2. 合并相似记忆
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT id, content, embedding, importance, tags, metadata, access_count FROM memories ORDER BY importance DESC")
            all_mems = [dict(r) for r in c.fetchall()]
        merged_ids = set()
        merge_count = 0
        for i in range(len(all_mems)):
            if all_mems[i]["id"] in merged_ids:
                continue
            for j in range(i + 1, len(all_mems)):
                if all_mems[j]["id"] in merged_ids:
                    continue
                emb_i = self._parse_emb(all_mems[i].get("embedding"))
                emb_j = self._parse_emb(all_mems[j].get("embedding"))
                sim = _cosine_similarity_sparse(emb_i, emb_j)
                if sim >= CONSOLIDATION_SIMILARITY:
                    target = all_mems[i]
                    victim = all_mems[j]
                    tags_i = self._parse_json_list(target.get("tags"))
                    tags_j = self._parse_json_list(victim.get("tags"))
                    merged_tags = list(set(tags_i + tags_j))
                    meta_i = self._parse_json_dict(target.get("metadata"))
                    meta_j = self._parse_json_dict(victim.get("metadata"))
                    merged_meta = {**meta_j, **meta_i}
                    merged_access = (target.get("access_count") or 1) + (victim.get("access_count") or 1)
                    merged_imp = max(target["importance"], victim["importance"])
                    merge_count += 1
                    with self._lock:
                        c = self.conn.cursor()
                        c.execute(
                            "UPDATE memories SET tags=?, metadata=?, access_count=?, importance=?, updated=? WHERE id=?",
                            (
                                json.dumps(merged_tags, ensure_ascii=False),
                                json.dumps(merged_meta, ensure_ascii=False),
                                merged_access,
                                merged_imp,
                                now,
                                target["id"],
                            ),
                        )
                        c.execute("DELETE FROM memories WHERE id=?", (victim["id"],))
                        self.conn.commit()
                    merged_ids.add(victim["id"])
        if merge_count:
            log.debug(f"[DB] 生命周期：合并了 {merge_count} 组相似记忆")

        # 3. 清理过期低重要性记忆
        prune_count = 0
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT id, memory_type, importance, last_recalled FROM memories "
                "WHERE importance <= ? AND memory_type NOT IN ('summary', 'milestone')",
                (PRUNE_IMPORTANCE_FLOOR,),
            )
            for row in c.fetchall():
                lr = row["last_recalled"] or 0
                if (now - lr) / 86400 >= PRUNE_DAYS:
                    c.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
                    prune_count += 1
            self.conn.commit()
        if prune_count:
            log.debug(f"[DB] 生命周期：清理了 {prune_count} 条过期记忆")

    def _parse_emb(self, emb):
        if not emb:
            return []
        if isinstance(emb, list):
            return emb
        if isinstance(emb, str):
            try:
                return json.loads(emb) if emb else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def _parse_json_list(self, val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val) if val else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def _parse_json_dict(self, val):
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val) if val else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    # ========================
    # 对话摘要
    # ========================
    def check_summarization_trigger(self) -> bool:
        """每 SUMMARIZE_EVERY_N 条消息触发一次"""
        count = int(self._get_state("messages_since_summary", "0"))
        triggered = count >= SUMMARIZE_EVERY_N
        if triggered:
            log.debug(f"[DB] 摘要触发器触发 count={count}")
        return triggered

    def increment_message_counter(self):
        count = int(self._get_state("messages_since_summary", "0"))
        self._set_state("messages_since_summary", str(count + 1))
        log.debug(f"[DB] 消息计数器 {count}→{count+1}")

    def reset_summarization_counter(self):
        self._set_state("messages_since_summary", "0")

    def prepare_summarization_context(self, limit: int = SUMMARY_CHAT_LIMIT) -> Tuple[Optional[List[Dict]], Optional[List[int]]]:
        """返回 (对话列表, id列表) 用于 LLM 摘要。如果不够长则返回 None"""
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT id, role, content FROM chat_history WHERE summarized = 0 ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
        if len(rows) < 4:
            log.debug(f"[DB] 准备摘要上下文：未摘要消息仅 {len(rows)} 条，跳过")
            return None, None
        rows.reverse()
        chats = []
        ids = []
        for r in rows:
            chats.append({"role": r["role"], "content": r["content"]})
            ids.append(r["id"])
        log.debug(f"[DB] 准备摘要上下文：{len(ids)} 条消息 id={ids}")
        return chats, ids

    def store_summary(self, summary_text: str, source_ids: List[int], importance: int = 3):
        """将 LLM 生成的摘要存为 memory"""
        mem_id = self.create_memory(
            content=summary_text,
            importance=importance,
            memory_type="summary",
            tags=["auto-summary"],
            source_ids=source_ids,
            decay_factor=0.5,
        )
        # 标记已摘要
        with self._lock:
            c = self.conn.cursor()
            for sid in source_ids:
                c.execute("UPDATE chat_history SET summarized = 1 WHERE id = ?", (sid,))
            self.conn.commit()
        self.reset_summarization_counter()
        log.debug(f"[DB] 存储摘要 memory_id={mem_id} source_ids={source_ids}")

    # ========================
    # JSON 导入导出
    # ========================
    def export_to_json(self, filepath: Optional[str] = None) -> dict:
        data = {
            "version": 2,
            "exported_at": time.time(),
            "memories": [],
            "master_info": self.get_all_master_info(),
            "events": [],
            "state": {},
        }
        transient_keys = {
            "affection_gained_", "chatted_", "messages_since_summary",
        }
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM memories ORDER BY id")
            for row in c.fetchall():
                data["memories"].append(self._row_to_memory_dict(row))
            c.execute("SELECT * FROM events ORDER BY id")
            data["events"] = [dict(r) for r in c.fetchall()]
            c.execute("SELECT key, value FROM mea_state")
            for r in c.fetchall():
                key = r["key"]
                if not any(key.startswith(t) for t in transient_keys):
                    data["state"][key] = r["value"]
        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        log.debug(f"[DB] 导出 JSON：{len(data['memories'])} 条记忆，{len(data['events'])} 条事件")
        return data

    def import_from_json(self, source: Any, merge: bool = False) -> int:
        if isinstance(source, str):
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif isinstance(source, dict):
            data = source
        else:
            raise TypeError("source must be file path (str) or dict")

        if not merge:
            with self._lock:
                c = self.conn.cursor()
                c.execute("DELETE FROM memories")
                self.conn.commit()

        existing_hashes = set()
        if merge:
            with self._lock:
                c = self.conn.cursor()
                c.execute("SELECT content FROM memories")
                for r in c.fetchall():
                    existing_hashes.add(_content_hash(r["content"]))

        imported = 0
        for mem in data.get("memories", []):
            if merge and _content_hash(mem.get("content", "")) in existing_hashes:
                continue
            self.create_memory(
                content=mem.get("content", ""),
                importance=mem.get("importance", 1),
                memory_type=mem.get("memory_type", "fact"),
                tags=mem.get("tags", []),
                metadata=mem.get("metadata", {}),
                source_ids=mem.get("source_ids", []),
                decay_factor=mem.get("decay_factor", 1.0),
            )
            imported += 1

        if "master_info" in data:
            for key, value in data["master_info"].items():
                if isinstance(value, str):
                    self.set_master_info(key, value)

        if data.get("state"):
            preserved_keys = {
                "affection", "mood", "nickname", "master_name",
                "total_chats", "total_days", "first_met",
            }
            for key, value in data["state"].items():
                if key in preserved_keys:
                    self._set_state(key, str(value))

        log.debug(f"[DB] 导入 JSON：{imported} 条（merge={merge}）")
        return imported

    # ========================
    # 综合：LLM 上下文构建（语义版）
    # ========================
    def build_context_prompt(self, current_query: str = "") -> str:
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

        # 语义检索：用当前查询或最近对话内容
        search_query = current_query
        if not search_query:
            recent = self.get_recent_chats(2, exclude_summarized=True)
            if recent:
                search_query = " ".join(c["content"] for c in recent[:2])

        relevant = self.search_memories(query=search_query, limit=7)
        if relevant:
            lines.append("")
            lines.append("## 你记得的事情")
            for m in relevant:
                mtype = m.get("memory_type", "fact")
                tags = m.get("tags") or []
                tag_str = f"[{', '.join(tags)}] " if tags else ""
                prefix = {"summary": "📖 ", "fact": "  "}.get(mtype, "  ")
                lines.append(f"- {prefix}{tag_str}{m['content']}")

        # 最近未摘要对话
        recent = self.get_recent_chats(4, exclude_summarized=True)
        if recent:
            lines.append("")
            lines.append("## 最近对话（参考语境）")
            for chat in recent:
                if chat["role"] == "user":
                    lines.append(f"主人（{master_name}）：{chat['content'][:80]}")
                else:
                    lines.append(f"你：{chat['content'][:80]}")

        result = "\n".join(lines)
        log.debug(f"[DB] 构建上下文提示词完成，共 {len(lines)} 行")
        return result

    # ========================
    # 每日维护
    # ========================
    def daily_maintenance(self):
        mood_updated = float(self._get_state("mood_updated", "0"))
        if time.time() - mood_updated > 14400:
            if random.random() < 0.3:
                new_mood = random.choice(["平静", "开心", "忧郁", "烦躁", "困倦", "期待"])
                self.set_mood(new_mood)
        today = datetime.now().strftime("%Y-%m-%d")
        today_key = f"chatted_{today}"
        if self._get_state(today_key) == "":
            self._set_state(today_key, "0")
        # 运行生命周期维护
        self.lifecycle_maintenance()
        log.debug("[DB] 每日维护完成")

    def mark_today_chatted(self):
        today = datetime.now().strftime("%Y-%m-%d")
        today_key = f"chatted_{today}"
        count = int(self._get_state(today_key, "0"))
        self._set_state(today_key, str(count + 1))
        log.debug(f"[DB] 标记今日已聊天 count={count + 1}")

    def get_today_chat_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        count = int(self._get_state(f"chatted_{today}", "0"))
        return count

    def close(self):
        with self._lock:
            self.conn.close()
        log.debug("[DB] 数据库连接已关闭")

    def reset_all(self):
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM chat_history")
            c.execute("DELETE FROM mea_state")
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM memories")
            self.conn.commit()
        self._ensure_defaults()
        log.debug("[DB] 所有数据已重置")
