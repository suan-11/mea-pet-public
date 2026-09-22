"""centralize-prompt-strings 迁移回归。

证明仍内联的三条模型提示词（记忆提取 system / 对话摘要前缀 / 好感度语气模板）
迁入 config/system_prompts.json 后**逐字节等价**，且好感度语气**跨文件去重到单一来源**。
锁死迁移不变量，防止字面量再次散落或漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meapet.config import prompt_loader
from meapet.config.prompt_loader import (
    load_system_prompt,
    reload_system_prompts,
    _CACHE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "config" / "system_prompts.json"

# ── 迁移前的原始字面量（真值锚点，改动即红）──
ORIG_MEMORY_EXTRACT = (
    "你是一个信息提取助手。从对话中提取值得长期记住的事实，每行一条用「- 」开头。"
    "如果没有值得记的内容回复「无」。"
)
ORIG_SUMMARY_PREFIX = "请用一句话概括以下对话的核心内容（不超过50字）。只输出概括，不要前缀。\n\n"
ORIG_AFFECTION_TEMPLATE = "[内部：好感度升至{tier}。请用稍暖的语气回应。]"


@pytest.fixture(autouse=True)
def _isolate_loader_state():
    original_path = prompt_loader._CONFIG_PATH
    _CACHE.clear()
    yield
    prompt_loader._CONFIG_PATH = original_path
    _CACHE.clear()


def test_config_file_is_valid_and_has_new_keys():
    data = json.loads(REAL_CONFIG.read_text(encoding="utf-8"))
    for key in ("memory_extract_system", "conversation_summary_instruction", "affection_warmth_hint"):
        assert key in data, key
        assert isinstance(data[key]["prompt"], str) and data[key]["prompt"]


def test_memory_extract_system_byte_equal():
    assert load_system_prompt("memory_extract_system") == ORIG_MEMORY_EXTRACT


def test_conversation_summary_prefix_byte_equal():
    assert load_system_prompt("conversation_summary_instruction") == ORIG_SUMMARY_PREFIX


@pytest.mark.parametrize("tier", ["陌生", "熟悉", "亲密", "依赖"])
def test_affection_template_renders_byte_equal(tier):
    rendered = load_system_prompt("affection_warmth_hint").format(tier=tier)
    assert rendered == f"[内部：好感度升至{tier}。请用稍暖的语气回应。]"


def test_engine_constants_wired_to_loader():
    from meapet.chat import engine
    assert engine.MEMORY_EXTRACT_SYSTEM_PROMPT == ORIG_MEMORY_EXTRACT
    assert engine.CONVERSATION_SUMMARY_INSTRUCTION == ORIG_SUMMARY_PREFIX
    assert engine.AFFECTION_WARMTH_HINT == ORIG_AFFECTION_TEMPLATE


def test_fallbacks_byte_equal_to_originals_when_config_missing():
    """JSON 缺失时应急 default 亦须与原字面量逐字节相同（行为保持）。"""
    from meapet.chat import engine
    prompt_loader._CONFIG_PATH = REPO_ROOT / "config" / "__no_such_prompt_file__.json"
    reload_system_prompts()
    assert load_system_prompt("memory_extract_system", default=engine._MEMORY_EXTRACT_SYSTEM_FALLBACK) == ORIG_MEMORY_EXTRACT
    assert load_system_prompt("conversation_summary_instruction", default=engine._CONVERSATION_SUMMARY_FALLBACK) == ORIG_SUMMARY_PREFIX
    assert load_system_prompt("affection_warmth_hint", default=engine._AFFECTION_WARMTH_FALLBACK) == ORIG_AFFECTION_TEMPLATE


def test_affection_hint_is_single_object_across_modules():
    """去重核心：chat_flow 不再持自有副本，与 engine 是同一对象。"""
    from meapet.chat import engine
    from meapet.desktop import chat_flow
    assert chat_flow.AFFECTION_WARMTH_HINT is engine.AFFECTION_WARMTH_HINT


def test_no_residual_hardcoded_affection_in_chat_flow():
    src = (REPO_ROOT / "meapet" / "desktop" / "chat_flow.py").read_text(encoding="utf-8")
    assert "[内部：好感度升至" not in src, "chat_flow 不应再内联好感度字面量（应引用 engine 单一来源）"


def test_no_second_copy_of_promoted_literals_in_engine():
    """engine 里每条串最多各出现一次（作为 _..._FALLBACK 兜底），下发路径改引用常量。"""
    src = (REPO_ROOT / "meapet" / "chat" / "engine.py").read_text(encoding="utf-8")
    assert '"role": "system", "content": MEMORY_EXTRACT_SYSTEM_PROMPT' in src
    assert src.count("你是一个信息提取助手") == 1
    assert src.count("请用一句话概括以下对话的核心内容") == 1
