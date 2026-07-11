
"""
梅尔桌宠 - 主程序
透明异形窗口 + 拖拽移动 + 表情切换 + 对话气泡
"""
import sys
import os
# embeddable Python 的 ._pth 文件会抑制默认 sys.path，
# 手动把项目目录加进去，否则 import utils 等本地模块会找不到
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)
import json
import random
import time
import threading
import socket  # 必须在 PyQt5 之前导入（避免 QtNetwork hook 冲突）
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QLabel, QWidget, QMenu, QAction, QSystemTrayIcon,
    QVBoxLayout, QHBoxLayout, QInputDialog, QFrame, QMessageBox,
    QDialog, QSlider, QPushButton
)
from PyQt5.QtGui import QPixmap, QIcon, QFont, QColor
from PyQt5.QtCore import Qt, QTimer, QPoint, QThread, pyqtSignal, QObject, QRectF

from utils import safe_print, log_error, ensure_utf8_stdout

# Windows GBK 兼容：统一包装 stdout（仅一次！在各模块中不再重复包装）
ensure_utf8_stdout()

from renderer import SpriteRenderer, MOOD_TO_EXPRESSION
from live2d_widget import Live2DModel, Live2DWidget, init_live2d, dispose_live2d
from chat import ChatEngine, create_engine_from_config, SYSTEM_PROMPT
from memory import MeaMemory
from status_panel import StatusPanel
from chat_input import ChatInputBox
from watcher import ScreenWatcher
from tts import MeaTTS

# 在 QApplication 创建前设置环境变量
os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")

if sys.platform == "win32":
    import win32gui
    # 删除部分无用导入


def focusInEvent(self, event):
    super().focusInEvent(event)
    # 重置输入法状态，让 Fcitx5 知道这个窗口需要中文
    from PyQt5.QtGui import QInputMethod
    QInputMethod.instance().reset()


def wrap_text(text: str, width: int = 10) -> str:
    """中文按字符换行"""
    result = []
    line = ""
    for ch in text:
        line += ch
        if len(line) >= width:
            result.append(line)
            line = ""
    if line:
        result.append(line)
    return "\n".join(result)


class DialogueBox(QWidget):
    """Galgame 风格高级消息框 - 顶部姓名牌 + 半透明渐变底框 + 淡入淡出动画"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._opacity = 1.0
        self._fade_step = 0.0
        self._fading = False
        self._fade_out = False

        # 外容器
        self._container = QFrame(self)
        self._container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(18, 18, 38, 240),
                    stop:0.3 rgba(16, 16, 34, 245),
                    stop:0.7 rgba(12, 12, 28, 248),
                    stop:1 rgba(8, 8, 22, 250));
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 12px;
            }
        """)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # 姓名标签（顶部突出的标签）
        self.name_label = QLabel("梅尔")
        self.name_label.setStyleSheet("""
            QLabel {
                background: rgba(60, 50, 80, 245);
                color: #FFB6C1;
                padding: 5px 20px;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                border-bottom: 2px solid rgba(255, 182, 193, 80);
                font-size: 13px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }
        """)
        self.name_label.setFixedHeight(32)
        self.name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.name_label.hide()
        container_layout.addWidget(self.name_label)

        # 内容标签
        self.text_label = QLabel()
        self.text_label.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #F0F0F0;
                padding: 14px 20px 16px 20px;
                font-size: 15px;
                font-family: "Microsoft YaHei";
                line-height: 1.5;
            }
        """)
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumWidth(260)
        self.text_label.setMinimumHeight(40)
        container_layout.addWidget(self.text_label)

        # 底部装饰线
        self._deco_line = QLabel()
        self._deco_line.setFixedHeight(3)
        self._deco_line.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(255, 182, 193, 0),
                stop:0.3 rgba(255, 182, 193, 100),
                stop:0.7 rgba(255, 182, 193, 100),
                stop:1 rgba(255, 182, 193, 0));
        """)
        container_layout.addWidget(self._deco_line)

        self._container.adjustSize()

        # 淡入淡出计时器
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fadeout)

        self.setWindowOpacity(self._opacity)
        self.hide()

    def show_text(self, text: str, duration_ms: int = 6000, name: str = "梅尔"):
        import re
        clean_text = re.sub(r'【.*?】', '', text).strip()
    
        # 1. 【关键】使用你定义的 wrap_text 手动处理中文换行
        # 假设每行显示 16 个汉字（根据字体大小调整）
        wrapped_text = wrap_text(clean_text, width=16) 
        self.text_label.setText(wrapped_text)
    
        # 2. 关闭自动换行，改由我们手动控制
        self.text_label.setWordWrap(False) 
    
        # ... 姓名标签设置保持不变 ...
        self.name_label.setText(f" {name} ")
        self.name_label.show()

        # 3. 重新计算尺寸：基于换行后的行数
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.text_label.font())
        pad_h = 40
        pad_v = 30
        name_h = 32
    
        # 获取最长行的像素宽度
        lines = wrapped_text.split('\n')
        max_line_w = max(fm.horizontalAdvance(line) for line in lines) if lines else 0
        safe_margin = fm.averageCharWidth()  # 约等于一个汉字的宽度
        content_w = max(220, min(max_line_w + pad_h + safe_margin, 600))
    
        # 高度 = 行数 × 行高 + padding
        line_height = fm.lineSpacing()  # 推荐用 lineSpacing 而非 boundingRect.height
        content_h = len(lines) * line_height + pad_v
        content_h = max(50, min(content_h, 350))

        # 4. 【关键】使用 setMinimumWidth / setFixedHeight 代替 setFixedSize
        # 允许宽度有弹性，但高度固定以防止跳动
        self.text_label.setMinimumWidth(content_w)
        self.text_label.setFixedHeight(content_h)
    
        total_w = content_w
        total_h = name_h + content_h + 3
    
        self.setFixedSize(total_w, total_h)
        self._container.setFixedSize(total_w, total_h)
        self.name_label.setFixedWidth(total_w)
        self._deco_line.setFixedWidth(total_w)

        # ... 后续透明度重置和定时器逻辑保持不变 ...
        # 重置透明度到完全不透明
        self._opacity = 1.0
        self._fading = False
        self._fade_out = False
        self.setWindowOpacity(1.0)

        self.show()
        self.raise_()

        # 自动隐藏（0=持续显示，不自动隐藏）
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def _animate(self):
        """透明度动画"""
        if self._fade_out:
            self._opacity -= self._fade_step
            if self._opacity <= 0.0:
                self._opacity = 0.0
                self._anim_timer.stop()
                self._fading = False
                self.hide()
                return
        else:
            self._opacity += self._fade_step
            if self._opacity >= 1.0:
                self._opacity = 1.0
                self._anim_timer.stop()
                self._fading = False
                return
        self.setWindowOpacity(self._opacity)

    def _start_fadeout(self):
        """开始淡出"""
        self._fading = True
        self._fade_out = True
        self._fade_step = 0.06
        self._anim_timer.start(25)

    def close(self):
        self._anim_timer.stop()
        self._hide_timer.stop()
        super().close()


class ChatWorker:
    """后台对话线程 — threading.Thread + 主线程轮询"""
    def __init__(self, engine: ChatEngine, message: str):
        self.engine = engine
        self.message = message
        self._thread = None
        self._done = False
        self._result = None
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            reply, mood = self.engine.quick_chat(self.message)
            self._result = (reply, mood)
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
        self._done = True

    @property
    def done(self):
        return self._done

    def get_result(self):
        return self._result, self._error

    def isRunning(self):
        return self._thread is not None and self._thread.is_alive()

    def terminate(self):
        pass

    def wait(self, timeout_ms=1000):
        if self._thread:
            self._thread.join(timeout=timeout_ms / 1000)

    def deleteLater(self):
        self._thread = None


class TTSWorker:
    """后台 TTS 合成线程 — threading.Thread + 主线程轮询"""
    def __init__(self, tts: MeaTTS, text: str, mood: str = "neutral"):
        self.tts = tts
        self.text = text
        self.mood = mood
        self._thread = None
        self._done = False
        self._result = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            result = self.tts.speak(self.text, mood=self.mood)
            if result and result[0]:
                wav, lang = result
                self._result = f"{wav}|{lang}"
        except Exception:
            self._result = None
        self._done = True

    @property
    def done(self):
        return self._done

    def get_result(self):
        return self._result

    def isRunning(self):
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout_ms=1000):
        if self._thread:
            self._thread.join(timeout=timeout_ms / 1000)

    def deleteLater(self):
        self._thread = None


class SizeScaleDialog(QDialog):
    """立绘大小调节对话框 — 滑块实时预览"""
    def __init__(self, current_factor: float, pet=None):
        super().__init__(pet)
        self._pet = pet
        self._factor = current_factor
        self._original = current_factor  # 取消时还原
        self.setWindowTitle("调节立绘大小")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(280, 130)

        container = QFrame(self)
        container.setStyleSheet("""
            QFrame#sizeFrame {
                background: rgba(30,30,40,240);
                border: 1px solid rgba(255,255,255,30);
                border-radius: 12px;
            }
            QLabel {
                color: #F0F0F0;
                font-size: 14px;
                font-family: "Microsoft YaHei","Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;
            }
            QLabel#pctLabel {
                font-size: 20px;
                color: #FFB6C1;
                font-weight: bold;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(255,255,255,30);
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFB6C1;
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(255,182,193,120);
                border-radius: 3px;
            }
            QPushButton {
                background: rgba(255,255,255,20);
                color: #F0F0F0;
                border: 1px solid rgba(255,255,255,30);
                border-radius: 6px;
                padding: 5px 14px;
                font-size: 12px;
                font-family: "Microsoft YaHei","Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;
            }
            QPushButton:hover {
                background: rgba(255,255,255,40);
            }
            QPushButton#okBtn {
                background: rgba(255,182,193,60);
                border: 1px solid rgba(255,182,193,80);
            }
            QPushButton#okBtn:hover {
                background: rgba(255,182,193,90);
            }
        """)
        container.setObjectName("sizeFrame")

        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(16, 12, 16, 12)
        c_layout.setSpacing(8)

        # 百分比标签
        self._pct_label = QLabel(f"{int(current_factor * 100)}%", self)
        self._pct_label.setObjectName("pctLabel")
        self._pct_label.setAlignment(Qt.AlignCenter)

        # 滑块 (30%–300%)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setRange(30, 300)
        self._slider.setValue(int(current_factor * 100))
        self._slider.valueChanged.connect(self._on_slider)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        reset_btn = QPushButton("重置", self)
        reset_btn.clicked.connect(self._reset)
        ok_btn = QPushButton("确定", self)
        ok_btn.setObjectName("okBtn")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消", self)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)

        c_layout.addWidget(self._pct_label)
        c_layout.addWidget(self._slider)
        c_layout.addLayout(btn_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        self.setLayout(outer)

    def _on_slider(self, value: int):
        self._factor = value / 100.0
        self._pct_label.setText(f"{value}%")
        if self._pet and hasattr(self._pet, '_size_factor_preview'):
            self._pet._size_factor_preview(self._factor)

    def _reset(self):
        self._slider.setValue(100)

    def get_value(self) -> float:
        return self._factor

    def reject(self):
        """取消时还原到打开前的值"""
        self._factor = self._original
        if self._pet and hasattr(self._pet, '_size_factor_preview'):
            self._pet._size_factor_preview(self._original)
        super().reject()


class MeaPet(QWidget):
    """梅尔桌宠主窗口"""

    def __init__(self, config_path: str = "config.json"):
        super().__init__()
        self.config = self._load_config(config_path)
        self._awaiting_reply = False
        self._pending_input = None
        self._chat_worker = None
        self._tts_worker = None
        self._dragging = False
        self._standby = False
        self._standby_bubble = None
        safe_print("[__init__] config loaded")

        self._init_window()
        safe_print("[__init__] window done")
        self._init_renderer()
        safe_print("[__init__] renderer done")
        safe_print("[__init__] 正在加载对话模型，如果卡住了请重启")
        self._init_chat()
        safe_print("[__init__] chat done")
        self._init_tts()
        safe_print("[__init__] tts done")
        self._init_watcher()
        safe_print("[__init__] watcher done")
        self._setup_tray()
        safe_print("[__init__] tray done")
        self._init_interaction()
        safe_print("[__init__] interaction done")
        self._init_timers()
        safe_print("[__init__] timers done")

        self._place_bottom_right()
        safe_print("[__init__] placed")
        self.show()
        safe_print("[__init__] shown")
        self._apply_hit_region()
        safe_print("[__init__] hit region done")

    def _init_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool              # ← 关键：Tool 比 Window 更像"悬浮工具"
            | Qt.SubWindow         # 辅助
        )
        # 启用输入法支持
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        # 确保窗口可以获得焦点（否则输入法不会激活）
        self.setFocusPolicy(Qt.StrongFocus)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)  # X11 置顶加固
        # 给 X11 WM 打 hint：这是 utility/dock，别 tile 我
        self.setProperty("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_UTILITY")
        self.setWindowTitle("mea-pet")
        # 或者更狠的：
        # self.setWindowRole("desktop-pet")  # 有些 WM 认 role
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.bubble = DialogueBox(None)


        # 对话气泡（独立浮窗）
        self.bubble = DialogueBox(None)

    def _init_renderer(self):
        """初始化渲染器：先 PNG（快速显示），Live2D 延后异步加载"""
        char = self.config.get("character", {})
        display_cfg = self.config.get("display", {})
        self._scale = display_cfg.get("scale", 1.0) * 1.25  # 放大 25%
        self._size_factor = display_cfg.get("size_factor", 1.0)
        self._use_live2d = False
        self._l2d_model = None
        self._l2d_pending = False
        self.renderer = None
        self.sprite_label = None

        # 先启用 PNG 渲染（快速，不阻塞）
        sprite_dir = self.config.get(
            "sprite_dir", os.path.join(os.path.dirname(__file__), "sprites")
        )
        outfit = char.get("default_outfit", "01")
        direction = char.get("default_direction", "A")
        self.sprite_label = QLabel(self)
        self.sprite_label.setAttribute(Qt.WA_TranslucentBackground)
        self.sprite_label.setStyleSheet("background: transparent;")
        self.sprite_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.sprite_label.show()
        self.renderer = SpriteRenderer(sprite_dir, outfit, direction)
        self.renderer.expression_changed.connect(self._on_sprite_changed)
        self._update_sprite()
        self.renderer.start_blink_animation()

        # Live2D 延后加载（不阻塞窗口显示）
        l2d_cfg = self.config.get("live2d", {})
        model_dir = l2d_cfg.get("model_dir", "")
        if l2d_cfg.get("enabled", False) and os.path.isdir(model_dir):
            self._l2d_pending = True
            QTimer.singleShot(1, self._deferred_init_live2d)

    def _deferred_init_live2d(self):
        """延后初始化 Live2D（不阻塞 __init__）"""
        try:
            # Live2D 全局初始化
            from live2d_widget import init_live2d
            init_live2d()

            # 先保存 PNG 标签的引用
            png_label = self.sprite_label

            self._init_live2d()

            # 隐藏并清理 PNG 标签
            if png_label:
                png_label.hide()
                png_label.deleteLater()

            self._use_live2d = True

            # 应用 size_factor（_use_live2d 已为 True，Live2D 分支生效）
            if self._size_factor != 1.0:
                self._size_factor_preview(self._size_factor)

            # 确保窗口和 Live2D 可见（避免切换后隐藏）
            self.show()
            self.raise_()
            if self.sprite_label:
                self.sprite_label.show()
                self.sprite_label.raise_()

            safe_print(f"[pet] Live2D 加载完成")
        except Exception as e:
            safe_print(f"[pet] Live2D 加载失败，使用 PNG: {e}")
            self._use_live2d = False

    def _init_chat(self):
        """初始化对话引擎 + 记忆系统"""
        self.memory = MeaMemory()
        self.memory.daily_maintenance()
        self.chat_engine = create_engine_from_config(self.config, self.memory)
        # 后台预加载模型到内存（让首次对话不再等模型加载）
        if self.chat_engine.backend == "ollama" and self.chat_engine.available:
            QTimer.singleShot(2000, self._show_warmup_status)

    def _show_warmup_status(self):
        """模型预加载后更新状态提示"""
        if self.chat_engine._warmed_up:
            self._show_bubble("✨ 梅尔准备好啦～双击对话喵", 3000)
        else:
            # 没完成可能是还在加载，不管它，双击时再试
            pass

    def _init_tts(self):
        """初始化 TTS"""
        self.tts = MeaTTS(self.config)

    def _init_watcher(self):
        """初始化屏幕观察器"""
        vision_model = self.config.get("vision", {}).get("model", "minicpm-v")
        self._watcher = ScreenWatcher(
            ollama_host=self.config.get("llm", {}).get("host", "http://127.0.0.1:11434"),
            vision_model=vision_model,
            chat_model=vision_model,  # 视觉模型兼做决策
        )
        self._watcher.result_ready.connect(self._on_watch_result)
        self._watcher.error.connect(self._on_watch_error)
        self._watcher.silent.connect(self._on_watch_silent)
        self._watcher.progress.connect(self._on_watch_progress)
        self._watcher.search_request.connect(self._on_search_request)
        self._last_interaction_time = time.time()

    def _init_interaction(self):
        """初始化交互状态"""
        self._last_interaction_time = time.time()
        self._head_press_x = None
        self._is_head_touching = False

    def _init_timers(self):
        """初始化定时器"""
        # 空闲动画定时器 — 每 20 秒
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._idle_action)
        self._idle_timer.start(20000)

        # 屏幕观察定时器 — 每 2~5 分钟随机截屏吐槽
        self._watcher_timer = QTimer(self)
        self._watcher_timer.timeout.connect(self._do_screen_watch)
        self._start_watcher_timer()

    # ========================
    # Live2D 辅助
    # ========================

    def _init_live2d(self):
        """初始化 Live2D widget 替代 PNG 立绘"""
        l2d_cfg = self.config.get("live2d", {})
        model_dir = l2d_cfg.get("model_dir", "")
        if not os.path.isdir(model_dir):
            self._use_live2d = False
            return
        self._l2d_model = Live2DModel(model_dir)
        widget = self._l2d_model.create_widget(self)
        self.sprite_label = widget
        # 连接摸头/摸尾巴信号
        widget.head_patted.connect(self._on_head_patted)
        widget.tail_patted.connect(self._on_tail_patted)
        widget.show()
        # Live2D 模型偏右偏上，左移下移让角色居中傻福吧你移你冯我靠
        # 我的发还有第二关还以为只在live2d_widget里
        w0 = widget.width()
        h0 = widget.height()
        shift_x = int(w0 * 0.00)
        shift_y = int(h0 * 0.00)
        widget.move(-shift_x, shift_y)
        widget.resize(w0 + shift_x, h0)
        self.resize(w0, h0)

    def _save_config(self):
        """保存配置到 config.json"""
        import json
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def _safe_renderer(self):
        """返回可用的渲染器（Live2D 或 PNG）"""
        if self._use_live2d and self._l2d_model:
            return self._l2d_model
        return self.renderer

    def _safe_set_mood(self, mood: str):
        r = self._safe_renderer()
        if r:
            r.set_mood(mood)

    def _safe_set_expression(self, expr: str):
        r = self._safe_renderer()
        if r:
            r.set_expression(expr)

    # ========================
    # 配置
    # ========================
    def _load_config(self, path: str) -> dict:
        """加载 JSON 配置"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    # ========================
    # 系统托盘
    # ========================
    def _setup_tray(self):
        """创建系统托盘图标和菜单"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # 用立绘做托盘图标（缩小）
        if self._use_live2d:
            # Live2D 模式下用默认透明图标
            icon = QIcon()
        else:
            pixmap = self.renderer.get_current_pixmap()
            icon = QIcon(pixmap.scaled(32, 32, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("梅尔桌宠")

        menu = QMenu()
        show_action = QAction("显示/隐藏", self)
        show_action.triggered.connect(self._toggle_visibility)
        menu.addAction(show_action)
        snap_tray = QAction("📸 看看我在干嘛", self)
        snap_tray.triggered.connect(lambda: self._do_screen_watch(force=True))
        menu.addAction(snap_tray)
        menu.addSeparator()
        # 开机自启
        auto_started = self._is_auto_start()
        auto_tray_text = "✅ 开机自启" if auto_started else "  开机自启"
        auto_tray_action = QAction(auto_tray_text, self)
        auto_tray_action.triggered.connect(self._toggle_auto_start)
        menu.addAction(auto_tray_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def _quit(self):
        if not self._use_live2d and self.renderer:
            self.renderer.stop_blink_animation()
        if self._use_live2d and self._l2d_model and self.sprite_label:
            self.sprite_label.shutdown()
        self._idle_timer.stop()
        self._watcher_timer.stop()
        # 安全停止后台线程
        if hasattr(self, '_watcher') and self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
        for attr in ['_chat_worker', '_tts_worker', '_speak_worker']:
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.wait(3000)
                except Exception:
                    pass
        if hasattr(self, 'tray'):
            self.tray.hide()
        if hasattr(self, 'bubble'):
            self.bubble.close()
        if hasattr(self, 'memory'):
            self.memory.close()
        QApplication.quit()

    # ========================
    # 开机自启
    # ========================
    def _is_auto_start(self) -> bool:
        """检查是否已注册开机自启"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ
            )
            val, _ = winreg.QueryValueEx(key, "MeaPet")
            winreg.CloseKey(key)
            return os.path.exists(val.split('"')[1] if '"' in val else val.split()[0])
        except Exception:
            return False

    def _toggle_auto_start(self):
        """切换开机自启"""
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE | winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, "MeaPet")
            # 已有 → 删除
            winreg.DeleteValue(key, "MeaPet")
            winreg.CloseKey(key)
            self._show_bubble("已关闭开机自启喵", 2000)
        except FileNotFoundError:
            # 没有 → 添加
            winreg.CloseKey(key)
            # 用 pythonw.exe 后台启动，避免弹黑框
            py = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(py):
                py = sys.executable
            pet_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet.py")
            cmd = f'"{py}" "{pet_path}"'
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "MeaPet", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            self._show_bubble("已开启开机自启喵 🖥️", 2000)

    # ========================
    # 上下文菜单（右键）
    # ========================
    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(30,30,30,230);
                color: white;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: rgba(255,255,255,60);
            }
        """)

        # 表情子菜单
        expr_menu = QMenu("切换表情", self)
        moods = [
            ("😊 默认", "neutral"), ("😄 开心", "happy"),
            ("😢 悲伤", "sad"), ("😳 害羞", "shy"),
            ("🤔 好奇", "curious"), ("😤 烦闷", "annoyed"),
            ("😔 忧郁", "melancholy")
        ]
        for label, mood in moods:
            action = QAction(label, self)
            action.triggered.connect(
                lambda checked, m=mood: self._safe_set_mood(m)
            )
            expr_menu.addAction(action)
        menu.addMenu(expr_menu)

        # 识图模型切换
        vision_menu = QMenu("🔍 识图模型", self)
        current_vision = self.config.get("vision", {}).get("model", "minicpm-v")
        vision_options = [
            ("minicpm-v (5.5G, 快)", "minicpm-v"),
            ("qwen2.5vl:7b (6G)", "qwen2.5vl:7b"),
        ]
        for label, model_name in vision_options:
            action = QAction(f"{'✅ ' if current_vision == model_name else '   '}{label}", self)
            action.triggered.connect(
                lambda checked, m=model_name: self._set_vision_model(m)
            )
            vision_menu.addAction(action)
        menu.addMenu(vision_menu)

        # 养成状态面板
        status_action = QAction("📊 养成状态", self)
        status_action.triggered.connect(self._show_status_panel)
        menu.addAction(status_action)

        # 待机开关
        standby_text = "💤 取消待机" if self._standby else "💤 待机（暂停识图）"
        standby_action = QAction(standby_text, self)
        standby_action.triggered.connect(self._toggle_standby)
        menu.addAction(standby_action)

        # Live2D / PNG 模式切换
        mode_text = "🎭 切回 PNG 立绘" if self._use_live2d else "🎭 切换到 Live2D"
        mode_action = QAction(mode_text, self)
        mode_action.triggered.connect(self._toggle_render_mode)
        menu.addAction(mode_action)

        # 立绘大小调节
        size_action = QAction("📐 立绘大小调节...", self)
        size_action.triggered.connect(self._open_size_dialog)
        menu.addAction(size_action)

        # 截图吐槽
        snap_action = QAction("📸 看看我在干嘛", self)
        snap_action.triggered.connect(lambda: self._do_screen_watch(force=True))
        menu.addAction(snap_action)

        menu.addSeparator()

        # 重置记忆
        reset_action = QAction("🔄 重置所有记忆", self)
        reset_action.triggered.connect(self._reset_memory)
        menu.addAction(reset_action)

        menu.addSeparator()

        # 开机自启
        auto_started = self._is_auto_start()
        auto_text = "✅ 开机自启" if auto_started else "  开机自启"
        auto_action = QAction(auto_text, self)
        auto_action.triggered.connect(self._toggle_auto_start)
        menu.addAction(auto_action)

        menu.addSeparator()

        # 再次配置
        reconf_action = QAction("⚙ 再次配置", self)
        reconf_action.triggered.connect(self._reopen_setup_wizard)
        menu.addAction(reconf_action)

        menu.addSeparator()
        menu.addAction("退出", self._quit)
        menu.exec_(self.mapToGlobal(pos))

    def _set_vision_model(self, model_name: str):
        """切换识图模型并保存到 config"""
        self.config.setdefault("vision", {})["model"] = model_name
        self._save_config()
        # 重新创建 ScreenWatcher
        if hasattr(self, '_watcher') and self._watcher is not None:
            try:
                self._watcher.result_ready.disconnect()
            except Exception:
                pass
            try:
                self._watcher.error.disconnect()
            except Exception:
                pass
            try:
                self._watcher.silent.disconnect()
            except Exception:
                pass
            try:
                self._watcher.progress.disconnect()
            except Exception:
                pass
            try:
                self._watcher.search_request.disconnect()
            except Exception:
                pass
            self._watcher.stop()
        chat_model = self.config.get("llm", {}).get("model", "qwen2.5:7b")
        self._watcher = ScreenWatcher(
            ollama_host=self.config.get("llm", {}).get("host", "http://127.0.0.1:11434"),
            vision_model=model_name,
            chat_model=chat_model,
        )
        self._watcher.result_ready.connect(self._on_watch_result)
        self._watcher.error.connect(self._on_watch_error)
        self._watcher.silent.connect(self._on_watch_silent)
        self._watcher.progress.connect(self._on_watch_progress)
        self._watcher.search_request.connect(self._on_search_request)
        short = model_name.split(":")[0]
        self._show_bubble(f"识图模型切换为 {short}", 2000)

    def _show_status_panel(self):
        """打开养成状态面板"""
        if not hasattr(self, '_status_panel') or self._status_panel is None:
            self._status_panel = StatusPanel(self.memory)
            self._status_panel.move(
                self.x() + self.width() + 10,
                self.y()
            )
        self._status_panel.show()
        self._status_panel.refresh()

    def _reset_memory(self):
        """重置所有记忆数据"""
        reply = QMessageBox.question(
            self, "确认重置",
            "确定要让梅尔忘掉一切喵？\n\n聊天记录、好感度、记忆都会清空。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.memory.reset_all()
            self._show_bubble("-什么都没发生喵。" if random.random() < 0.1 else "……你是谁喵？", 3000)

    def _reopen_setup_wizard(self):
        """重新打开配置向导"""
        try:
            from setup_wizard import SetupWizard
            self._setup_wizard = SetupWizard()
            self._setup_wizard.show()
        except Exception as e:
            safe_print(f"[pet] 启动配置向导失败: {e}")
            self._show_bubble(f"启动配置向导失败喵: {e}", 3000)

    # ========================
    # 立绘渲染
    # ========================
    def _update_sprite(self):
        """刷新立绘显示"""
        if self._use_live2d:
            return  # Live2D 自行渲染，不需要手动更新 pixmap
        pixmap = self.renderer.get_current_pixmap()
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            int(pixmap.width() * self._scale * self._size_factor),
            int(pixmap.height() * self._scale * self._size_factor),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.sprite_label.setPixmap(scaled)
        # 精灵左侧有透明留白且偏上，左移下移让角色居中
        sw, sh = scaled.width(), scaled.height()
        self.sprite_label.move(-int(sw * 0.00), int(sh * 0.00))
        self.sprite_label.resize(scaled.size())
        self.resize(scaled.size())

    def _on_sprite_changed(self, code: str):
        self._update_sprite()

    def _size_factor_preview(self, factor: float):
        """滑块拖动时实时预览立绘大小"""
        self._size_factor = factor
        if self._use_live2d and self.sprite_label:
            base_w, base_h = 400, 660
            new_w = max(80, int(base_w * factor))
            new_h = max(80, int(base_h * factor))
            self.sprite_label.resize(new_w, new_h)
            self.resize(new_w, new_h)
            self._apply_hit_region()
            QApplication.processEvents()
        else:
            pixmap = self.renderer.get_current_pixmap()
            if not pixmap.isNull():
                new_w = max(80, int(pixmap.width() * self._scale * factor))
                new_h = max(80, int(pixmap.height() * self._scale * factor))
                self.resize(new_w, new_h)
            self._update_sprite()
            self._apply_hit_region()
            QApplication.processEvents()
        self._position_bubble()

    def _open_size_dialog(self):
        """打开立绘大小调节滑块对话框"""
        dialog = SizeScaleDialog(self._size_factor, self)
        # 屏幕边界防护
        screen = QApplication.primaryScreen().availableGeometry()
        dlg_w, dlg_h = 280, 130
        x = self.x() + (self.width() - dlg_w) // 2
        y = self.y() + (self.height() - dlg_h) // 2
        x = max(screen.x(), min(x, screen.x() + screen.width() - dlg_w))
        y = max(screen.y(), min(y, screen.y() + screen.height() - dlg_h))
        dialog.move(x, y)

        if dialog.exec_() == QDialog.Accepted:
            new_factor = dialog.get_value()
            self._size_factor = new_factor
            self.config.setdefault("display", {})["size_factor"] = round(new_factor, 2)
            self._save_config()
            self._show_bubble(f"立绘大小已设为 {int(new_factor*100)}%", 1500)

    def _position_bubble(self):
        """把消息框定位到立绘下方，与腿部重叠"""
        if self.bubble.isVisible():
            # 水平居中
            bubble_x = self.pos().x() + (self.width() - self.bubble.width()) // 2
            # Live2D 模式下气泡再往上移（盖在腿部）
            offset = 100 if self._use_live2d else 30
            bubble_y = self.pos().y() + self.height() - self.bubble.height() - offset
            self.bubble.move(bubble_x, bubble_y)

    def _place_bottom_right(self):
        """放到桌面右下角"""
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.right() - self.width() - 50,
            screen.bottom() - self.height()
        )

    # ========================
    # 鼠标交互
    # ========================
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            head_threshold = int(self.height() * 0.35)
            self._is_head_touching = (event.y() < head_threshold)
            self._head_press_x = event.x() if self._is_head_touching else None
            self._dragging = True
            self._drag_offset = event.pos()

    def mouseMoveEvent(self, event):
        if not (self._dragging and event.buttons() & Qt.LeftButton):
            return
        # 摸头：头部区域横向拖拽 > 40px
        if self._is_head_touching and self._head_press_x is not None:
            if abs(event.x() - self._head_press_x) > 40:
                self._on_head_patted()
                self._is_head_touching = False
                self._head_press_x = None
                return
        # 拖拽窗口
        self.move(self.pos() + event.pos() - self._drag_offset)
        self._position_bubble()  # 气泡跟随移动

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._is_head_touching = False
        self._head_press_x = None

    def mouseDoubleClickEvent(self, event):
        """双击开启对话"""
        self._start_chat()

    # ========================
    # 摸头
    # ========================
    def _on_head_patted(self):
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
        self._interaction_speak(text, 3000, mood)
        QTimer.singleShot(3000, lambda: self._safe_set_mood("neutral"))

    def _on_tail_patted(self):
        """摸尾巴反应（Live2D 专属）"""
        self._record_interaction()
        reactions = [
            ("尾巴……不许碰喵！！", "angry"),
            ("……你想死一次吗？", "annoyed"),
            ("变态。", "annoyed"),
            ("……尾巴是很敏感的不知道吗。", "shy"),
        ]
        text, mood = random.choice(reactions)
        self._safe_set_mood(mood)
        self._interaction_speak(text, 3500, mood)
        QTimer.singleShot(4000, lambda: self._safe_set_mood("neutral"))

    def _interaction_speak(self, text: str, duration_ms: int, mood: str):
        """互动语音：优先缓存，否则走 TTS 合成"""
        cache_file = self._get_cached_interaction(text, "jp")
        if cache_file:
            self.show_reply(text, mood)
            self._play_audio(cache_file)
        else:
            self._speak_and_show(text, duration_ms, mood)

    def _safe_name(self, text: str) -> str:
        """文本 → 安全文件名"""
        safe = text.replace("……", "").replace("（", "").replace("）", "").replace(" ", "_").strip()
        return safe

    def _get_cached_interaction(self, text: str, lang: str) -> Optional[str]:
        """获取互动语音缓存（带语言前缀）"""
        if not self.tts:
            return None
        safe = self._safe_name(text)
        if not safe:
            return None
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_cache")
        path = os.path.join(cache_dir, f"{lang}_{safe}.wav")
        return path if os.path.exists(path) else None

    def _apply_hit_region(self):
        #设置窗口点击穿透区域（跨平台）
        if sys.platform == "win32":
            # Windows 原生：使用 GDI 区域（性能好）
            try:
                import win32gui
                hwnd = int(self.winId())
                w, h = self.width(), self.height()
                if not (w > 0 and h > 0):
                    return
                if self._use_live2d:
                    # Live2D 模式：椭圆裁剪
                    m = w // 16
                    t = h // 16
                    rgn = win32gui.CreateEllipticRgnIndirect((m, t, w - m, h - t))
                else:
                    # PNG 模式：矩形
                    rgn = win32gui.CreateRoundRectRgn(0, 0, w, h, 0, 0)
                win32gui.SetWindowRgn(hwnd, rgn, True)
                return
            except Exception as e:
                safe_print(f"[WARN] Win32 hit region failed, fallback to Qt mask: {e}")

        # 非 Windows 或 Win32 失败时：使用 Qt 的 setMask
        from PyQt5.QtGui import QPainterPath, QRegion
        from PyQt5.QtCore import QPoint, QRect
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        if self._use_live2d:
            # 椭圆遮罩
            path = QPainterPath()
            path.addEllipse(QRectF(w//16, h//16, w - w//8, h - h//8))

            region = QRegion(path.toFillPolygon().toPolygon())
        else:
            # 矩形遮罩（整个窗口）
            region = QRegion(0, 0, w, h)

        self.setMask(region)



    # ========================
    # 空闲动画
    # ========================
    def _idle_action(self):
        """随机空闲表情变化"""
        if random.random() < 0.4:
            return  # 60% 什么都不做
        moods = ["neutral", "happy", "curious", "melancholy"]
        self._safe_set_mood(random.choice(moods))

    # ========================
    # 对话气泡
    # ========================
    def _show_bubble(self, text: str, duration_ms: int = 5000):
        self.bubble.show_text(text, duration_ms)

    def _show_random_bubble(self, text: str):
        self._show_bubble(text, 3000)

    def _speak_and_show(self, text: str, duration_ms: int, mood: str = "neutral"):
        """显示文字 + 后台合成语音播放"""
        try:
            self.show_reply(text, mood)
        except Exception:
            pass
        if self.tts and self.tts.enabled and len(text.strip()) >= 2:
            # 保存当前文本供缓存命名用
            self._current_speaking_text = text
            cached = self.tts.get_cached(text)
            if cached:
                self._play_audio(cached)
                return
            # 保持引用防止 GC 导致 QThread 销毁
            self._speak_worker = TTSWorker(self.tts, text, mood=mood)
            self._speak_worker.start()
            self._ensure_tts_poll()

    def _on_speak_audio_ready(self, raw: str):
        """后台语音合成完成，播放并缓存"""
        wav_path = raw
        tts_lang = ""
        if "|" in raw:
            parts = raw.rsplit("|", 1)
            wav_path = parts[0]
            tts_lang = parts[1]
        if wav_path and os.path.exists(wav_path):
            # 缓存：用语言前缀统一命名
            if tts_lang:
                safe = self._safe_name(
                    self._current_speaking_text
                    if hasattr(self, "_current_speaking_text") else ""
                )
                if safe:
                    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_cache")
                    os.makedirs(cache_dir, exist_ok=True)
                    cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                    try:
                        shutil.copy2(wav_path, cache_path)
                    except Exception:
                        pass
            self._play_audio(wav_path)

    def show_reply(self, text: str, mood: str = "neutral", duration_ms: int = 6000):
        """显示 AI 回复，duration_ms 应与音频时长匹配"""
        self._safe_set_mood(mood)
        self._show_bubble(text, max(duration_ms, 3000))
        self._position_bubble()

    # ========================
    # LLM 对话
    # ========================
    def _start_chat(self):
        """双击触发：弹出 Galgame 风格输入框"""
        # 确定输入框位置（桌宠上方居中）
        input_x = self.pos().x() + (self.width() - 480) // 2
        input_y = self.pos().y() - 100
        if input_y < 30:
            input_y = self.pos().y() + self.height() + 20

        self._chat_input = ChatInputBox(None)
        self._chat_input.move(max(0, input_x), max(0, input_y))
        self._chat_input.text_submitted.connect(self._on_input_submit)
        self._chat_input.show()

    def _on_input_submit(self, text: str):
        """用户提交了输入"""
        self._record_interaction()
        safe_print(f"[pet] 收到输入: {text}")
        self._show_bubble(f"……？", 1500)
        self._position_bubble()
        QTimer.singleShot(1200, lambda: self._do_chat(text))

    def _do_chat(self, message: str):
        """执行 LLM 对话（后台线程）"""
        if self._awaiting_reply:
            safe_print(f"[pet] 对话被拒绝：正在等待回复中")
            return
        self._awaiting_reply = True
        self._safe_set_mood("talking")
        safe_print(f"[pet] 发送给 LLM: {message}")

        # 显示思考中提示
        self._show_bubble("💭 梅尔正在思考……", 0)  # 0 = 持久显示
        self._position_bubble()

        # 停止旧 worker（防止泄漏）
        if hasattr(self, '_chat_worker') and self._chat_worker is not None:
            if self._chat_worker.isRunning():
                self._chat_worker.terminate()
                self._chat_worker.wait(1000)
            self._chat_worker.deleteLater()
        if hasattr(self, '_chat_poll'):
            self._chat_poll.stop()

        # 超时保护（匹配 Ollama 读取超时 120s + 缓冲）
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        self._chat_timeout = QTimer(self)
        self._chat_timeout.setSingleShot(True)
        self._chat_timeout.timeout.connect(self._on_chat_timeout)
        self._chat_timeout.start(130000)

        self._chat_worker = ChatWorker(self.chat_engine, message)
        self._chat_worker.start()
        # 轮询 timer：每 100ms 检查 worker 是否完成
        self._chat_poll = QTimer(self)
        self._chat_poll.timeout.connect(self._poll_chat)
        self._chat_poll.start(100)

    def _poll_chat(self):
        """主线程轮询 ChatWorker 完成状态"""
        if not hasattr(self, '_chat_worker') or self._chat_worker is None:
            if hasattr(self, '_chat_poll') and self._chat_poll:
                self._chat_poll.stop()
            return
        if not self._chat_worker.done:
            return
        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
        result, error = self._chat_worker.get_result()
        self._chat_worker.deleteLater()
        if error:
            self._on_chat_error(error)
        elif result:
            reply, mood = result
            self._on_chat_done(reply, mood)

    def _do_memory_ops(self, reply: str, mood: str):
        """记忆操作放后台线程执行，不阻塞主线程"""
        import threading
        t = threading.Thread(target=self._do_memory_ops_sync, args=(reply, mood), daemon=True)
        t.start()

    def _do_memory_ops_sync(self, reply: str, mood: str):
        try:
            engine = self.chat_engine
            if not engine or not engine.memory:
                return
            user_msg = ""
            for m in reversed(engine.history):
                if m.get("role") == "user":
                    user_msg = m["content"]
                    break
            if not user_msg:
                return
            engine.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
            engine.memory.add_chat("user", user_msg)
            engine.memory.add_chat("mea", reply, mood)
            today_total = engine.memory.get_today_chat_count()
            delta = 1 if today_total == 0 else (1 if len(user_msg) < 10 else (2 if len(user_msg) < 50 else 3))
            upgrade_msg = engine.memory.add_affection(delta)
            full_system = SYSTEM_PROMPT + "\n\n" + engine.memory.build_context_prompt()
            if upgrade_msg:
                full_system += f"\n\n[内部：好感度升至{engine.memory.get_affection_tier()[1]}。请用稍暖的语气回应。]"
            engine.history[0] = {"role": "system", "content": full_system}
            engine.memory.mark_today_chatted()
            engine._extract_memories(user_msg, reply)
        except Exception as e:
            safe_print(f"[memory] 操作失败: {e}")

    def _on_chat_done(self, reply: str, mood: str):
        safe_print(f"[pet] LLM 回复 [{mood}]: {reply[:50]}...")
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        detected = self._detect_mood(reply)
        self.show_reply(reply, detected)
        self._awaiting_reply = False
        # 后台 TTS 合成
        self._tts_worker = TTSWorker(self.tts, reply, mood=detected)
        self._tts_worker.start()
        self._ensure_tts_poll()
        # 记忆系统操作（延后执行，不阻塞气泡）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._do_memory_ops(reply, detected))

    def _ensure_tts_poll(self):
        """确保 TTS 轮询 timer 在运行"""
        if not hasattr(self, '_tts_poll') or not self._tts_poll:
            self._tts_poll = QTimer(self)
            self._tts_poll.timeout.connect(self._poll_tts)
            self._tts_poll.start(100)

    def _poll_tts(self):
        """轮询所有 TTSWorker 完成状态"""
        if hasattr(self, '_tts_worker') and self._tts_worker and self._tts_worker.done:
            result = self._tts_worker.get_result()
            self._tts_worker = None
            if result:
                self._on_tts_audio(result)
        if hasattr(self, '_speak_worker') and self._speak_worker and self._speak_worker.done:
            result = self._speak_worker.get_result()
            self._speak_worker = None
            if result:
                self._on_speak_audio_ready(result)
        if hasattr(self, '_watch_tts_worker') and self._watch_tts_worker and self._watch_tts_worker.done:
            result = self._watch_tts_worker.get_result()
            self._watch_tts_worker = None
            # 把 _pending_reply 的数据取出来，传给回调，不要在回调前删除
            pending = getattr(self, '_pending_reply', None)
            if pending:
                reply, mood = pending
                self._on_watch_tts_and_show(result, reply, mood)  # 改为传参
            else:
                self._on_watch_tts_and_show(result, None, None)
            # 清理 _pending_reply 放到回调之后
            if hasattr(self, '_pending_reply'):
                reply, mood = self._pending_reply
                del self._pending_reply
                self._on_watch_tts_and_show(result)

                
        # 没有待处理的 worker 就停止
        if not any([
            getattr(self, '_tts_worker', None),
            getattr(self, '_speak_worker', None),
            getattr(self, '_watch_tts_worker', None),
        ]):
            if hasattr(self, '_tts_poll') and self._tts_poll:
                self._tts_poll.stop()
                self._tts_poll.deleteLater()
                self._tts_poll = None

    def _detect_mood(self, text: str) -> str:
        """从回复文本推测情绪（替代后端 mood 检测）"""
        t = text.lower()
        if any(k in t for k in ["嘿嘿","好吃","开心","高兴","棒","哈哈","喜欢"]):
            return "happy"
        if any(k in t for k in ["烦","无聊","没兴趣","别吵","哼","切"]):
            return "annoyed"
        if any(k in t for k in ["哦？","咦","诶","真的？","意外"]):
            return "surprised"
        if any(k in t for k in ["有意思","有趣","让我看看","好奇"]):
            return "curious"
        if any(k in t for k in ["唉","难过","伤心","可惜"]):
            return "sad"
        if any(k in t for k in ["又没在","随便","……","脸红","害羞"]):
            return "shy"
        return "neutral"

    def _on_tts_audio(self, raw: str):
        """TTS 合成完成 → 播放语音（文字已显示）"""
        wav_path = raw.rsplit("|", 1)[0] if "|" in raw else raw
        if wav_path and os.path.exists(wav_path):
            # 更新气泡时长以匹配音频
            audio_ms = self._get_wav_duration_ms(wav_path)
            if audio_ms > 0 and hasattr(self, 'bubble') and self.bubble:
                self.bubble.show_text(self.bubble.text_label.text(), duration_ms=audio_ms + 500)
            self._play_audio(wav_path)

    def _on_chat_error(self, err: str):
        safe_print(f"[pet] Chat错误: {err}")
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        log_error("pet_chat", err)
        self.show_reply(f"出错啦：{err}", "annoyed", duration_ms=10000)
        self._awaiting_reply = False

    def _on_chat_timeout(self):
        """ChatWorker 超时 — 强制终止线程并释放锁"""
        safe_print(f"[pet] Chat超时，已释放对话锁")
        self._awaiting_reply = False
        self._show_bubble("唔…好像没响应喵。再试一次？", 3000)
        self._position_bubble()
        if hasattr(self, '_chat_worker') and self._chat_worker:
            if self._chat_worker.isRunning():
                # quit() 无法中断阻塞在 requests.post 的线程
                # 用 terminate() 强制终止
                self._chat_worker.terminate()
                if not self._chat_worker.wait(2000):
                    safe_print(f"[pet] ChatWorker 无法终止")
            self._chat_worker.deleteLater()
            self._chat_worker = None

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

    def _play_audio(self, wav_path: str):
        """播放 wav 音频（Windows 原生优先）"""
        if not os.path.exists(wav_path):
            safe_print(f"[audio] 文件不存在: {wav_path}")
            return
        try:
            # Windows 原生播放（最可靠）
            import winsound
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            safe_print(f"[audio] 播放: {os.path.basename(wav_path)}")
            return
        except Exception as e:
            safe_print(f"[audio] winsound 失败，尝试 Qt: {e}")

        # 备用：PyQt5 QtMultimedia
        try:
            from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
            from PyQt5.QtCore import QUrl
            mp = QMediaPlayer()
            mp.setMedia(QMediaContent(QUrl.fromLocalFile(wav_path)))
            mp.play()
            safe_print(f"[audio] Qt 播放: {os.path.basename(wav_path)}")
        except Exception as e:
            safe_print(f"[audio] 播放失败: {e}")

    # ========================
    # 屏幕观察（截屏吐槽）
    # ========================
    def _start_watcher_timer(self):
        """随机间隔 3~6 分钟"""
        import random as r
        ms = r.randint(180_000, 360_000)  # 3-6 分钟
        self._watcher_timer.start(ms)

    def _do_screen_watch(self, force: bool = False):
        """截屏 + 视觉吐槽（含冷落感知）"""
        if self._standby and not force:
            return  # 待机中，不触发识图
        if self._awaiting_reply and not force:
            self._start_watcher_timer()
            return
        if self._watcher.isRunning():
            if force:
                self._watcher.stop()
            else:
                return
        self._awaiting_reply = True
        # 传入冷落时长
        idle_s = time.time() - self._last_interaction_time
        self._watcher.set_idle_minutes(idle_s / 60.0)
        self._show_bubble("梅尔酱偷看了一眼……", 30000)
        self._position_bubble()
        self._watcher.start()

    def _on_watch_result(self, text: str, mood: str):
        # 清洗 Markdown/引号残留
        import re
        text = re.sub(r'["\'「」『』`]', '', text)
        text = re.sub(r'```', '', text)
        text = text.strip()
        try:
            self._pending_reply = (text, mood)
            safe_print(f"[watch] _pending_reply 已设置: {text[:40]}")
            # 使用专用 worker，不和互动触摸抢 _speak_worker
            self._watch_tts_worker = TTSWorker(self.tts, text, mood=mood)
            self._watch_tts_worker.start()
            self._ensure_tts_poll()
        except Exception as e:
            import traceback
            log_error("watch_result", f"{e}")
            self.show_reply(text, mood, duration_ms=5000)
            self._awaiting_reply = False
            self._start_watcher_timer()

    def _on_watch_tts_and_show(self, raw: str, reply: str = None, mood: str = None):
        safe_print(f"[watch] _on_watch_tts_and_show called, raw={raw is not None}, reply={reply is not None}")
        if raw is None or reply is None:
            safe_print("[TTS] watch tts returned None, skip audio")
            if reply and mood:
                self.show_reply(reply, mood, duration_ms=6000)
            else:
                safe_print("[watch] _pending_reply 已丢失!")
            self._awaiting_reply = False
            self._start_watcher_timer()
            return
            
        """屏幕吐槽：语音合成完成 → 显示文字 + 播放"""
        wav_path = raw.rsplit("|", 1)[0] if "|" in raw else raw
        if hasattr(self, '_pending_reply'):
            reply, mood = self._pending_reply
            del self._pending_reply
        else:
            log_error("watch_tts", "_pending_reply missing")
            self._awaiting_reply = False
            self._start_watcher_timer()
            return
        audio_duration_ms = self._get_wav_duration_ms(wav_path) if wav_path else 0
        bubble_ms = max(audio_duration_ms + 500, 3000)
        self.show_reply(reply, mood, duration_ms=bubble_ms)
        self._awaiting_reply = False
        self._start_watcher_timer()
        if wav_path and os.path.exists(wav_path):
            self._play_audio(wav_path)

    def _on_watch_tts_error(self, err: str):
        """屏幕吐槽 TTS 合成失败 —— 至少显示文字，不卡死"""
        self._awaiting_reply = False
        if hasattr(self, '_pending_reply'):
            reply, mood = self._pending_reply
            del self._pending_reply
            self.show_reply(reply, mood, duration_ms=5000)
        self._start_watcher_timer()

    def _on_watch_error(self, err: str):
        print(f"[watch error] {err}")  # 终端输出
        # 显示简短提示，不打扰主人
        self._awaiting_reply = False
        self._show_bubble(f"唔…看不清喵 ({err[:30]})", 3000)
        self._start_watcher_timer()

    def _on_watch_silent(self):
        """视觉模型评估后决定不说话——安静恢复"""
        self._awaiting_reply = False
        self._show_bubble("😼 没什么好说的喵…", 2000)
        self._start_watcher_timer()

    def _on_watch_progress(self, msg: str):
        """显示识图/评估阶段状态"""
        self._show_bubble(msg, 0)  # 持久显示直到下一个阶段

    def _on_search_request(self, query: str):
        """处理 Web 搜索请求（来自 watcher）—— 暂无可用搜索后端"""
        result = f"（关于「{query}」的搜索结果暂时无法获取喵）"
        if hasattr(self, '_watcher') and self._watcher:
            self._watcher.set_search_result(result)

    def _toggle_standby(self):
        self._standby = not self._standby
        if self._standby:
            self._watcher_timer.stop()
            self._safe_set_expression("011")  # 闭眼
            self._show_bubble("💤 梅尔酱待机中……", 0)
            self._position_bubble()
            # 设置极小可点击区域
            self._set_standby_region()
        else:
            self._safe_set_expression("001")
            if hasattr(self, 'bubble') and self.bubble:
                self.bubble.hide()
            self._show_bubble("✨ 梅尔酱回来了喵～", 2500)
            self._position_bubble()
            self._apply_hit_region()  # 恢复正常区域
            self._start_watcher_timer()

    def _set_standby_region(self):
        """待机时缩小可点击区域（跨平台）"""
        if sys.platform == "win32":
            try:
                import win32gui
                w, h = self.width(), self.height()
                margin_x = w // 4
                margin_y = h // 4
                rgn = win32gui.CreateRectRgn(margin_x, margin_y, w - margin_x, h - margin_y)
                win32gui.SetWindowRgn(int(self.winId()), rgn, True)
                return
            except Exception as e:
                safe_print(f"[WARN] Standby region failed: {e}")

        # 非 Windows：使用 Qt mask 缩小到中心矩形
        from PyQt5.QtGui import QRegion
        from PyQt5.QtCore import QRect
        w, h = self.width(), self.height()
        margin_x = w // 4
        margin_y = h // 4
        region = QRegion(QRect(margin_x, margin_y, w - 2*margin_x, h - 2*margin_y))
        self.setMask(region)


    def _toggle_render_mode(self):
        """切换 Live2D / PNG 立绘渲染模式"""
        # 停止当前渲染器
        if self._use_live2d:
            # 从 Live2D 切回 PNG
            if self.sprite_label:
                self.sprite_label.shutdown()
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self._l2d_model = None
            self._use_live2d = False
            # 重建 PNG 立绘标签
            self.sprite_label = QLabel(self)
            self.sprite_label.setAttribute(Qt.WA_TranslucentBackground)
            self.sprite_label.setStyleSheet("background: transparent;")
            self.sprite_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.sprite_label.show()
            # 初始化 PNG 渲染器
            char = self.config.get("character", {})
            sprite_dir = self.config.get("sprite_dir", os.path.join(os.path.dirname(__file__), "sprites"))
            outfit = char.get("default_outfit", "01")
            direction = char.get("default_direction", "A")
            self.renderer = SpriteRenderer(sprite_dir, outfit, direction)
            self.renderer.expression_changed.connect(self._on_sprite_changed)
            self._update_sprite()
            self.renderer.start_blink_animation()
            if self._size_factor != 1.0:
                self._size_factor_preview(self._size_factor)
            self._apply_hit_region()
            self._show_bubble("🎭 切回 PNG 立绘喵～", 2500)
        else:
            # 从 PNG 切到 Live2D
            if self.renderer:
                self.renderer.stop_blink_animation()
                self.renderer = None
            if self.sprite_label:
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self._use_live2d = True
            self._init_live2d()
            if self._size_factor != 1.0:
                self._size_factor_preview(self._size_factor)
            self._apply_hit_region()
            self._show_bubble("🎭 Live2D 模式喵～", 2500)
        self._position_bubble()

        # 更新配置并保存
        self.config.setdefault("live2d", {})["enabled"] = self._use_live2d
        self._save_config()

    def _record_interaction(self):
        """记录互动时间（聊天、摸头等触发）"""
        self._last_interaction_time = time.time()

    # ========================
    # 关闭事件
    # ========================
    def closeEvent(self, event):
        self._quit()
        super().closeEvent(event)


def main():
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    safe_print("[main] 创建 QApplication...")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    safe_print("[main] QApplication OK")

    safe_print("[main] 创建 MeaPet...")
    pet = MeaPet("config.json")
    safe_print("[main] MeaPet OK")

    # 启动问候
    QTimer.singleShot(1500, lambda: pet.show_reply(
        "……", "neutral"
    ) if hasattr(pet, 'show_reply') else None)

    safe_print("[main] 进入事件循环...")
    sys.exit(app.exec_())

    # Cleanup
    try:
        dispose_live2d()
    except Exception:
        pass


if __name__ == "__main__":
    # 1. 打印环境变量，看一眼启动瞬间的值是多少
    print(f"[ENV CHECK] QT_IM_MODULE = {os.environ.get('QT_IM_MODULE')}")
    main()

