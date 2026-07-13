"""
梅尔桌宠 - 养成状态面板
半透明 overlay，显示好感度、心情、统计等信息
"""
import os
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QProgressBar, QFrame, QPushButton,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap, QPainter, QColor
from meapet.desktop import status_language
from meapet.desktop.icons import standard_icon
from meapet.desktop.theme import STATUS_PANEL_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)
from meapet.paths import project_path


BG_PATH = project_path("ev312b.png")


class StatusPanel(QWidget):
    """梅尔养成状态面板 — 半透明 floating window"""

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.memory = memory
        self._build_ui()
        self.refresh()
        # 每 5 秒自动刷新（好感度等可能有变化）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(5000)

    def _build_ui(self):
        self.setWindowTitle("梅尔酱 - 养成状态")
        self.setObjectName("StatusPanelRoot")
        self.setFixedSize(440, 620)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName("梅尔养成状态")
        self.setAccessibleDescription("查看好感度、心情、对话统计和重要记忆")
        set_scaled_stylesheet(self, STATUS_PANEL_STYLE)

        # 背景
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, self.width(), self.height())
        if os.path.exists(BG_PATH):
            pix = QPixmap(BG_PATH).scaled(
                self.width(), self.height(), Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            self.bg_pix = pix
        else:
            self.bg_pix = None

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 22, 24, 22)
        main_layout.setSpacing(12)

        # ===== 标题区 =====
        header = QHBoxLayout()
        header.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        eyebrow = QLabel("COMPANION STATUS")
        eyebrow.setObjectName("PanelEyebrow")
        heading.addWidget(eyebrow)
        title = QLabel("梅尔的养成日记")
        title.setObjectName("PanelTitle")
        heading.addWidget(title)
        header.addLayout(heading, 1)

        self.close_button = QPushButton("关闭")
        self.close_button.setIcon(standard_icon("close"))
        self.close_button.setObjectName("PanelCloseButton")
        self.close_button.setMinimumSize(64, MIN_TARGET_SIZE)
        self.close_button.setAccessibleName("关闭养成状态")
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button, 0, Qt.AlignTop)
        main_layout.addLayout(header)

        # ===== 好感度区 =====
        section1 = QFrame()
        section1.setObjectName("StatusCard")
        s1 = QVBoxLayout(section1)
        s1.setContentsMargins(16, 14, 16, 16)
        s1.setSpacing(8)

        relationship_caption = QLabel("RELATIONSHIP")
        relationship_caption.setObjectName("PanelEyebrow")
        s1.addWidget(relationship_caption)

        self.tier_label = QLabel()
        self.tier_label.setObjectName("TierLabel")
        self.tier_label.setAccessibleName("当前好感等级")
        s1.addWidget(self.tier_label)

        self.aff_bar = QProgressBar()
        self.aff_bar.setRange(0, 100)
        self.aff_bar.setTextVisible(True)
        self.aff_bar.setFormat("好感度 %v / %m")
        self.aff_bar.setAccessibleName("好感度进度")
        s1.addWidget(self.aff_bar)

        self.tier_quote = QLabel()
        self.tier_quote.setObjectName("QuoteLabel")
        self.tier_quote.setAccessibleName("当前关系描述")
        self.tier_quote.setWordWrap(True)
        s1.addWidget(self.tier_quote)

        main_layout.addWidget(section1)

        # ===== 心情 + 统计区 =====
        section2 = QFrame()
        section2.setObjectName("StatusCard")
        s2 = QVBoxLayout(section2)
        s2.setContentsMargins(16, 14, 16, 14)
        s2.setSpacing(6)

        mood_caption = QLabel("CURRENT MOOD")
        mood_caption.setObjectName("PanelEyebrow")
        s2.addWidget(mood_caption)

        self.mood_label = QLabel()
        self.mood_label.setObjectName("StatsLabel")
        self.mood_label.setAccessibleName("当前心情")
        s2.addWidget(self.mood_label)

        main_layout.addWidget(section2)

        # ===== 统计区 =====
        section3 = QFrame()
        section3.setObjectName("StatusCard")
        s3 = QVBoxLayout(section3)
        s3.setContentsMargins(16, 14, 16, 16)
        s3.setSpacing(8)

        memories_caption = QLabel("TOGETHER")
        memories_caption.setObjectName("PanelEyebrow")
        s3.addWidget(memories_caption)

        self.stats_label = QLabel()
        self.stats_label.setObjectName("StatsLabel")
        self.stats_label.setAccessibleName("相处统计")
        self.stats_label.setWordWrap(True)
        s3.addWidget(self.stats_label)

        self.memory_label = QLabel()
        self.memory_label.setObjectName("MemoryLabel")
        self.memory_label.setAccessibleName("梅尔的重要记忆")
        self.memory_label.setWordWrap(True)
        s3.addWidget(self.memory_label)

        main_layout.addWidget(section3)

        # 底部关闭提示
        hint = QLabel("按 ESC 或右键关闭")
        hint.setObjectName("PanelHint")
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
        if getattr(self, '_drag_start', None) is not None:
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
        self.tier_label.setText(f"{tier[1]}  ·  Lv.{aff}")

        # 好感进度条
        self.aff_bar.setValue(aff)

        # 等级描述
        self.tier_quote.setText(f"「{tier[2]}」")

        # 心情
        mood_emoji = {"平静": "😶", "开心": "😊", "忧郁": "😔",
                      "烦躁": "😤", "困倦": "😴", "期待": "🤗"}
        emoji = mood_emoji.get(mood, "😶")
        self.mood_label.setText(f"{mood}（{emoji}）")

        # 统计
        self.stats_label.setText(
            f"相识 {days} 天  ·  对话 {total_chats} 次  ·  今日 {today} 次"
        )

        # 重要记忆
        mems = m.get_important_memories(4)
        if mems:
            self.memory_label.setText(
                "梅尔记住了：\n" + "\n".join(f"  · {x}" for x in mems)
            )
        else:
            self.memory_label.setText(status_language.empty_memories())

        self.setAccessibleDescription(
            f"好感等级 {tier[1]}，好感度 {aff}，心情 {mood}，累计对话 {total_chats} 次"
        )

    def showEvent(self, event):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(5000)
        super().showEvent(event)

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
