"""
梅尔桌宠 - 养成状态面板
半透明 overlay，显示好感度、心情、统计等信息
背景：ev312b.png（梅尔 CG 图）
"""
import os
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QProgressBar, QFrame,
    QGraphicsOpacityEffect
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap, QFont, QPainter, QColor


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(BASE_DIR, "ev312b.png")


FONT_STYLE = """
    QWidget {
        color: #ffffff;
        font-family: "Microsoft YaHei", "SimHei", sans-serif;
    }
    QLabel {
        background: transparent;
    }
"""


class StatusPanel(QWidget):
    """梅尔养成状态面板 — 半透明 floating window"""

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.memory = memory
        self._build_ui()
        self.refresh()
        # 每 5 秒自动刷新（好感度等可能有变化）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(5000)

    def _build_ui(self):
        self.setWindowTitle("梅尔酱 - 养成状态")
        self.setFixedSize(400, 600)  # 稍微增高一点，让布局更舒适
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 背景
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, 400, 600)
        if os.path.exists(BG_PATH):
            pix = QPixmap(BG_PATH).scaled(
                400, 600, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            self.bg_pix = pix
        else:
            self.bg_pix = None

        # 主布局
        main_layout = QVBoxLayout(self)
        # 【修改】上边距从 300 改为 30，让文字区域上移
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(12)

        # ===== 标题区 =====
        title = QLabel("🐱 梅尔酱养成日记")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #fff;")
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        # ===== 好感度区 =====
        section1 = QFrame()
        section1.setStyleSheet("QFrame { background: rgba(0,0,0,140); border-radius: 10px; padding: 8px; }")
        s1 = QVBoxLayout(section1)

        self.tier_label = QLabel()
        self.tier_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffcc80;")
        s1.addWidget(self.tier_label)

        self.aff_bar = QProgressBar()
        self.aff_bar.setRange(0, 100)
        self.aff_bar.setTextVisible(True)
        self.aff_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgba(255,255,255,60);
                border-radius: 6px;
                background: rgba(255,255,255,40);
                height: 22px;
                text-align: center;
                color: white;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                border-radius: 5px;
            }
        """)
        s1.addWidget(self.aff_bar)

        self.tier_quote = QLabel()
        self.tier_quote.setStyleSheet("font-size: 14px; color: #ccc; font-style: italic;")
        self.tier_quote.setWordWrap(True)
        s1.addWidget(self.tier_quote)

        main_layout.addWidget(section1)

        # ===== 心情 + 统计区 =====
        section2 = QFrame()
        section2.setStyleSheet("QFrame { background: rgba(0,0,0,140); border-radius: 10px; padding: 8px; }")
        s2 = QVBoxLayout(section2)

        self.mood_label = QLabel()
        self.mood_label.setStyleSheet("font-size: 16px;")
        s2.addWidget(self.mood_label)

        main_layout.addWidget(section2)

        # ===== 统计区 =====
        section3 = QFrame()
        section3.setStyleSheet("QFrame { background: rgba(0,0,0,140); border-radius: 10px; padding: 8px; }")
        s3 = QVBoxLayout(section3)

        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("font-size: 14px; color: #ddd;")
        self.stats_label.setWordWrap(True)
        s3.addWidget(self.stats_label)

        self.memory_label = QLabel()
        self.memory_label.setStyleSheet("font-size: 13px; color: #aac;")
        self.memory_label.setWordWrap(True)
        s3.addWidget(self.memory_label)

        main_layout.addWidget(section3)

        # 底部关闭提示
        hint = QLabel("按 ESC 或右键关闭")
        hint.setStyleSheet("font-size: 11px; color: #aaa;")
        hint.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(hint)

        self.setLayout(main_layout)

    def paintEvent(self, event):
        """自定义绘制：先画背景图，再画半透明整体暗化层（不覆盖文字）"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.bg_pix:
            # 绘制背景图
            painter.drawPixmap(0, 0, self.bg_pix)
            # 【修改】只保留整体暗化，去掉底部渐变遮罩
            painter.fillRect(0, 0, self.width(), self.height(),
                             QColor(0, 0, 0, 70))  # 略微加深以保证文字可读
        else:
            painter.fillRect(self.rect(), QColor(20, 20, 40, 230))

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.close()
        self._drag_start = event.globalPos()
        self._win_start = self.pos()

    def mouseMoveEvent(self, event):
        if hasattr(self, '_drag_start'):
            delta = event.globalPos() - self._drag_start
            self.move(self._win_start + delta)

    def mouseReleaseEvent(self, event):
        self._drag_start = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def refresh(self):
        """刷新面板数据"""
        m = self.memory
        aff = m.get_affection()
        tier = m.get_affection_tier()
        mood = m.get_mood()
        total_chats = m.get_total_chats()
        days = m.get_total_days()
        today = m.get_today_chat_count()

        # 好感度等级
        self.tier_label.setText(f"❤️ {tier[1]}  Lv.{aff}")

        # 好感进度条
        self.aff_bar.setValue(aff)

        # 等级描述
        self.tier_quote.setText(f"「{tier[2]}」")

        # 心情
        mood_emoji = {"平静": "😶", "开心": "😊", "忧郁": "😔",
                      "烦躁": "😤", "困倦": "😴", "期待": "🤗"}
        emoji = mood_emoji.get(mood, "😶")
        self.mood_label.setText(f"{emoji} 心情：{mood}")

        # 统计
        self.stats_label.setText(
            f"📅 相识 {days} 天  |  💬 对话 {total_chats} 次  |  🗣 今日 {today} 次"
        )

        # 重要记忆
        mems = m.get_important_memories(4)
        if mems:
            self.memory_label.setText(
                "📝 梅尔的记忆：\n" + "\n".join(f"  · {x}" for x in mems)
            )
        else:
            self.memory_label.setText("📝 梅尔对你还不太了解喵…")

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
