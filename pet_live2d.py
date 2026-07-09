"""
梅尔桌宠 - Live2D 版 (QWebEngine + Live2D Cubism 4 Web SDK)
"""
import sys
import json
import os
import random
import signal

# 必须在 QApplication 之前
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

from PyQt5.QtWidgets import (
    QApplication, QLabel, QWidget, QMenu, QAction, QSystemTrayIcon,
    QVBoxLayout,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtGui import QPixmap, QIcon, QFont, QColor, QPainter
from PyQt5.QtCore import (
    Qt, QTimer, QPoint, QUrl, QObject, pyqtSlot, pyqtSignal, QThread,
)
from PyQt5.QtNetwork import QNetworkProxy

from chat import ChatEngine, create_engine_from_config
ScreenWatcher = None  # 延迟导入


def wrap_text(text: str, width: int = 12) -> str:
    result = []
    line = ""
    for ch in text:
        line += ch
        if len(line) >= width:
            result.append(line)
            line = ""
    if line:
        result.append(line)
    return "\n".join(line)


class DialogueBubble(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("""
            QLabel {
                background: rgba(30, 30, 30, 210);
                color: white;
                padding: 10px 14px;
                border-radius: 14px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }
        """)
        self.setWordWrap(True)
        self.setMaximumWidth(280)
        self.setMinimumWidth(120)
        self.hide()

    def show_text(self, text: str, duration_ms: int = 5000):
        self.setText(text)
        self.adjustSize()
        self.show()
        QTimer.singleShot(duration_ms, self.hide)


class Live2DBridge(QObject):
    """QWebChannel 桥接：Python ↔ JavaScript"""
    clicked = pyqtSignal()

    @pyqtSlot(str)
    def onEvent(self, event: str):
        if event == "click":
            self.clicked.emit()


class ChatWorker(QThread):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, engine: ChatEngine, message: str):
        super().__init__()
        self.engine = engine
        self.message = message

    def run(self):
        try:
            reply, mood = self.engine.chat(self.message)
            self.finished.emit(reply, mood)
        except Exception as e:
            self.error.emit(str(e))


class MeaPetLive2D(QWidget):
    """梅尔桌宠 Live2D Web 渲染版"""

    def __init__(self, config_path: str = "config.json"):
        super().__init__()
        self.config = self._load_config(config_path)
        display_cfg = self.config.get("display", {})
        self._scale = display_cfg.get("scale", 0.5)

        # 窗口设置
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 布局
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # 禁用代理（避免 GUI 卡住）
        QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.NoProxy))

        # WebEngine 视图
        self.webview = QWebEngineView(self)
        settings = self.webview.settings()
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.ErrorPageEnabled, False)
        self.webview.setAttribute(Qt.WA_TranslucentBackground)
        self.webview.page().setBackgroundColor(Qt.transparent)
        self.layout.addWidget(self.webview)

        # QWebChannel
        self._bridge = Live2DBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self.webview.page().setWebChannel(self._channel)
        self._bridge.clicked.connect(self._on_web_click)

        # 加载 HTML
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "live2d", "index.html")
        self.webview.load(QUrl.fromLocalFile(html_path))

        # 窗口尺寸
        w = int(525 * self._scale)
        h = int(1043 * self._scale)
        self.resize(w, h)

        # 对话气泡
        self.bubble = DialogueBubble(self)

        # LLM 引擎
        self.chat_engine = create_engine_from_config(self.config)
        self._awaiting_reply = False

        # 拖拽状态
        self._dragging = False
        self._drag_offset = QPoint()
        self._head_press_y = None

        # 上下文菜单 + 托盘
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._setup_tray()

        # 放置右下角
        self._place_bottom_right()

        # 空闲 + 屏幕观察
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._idle_action)
        self._idle_timer.start(25000)

        self._watcher_timer = QTimer(self)
        self._watcher_timer.timeout.connect(self._do_screen_watch)
        self._start_watcher_timer()

        self.show()

    def _load_config(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor(255, 180, 100))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        self.tray.setToolTip("梅尔桌宠 🐱")
        menu = QMenu()
        menu.addAction("显示/隐藏", self._toggle_visibility)
        menu.addAction("📸 看看我在干嘛", self._do_screen_watch)
        menu.addAction("退出", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def _quit(self):
        self._idle_timer.stop()
        self._watcher_timer.stop()
        if hasattr(self, 'tray'):
            self.tray.hide()
        QApplication.quit()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: rgba(30,30,30,230); color: white;
                    border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background: rgba(255,255,255,60); }
        """)
        menu.addAction("📸 看看我在干嘛", self._do_screen_watch)
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        menu.exec_(self.mapToGlobal(pos))

    def _place_bottom_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.right() - self.width() - 50,
            screen.bottom() - self.height()
        )

    def _on_web_click(self):
        """Web 页面点击 - 进入对话"""
        self._start_chat()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.pos()
            self._head_press_y = event.y()

    def mouseMoveEvent(self, event):
        if not (self._dragging and event.buttons() & Qt.LeftButton):
            return
        # 摸头检测
        if self._head_press_y is not None and self._head_press_y < self.height() * 0.35:
            if abs(event.x() - self._drag_offset.x()) > 50:
                self._on_head_patted()
                self._head_press_y = None
                return
        self.move(self.pos() + event.pos() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._head_press_y = None

    def _on_head_patted(self):
        reactions = [
            ("……别摸我头发。", "annoyed"),
            ("……有事吗？", "curious"),
            ("哼。", "melancholy"),
            ("……", "shy"),
        ]
        text, _ = random.choice(reactions)
        self._show_bubble(text, 3000)

    def _idle_action(self):
        pass

    def _show_bubble(self, text: str, duration_ms: int = 5000):
        wrapped = wrap_text(text, 12)
        self.bubble.show_text(f"🐱 {wrapped}", duration_ms)

    def show_reply(self, text: str, mood: str = "neutral"):
        self._show_bubble(text, 6000)

    def _start_chat(self):
        prompts = ["你好呀", "你在干嘛", "今天过得怎么样"]
        prompt = random.choice(prompts)
        self._show_bubble("……？", 2000)
        QTimer.singleShot(1500, lambda: self._do_chat(prompt))

    def _do_chat(self, message: str):
        if self._awaiting_reply:
            return
        self._awaiting_reply = True
        self._chat_worker = ChatWorker(self.chat_engine, message)
        self._chat_worker.finished.connect(self._on_chat_done)
        self._chat_worker.error.connect(self._on_chat_error)
        self._chat_worker.start()

    def _on_chat_done(self, reply: str, mood: str):
        self.show_reply(reply, mood)
        self._awaiting_reply = False

    def _on_chat_error(self, err: str):
        self.show_reply("……喵。", "annoyed")
        self._awaiting_reply = False

    def _start_watcher_timer(self):
        ms = random.randint(180_000, 360_000)
        self._watcher_timer.start(ms)

    def _do_screen_watch(self):
        global ScreenWatcher
        if ScreenWatcher is None:
            from watcher import ScreenWatcher
        if self._awaiting_reply:
            self._start_watcher_timer()
            return
        self._awaiting_reply = True
        self._watcher = ScreenWatcher(
            ollama_host=self.config.get("llm", {}).get("host", "http://127.0.0.1:11434"),
            model="minicpm-v",
        )
        self._watcher.result_ready.connect(self._on_watch_result)
        self._watcher.error.connect(self._on_watch_error)
        self._watcher.start()

    def _on_watch_result(self, text: str, mood: str):
        self.show_reply(text, mood)
        self._awaiting_reply = False
        self._start_watcher_timer()

    def _on_watch_error(self, err: str):
        self._awaiting_reply = False
        self._start_watcher_timer()

    def closeEvent(self, event):
        self._quit()
        super().closeEvent(event)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    pet = MeaPetLive2D("config.json")
    QTimer.singleShot(5000, lambda: pet.show_reply("……哼，来了喵。", "happy"))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
