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

from meapet.utils import safe_print, log_error, cloud_vision_allowed
from meapet.desktop.workers import ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox
from meapet.desktop.status_panel import StatusPanel


AUDIO_BUBBLE_TAIL_MS = 500
MIN_REPLY_BUBBLE_MS = 3000

#: 每进程只探一次：None=没探过，探过后是 (类三元组或 None, 真因)。
#: 底层库缺席（整合包把 libQt5Multimedia.so.5 那几条裁掉了）与"这一发没播成"是两件事，
#: 所以前者印一次「首选 Qt 播放不可用」，后者才配得上「播放失败」四个字。
_QT_MEDIA_STATE = None
_QT_MEDIA_NOTICED = False


def _qt_media_bundle():
    """返回 (QMediaPlayer, QMediaContent, QUrl)，底层库不可用时返回 None 并把真因**只印一次**。"""
    global _QT_MEDIA_STATE, _QT_MEDIA_NOTICED
    if _QT_MEDIA_STATE is None:
        try:
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtCore import QUrl
            _QT_MEDIA_STATE = ((QMediaPlayer, QMediaContent, QUrl), "")
        except Exception as exc:  # noqa: BLE001 - 不可用是这条链上的一条读数，不是异常
            _QT_MEDIA_STATE = (None, f"{type(exc).__name__}: {exc}")
    bundle, reason = _QT_MEDIA_STATE
    if bundle is None and not _QT_MEDIA_NOTICED:
        _QT_MEDIA_NOTICED = True
        safe_print(f"[audio] 首选 Qt 播放不可用：{reason} ⇒ 本进程改走系统播放器，音量档不生效")
    return bundle


def hold_echo_gate(wav_path: str, config=None, duration_ms: int = 0) -> None:
    """P1-3：告诉麦克风门控「接下来这段时间扬声器在说话」。

    必须**在播放之前**推开：等播完再推，第一句就已经被录进去了。
    时长优先用播放器给的（duration_ms），没有就按 wav 头预测。

    这一段不碰 self：audio mixin 的单测只挂 _play_audio 一件，
    门控不该因此要求测试去凑一整只桌宠窗口。
    """
    try:
        from meapet.voice.gate import GLOBAL_ECHO_GATE, wav_duration_seconds

        voice_cfg = (config or {}).get("voice_input") or {}
        try:
            tail_ms = int(voice_cfg.get("echo_gate_tail_ms", 300))
        except (TypeError, ValueError):
            tail_ms = 300
        if duration_ms:
            try:
                seconds = max(0.0, float(duration_ms) / 1000.0)
            except (TypeError, ValueError):
                seconds = wav_duration_seconds(wav_path)
        else:
            seconds = wav_duration_seconds(wav_path)
        GLOBAL_ECHO_GATE.hold(seconds, tail_ms=tail_ms)
    except Exception as exc:  # noqa: BLE001 - 门控推不开是读数问题，不该阻断播放
        safe_print(f"[audio] 回声门控推开失败: {type(exc).__name__}: {exc}")


def bubble_duration_for_audio(
    audio_duration_ms: int,
    minimum_duration_ms: int,
) -> int:
    """保证气泡至少比有效音频多保留半秒。"""
    try:
        audio_ms = max(0, int(audio_duration_ms))
    except (TypeError, ValueError):
        audio_ms = 0
    try:
        minimum_ms = max(0, int(minimum_duration_ms))
    except (TypeError, ValueError):
        minimum_ms = 0
    if audio_ms <= 0:
        return minimum_ms
    return max(minimum_ms, audio_ms + AUDIO_BUBBLE_TAIL_MS)


class PetAudioMixin:
    @staticmethod
    def _get_wav_duration_ms(wav_path: str) -> int:
        """读取 wav 文件时长（毫秒）"""
        try:
            import wave
            with wave.open(wav_path, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return int(frames / rate * 1000)
        except Exception:
            pass
        return 0

    def _hold_echo_gate(self, wav_path: str, duration_ms: int = 0) -> None:
        """P1-3：告诉麦克风门控「接下来这段时间扬声器在说话」（实现见模块级同名函数）。"""
        hold_echo_gate(wav_path, getattr(self, "config", None), duration_ms=duration_ms)

    def _play_audio(self, wav_path: str, audio_type: str = "sfx"):
        """播放 wav 音频，audio_type 为 'sfx' 或 'tts'，音量从配置读取"""
        if not os.path.exists(wav_path):
            safe_print(f"[audio] 文件不存在: {wav_path}")
            return
        abs_path = os.path.abspath(wav_path)
        # TTS 会经麦克风回授；sfx 是本地提示音，同样进麦克风，但只闭一小格——
        # 提示音短，按它的真实时长闭就够了。走模块级函数而不是 self 上的方法：
        # 这个 mixin 的单测只挂 _play_audio 一件。
        hold_echo_gate(abs_path, getattr(self, "config", None))
        size = 0
        try:
            size = os.path.getsize(abs_path)
        except Exception:
            pass
        safe_print(f"[audio] 准备播放: {abs_path} ({size} bytes)")

        # 从配置读取音量
        volume = 100  # 默认
        try:
            config_audio = self.config.get("audio", {})
            if audio_type == "tts":
                volume = int(config_audio.get("tts_volume_percent", 90))
            else:
                volume = int(config_audio.get("sfx_volume_percent", 80))
            volume = max(0, min(100, volume))
        except (TypeError, ValueError):
            volume = 100

        # 首选：PyQt5 QtMultimedia（跨平台，支持音量控制）
        bundle = _qt_media_bundle()
        if bundle is not None:
            QMediaPlayer, QMediaContent, QUrl = bundle
            try:
                if not hasattr(self, "_media_player") or self._media_player is None:
                    self._media_player = QMediaPlayer(self)
                self._media_player.stop()
                self._media_player.setMedia(QMediaContent(QUrl.fromLocalFile(abs_path)))
                self._media_player.setVolume(volume)
                self._media_player.play()
                safe_print(f"[audio] Qt 播放: {os.path.basename(abs_path)}, volume={volume}%")
                return
            except Exception as e:
                # 这一行只留给**真发了却没播成**的那一发；底层库缺席走的是上面那句「不可用」
                safe_print(f"[audio] Qt 播放失败: {e}")

        # 备用：系统默认播放器（不支持音量控制）
        try:
            if sys.platform.startswith("win"):
                os.startfile(abs_path)  # type: ignore[attr-defined]
                played = "startfile 打开"
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                played = "afplay 播放"
            else:
                subprocess.Popen(["aplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                played = "aplay 播放"
            safe_print(
                f"[audio] {played}: {os.path.basename(abs_path)}"
                f"（音量档 {volume}% 未生效：这条不带增益）"
            )
        except Exception as e:
            safe_print(f"[audio] 最终播放失败: {e}")

    # ========================
    # 屏幕观察（截屏吐槽）
    # ========================

