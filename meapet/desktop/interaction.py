"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import re
import sys
import time
import wave
import subprocess
from typing import Optional

from PyQt5.QtWidgets import QMessageBox, QApplication
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QRegion
from PyQt5.QtCore import QRect

from meapet.utils import (
    audio_cache_key,
    legacy_audio_cache_name,
    safe_print,
    log_error,
    cloud_vision_allowed,
)
from meapet.desktop.workers import ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox
from meapet.desktop.status_panel import StatusPanel


class PetInteractionMixin:
    def _on_head_patted(self):
        try:
            self._record_interaction()
            reactions = [
                ("……别摸我头发。", "annoyed"),
                ("……有事吗？", "curious"),
                ("哼。", "melancholy"),
                ("……", "shy"),
                ("别摸了……", "annoyed"),
            ]
            text, mood = random.choice(reactions)
            self._safe_set_mood(mood)
            dur = (self.config.get("bubble_duration_ms") or {}).get("interaction", 3000)
            self._interaction_speak(text, dur, mood)
            QTimer.singleShot(3000, lambda: self._safe_set_mood("neutral"))
        except Exception as e:
            log_error("head_patted", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] head_patted error: {e}")
            try:
                self._show_bubble("唔…摸头出了点问题喵", 2500)
            except Exception:
                pass

    def _on_tail_patted(self):
        """摸尾巴反应（Live2D 专属）"""
        try:
            self._record_interaction()
            reactions = [
                ("尾巴……不许碰喵！！", "angry"),
                ("……你想死一次吗？", "annoyed"),
                ("变态。", "annoyed"),
                ("……尾巴是很敏感的不知道吗。", "shy"),
            ]
            text, mood = random.choice(reactions)
            self._safe_set_mood(mood)
            dur = (self.config.get("bubble_duration_ms") or {}).get("interaction", 3000)
            self._interaction_speak(text, dur, mood)
            QTimer.singleShot(4000, lambda: self._safe_set_mood("neutral"))
        except Exception as e:
            log_error("tail_patted", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] tail_patted error: {e}")
            try:
                self._show_bubble("唔…尾巴反应出错了喵", 2500)
            except Exception:
                pass

    def _interaction_speak(self, text: str, duration_ms: int, mood: str):
        """互动语音：优先缓存，否则走 TTS 合成（失败不抛到 Qt 事件循环）"""
        try:
            cache_file = self._get_cached_interaction(text, "jp")
            if cache_file:
                self.show_reply(text, mood)
                self._play_audio(cache_file)
            else:
                self._speak_and_show(text, duration_ms, mood)
        except Exception as e:
            log_error("interaction_speak", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] interaction_speak error: {e}")
            try:
                # 至少显示文字，不因 TTS/缓存失败而静默或崩溃
                self.show_reply(text, mood, duration_ms=duration_ms)
            except Exception:
                try:
                    self._show_bubble(text, duration_ms or 3000)
                except Exception:
                    pass

    def _safe_name(self, text: str) -> str:
        """文本 → 不暴露原文的稳定缓存键。"""
        return audio_cache_key(text)

    def _get_cached_interaction(self, text: str, lang: str) -> Optional[str]:
        """获取互动语音缓存（带语言前缀）"""
        if not self.tts:
            return None
        safe = self._safe_name(text)
        if not safe:
            return None
        from meapet.paths import project_path
        cache_dir = project_path("voice_cache")
        path = os.path.join(cache_dir, f"{lang}_{safe}.wav")
        if os.path.exists(path):
            return path
        legacy = legacy_audio_cache_name(text)
        if not legacy:
            return None
        legacy_path = os.path.join(cache_dir, f"{lang}_{legacy}.wav")
        return legacy_path if os.path.exists(legacy_path) else None

    def _idle_action(self):
        """随机空闲表情变化"""
        if random.random() < 0.4:
            return  # 60% 什么都不做
        moods = ["neutral", "happy", "curious", "melancholy"]
        self._safe_set_mood(random.choice(moods))

    # ========================
    # 对话气泡
    # ========================
    def _on_bubble_stack_changed(self) -> None:
        stack = getattr(self, "_bubble_stack", None)
        if stack is None:
            return
        self.bubble = stack.latest
        if hasattr(self, "_position_bubble"):
            self._position_bubble(animate=True)

    def _clear_bubbles(self) -> None:
        stack = getattr(self, "_bubble_stack", None)
        if stack is not None:
            stack.hide_all()
            self.bubble = None
            return
        bubble = getattr(self, "bubble", None)
        if bubble is not None:
            try:
                bubble.hide()
            except RuntimeError:
                pass

    def _show_bubble(self, text: str, duration_ms: int = None, mood: str | None = None):
        try:
            if duration_ms is None:
                duration_ms = (self.config.get("bubble_duration_ms") or {}).get("default", 5000)
            stack = getattr(self, "_bubble_stack", None)
            if stack is not None:
                self.bubble = stack.show_message(text, duration_ms, mood=mood)
            elif getattr(self, "bubble", None) is not None:
                self.bubble.show_text(text, duration_ms, mood=mood)
                if hasattr(self, "_position_bubble"):
                    self._position_bubble()
        except Exception as e:
            log_error("show_bubble", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] show_bubble error: {e}")


    def _show_random_bubble(self, text: str):
        self._show_bubble(text, 3000)

    def _record_interaction(self):
        """记录互动时间（聊天、摸头等触发）"""
        self._last_interaction_time = time.time()

    # ========================
    # 关闭事件
    # ========================
