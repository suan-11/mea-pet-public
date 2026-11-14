"""句级切分缓冲器（P0-4 句级 TTS 流水线的纯逻辑核心）。

流式显示文本按句切给 TTS：边界取 。！？…；与换行（ASCII !?; 同待），
**不含小数点**——3.14 不会被切碎；连续边界符（……、！？）并成一个边界；
短于最小阈值的候选句持留到下一句合并，避免"好。"这种碎句单独起 synth。

本模块不依赖 Qt / 网络 / meapet 其他模块，可独立单测。
"""

from __future__ import annotations

# 句边界字符集：中文句读 + 换行 + ASCII 叹号/问号/分号。
# 刻意不含 ASCII 句点（小数点/缩写）与逗号（半句不该起 synth）。
_BOUNDARIES = frozenset("。！？…；!?" + "\n" + ";")

DEFAULT_MIN_CHARS = 6


class SentenceBuffer:
    """喂增量文本，吐整句；flush() 收尾巴。非线程安全（单轮单线程消费）。"""

    def __init__(self, min_chars: int = DEFAULT_MIN_CHARS) -> None:
        self.min_chars = max(1, int(min_chars))
        self._pending = ""

    def feed(self, delta: str) -> list[str]:
        """喂一段增量，返回本次凑齐的整句（可能为空）。"""
        self._pending += str(delta or "")
        out: list[str] = []
        search_from = 0
        while True:
            cut = -1
            for i in range(search_from, len(self._pending)):
                if self._pending[i] in _BOUNDARIES:
                    cut = i
                    break
            if cut < 0:
                break
            # 连续边界符并成一个边界（…… / ！？ / 多个换行）
            end = cut + 1
            while end < len(self._pending) and self._pending[end] in _BOUNDARIES:
                end += 1
            candidate = self._pending[:cut].strip()
            if len(candidate) < self.min_chars:
                # 碎句不切：跳过这个边界继续往后找，短候选自然并入下一句。
                search_from = end
                continue
            out.append(candidate)
            self._pending = self._pending[end:]
            search_from = 0
        return out

    def flush(self) -> str:
        """回合收尾：剩余不足一句的尾巴整体交出（去首尾空白）。"""
        tail = self._pending.strip()
        self._pending = ""
        return tail
