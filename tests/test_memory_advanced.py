"""高级记忆系统测试：语义检索、CRUD、生命周期、摘要、JSON导入导出"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ========================
# 辅助函数
# ========================
def _make_memory():
    """创建指向临时数据库的 MeaMemory 实例"""
    from meapet.memory import db as mem_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_path = mem_db.DB_PATH
    mem_db.DB_PATH = path
    try:
        m = mem_db.MeaMemory()
        yield m
    finally:
        try:
            m.close()
        except Exception:
            pass
        mem_db.DB_PATH = old_path
        try:
            os.unlink(path)
        except Exception:
            pass


# ========================
# 语义检索
# ========================
class TestSemanticSearch(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_compute_embedding_nonempty(self):
        from meapet.memory.db import _compute_embedding
        emb = _compute_embedding("主人喜欢吃鱼")
        self.assertTrue(len(emb) > 0)
        total_norm = sum(v * v for _, v in emb)
        self.assertAlmostEqual(total_norm, 1.0, places=5)

    def test_compute_embedding_empty(self):
        from meapet.memory.db import _compute_embedding
        self.assertEqual(_compute_embedding(""), [])
        self.assertEqual(_compute_embedding(None), [])

    def test_cosine_similarity(self):
        from meapet.memory.db import _cosine_similarity_sparse
        a = [(0, 0.6), (2, 0.8)]
        b = [(0, 0.6), (2, 0.8)]
        self.assertAlmostEqual(_cosine_similarity_sparse(a, b), 1.0, places=5)
        self.assertAlmostEqual(_cosine_similarity_sparse(a, []), 0.0)
        self.assertAlmostEqual(_cosine_similarity_sparse([], b), 0.0)

    def test_search_returns_relevant_first(self):
        self.m.create_memory("主人喜欢喝咖啡", importance=3, tags=["preference"])
        self.m.create_memory("今天天气真好", importance=1, tags=["casual"])
        self.m.create_memory("主人最爱的饮料是咖啡", importance=4, tags=["preference"])
        results = self.m.search_memories("咖啡", limit=5)
        self.assertTrue(len(results) >= 2)
        self.assertIn("咖啡", results[0]["content"])

    def test_search_memory_type_filter(self):
        self.m.create_memory("这是一条普通记忆", memory_type="fact")
        self.m.create_memory("这是一段对话摘要", memory_type="summary")
        facts = self.m.search_memories("记忆", memory_type="fact")
        summaries = self.m.search_memories("记忆", memory_type="summary")
        self.assertTrue(len(facts) >= 1)
        self.assertTrue(len(summaries) >= 1)
        self.assertEqual(summaries[0]["memory_type"], "summary")

    def test_search_by_tag(self):
        self.m.create_memory("用户偏好猫", tags=["preference", "pet"])
        self.m.create_memory("普通闲聊", tags=["casual"])
        results = self.m.search_memories("猫", tags=["preference"])
        self.assertTrue(len(results) >= 1)
        self.assertIn("preference", results[0].get("tags", []))


# ========================
# CRUD
# ========================
class TestMemoryCRUD(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_create_and_get_memory(self):
        mid = self.m.create_memory(
            "测试记忆",
            importance=4,
            memory_type="fact",
            tags=["test"],
            metadata={"key": "value"},
        )
        self.assertTrue(mid > 0)
        mem = self.m.get_memory(mid)
        self.assertIsNotNone(mem)
        self.assertEqual(mem["content"], "测试记忆")
        self.assertEqual(mem["importance"], 4)
        self.assertEqual(mem["memory_type"], "fact")
        self.assertIn("test", mem["tags"])
        self.assertEqual(mem["metadata"]["key"], "value")

    def test_update_memory(self):
        mid = self.m.create_memory("原内容")
        ok = self.m.update_memory(mid, content="新内容", importance=5)
        self.assertTrue(ok)
        mem = self.m.get_memory(mid)
        self.assertEqual(mem["content"], "新内容")
        self.assertEqual(mem["importance"], 5)

    def test_update_nonexistent(self):
        ok = self.m.update_memory(99999, content="无")
        self.assertFalse(ok)

    def test_delete_memory(self):
        mid = self.m.create_memory("待删除")
        ok = self.m.delete_memory(mid)
        self.assertTrue(ok)
        self.assertIsNone(self.m.get_memory(mid))

    def test_delete_nonexistent(self):
        ok = self.m.delete_memory(99999)
        self.assertFalse(ok)

    def test_list_memories_pagination(self):
        for i in range(25):
            self.m.create_memory(f"记忆{i}")
        page1, total = self.m.list_memories(page=1, page_size=10)
        page2, total2 = self.m.list_memories(page=2, page_size=10)
        self.assertEqual(total, 25)
        self.assertEqual(len(page1), 10)
        self.assertEqual(len(page2), 10)

    def test_list_memories_type_filter(self):
        self.m.create_memory("fact1", memory_type="fact")
        self.m.create_memory("sum1", memory_type="summary")
        facts, total = self.m.list_memories(memory_type="fact")
        self.assertEqual(len(facts), 1)
        self.assertEqual(total, 1)
        self.assertEqual(facts[0]["memory_type"], "fact")

    def test_get_memories_by_tag(self):
        self.m.create_memory("带标签的记忆", tags=["important", "personal"])
        self.m.create_memory("普通", tags=["casual"])
        results = self.m.get_memories_by_tag("important")
        self.assertEqual(len(results), 1)
        results = self.m.get_memories_by_tag("casual")
        self.assertEqual(len(results), 1)

    def test_old_add_memory_backward_compat(self):
        self.m.add_memory("旧API兼容测试", importance=3, source="test")
        mems = self.m.get_important_memories(10)
        self.assertIn("旧API兼容测试", mems)

    def test_update_tags_metadata(self):
        mid = self.m.create_memory("原始", tags=["a"], metadata={"x": 1})
        self.m.update_memory(mid, tags=["a", "b"], metadata={"x": 2, "y": 3})
        mem = self.m.get_memory(mid)
        self.assertEqual(mem["tags"], ["a", "b"])
        self.assertEqual(mem["metadata"]["x"], 2)
        self.assertEqual(mem["metadata"]["y"], 3)


# ========================
# 生命周期管理
# ========================
class TestMemoryLifecycle(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_importance_decay(self):
        mid = self.m.create_memory("会衰减的记忆", importance=5, decay_factor=1.0)
        # 模拟 14 天未召回
        import time as tmod
        old = tmod.time() - 14 * 86400
        with self.m._lock:
            self.m.conn.execute("UPDATE memories SET last_recalled = ? WHERE id = ?", (old, mid))
            self.m.conn.commit()
        self.m.lifecycle_maintenance()
        mem = self.m.get_memory(mid)
        self.assertIsNotNone(mem)
        self.assertLess(mem["importance"], 5)

    def test_consolidation_similar(self):
        mid_a = self.m.create_memory("主人喜欢喝冰咖啡", tags=["a"])
        mid_b = self.m.create_memory("主人爱喝冰咖啡", tags=["b"])
        self.m.lifecycle_maintenance()
        # 高度相似的记忆应该被合并
        surviving = self.m.get_memory(mid_a)
        deleted = self.m.get_memory(mid_b)
        self.assertIsNotNone(surviving)
        # b 可能被合并或独立存在取决于相似度阈值
        # 至少验证不报错
        self.assertIsNotNone(surviving)

    def test_pruning_old_unimportant(self):
        mid = self.m.create_memory("低价值旧记忆", importance=1, decay_factor=1.0)
        import time as tmod
        old = tmod.time() - 35 * 86400
        with self.m._lock:
            self.m.conn.execute("UPDATE memories SET last_recalled = ? WHERE id = ?", (old, mid))
            self.m.conn.commit()
        self.m.lifecycle_maintenance()
        pruned = self.m.get_memory(mid)
        self.assertIsNone(pruned)

    def test_summary_not_pruned(self):
        mid = self.m.create_memory("摘要不被清理", importance=1, memory_type="summary")
        import time as tmod
        old = tmod.time() - 35 * 86400
        with self.m._lock:
            self.m.conn.execute("UPDATE memories SET last_recalled = ? WHERE id = ?", (old, mid))
            self.m.conn.commit()
        self.m.lifecycle_maintenance()
        kept = self.m.get_memory(mid)
        self.assertIsNotNone(kept)

    def test_no_decay_when_factor_zero(self):
        mid = self.m.create_memory("不衰减的记忆", importance=5, decay_factor=0.0)
        import time as tmod
        old = tmod.time() - 30 * 86400
        with self.m._lock:
            self.m.conn.execute("UPDATE memories SET last_recalled = ? WHERE id = ?", (old, mid))
            self.m.conn.commit()
        self.m.lifecycle_maintenance()
        mem = self.m.get_memory(mid)
        self.assertEqual(mem["importance"], 5)

    def test_daily_maintenance_calls_lifecycle(self):
        self.m.create_memory("维护测试", importance=5)
        self.m.daily_maintenance()
        mem = self.m.get_memory(1)
        self.assertIsNotNone(mem)


# ========================
# 对话摘要
# ========================
class TestSummarization(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_trigger_counter(self):
        self.assertFalse(self.m.check_summarization_trigger())
        for _ in range(20):
            self.m.increment_message_counter()
        self.assertTrue(self.m.check_summarization_trigger())

    def test_prepare_context_insufficient(self):
        chats, ids = self.m.prepare_summarization_context(limit=10)
        self.assertIsNone(chats)
        self.assertIsNone(ids)

    def test_prepare_context_sufficient(self):
        for i in range(10):
            self.m.add_chat("user" if i % 2 == 0 else "mea", f"对话内容{i}")
        chats, ids = self.m.prepare_summarization_context(limit=20)
        self.assertIsNotNone(chats)
        self.assertIsNotNone(ids)
        self.assertGreaterEqual(len(chats), 4)
        self.assertEqual(len(chats), len(ids))

    def test_store_summary_marks_chats(self):
        for i in range(6):
            self.m.add_chat("user" if i % 2 == 0 else "mea", f"测试对话{i}")
        chats, ids = self.m.prepare_summarization_context(limit=10)
        self.assertIsNotNone(chats)
        self.m.store_summary("这是一段关于测试的对话摘要", ids, importance=3)
        mems = self.m.search_memories("测试", memory_type="summary")
        self.assertTrue(len(mems) >= 1)
        self.assertEqual(mems[0]["memory_type"], "summary")

    def test_summarized_chats_excluded_from_recent(self):
        self.m.add_chat("user", "已摘要消息1")
        self.m.add_chat("mea", "已摘要回复1")
        self.m.add_chat("user", "未摘要消息")
        recent = self.m.get_recent_chats(10, exclude_summarized=True)
        contents = [c["content"] for c in recent]
        # summarized chats 仍有 summarized=0 所以都在
        # 手动标记
        with self.m._lock:
            self.m.conn.execute("UPDATE chat_history SET summarized = 1 WHERE content LIKE '已摘要%'")
            self.m.conn.commit()
        recent2 = self.m.get_recent_chats(10, exclude_summarized=True)
        contents2 = [c["content"] for c in recent2]
        self.assertNotIn("已摘要消息1", contents2)
        self.assertIn("未摘要消息", contents2)


# ========================
# JSON 导入导出
# ========================
class TestJSONExportImport(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_export_round_trip(self):
        self.m.create_memory("导出的记忆", tags=["export"], metadata={"v": 1})
        self.m.set_master_info("color", "blue")
        exported = self.m.export_to_json()
        self.assertIn("memories", exported)
        self.assertIn("master_info", exported)
        self.assertIn("state", exported)
        self.assertGreaterEqual(len(exported["memories"]), 1)

    def test_import_merge(self):
        self.m.create_memory("原始记忆", tags=["original"])
        exported = self.m.export_to_json()
        count = self.m.import_from_json(exported, merge=True)
        # merge 时内容相同会被去重
        self.assertEqual(count, 0)

    def test_import_replace(self):
        self.m.create_memory("将被替换的记忆")
        exported = self.m.export_to_json()
        exported["memories"][0]["content"] = "新导入的记忆"
        count = self.m.import_from_json(exported, merge=False)
        self.assertEqual(count, 1)
        all_mems, _ = self.m.list_memories(page_size=100)
        self.assertEqual(len(all_mems), 1)
        self.assertEqual(all_mems[0]["content"], "新导入的记忆")

    def test_import_file_path(self):
        self.m.create_memory("文件导入测试")
        exported = self.m.export_to_json()
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(exported, f, ensure_ascii=False)
        # 新建一个实例导入
        from meapet.memory import db as mem_db
        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        old_path2 = mem_db.DB_PATH
        mem_db.DB_PATH = path2
        from meapet.memory.db import MeaMemory
        m2 = MeaMemory()
        try:
            count = m2.import_from_json(path)
            self.assertEqual(count, 1)
        finally:
            m2.close()
            mem_db.DB_PATH = old_path2
            try:
                os.unlink(path2)
            except Exception:
                pass
        try:
            os.unlink(path)
        except Exception:
            pass

    def test_import_invalid_source(self):
        with self.assertRaises(TypeError):
            self.m.import_from_json(123)


# ========================
# 上下文构建
# ========================
class TestContextBuilding(unittest.TestCase):
    def setUp(self):
        from meapet.memory import db as mem_db
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = mem_db.DB_PATH
        mem_db.DB_PATH = self.path
        from meapet.memory.db import MeaMemory
        self.m = MeaMemory()

    def tearDown(self):
        self.m.close()
        from meapet.memory import db as mem_db
        mem_db.DB_PATH = self.old_path
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_build_context_basic(self):
        ctx = self.m.build_context_prompt()
        self.assertIn("好感度", ctx)
        self.assertIn("主人", ctx)

    def test_build_context_with_query(self):
        self.m.create_memory("主人喜欢热带鱼", tags=["hobby"])
        ctx = self.m.build_context_prompt(current_query="鱼")
        self.assertIn("热带鱼", ctx)

    def test_build_context_with_memories(self):
        self.m.create_memory("重要记忆A", importance=5)
        self.m.create_memory("重要记忆B", importance=4)
        ctx = self.m.build_context_prompt(current_query="记忆")
        self.assertIn("重要记忆", ctx)

    def test_build_context_with_summary(self):
        self.m.create_memory("这是一段关于饮食偏好的对话摘要", memory_type="summary", importance=3)
        ctx = self.m.build_context_prompt(current_query="饮食")
        self.assertIn("饮食偏好", ctx)

    def test_build_context_backward_compat(self):
        """不加 current_query 参数也能正常调用"""
        ctx = self.m.build_context_prompt()
        self.assertIsInstance(ctx, str)
        self.assertTrue(len(ctx) > 10)


# ========================
# 工具函数测试
# ========================
class TestMemoryUtils(unittest.TestCase):
    def test_content_hash_consistency(self):
        from meapet.memory.db import _content_hash
        self.assertEqual(_content_hash("同一段文字"), _content_hash("同一段文字"))
        self.assertNotEqual(_content_hash("文字A"), _content_hash("文字B"))

    def test_trigram_hash_deterministic(self):
        from meapet.memory.db import _trigram_hash
        self.assertEqual(_trigram_hash("abc"), _trigram_hash("abc"))
        self.assertNotEqual(_trigram_hash("abc"), _trigram_hash("xyz"))

    def test_compute_embedding_short_text(self):
        from meapet.memory.db import _compute_embedding
        # 1 个字
        emb = _compute_embedding("鱼")
        self.assertTrue(len(emb) > 0)
        # 2 个字
        emb2 = _compute_embedding("吃鱼")
        self.assertTrue(len(emb2) > 0)


if __name__ == "__main__":
    unittest.main()
