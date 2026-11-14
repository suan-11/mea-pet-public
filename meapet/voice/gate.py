"""播音期麦克风门控（P1-3）：TTS 出声时把麦克风帧丢掉，杜绝自激回声。

为什么要单独一格
----------------
「sherpa VAD/KWS 常驻监听」与「桌宠自己说话」天生冲突：扬声器放出来的正是被转写
的那段话，麦克风就在旁边，不门控就会出现「她说完一句、自己把这句话听成用户输入、
再接一句」的死循环（路线图风险表把这项列成 高/中 风险，验收线是 8 小时自播音
误唤醒 < 1 次）。

设计：到期时间戳，不是引用计数
------------------------------
每一发 TTS 播放只把 until 推到 max(now + 时长 + 尾音缓冲)，播放方可确认
已停时再 release()。理由：

- _play_audio 有三条路（Qt 播放器 / Windows startfile / aplay），后两条拿不到
  「播完」回调，只有「播了」这个事实——所以门控必须能用预测时长自我过期，
  否则一发 fallback 播放会把麦克风永久糊住。
- 逐句流水线（P0-4）会出现「下一句在上一句尾音里就进队」，取 max 才不会让后一句
  把前一句的门提前解开。

尾音缓冲不是可选项：播放器报「播完」时，系统输出缓冲 + 房间混响的余音还在，
DEFAULT_ECHO_GATE_TAIL_MS 就是给这段留的默认留量（配置 voice_input.echo_gate_tail_ms
可调）。
"""
from __future__ import annotations

import time

#: 默认尾音缓冲（毫秒）。300 ms 是扬声器余音与系统输出缓冲的实测留量档；
#: 真测出有问题的机器在配置里调 voice_input.echo_gate_tail_ms，不在代码里拍死。
DEFAULT_ECHO_GATE_TAIL_MS = 300


def wav_duration_seconds(path: str) -> float:
    """读一个 RIFF/WAVE 的总时长（秒）；读不了就返回 0.0，不抛。

    只用 stdlib 解 chunk 头：fmt 块给 byte_rate，data 块给总字节数，
    时长 = data 字节数 / byte_rate。不用 wave 模块——fmt=3（32-bit float）它读不了，
    而 VITS 那一路产的就是这种（见 meapet/voice/engine.py:read_wav_samples 的注释）。
    byte_rate / data 缺任何一格就判 0：没有这一格的读数，就不猜。
    """
    try:
        with open(path, "rb") as handle:
            head12 = handle.read(12)
            if len(head12) < 12 or head12[:4] != b"RIFF" or head12[8:12] != b"WAVE":
                return 0.0
            byte_rate = 0
            data_bytes = 0
            while True:
                chunk_head = handle.read(8)
                if len(chunk_head) < 8:
                    break
                tag = chunk_head[:4]
                size = int.from_bytes(chunk_head[4:8], "little")
                if tag == b"fmt ":
                    body = handle.read(size + (size & 1))
                    if len(body) >= 12:
                        byte_rate = int.from_bytes(body[8:12], "little")
                elif tag == b"data":
                    data_bytes = size
                    break
                else:
                    handle.seek(size + (size & 1), 1)
        if byte_rate <= 0 or data_bytes <= 0:
            return 0.0
        return data_bytes / byte_rate
    except OSError:
        return 0.0


class EchoGate:
    """TTS 播音期门控：hold() 只叠 max、active() 到期自动解除。

    clock 可注入（测试用假钟），生产路径默认 time.monotonic。
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._until = 0.0

    def hold(self, duration_s: float, tail_ms: int = DEFAULT_ECHO_GATE_TAIL_MS) -> None:
        """宣告「接下来这段时间扬声器在说话」。

        重复 hold 取更长的截止，不缩短已宣告的窗口——逐句流水线下一句来得比
        上一句的尾音早，缩短会把上一句的余音放进来。
        """
        # 时长与尾音分开解析：时长读不出来（None / 垃圾串）时**仍要闭尾音那一格**。
        # 合成一个 try 的话，糟糕的时长会把整个 hold 变成空操作——那正是最该闭嘴
        # 的时候（播放方连自己播了多久都不知道）。测试
        # test_gate_hold_survives_garbage_duration 钉的就是这一格。
        try:
            duration = max(0.0, float(duration_s or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        try:
            tail = max(0, int(tail_ms or 0)) / 1000.0
        except (TypeError, ValueError):
            # 尾音配置本身坏了就退回默认留量——不闭是不允许的
            tail = DEFAULT_ECHO_GATE_TAIL_MS / 1000.0
        deadline = self._clock() + duration + tail
        if deadline > self._until:
            self._until = deadline

    def release(self) -> None:
        """播放方确认已停（Qt 那一路有状态信号时用）；预测时长的路只等它自己过期。"""
        self._until = 0.0

    def active(self) -> bool:
        return self._clock() < self._until

    def remaining(self) -> float:
        return max(0.0, self._until - self._clock())

    def snapshot(self) -> str:
        left = self.remaining()
        return f"门控={'生效中' if left > 0 else '空闲'} 剩余={left:.2f}s"


#: 全局唯一一枚：_play_audio（桌面音频 mixin）与 VoiceEngine（录音线程）
#: 共享同一只钟。不注入也不允许再造第二只——两处各持一只的话，「播音时丢帧」
#: 就成了一句空话。
GLOBAL_ECHO_GATE = EchoGate()
