"""
梅尔桌宠 - 立绘渲染 & 差分表情映射
"""
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QMovie, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget


@dataclass
class ExpressionMeta:
    """表情元数据"""
    code: str
    name: str
    description: str
    has_blink: bool = False


# ========================
# 表情映射表
# ========================
# 表情含义基于 Ollama qwen3.5:4b 识图结果：
# 001 = 含泪、悲伤/恐惧 → 实际是默认表情（含角色特色泪光）
# 002 = 悲伤、忧郁
# 011 = 眯眼、闭唇微笑 → 满足/害羞
# 012 = 半闭眼、满足微笑 → 安宁/幸福
# 101 = 睁大眼、下看 → 好奇/内省
# 102 = 好奇天真、带点害羞
# 171 = 泪眼、脸红、嘴角下垂 → 轻度悲伤/失望
# 191 = 眉毛上扬、微笑 → 好奇/轻微兴趣
# 192 = 好奇、稍微惊讶
# 301 = 闪亮眼、嘴角下垂 → 悲伤梦幻
# 601 = 眼睛大而微弯、小嘴下垂 → 温柔好奇/轻微担忧
# 701 = 温柔下垂眼、中性微笑 → 温柔悲伤/沉思

EXPRESSION_MAP: Dict[str, ExpressionMeta] = {
    "001": ExpressionMeta("001", "default", "默认表情（含泪光特色）", True),
    "002": ExpressionMeta("002", "melancholy", "忧郁/略带悲伤", True),
    "011": ExpressionMeta("011", "content", "满足/眯眼微笑", False),
    "012": ExpressionMeta("012", "peaceful", "安宁/幸福微笑", False),
    "101": ExpressionMeta("101", "curious", "好奇/内省", True),
    "102": ExpressionMeta("102", "innocent", "天真/微羞好奇", True),
    "171": ExpressionMeta("171", "teary", "泪眼/失望/悲伤", True),
    "181": ExpressionMeta("181", "shy_a", "害羞/别扭A", True),
    "182": ExpressionMeta("182", "shy_b", "害羞/别扭B", True),
    "191": ExpressionMeta("191", "intrigued", "感兴趣/挑眉", True),
    "192": ExpressionMeta("192", "surprised", "惊讶/好奇瞪眼", True),
    "301": ExpressionMeta("301", "sad_a", "悲伤/梦幻落寞", True),
    "302": ExpressionMeta("302", "sad_b", "悲伤/忧郁B", True),
    "601": ExpressionMeta("601", "gentle", "温柔好奇/微担忧", True),
    "611": ExpressionMeta("611", "annoyed_a", "不耐烦/烦躁A", True),
    "612": ExpressionMeta("612", "annoyed_b", "不耐烦/烦躁B", True),
    "701": ExpressionMeta("701", "wistful", "沉思/温柔悲伤", True),
    "702": ExpressionMeta("702", "pensive", "忧愁/更深沉思", True),
}

# mea01A 拥有哪些表情
MEAA01_EXPRESSIONS = [
    "001", "002", "011", "012",
    "101", "102", "171",
    "181", "182", "191", "192",
    "301", "302",
    "601", "611", "612",
    "701", "702",
]

# 情绪 → 可用表情列表
MOOD_TO_EXPRESSION = {
    "neutral": ["001", "011"],
    "happy": ["012", "001"],
    "talking": ["011", "012", "001"],
    "surprised": ["192", "102"],
    "curious": ["101", "191", "102"],
    "sad": ["301", "302", "171", "002"],
    "shy": ["181", "182", "601"],
    "annoyed": ["611", "612"],
    "melancholy": ["002", "701", "702"],
    "embarrassed": ["601", "181"],
    "intrigued": ["191", "101"],
    "teary": ["171", "301"],
    "wistful": ["701", "702", "002"],
}


class SpriteCanvas(QWidget):
    """在一个透明绘制面中同步提交完整 PNG 帧。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = QPixmap()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def sizeHint(self) -> QSize:
        if self._frame.isNull():
            return super().sizeHint()
        return self._frame.size()

    def set_frame(self, frame: QPixmap) -> None:
        """替换完整后备帧，并在当前 GUI 轮次同步重绘整个表面。"""
        self._frame = QPixmap(frame)
        if self.size() != self._frame.size():
            self.resize(self._frame.size())
        self.repaint(self.rect())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        # Source 模式会同时替换颜色与 Alpha，避免透明窗口残留上一帧局部。
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        if not self._frame.isNull():
            painter.drawPixmap(0, 0, self._frame)
        painter.end()


class SpriteRenderer(QObject):
    """立绘渲染器 - 管理 PNG 差分切换 & 眨眼动画"""
    
    expression_changed = pyqtSignal(str)  # 发出新的表情 code
    
    def __init__(self, sprite_dir: str, outfit: str = "01", direction: str = "A"):
        super().__init__()
        self.sprite_dir = sprite_dir
        self.outfit = outfit
        self.direction = direction
        self._current_expression = "001"
        self._current_mood = "neutral"
        self._blink_timer: Optional[QTimer] = None
        self._blink_index = 0
        self._is_blinking = False
        self._expression_has_blink = True
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._scaled_pixmap_cache: dict[tuple[str, int, int], QPixmap] = {}
        self._preload_initial_frames()
        
    @property
    def prefix(self) -> str:
        return f"mea{self.outfit}{self.direction}"

    # 闭眼差分编号（_011 眯眼/_012 半闭眼 = 闭眼状态）
    BLINK_CODES = ("011", "012")

    def _get_path(self, code: str) -> str:
        """获取差分文件路径"""
        filename = f"{self.prefix}_{code}.png"
        return os.path.join(self.sprite_dir, filename)

    def _get_blink_code(self) -> Optional[str]:
        """找到当前 outfit+direction 支持的闭眼差分"""
        for bc in self.BLINK_CODES:
            path = self._get_path(bc)
            if os.path.exists(path):
                return bc
        return None

    def _load_pixmap(self, path: str) -> QPixmap:
        pixmap = self._pixmap_cache.get(path)
        if pixmap is None:
            pixmap = QPixmap(path)
            self._pixmap_cache[path] = pixmap
        return pixmap

    def _preload_initial_frames(self) -> None:
        """启动时预读默认与闭眼帧，眨眼期间不触发磁盘解码。"""
        codes = (self._current_expression, self._get_blink_code())
        for code in codes:
            if not code:
                continue
            path = self._get_path(code)
            if os.path.exists(path):
                self._load_pixmap(path)

    def _get_current_path(self) -> str:
        if self._is_blinking and self._expression_has_blink:
            blink_code = self._get_blink_code()
            if blink_code:
                blink_path = self._get_path(blink_code)
                if os.path.exists(blink_path):
                    return blink_path

        path = self._get_path(self._current_expression)
        if os.path.exists(path):
            return path
        base_path = os.path.join(self.sprite_dir, f"{self.prefix}_base.png")
        if os.path.exists(base_path):
            return base_path
        raise FileNotFoundError(
            f"Cannot find sprite for {self.prefix}_{self._current_expression}"
        )

    def get_current_pixmap(self) -> QPixmap:
        """获取当前应该显示的 QPixmap"""
        return self._load_pixmap(self._get_current_path())

    def _get_scaled_pixmap_for_path(
        self,
        path: str,
        width: int,
        height: int,
    ) -> QPixmap:
        key = (path, max(1, int(width)), max(1, int(height)))
        scaled = self._scaled_pixmap_cache.get(key)
        if scaled is None:
            scaled = self._load_pixmap(path).scaled(
                key[1],
                key[2],
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._scaled_pixmap_cache[key] = scaled
        return scaled

    def get_scaled_pixmap(self, width: int, height: int) -> QPixmap:
        """返回当前帧的缓存缩放结果。"""
        return self._get_scaled_pixmap_for_path(
            self._get_current_path(),
            width,
            height,
        )

    def preload_scaled_frames(self, width: int, height: int) -> None:
        """预缩放当前与闭眼帧，避免 150ms 眨眼窗口内做重采样。"""
        paths = {self._get_current_path()}
        blink_code = self._get_blink_code()
        if blink_code:
            blink_path = self._get_path(blink_code)
            if os.path.exists(blink_path):
                paths.add(blink_path)
        for path in paths:
            self._get_scaled_pixmap_for_path(path, width, height)

    def set_mood(self, mood: str):
        """根据情绪设置表情"""
        if mood in MOOD_TO_EXPRESSION:
            candidates = MOOD_TO_EXPRESSION[mood]
            # 选择当前衣服/朝向支持的表情
            available = [c for c in candidates 
                        if os.path.exists(self._get_path(c))]
            if available:
                expr = random.choice(available)
                self.set_expression(expr)
                return
        # fallback
        self.set_expression("001")

    def set_expression(self, code: str):
        """直接设置表情差分编号"""
        if code == self._current_expression:
            return
        path = self._get_path(code)
        if not os.path.exists(path):
            return
        self._current_expression = code
        meta = EXPRESSION_MAP.get(code)
        self._expression_has_blink = meta.has_blink if meta else False
        # 011/012 本身就是闭眼图，不需要眨眼动画
        if code in self.BLINK_CODES:
            self._expression_has_blink = False
        self.expression_changed.emit(code)

    def start_blink_animation(self, interval_ms: int = 4000):
        """启动随机眨眼动画"""
        if self._blink_timer is not None:
            self._blink_timer.stop()
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._do_blink)
        self._blink_timer.start(interval_ms + random.randint(-1000, 1000))

    def stop_blink_animation(self):
        if self._blink_timer is not None:
            self._blink_timer.stop()

    def _do_blink(self):
        """执行一次眨眼"""
        if not self._expression_has_blink:
            return
        self._is_blinking = True
        self.expression_changed.emit(self._current_expression)
        # 眨眼持续 150ms
        QTimer.singleShot(150, self._finish_blink)

    def _finish_blink(self):
        self._is_blinking = False
        self.expression_changed.emit(self._current_expression)
