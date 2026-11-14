"""句级流式呈现（AgentTurnPresentation.sentence_streaming）的契约测试。

能证明：显示增量流式切句即提交 TTS、首句音频就绪即 FinalizeBubble+顺序播放、
翻译/重写降级撤销 provisional 整段重交、短回复整段提交、缺字段静默收尾、
旧模式（sentence_streaming=False）行为不变。
不能证明：真实 TTS 延迟读数（那是 --speech-latency 真机验收）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meapet.agent.base import TurnCompleted  # noqa: E402
from meapet.agent.presentation import (  # noqa: E402
    AgentTurnPresentation,
    CancelTTS,
    FinalizeBubble,
    FinishTurn,
    PlayAudio,
    SetMood,
    SubmitTTS,
)
from meapet.conversation.output_protocol import (  # noqa: E402
    SegmentCompleted,
    SegmentStarted,
    SegmentTextDelta,
)
from meapet.conversation.types import ReplySegment  # noqa: E402


def _pres(**kwargs):
    params = dict(tts_enabled=True, reply_min_duration_ms=3000, sentence_streaming=True)
    params.update(kwargs)
    return AgentTurnPresentation(**params)


def _segment(display, voice=None, index=0, mood="happy", lang="zh-CN"):
    return ReplySegment(
        display_text=display,
        voice_text=voice if voice is not None else display,
        voice_language=lang,
        mood=mood,
        tts_style="",
        index=index,
    )


def _completed(seg):
    return SegmentCompleted(seg)


def _turn_done(segments):
    return TurnCompleted("t1", SimpleNamespace(segments=segments))


def test_sentences_submitted_during_deltas():
    pres = _pres()
    assert pres.consume(SegmentStarted(0)) == ()
    actions = pres.consume(SegmentTextDelta(0, "第一句话在这里。第二句话也够长！尾巴"))
    submits = [a for a in actions if isinstance(a, SubmitTTS)]
    assert [s.segment.voice_text for s in submits] == ["第一句话在这里", "第二句话也够长"]
    # provisional 阶段没有气泡动作（首句音频就绪才呈现）
    assert not any(isinstance(a, FinalizeBubble) for a in actions)
    # 伪段携带提交时刻的累积显示文本（气泡随流式长大）与父段气泡归属
    assert submits[0].segment.display_text == "第一句话在这里。第二句话也够长！尾巴"
    assert submits[0].segment.index != 0 or submits[0].segment.voice_text == "第一句话在这里"


def test_first_audio_ready_finalizes_and_plays_in_order():
    pres = _pres()
    seg = _segment("第一句话在这里。第二句话也够长！")
    pres.consume(SegmentStarted(0))
    pres.consume(SegmentTextDelta(0, "第一句话在这里。第二句话也够长！"))
    pres.consume(_completed(seg))
    pres.consume(_turn_done([seg]))
    # 第二句先就绪也不能插队
    assert pres.tts_ready(1, "s1.wav", audio_duration_ms=900) == ()
    actions = pres.tts_ready(0, "s0.wav", audio_duration_ms=1000)
    fin = [a for a in actions if isinstance(a, FinalizeBubble)]
    play = [a for a in actions if isinstance(a, PlayAudio)]
    assert len(fin) == 1 and fin[0].bubble_index == 0
    assert fin[0].duration_ms == 3000  # max(3000, 1000+500)
    assert play[0].index == 0
    # 第一句播完 → 第二句呈现并播放
    actions = pres.audio_finished(0)
    play = [a for a in actions if isinstance(a, PlayAudio)]
    assert play and play[0].index == 1
    actions = pres.audio_finished(1)
    assert any(isinstance(a, FinishTurn) for a in actions)


def test_translation_fallback_cancels_and_resubmits_whole():
    pres = _pres()
    seg = _segment("你好世界真美好。", voice="こんにちは世界。", lang="ja")
    pres.consume(SegmentStarted(0))
    pres.consume(SegmentTextDelta(0, "你好世界真美好。"))
    actions = pres.consume(_completed(seg))
    cancels = [a for a in actions if isinstance(a, CancelTTS)]
    submits = [a for a in actions if isinstance(a, SubmitTTS)]
    assert len(cancels) == 1 and cancels[0].indices == (0,)
    assert len(submits) == 1
    assert submits[0].segment.voice_text == "こんにちは世界。"
    assert submits[0].segment.voice_language == "ja"
    pres.consume(_turn_done([seg]))
    # 被撤销序号的迟到 TTS 结果必须被忽略
    assert pres.tts_ready(0, "stale.wav", audio_duration_ms=500) == ()
    actions = pres.tts_ready(1, "whole.wav", audio_duration_ms=800)
    assert any(isinstance(a, PlayAudio) and a.index == 1 for a in actions)
    assert any(isinstance(a, FinishTurn) for a in pres.audio_finished(1))


def test_short_reply_submits_whole_segment():
    pres = _pres()
    seg = _segment("你好呀")
    pres.consume(SegmentStarted(0))
    pres.consume(SegmentTextDelta(0, "你好呀"))
    actions = pres.consume(_completed(seg))
    submits = [a for a in actions if isinstance(a, SubmitTTS)]
    assert len(submits) == 1
    assert submits[0].segment.voice_text == "你好呀"
    assert submits[0].segment.mood == "happy"


def test_tail_carries_real_meta_and_set_mood():
    pres = _pres()
    seg = _segment("第一句话在这里。尾巴不足一句", mood="shy")
    pres.consume(SegmentStarted(0))
    pres.consume(SegmentTextDelta(0, "第一句话在这里。尾巴不足一句"))
    actions = pres.consume(_completed(seg))
    submits = [a for a in actions if isinstance(a, SubmitTTS)]
    assert len(submits) == 1
    assert submits[0].segment.voice_text == "尾巴不足一句"
    assert submits[0].segment.mood == "shy"
    assert submits[0].segment.voice_language == "zh-CN"
    assert any(isinstance(a, SetMood) and a.mood == "shy" for a in actions)


def test_missing_voice_fields_silent_finalize():
    pres = _pres()
    seg = ReplySegment(
        display_text="第一句话在这里。", voice_text="", voice_language="",
        mood="neutral", tts_style="", index=0,
    )
    pres.consume(SegmentStarted(0))
    pres.consume(SegmentTextDelta(0, "第一句话在这里。"))
    actions = pres.consume(_completed(seg))
    assert any(isinstance(a, CancelTTS) for a in actions)
    assert not any(isinstance(a, SubmitTTS) for a in actions)
    assert any(isinstance(a, FinalizeBubble) and a.wav_path == "" for a in actions)
    assert any(isinstance(a, FinishTurn) for a in pres.consume(_turn_done([seg])))


def test_legacy_mode_unchanged():
    pres = _pres(sentence_streaming=False)
    seg = _segment("第一句话在这里。第二句话也够长！")
    pres.consume(SegmentStarted(0))
    assert pres.consume(SegmentTextDelta(0, "第一句话在这里。")) == ()
    actions = pres.consume(_completed(seg))
    submits = [a for a in actions if isinstance(a, SubmitTTS)]
    assert len(submits) == 1
    assert submits[0].segment.index == 0  # 旧模式用原段 index，不是 seq
    assert submits[0].segment.voice_text == seg.voice_text


def test_tts_disabled_sentence_flag_inert():
    pres = _pres(tts_enabled=False)
    assert pres.sentence_streaming is False
    seg = _segment("你好呀")
    pres.consume(SegmentStarted(0))
    actions = pres.consume(SegmentTextDelta(0, "你好呀"))
    assert any(a.__class__.__name__ == "UpdateBubble" for a in actions)
