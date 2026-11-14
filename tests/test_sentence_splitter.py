"""句级切分缓冲器（meapet/tts/sentence_splitter.py）的边界测试。

能证明：边界集（。！？…；!?; 与换行，不含小数点/逗号）、连续边界合并、
最小阈值碎句合并、增量喂入与 flush 尾巴。不能证明：听感（那是 TTS 验收）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meapet.tts.sentence_splitter import SentenceBuffer  # noqa: E402


def test_basic_boundaries():
    buf = SentenceBuffer()
    out = buf.feed("今天天气真不错！我们出去玩吧？好的。")
    assert out == ["今天天气真不错", "我们出去玩吧"]
    # "好的。"不足阈值时持留，flush 交出
    assert buf.flush() == "好的。"


def test_decimal_point_not_split():
    buf = SentenceBuffer()
    out = buf.feed("圆周率是3.14哦。版本2.5也行！")
    assert out == ["圆周率是3.14哦", "版本2.5也行"]
    assert buf.flush() == ""


def test_comma_not_split():
    buf = SentenceBuffer()
    assert buf.feed("你好，世界，这是一个长句子，") == []
    assert buf.flush() == "你好，世界，这是一个长句子，"


def test_consecutive_boundaries_merge():
    buf = SentenceBuffer()
    out = buf.feed("真的吗！？嗯……让我想想！")
    # "真的吗"不足阈值并入后句；！？与……各并成一个边界
    assert out == ["真的吗！？嗯"]
    assert buf.flush() == "让我想想！"


def test_short_candidate_merges_forward():
    buf = SentenceBuffer(min_chars=6)
    out = buf.feed("好。今天天气真不错！")
    assert out == ["好。今天天气真不错"]


def test_incremental_feeds_char_by_char():
    buf = SentenceBuffer()
    text = "第一句话在这里。第二句话也在这里！尾巴"
    collected = []
    for ch in text:
        collected.extend(buf.feed(ch))
    assert collected == ["第一句话在这里", "第二句话也在这里"]
    assert buf.flush() == "尾巴"


def test_newline_boundary():
    buf = SentenceBuffer()
    out = buf.feed("第一行内容够长了\n第二行内容也够长\n")
    assert out == ["第一行内容够长了", "第二行内容也够长"]


def test_flush_only_once():
    buf = SentenceBuffer()
    buf.feed("不足一句的尾巴")
    assert buf.flush() == "不足一句的尾巴"
    assert buf.flush() == ""


def test_empty_feed():
    buf = SentenceBuffer()
    assert buf.feed("") == []
    assert buf.feed(None) == []
    assert buf.flush() == ""
