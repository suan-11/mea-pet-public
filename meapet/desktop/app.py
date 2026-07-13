"""
梅尔桌宠 - 主程序
透明异形窗口 + 拖拽移动 + 表情切换 + 对话气泡
"""
from __future__ import annotations

import os
import sys
import time
import socket  # must import before PyQt (QtNetwork hook)
from meapet.log import get_color_logger

log = get_color_logger("app")

from meapet.paths import PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from typing import Optional

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer

from meapet.utils import (
    safe_print,
    log_error,
    ensure_utf8_stdout,
    cleanup_audio_cache,
)
from meapet.config.store import (
    load_config,
    normalize_config,
    resolve_startup_config_path,
    resolve_vision_api_base,
    resolve_vision_api_key,
    resolve_vision_backend,
    resolve_vision_host,
)
from meapet.config.checker import check_config_lines

ensure_utf8_stdout()

from meapet.chat.engine import create_engine_from_config
from meapet.memory.db import MeaMemory
from meapet.watcher.screen import ScreenWatcher
from meapet.tts.service import MeaTTS
from meapet.desktop.widgets import DialogueBubbleStack
from meapet.desktop.audio import PetAudioMixin
from meapet.desktop.watch_ctrl import PetWatcherMixin
from meapet.desktop.chat_flow import PetChatFlowMixin
from meapet.desktop.interaction import PetInteractionMixin
from meapet.desktop.window_chrome import PetWindowChromeMixin
from meapet.desktop.render_host import PetRenderHostMixin, calculate_drag_position
from meapet.desktop.config_bridge import PetConfigBridgeMixin
from meapet.desktop.splash import StartupSplash

os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")


def _install_excepthook():
    import traceback

    def _hook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error(f"[excepthook] 未捕获异常: {exc_type.__name__}: {exc}\n{msg}")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _abort_failed_startup(app, keepalive, splash=None) -> None:
    """主窗口创建失败时关闭保活对象并结束应用。"""
    for widget in (splash, keepalive):
        if widget is None:
            continue
        try:
            widget.close()
        except Exception:
            pass
    try:
        app.quit()
    except Exception:
        pass


class MeaPet(
    PetAudioMixin,
    PetWatcherMixin,
    PetChatFlowMixin,
    PetInteractionMixin,
    PetWindowChromeMixin,
    PetRenderHostMixin,
    PetConfigBridgeMixin,
    QWidget,
):
    """梅尔桌宠主窗口"""

    def __init__(self, config_path: Optional[str] = None):
        super().__init__()
        config_path = config_path or resolve_startup_config_path(PROJECT_ROOT)
        self.config = self._load_config(config_path)
        if not check_config_lines(config_path):
            # bubble not ready yet; defer message
            self._config_broken = True
        else:
            self._config_broken = False

        self.config = normalize_config(self.config)
        from meapet.ui_theme import set_ui_font_scale

        set_ui_font_scale(
            (self.config.get("display") or {}).get("font_scale", 1.0)
        )
        bub = self.config.get("bubble_duration_ms") or {}
        log.info(
            f"[config] 气泡时长配置: "
            f"default={bub.get('default')} reply={bub.get('reply')} "
            f"watch={bub.get('watch')} interaction={bub.get('interaction')} "
            f"sync_with_audio={self.config.get('tts', {}).get('sync_with_audio')}"
        )

        self._awaiting_reply = False
        self._pending_input = None
        self._chat_worker = None
        self._tts_worker = None
        self._dragging = False
        self._drag_pointer_origin = None
        self._drag_window_origin = None
        self._pending_drag_position = None
        self._drag_move_timer = QTimer(self)
        self._drag_move_timer.setSingleShot(True)
        self._drag_move_timer.setTimerType(Qt.PreciseTimer)
        self._drag_move_timer.timeout.connect(self._flush_drag_position)
        self._standby = False
        self._standby_bubble = None

        self._init_window()

        def _safe(step, fn):
            try:
                fn()
                log.info(f"[init] {step} 初始化完成")
            except Exception as e:
                import traceback
                log.error(f"[init] {step} 初始化失败: {e}")
                log.error(f"[init] {step} 异常堆栈:\n{traceback.format_exc()}")

        _safe("renderer", self._init_renderer)
        _safe("chat", self._init_chat)
        _safe("tts", self._init_tts)
        self._cloud_watch_confirmed = False
        _safe("watcher", self._init_watcher)
        _safe("tray", self._setup_tray)
        _safe("interaction", self._init_interaction)
        _safe("timers", self._init_timers)

        try:
            self._place_bottom_right()
        except Exception as e:
            log.warn(f"[init] 窗口定位失败: {e}")
        self.show()
        self.raise_()
        try:
            self._apply_hit_region()
        except Exception as e:
            log.warn(f"[init] 碰撞区域设置失败: {e}")

        try:
            cache_dir = str(PROJECT_ROOT / "audio_cache")
            stats = cleanup_audio_cache(cache_dir, max_files=40, max_age_hours=48.0)
            if stats.get("removed"):
                log.info(
                    f"[audio_cache] 缓存清理完成: removed={stats['removed']} kept={stats['kept']}"
                )
        except Exception as e:
            log.warn(f"[audio_cache] 缓存清理跳过: {e}")

        if self._config_broken:
            QTimer.singleShot(800, lambda: self._show_bubble("配置文件坏了喵", 5000))

    def _init_window(self):
        # 注意：不要用 SubWindow（无父窗口时在部分 Windows 上会"存在但不可见/无任务栏"）
        self.setWindowFlags(
            Qt.FramelessWindowHint

            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 允许激活，避免完全无法交互
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        self.setProperty("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_UTILITY")
        self.setWindowTitle("mea-pet")
        # 桌宠是 Tool 悬浮窗：关闭/隐藏时不要拖垮整个 QApplication
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._bubble_stack = DialogueBubbleStack(self)
        self._bubble_stack.changed.connect(self._on_bubble_stack_changed)
        self.bubble = None

    def _init_chat(self):
        self.memory = MeaMemory()
        self.memory.daily_maintenance()
        self.chat_engine = create_engine_from_config(self.config, self.memory)
        if self.chat_engine.backend == "ollama" and self.chat_engine.available:
            QTimer.singleShot(2000, self._show_warmup_status)

    def _show_warmup_status(self):
        if getattr(self.chat_engine, "_warmed_up", False):
            self._show_bubble("梅尔准备好啦~双击对话喵", 3000)

    def _init_tts(self):
        self.tts = MeaTTS(self.config)

    def _init_watcher(self):
        llm_cfg = self.config.get("llm", {}) or {}
        vision_cfg = self.config.get("vision", {}) or {}

        backend = resolve_vision_backend(vision_cfg, llm_cfg)
        vision_model = vision_cfg.get("model") or (
            "mimo" if backend == "mimo" else "qwen3.5:4b"
        )
        if backend == "mimo":
            mimo_model = (
                vision_cfg.get("model")
                if vision_cfg.get("model")
                and vision_cfg.get("model") not in ("mimo", "qwen3.5:4b")
                else llm_cfg.get("model", "mimo-v2.5")
            )
            if not mimo_model or mimo_model in ("mimo", "qwen3.5:4b"):
                mimo_model = llm_cfg.get("model", "mimo-v2.5")
        else:
            mimo_model = llm_cfg.get("model", "mimo-v2.5")

        api_key = resolve_vision_api_key(vision_cfg, llm_cfg)
        api_base = resolve_vision_api_base(vision_cfg, llm_cfg)
        if backend == "mimo":
            try:
                from meapet.config.store import normalize_mimo_model_id
                mimo_model = normalize_mimo_model_id(mimo_model, for_vision=True)
            except Exception:
                if not mimo_model or mimo_model in ("mimo", "qwen3.5:4b") or str(mimo_model).startswith("XiaomiMiMo/"):
                    mimo_model = "mimo-v2.5"
        ollama_host = resolve_vision_host(vision_cfg, llm_cfg)

        log.info(
            f"[watcher] 视觉后端配置: backend={backend} "
            f"model={vision_model if backend != 'mimo' else mimo_model} "
            f"allow_cloud={self.config.get('watcher', {}).get('allow_cloud', False)}"
        )
        self._watcher = ScreenWatcher(
            ollama_host=ollama_host,
            vision_model=vision_model if backend != "mimo" else mimo_model,
            chat_model=vision_model if backend != "mimo" else mimo_model,
            backend=backend,
            api_base=api_base,
            api_key=api_key,
            mimo_model=mimo_model,
        )
        self._watcher.result_ready.connect(self._on_watch_result)
        self._watcher.error.connect(self._on_watch_error)
        self._watcher.silent.connect(self._on_watch_silent)
        self._watcher.progress.connect(self._on_watch_progress)
        self._watcher.search_request.connect(self._on_search_request)
        self._last_interaction_time = time.time()

    def _init_interaction(self):
        self._last_interaction_time = time.time()
        self._head_press_x = None
        self._is_head_touching = False

    def _init_timers(self):
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._idle_action)
        self._idle_timer.start(20000)

        self._watcher_timer = QTimer(self)
        self._watcher_timer.timeout.connect(self._do_screen_watch)
        watcher_cfg = self.config.get("watcher", {})
        if watcher_cfg.get("enabled", False):
            self._start_watcher_timer()
        else:
            log.info("[watcher] 屏幕观察默认关闭（隐私），右键菜单可开启")

    # ── mouse ──────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            head_threshold = int(self.height() * 0.35)
            self._is_head_touching = event.y() < head_threshold
            self._head_press_x = event.x() if self._is_head_touching else None
            self._dragging = True
            self._drag_pointer_origin = event.globalPos()
            self._drag_window_origin = self.pos()

    def mouseMoveEvent(self, event):
        if not (self._dragging and event.buttons() & Qt.LeftButton):
            return
        if self._is_head_touching and self._head_press_x is not None:
            if abs(event.x() - self._head_press_x) > 40:
                self._on_head_patted()
                self._is_head_touching = False
                self._head_press_x = None
                return
        if self._drag_pointer_origin is None or self._drag_window_origin is None:
            return
        self._queue_drag_position(
            calculate_drag_position(
                self._drag_window_origin,
                self._drag_pointer_origin,
                event.globalPos(),
            )
        )

    def _queue_drag_position(self, position):
        """合并高频 move 事件，同时保留首个事件的即时反馈。"""
        self._pending_drag_position = position
        if not self._drag_move_timer.isActive():
            self._flush_drag_position()
            self._drag_move_timer.start(8)

    def _flush_drag_position(self):
        position = self._pending_drag_position
        if position is None:
            return
        self._pending_drag_position = None
        self.move(position)
        self._position_bubble()

    def mouseReleaseEvent(self, event):
        self._drag_move_timer.stop()
        self._flush_drag_position()
        self._dragging = False
        self._drag_pointer_origin = None
        self._drag_window_origin = None
        self._is_head_touching = False
        self._head_press_x = None

    def mouseDoubleClickEvent(self, event):
        self._start_chat()

    def showEvent(self, event):
        """窗口显示时恢复空闲动画等后台定时器"""
        super().showEvent(event)
        if hasattr(self, '_idle_timer') and self._idle_timer and not self._idle_timer.isActive():
            self._idle_timer.start(20000)

    def closeEvent(self, event):
        # 桌宠是常驻悬浮窗：系统/误触关闭只隐藏，真正退出走右键「退出」
        log.info("[pet] 关闭事件触发 -> 隐藏窗口（使用托盘或右键菜单退出）")
        event.ignore()
        if hasattr(self, '_idle_timer') and self._idle_timer:
            self._idle_timer.stop()
        self.hide()




def main():
    """启动桌宠：托盘 + 屏外保活 + boot 日志。"""
    _install_excepthook()
    import signal
    import traceback
    from datetime import datetime
    from pathlib import Path as _Path

    # 原生崩溃落盘（OpenGL / Live2D C++）
    try:
        import faulthandler
        _fault_fp = open(_Path(PROJECT_ROOT) / "meapet_fault.log", "a", encoding="utf-8")
        faulthandler.enable(file=_fault_fp, all_threads=True)
    except Exception:
        pass

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    boot_log = _Path(PROJECT_ROOT) / "meapet_boot.log"

    try:
        boot_log.write_text(
            f"===== MeaPet boot {datetime.now().isoformat(timespec='seconds')} =====\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    log.info(f"[boot] python={sys.version.split()[0]} exe={sys.executable}")
    log.info(f"[boot] cwd={os.getcwd()} root={PROJECT_ROOT}")
    log.info(f"[boot] FORCE_PNG={os.environ.get('MEAPETFORCE_PNG', '')}")

    try:
        app = QApplication(sys.argv)
    except Exception:
        log.error(f"[boot] QApplication 创建失败:\n{traceback.format_exc()}")
        raise

    from meapet.ui_theme import ensure_application_fonts, set_ui_font_scale

    ensure_application_fonts()

    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MeaPet")
    app.setOrganizationName("MeaPet")
    app.aboutToQuit.connect(lambda: log.info("[boot] 应用即将退出 (aboutToQuit)"))

    holder: dict = {"pet": None}
    app._meapet_holder = holder

    # 屏外保活窗（避免仅托盘时进程被过早回收；不可见）
    keepalive = QWidget()
    keepalive.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
    keepalive.setAttribute(Qt.WA_QuitOnClose, False)
    keepalive.setAttribute(Qt.WA_ShowWithoutActivating, True)
    keepalive.setWindowTitle("MeaPet-keepalive")
    keepalive.setFixedSize(1, 1)
    keepalive.move(-10000, -10000)
    keepalive.show()
    app._meapet_keepalive = keepalive
    log.info("[boot] 保活窗口就绪")

    config_path = resolve_startup_config_path(PROJECT_ROOT)
    if os.path.basename(config_path) == "config.example.json":
        log.info("[boot] 使用默认示例配置文件 config.example.json")

    # 可选启动页（失败忽略）
    splash = None
    try:
        splash = StartupSplash()
        splash.setAttribute(Qt.WA_QuitOnClose, False)
        if hasattr(splash, "status"):
            splash.status.setText("正在启动...")
        splash.show()
        app.processEvents()
    except Exception as e:
        log.warn(f"[boot] 启动页跳过: {e}")
        splash = None

    pet = None
    try:
        log.info("[boot] 正在创建 MeaPet 实例...")
        pet = MeaPet(config_path)
        holder["pet"] = pet
        app._meapet_pet = pet
        pet.show()
        pet.raise_()
        log.info(
            f"[boot] MeaPet 创建成功: "
            f"size={pet.width()}x{pet.height()} "
            f"pos=({pet.x()},{pet.y()}) vis={pet.isVisible()} "
            f"live2d={getattr(pet, '_use_live2d', None)} tray={getattr(pet, 'tray', None) is not None}"
        )
    except Exception:
        tb = traceback.format_exc()
        log.error(f"[boot] MeaPet 创建失败:\n{tb}")
        try:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(None, "MeaPet 启动失败", tb[-1200:])
        except Exception:
            pass
        _abort_failed_startup(app, keepalive, splash)
        return 1

    if splash is not None:
        try:
            splash.hide()
        except Exception:
            pass

    def _ensure_visible():
        pet2 = holder.get("pet")
        if pet2 is None:
            return
        try:
            if hasattr(pet2, "_place_bottom_right"):
                pet2._place_bottom_right()
            pet2.show()
            pet2.raise_()
            log.debug(
                f"[boot] 确保窗口可见: "
                f"size={pet2.width()}x{pet2.height()} "
                f"@({pet2.x()},{pet2.y()}) vis={pet2.isVisible()}"
            )
        except Exception as e:
            log.warn(f"[boot] 确保窗口可见失败: {e}")

    def _greet():
        try:
            pet2 = holder.get("pet")
            if pet2 is not None and hasattr(pet2, "show_reply"):
                pet2.show_reply("......", "neutral")
        except Exception as e:
            log.warn(f"[boot] 问候消息跳过: {e}")

    startup_finished = {"done": False}

    def _finish_startup():
        if startup_finished["done"]:
            return
        startup_finished["done"] = True
        if splash is not None:
            try:
                splash.hide()
            except Exception:
                pass
        pet2 = holder.get("pet")
        if pet2 is not None:
            try:
                # 几何位置已在构造阶段确定，这里只显现，不再二次定位。
                pet2.show()
                pet2.raise_()
                log.debug(
                    f"renderer ready size={pet2.width()}x{pet2.height()} "
                    f"@({pet2.x()},{pet2.y()}) vis={pet2.isVisible()} "
                    f"opacity={pet2.windowOpacity():.2f} mapping=continuous"
                )
            except Exception as exc:
                log.error(f"渲染器显示失败: {exc}")
        QTimer.singleShot(600, _greet)

    if hasattr(pet, "when_renderer_ready"):
        pet.when_renderer_ready(_finish_startup)
    else:
        _finish_startup()

    heartbeat = QTimer()
    beats = {"n": 0}

    def _beat():
        beats["n"] += 1
        pet2 = holder.get("pet")
        vis = pet2.isVisible() if pet2 is not None else None
        if beats["n"] <= 5 or beats["n"] % 30 == 0:
            log.debug(f"[heartbeat] #{beats['n']} pet_visible={vis}")

    heartbeat.timeout.connect(_beat)
    heartbeat.start(1000)
    app._meapet_heartbeat = heartbeat

    log.info("[boot] 进入事件循环 app.exec_()")
    code = app.exec_()
    log.info(f"[boot] 事件循环退出 code={code}")
    try:
        from meapet.desktop.live2d_widget import dispose_live2d
        dispose_live2d()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main() or 0)
    except SystemExit:
        raise
    except Exception:
        import traceback
        msg = traceback.format_exc()
        log.error(f"[fatal] 启动阶段未捕获异常:\n{msg}")
        try:
            if sys.platform == "win32":
                input("启动失败，按回车退出...")
        except Exception:
            pass
        raise