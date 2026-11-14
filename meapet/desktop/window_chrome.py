"""Tray, context menu, autostart, quit — window chrome for MeaPet."""
from __future__ import annotations

import os
import sys
import uuid

from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QRect, Qt

from meapet.desktop import status_language
from meapet.desktop.icons import standard_icon
from meapet.desktop.menu_window import SWITCH_ACTION_NAME
from meapet.config.defaults import DEFAULT_OLLAMA_VISION_MODEL
from meapet.desktop.screen_geometry import (
    available_geometry_for,
    calculate_centered_position,
    calculate_popup_position,
    move_within_screen,
    widget_size,
)
from meapet.desktop.theme import COLOR_ACCENT, COLOR_ACCENT_2, COLOR_TEXT, MENU_STYLE
from meapet.message_dialog import show_message_dialog
from meapet.paths import PROJECT_ROOT
from meapet.ui_theme import (
    PALETTE,
    PET_SIZE_PRESETS,
    normalize_pet_size_factor,
    apply_named_style, )
from meapet.utils import safe_print


def _tavily_key_hint(status: str) -> str:
    """爬虫子菜单那行灰字：Tavily key 现在从哪来（`secret_status` 的取值口径）。

    菜单不收 key（人工口径「Key什么的让用户自己填」），但也不许用户对着"没反应"猜。
    免注册档是实测过的（案文 2026-10：keyless 可用），所以"没填"不是故障；至于填了之后
    key 有效与否，这一层量不出来（实测「无效 key 不退回 keyless」），文案就不替它作保。
    """
    if status == "missing":
        return "Tavily key 没填：走免注册档，可用"
    if status == "file":
        return "Tavily key 读自配置文件"
    if status.startswith("env:"):
        return "Tavily key 读自环境变量"
    return "Tavily key 已配置"


class PetWindowChromeMixin:
    """System tray + right-click menu + Windows autostart."""

    def _setup_tray(self):
        from PyQt5.QtGui import QPixmap, QColor, QPainter
        from meapet.utils import safe_print

        available = QSystemTrayIcon.isSystemTrayAvailable()
        safe_print(f"[tray] system tray available={available}")
        if not available:
            safe_print("[tray] 系统托盘不可用（WSL/部分桌面环境）。仍继续运行窗口。")
            self.tray = None
            return

        icon = QIcon()
        try:
            if getattr(self, "renderer", None) is not None and not getattr(self, "_use_live2d", False):
                pixmap = self.renderer.get_current_pixmap()
                if pixmap is not None and not pixmap.isNull():
                    icon = QIcon(pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as e:
            safe_print(f"[tray] sprite icon failed: {e}")
        if icon.isNull():
            # 生成粉色猫爪色块图标，避免空 QIcon 导致托盘不显示
            pm = QPixmap(32, 32)
            pm.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(COLOR_ACCENT))
            painter.setPen(QColor(COLOR_ACCENT_2))
            painter.drawEllipse(2, 2, 28, 28)
            # 粉底墨心：与 BrandMark / SplashMark 同一品牌记号
            painter.setBrush(QColor(PALETTE["on_primary"]))
            painter.drawEllipse(10, 10, 12, 12)
            painter.end()
            icon = QIcon(pm)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(status_language.tray_running_tooltip())

        menu = self._build_tray_menu()
        self.tray.setContextMenu(menu)
        self._refresh_tray_state()
        try:
            self.tray.activated.connect(self._on_tray_activated)
        except Exception:
            pass
        self.tray.show()
        safe_print(f"[tray] shown visible={self.tray.isVisible()}")
        try:
            self.tray.showMessage(
                "MeaPet 已启动",
                "桌宠在运行中。找不到时看右下角托盘图标；右键桌宠可退出。",
                QSystemTrayIcon.Information,
                5000,
            )
        except Exception:
            pass

    def _on_tray_activated(self, reason):
        # 单击/双击托盘：显示桌宠；待机时顺带唤醒
        try:
            if reason in (
                QSystemTrayIcon.Trigger,
                QSystemTrayIcon.DoubleClick,
                QSystemTrayIcon.MiddleClick,
            ):
                if getattr(self, "_standby", False):
                    self._toggle_standby()
                self.show()
                self.raise_()
                self.activateWindow()
                self._refresh_tray_state()
        except Exception:
            pass

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def _quit(self):
        safe_print("[pet] quitting by user/menu…")
        try:
            save_position = getattr(self, "_save_pet_position", None)
            if callable(save_position):
                save_position()
        except Exception:
            pass
        try:
            self._close_menu_window()
        except Exception:
            pass
        stop_control = getattr(self, "_stop_control", None)
        if callable(stop_control):
            try:
                stop_control()
            except Exception:
                pass
        # P1-3：全局热键与常驻唤醒监听是系统级资源，进程退出前必须显式卸掉——
        # RegisterHotKey 不会随进程收尾自动释放，监听线程也会被拖成僵尸。
        for name in ("_teardown_ptt", "_stop_wake_listener", "_stop_voice_engine"):
            cleanup = getattr(self, name, None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception as exc:  # noqa: BLE001 - 一件收不干净不该挡住其余
                    # 退出路径不中断其余清理，但要留痕：系统级资源（热键、监听线程）
                    # 没收到干净，下次启动的症状会指向别处。
                    safe_print(f"[pet] {name} 清理失败: {type(exc).__name__}: {exc}")
        stop_onebot = getattr(self, "_stop_onebot", None)
        if callable(stop_onebot):
            try:
                stop_onebot()
            except Exception:
                pass
        try:
            adapter = getattr(self, "agent_adapter", None)
            close = getattr(adapter, "close", None)
            if callable(close):
                from meapet.async_runtime import submit

                submit(close())
        except Exception:
            pass
        try:
            if not self._use_live2d and self.renderer:
                self.renderer.stop_blink_animation()
        except Exception:
            pass
        try:
            if self._use_live2d and self._l2d_model and self.sprite_label:
                self.sprite_label.shutdown()
        except Exception:
            pass
        try:
            self._idle_timer.stop()
            self._watcher_timer.stop()
        except Exception:
            pass
        # 退出路径只发取消/停止信号，不在 GUI 线程 join worker 或 QThread，
        # 避免 MCP/watcher/TTS 叠加 wait 把窗口卡成“未响应”。
        if hasattr(self, "_watcher") and self._watcher is not None:
            try:
                self._watcher.stop(timeout_ms=0)
            except TypeError:
                try:
                    self._watcher.stop()
                except Exception:
                    pass
            except Exception:
                pass
        for attr in ["_chat_worker", "_tts_worker", "_speak_worker"]:
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                terminate = getattr(w, "terminate", None)
                if callable(terminate):
                    terminate()
            except Exception:
                pass
        if hasattr(self, "tray"):
            try:
                self.tray.hide()
            except Exception:
                pass
        bubble_stack = getattr(self, "_bubble_stack", None)
        if bubble_stack is not None:
            try:
                bubble_stack.close_all()
            except Exception:
                pass
        elif hasattr(self, "bubble"):
            try:
                self.bubble.close()
            except Exception:
                pass
        if hasattr(self, "memory"):
            try:
                self.memory.close()
            except Exception:
                pass
        try:
            from meapet.desktop.live2d_widget import dispose_live2d
            dispose_live2d()
        except Exception:
            pass
        QApplication.quit()

    def _is_auto_start(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            val, _ = winreg.QueryValueEx(key, "MeaPet")
            winreg.CloseKey(key)
            path = val.split('"')[1] if '"' in val else val.split()[0]
            return os.path.exists(path)
        except Exception:
            return False

    def _toggle_auto_start(self):
        if sys.platform != "win32":
            self._show_bubble("Autostart currently only supports Windows", 2500)
            return
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, "MeaPet")
            winreg.DeleteValue(key, "MeaPet")
            winreg.CloseKey(key)
            self._show_bubble("Autostart disabled", 2000)
        except FileNotFoundError:
            winreg.CloseKey(key)
            # Build the command to register.
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                # PyInstaller build: exe path is already the launcher.
                cmd = f'"{sys.executable}"'
            else:
                py = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(py):
                    py = sys.executable
                pet_path = str(PROJECT_ROOT / "pet.py")
                cmd = f'"{py}" "{pet_path}"'
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "MeaPet", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            self._show_bubble("Autostart enabled", 2000)


    def _build_tray_menu(self) -> QMenu:
        """托盘菜单：提供待机恢复与基础控制。"""
        menu = QMenu()
        apply_named_style(menu, "MENU_STYLE")
        menu.setAccessibleName("MeaPet 托盘菜单")

        show_action = QAction("显示 / 隐藏", self)
        show_action.setIcon(standard_icon("show"))
        show_action.triggered.connect(self._toggle_visibility)
        menu.addAction(show_action)

        if getattr(self, "_standby", False):
            wake = QAction(status_language.tray_recover_standby(), self)
            wake.setIcon(standard_icon("wake"))
            wake.triggered.connect(self._recover_from_standby)
            menu.addAction(wake)
        else:
            snap_tray = QAction("看看我在干嘛", self)
            snap_tray.setIcon(standard_icon("watch"))
            snap_tray.triggered.connect(lambda: self._do_screen_watch(force=True))
            menu.addAction(snap_tray)

        menu.addSeparator()
        auto_started = self._is_auto_start()
        auto_tray_action = QAction("开机自启", self)
        auto_tray_action.setIcon(standard_icon("autostart"))
        auto_tray_action.setCheckable(True)
        auto_tray_action.setChecked(auto_started)
        auto_tray_action.triggered.connect(self._toggle_auto_start)
        menu.addAction(auto_tray_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setIcon(standard_icon("quit"))
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        return menu

    def _recover_from_standby(self) -> None:
        if getattr(self, "_standby", False) and hasattr(self, "_toggle_standby"):
            self._toggle_standby()
        self.show()
        self.raise_()
        self.activateWindow()
        self._refresh_tray_state()

    def _refresh_tray_state(self) -> None:
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        try:
            if getattr(self, "_standby", False):
                tray.setToolTip(status_language.tray_standby_tooltip())
            else:
                tray.setToolTip(status_language.tray_running_tooltip())
            # 重建菜单以刷新待机项
            tray.setContextMenu(self._build_tray_menu())
        except Exception:
            pass

    def _build_context_menu(self) -> QMenu:
        """构建分组菜单；根层只保留高频操作和清晰的功能入口。"""
        menu = QMenu(self)
        menu.setObjectName("PetContextMenu")
        apply_named_style(menu, "MENU_STYLE")
        menu.setAccessibleName("MeaPet 操作菜单")

        status_action = QAction("养成状态", self)
        status_action.setIcon(standard_icon("status"))
        status_action.triggered.connect(self._show_status_panel)
        menu.addAction(status_action)

        snap_action = QAction("看看我在干嘛", self)
        snap_action.setIcon(standard_icon("watch"))
        snap_action.triggered.connect(lambda: self._do_screen_watch(force=True))
        menu.addAction(snap_action)
        menu.addSeparator()

        # 「设置与数据」先建出来：凡是要写 self.config 的项都挂它下面，根层只留高频动作
        # （人工 2026-10-06 点的形状：根＝养成状态·看看我在干嘛·切换表情·设置与数据▸·关于·退出）。
        settings_menu = QMenu("设置与数据", self)
        settings_menu.setIcon(standard_icon("settings"))
        settings_menu.setObjectName("SettingsAndDataMenu")
        apply_named_style(settings_menu, "MENU_STYLE")
        settings_menu.setAccessibleName("设置与数据")

        expr_menu = QMenu("切换表情", self)
        expr_menu.setIcon(standard_icon("expression"))
        expr_menu.setObjectName("ExpressionMenu")
        apply_named_style(expr_menu, "MENU_STYLE")
        expr_menu.setAccessibleName("切换表情")
        moods = [
            ("默认", "neutral"),
            ("开心", "happy"),
            ("悲伤", "sad"),
            ("害羞", "shy"),
            ("好奇", "curious"),
            ("烦闷", "annoyed"),
            ("忧郁", "melancholy"),
        ]
        for label, mood in moods:
            action = QAction(label, self)
            action.triggered.connect(lambda checked, m=mood: self._safe_set_mood(m))
            expr_menu.addAction(action)
        menu.addMenu(expr_menu)

        vision_cfg = self.config.get("vision", {}) or {}
        llm_cfg = self.config.get("llm", {}) or {}
        vision_backend = (
            vision_cfg.get("backend") or llm_cfg.get("backend") or "ollama"
        ).lower()
        vision_menu = QMenu("识图与观察", self)
        vision_menu.setIcon(standard_icon("watch"))
        vision_menu.setObjectName("VisionAndWatchMenu")
        apply_named_style(vision_menu, "MENU_STYLE")
        vision_menu.setAccessibleName("识图与观察设置")
        current_vision = vision_cfg.get("model", DEFAULT_OLLAMA_VISION_MODEL)
        for label, bname in (
            ("Ollama 本地识图", "ollama"),
            ("MiMo 云端识图", "mimo"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(vision_backend == bname)
            act.triggered.connect(lambda checked, b=bname: self._set_vision_backend(b))
            vision_menu.addAction(act)
        vision_menu.addSeparator()
        if vision_backend != "mimo":
            for label, model_name in (
                (f"模型 · {DEFAULT_OLLAMA_VISION_MODEL}", DEFAULT_OLLAMA_VISION_MODEL),
            ):
                action = QAction(label, self)
                action.setCheckable(True)
                action.setChecked(current_vision == model_name)
                action.triggered.connect(
                    lambda checked, m=model_name: self._set_vision_model(m)
                )
                vision_menu.addAction(action)
        else:
            tip = QAction("MiMo 使用当前云端模型", self)
            tip.setEnabled(False)
            vision_menu.addAction(tip)

        vision_menu.addSeparator()
        w_enabled = bool((self.config.get("watcher") or {}).get("enabled", False))
        watch_action = QAction(status_language.menu_watch_toggle(), self)
        # 这一项是状态开关而不是动作：文字恒定，状态由拨杆本身表示
        # （人工 2026-10-06 点的形状）。菜单窗口见到这个 objectName 才画拨杆。
        watch_action.setObjectName(SWITCH_ACTION_NAME)
        watch_action.setCheckable(True)
        watch_action.setChecked(w_enabled)
        watch_action.setToolTip("开启后可能定时截取屏幕内容")
        watch_action.triggered.connect(self._toggle_watcher_enabled)
        vision_menu.addAction(watch_action)

        standby_text = (
            status_language.menu_standby_leave()
            if self._standby
            else status_language.menu_standby_enter()
        )
        standby_action = QAction(standby_text, self)
        standby_action.triggered.connect(self._toggle_standby)
        vision_menu.addAction(standby_action)
        settings_menu.addMenu(vision_menu)

        voice_enabled = self.config.get("voice_input", {}).get("enabled", False)
        voice_text = (
            status_language.menu_voice_input_disable()
            if voice_enabled
            else status_language.menu_voice_input_enable()
        )
        voice_action = QAction(voice_text, self)
        voice_action.setToolTip("开启后输入框出现麦克风按钮，点击录音转文字（本地离线）")
        voice_action.triggered.connect(self._toggle_voice_input)
        settings_menu.addAction(voice_action)

        display_menu = QMenu("显示与立绘", self)
        display_menu.setIcon(standard_icon("display"))
        display_menu.setObjectName("DisplayMenu")
        apply_named_style(display_menu, "MENU_STYLE")
        display_menu.setAccessibleName("显示与立绘设置")
        mode_text = (
            status_language.menu_render_to_png()
            if self._use_live2d
            else status_language.menu_render_to_live2d()
        )
        mode_action = QAction(mode_text, self)
        mode_action.triggered.connect(self._toggle_render_mode)
        display_menu.addAction(mode_action)

        current_factor = normalize_pet_size_factor(
            (self.config.get("display") or {}).get("size_factor", 1.0)
        )
        size_menu = QMenu(
            status_language.menu_window_size(current_factor), self
        )
        size_menu.setObjectName("WindowSizeMenu")
        apply_named_style(size_menu, "MENU_STYLE")
        size_menu.setAccessibleName("桌宠窗口大小")
        for percent in PET_SIZE_PRESETS:
            preset_action = QAction(f"{percent}%", self)
            preset_action.setCheckable(True)
            preset_action.setChecked(round(current_factor * 100) == percent)
            preset_action.triggered.connect(
                lambda _checked=False, p=percent: self._set_size_factor(p / 100.0)
            )
            size_menu.addAction(preset_action)
        size_menu.addSeparator()
        size_action = QAction("自定义…", self)
        size_action.setToolTip("滑块实时预览，30%–300%")
        size_action.triggered.connect(self._open_size_dialog)
        size_menu.addAction(size_action)
        display_menu.addMenu(size_menu)
        settings_menu.addMenu(display_menu)

        # 穿透是写侧（我们告诉系统怎么对待指针）、定位是读侧（我们想知道系统把我们
        # 安置在哪），同属"surface 与合成器之间的那层合同"，故并为一个子菜单。
        locate_menu = QMenu("定位与穿透", self)
        locate_menu.setIcon(standard_icon("display"))
        locate_menu.setObjectName("FidusLocateMenu")
        apply_named_style(locate_menu, "MENU_STYLE")
        locate_menu.setAccessibleName("定位与穿透")
        fidus_action = QAction("启用fidus", self)
        fidus_action.setCheckable(True)
        fidus_action.setChecked(
            bool((self.config.get("fidus") or {}).get("enabled", False))
        )
        fidus_action.setToolTip(
            "切换穿透时用屏幕贴片匹配量出窗口真实位置（Wayland 下客户端查不到自己"
            "在哪）。首次约需 2 秒校准并闪一下屏幕，之后每次切换仍需数秒测量，"
            "测不准则退回原行为。"
        )
        fidus_action.triggered.connect(self._toggle_fidus_enabled)
        locate_menu.addAction(fidus_action)
        settings_menu.addMenu(locate_menu)

        settings_menu.addMenu(self._build_click_feedback_menu())

        auto_started = self._is_auto_start()
        auto_action = QAction("开机自启", self)
        auto_action.setCheckable(True)
        auto_action.setChecked(auto_started)
        auto_action.triggered.connect(self._toggle_auto_start)
        settings_menu.addAction(auto_action)

        reconf_action = QAction("打开配置页…", self)
        reconf_action.triggered.connect(self._reopen_setup_wizard)
        settings_menu.addAction(reconf_action)
        timeline_action = QAction("对话时间线…", self)
        timeline_action.triggered.connect(self._show_timeline)
        settings_menu.addAction(timeline_action)

        # ── 新增：音量调节 ──
        volume_action = QAction("音量调节", self)
        volume_action.setIcon(standard_icon("settings"))  # 可复用设置图标
        volume_action.triggered.connect(self._show_volume_dialog)
        settings_menu.addAction(volume_action)

        update_action = QAction("检查更新…", self)
        update_action.setToolTip("查看 GitHub Releases 是否有新版本（只提示，不自动下载安装）")
        update_action.triggered.connect(self._check_for_updates)
        settings_menu.addAction(update_action)

        llm_mode = str(
            ((self.config.get("llm") or {}).get("mode") or "direct")
        ).strip().lower()
        control_enabled = bool(
            (self.config.get("agent_control") or {}).get("enabled", False)
        )
        agent_kind = str(
            (((self.config.get("llm") or {}).get("agent") or {}).get("kind"))
            or "hermes"
        ).strip().lower()
        if (
            llm_mode == "agent"
            and agent_kind != "agent_link"
            and control_enabled
        ):
            copy_token_action = QAction("复制 Agent 控制令牌", self)
            copy_token_action.triggered.connect(self._copy_agent_control_token)
            settings_menu.addAction(copy_token_action)
            rotate_token_action = QAction("重新生成 Agent 控制令牌…", self)
            rotate_token_action.triggered.connect(self._regenerate_agent_control_token)
            settings_menu.addAction(rotate_token_action)
        if (self.config.get("onebot") or {}).get("enabled"):
            onebot_status = QAction(
                status_language.menu_onebot_status_prefix()
                + str(getattr(self, "_onebot_status_text", lambda: "")() or ""),
                self,
            )
            onebot_status.setEnabled(False)
            settings_menu.addAction(onebot_status)
            onebot_reload = QAction(status_language.menu_onebot_reload(), self)
            onebot_reload.triggered.connect(self._reload_onebot)
            settings_menu.addAction(onebot_reload)
        settings_menu.addSeparator()
        reset_label = (
            "新建 Agent 会话…"
            if str(((self.config.get("llm") or {}).get("mode") or "direct")).lower()
            == "agent"
            else "重置所有记忆…"
        )
        reset_action = QAction(reset_label, self)
        reset_action.setObjectName("DangerAction")
        reset_action.setIcon(standard_icon("reset"))
        reset_action.triggered.connect(self._reset_memory)
        settings_menu.addAction(reset_action)
        if getattr(self, "_dev_unlocked", False):
            # 未解锁时这一支压根不存在（不是禁用摆在那儿）
            settings_menu.addMenu(self._dev_build_menu())
        menu.addMenu(settings_menu)

        menu.addSeparator()
        about_action = QAction("关于", self)
        about_action.setIcon(standard_icon("about"))
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)

        quit_action = QAction("退出", self)
        quit_action.setIcon(standard_icon("quit"))
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        return menu

    def _build_click_feedback_menu(self) -> QMenu:
        """「点击反馈」子菜单：两路走网的内容各自开关，抽中哪一路看权重。

        拨杆读 `is_source_live`（开了**且**点过隐私告知）而不是裸 `enabled`：手改配置把
        `enabled` 拧成 true 却从没确认过告知时，这一路压根不参与抽样，拨杆跟着显示开着就成了
        「开了但没反应，还没处问」。状态与理由一起给——紧跟着的那行灰字读 `participation_gap`。
        """
        from meapet.click_feedback import feedback
        from meapet.click_feedback.sources import SOURCE_LABELS
        from meapet.config.store import secret_status

        menu = QMenu("点击反馈", self)
        menu.setIcon(standard_icon("expression"))
        menu.setObjectName("ClickFeedbackMenu")
        apply_named_style(menu, "MENU_STYLE")
        menu.setAccessibleName("点击反馈")

        # 顶层这一行是三路各自状态的合成结果：开关分散在下一层，这里给"那么我点什么会怎样"。
        live_titles = [
            feedback.SOURCE_TITLES.get(name, name)
            for name, _weight in feedback.participating_sources(self.config)
        ]
        summary_text = (
            "现在点击会从：" + " · ".join(live_titles)
            if live_titles
            else "三路都不抽中，点击还是原来的反应（预制语音）"
        )
        summary = QAction(summary_text, self)
        summary.setEnabled(False)
        menu.addAction(summary)

        for name in (feedback.CRAWLER, feedback.POETRY):
            title = feedback.SOURCE_TITLES[name]
            submenu = QMenu(title, self)
            submenu.setObjectName(f"ClickFeedback{name.capitalize()}Menu")
            apply_named_style(submenu, "MENU_STYLE")
            submenu.setAccessibleName(f"点击反馈 {title}")

            switch = QAction(status_language.menu_click_feedback_toggle(title), self)
            # 状态开关不是动作：文字恒定，状态由拨杆表示（与屏幕观察那一项同形）。
            switch.setObjectName(SWITCH_ACTION_NAME)
            switch.setCheckable(True)
            switch.setChecked(feedback.is_source_live(self.config, name))
            switch.triggered.connect(
                lambda _checked, n=name: self._toggle_click_feedback_source(n)
            )
            submenu.addAction(switch)

            if name == feedback.POETRY:
                poetry = ((self.config.get("click_feedback") or {}).get(name)) or {}
                chosen = [str(s) for s in (poetry.get("sources") or ())]
                for sname in sorted(SOURCE_LABELS):
                    label, description = SOURCE_LABELS[sname]
                    source_action = QAction(label, self)
                    # 这两档也用拨杆而不是勾：`PetMenuWindow` 对普通可勾项只在 accessible
                    # 名字里写「已选中」，屏上看不出差别（尺寸预设那几项正有此病）。拨杆是唯一
                    # 看得见状态的那一形。
                    source_action.setObjectName(SWITCH_ACTION_NAME)
                    source_action.setCheckable(True)
                    source_action.setChecked(sname in chosen)
                    source_action.setToolTip(description)
                    source_action.triggered.connect(
                        lambda checked, s=sname: self._set_click_feedback_poetry_source(
                            s, bool(checked)
                        )
                    )
                    submenu.addAction(source_action)

            keywords_action = QAction("关键词…", self)
            keywords_action.setToolTip(
                "检索围绕这些词，一行一个"
                if name == feedback.CRAWLER
                else "只有「古籍检索」用这些词做检索词；「一句随机诗词」不查词"
            )
            keywords_action.triggered.connect(
                lambda _checked, n=name: self._open_click_feedback_keywords(n)
            )
            submenu.addAction(keywords_action)

            if name == feedback.CRAWLER:
                # key 不在菜单里收（人工口径：「Key什么的让用户自己填」），只说它现在从哪来。
                key_line = QAction(
                    _tavily_key_hint(secret_status(self.config).get("tavily", "")), self
                )
                key_line.setEnabled(False)
                submenu.addAction(key_line)

            gap = feedback.participation_gap(self.config, name)
            if gap:
                gap_line = QAction(gap, self)
                gap_line.setEnabled(False)
                submenu.addAction(gap_line)

            menu.addMenu(submenu)

        menu.addSeparator()
        weight_action = QAction("抽中权重…", self)
        weight_action.setToolTip("三路各给 0–100 的相对值，不必凑成 100")
        weight_action.triggered.connect(self._open_click_feedback_weights)
        menu.addAction(weight_action)
        return menu

    def _show_about(self):
        """打开"关于"页面（版本信息与 Live2D 模型来源致谢）。"""
        from meapet.desktop.dialogs import AboutDialog
        AboutDialog(self).exec_()

    def _check_for_updates(self) -> None:
        """检查更新：后台线程拉 Release 元数据，只提示，绝不下载/覆盖/执行。"""
        from meapet.update_check import UpdateCheckWorker

        worker = getattr(self, "_update_check_worker", None)
        if worker is not None and worker.isRunning():
            return  # 已有一次检查在途，不叠加第二次
        worker = UpdateCheckWorker(self)
        worker.checked.connect(self._on_update_check_done)
        worker.finished.connect(worker.deleteLater)
        self._update_check_worker = worker
        worker.start()

    def _on_update_check_done(self, result) -> None:
        """GUI 线程里弹结果信息框；有更新时给一个「打开下载页」按钮。"""
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        from meapet.update_check import format_result_text

        self._update_check_worker = None
        title, text = format_result_text(result)
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Information)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        open_button = None
        if result.has_update and result.release_url:
            open_button = box.addButton("打开下载页", QMessageBox.ActionRole)
        box.exec_()
        if open_button is not None and box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(result.release_url))

    def _show_volume_dialog(self):
        """打开音量调节对话框，保存配置。"""
        from meapet.desktop.dialogs import VolumeDialog
        dialog = VolumeDialog(self.config, self)
        if dialog.exec_() == QDialog.Accepted:
            self._save_config()

    def _show_context_menu(self, pos):
        """以独立可拖动窗口的形式打开右键菜单。"""
        # 待机穿透重开菜单与 Qt CustomContextMenu 可能叠到同一次右键，避免重入。
        if getattr(self, "_context_menu_executing", False):
            return
        self._context_menu_executing = True
        try:
            self._close_menu_window()
            from meapet.desktop.menu_window import PetMenuWindow

            menu = self._build_context_menu()
            window = PetMenuWindow(menu, None)
            self._menu_window = window
            window.show_at(self.mapToGlobal(pos))
        except Exception as exc:
            safe_print(f"[menu] 打开菜单窗口失败: {type(exc).__name__}: {exc}")
        finally:
            self._context_menu_executing = False

    def _close_menu_window(self) -> None:
        """关闭并释放上一次的菜单窗口（含其源 QMenu）。"""
        window = getattr(self, "_menu_window", None)
        self._menu_window = None
        if window is None:
            return
        try:
            source = getattr(window, "_source_menu", None)
            window.close()
            window.deleteLater()
            if source is not None:
                source.deleteLater()
        except Exception:
            pass

    def _show_status_panel(self):
        from meapet.desktop.status_panel import StatusPanel
        if not hasattr(self, "_status_panel") or self._status_panel is None:
            self._status_panel = StatusPanel(self.memory)
            self._place_beside_pet(self._status_panel, placement="right", gap=10)
        else:
            # 已存在的面板保留用户拖动过的位置，只在越界时（换屏幕、改分辨率）拉回来。
            move_within_screen(self._status_panel, self._status_panel.pos())
        self._status_panel.show()
        self._status_panel.refresh()

    def _pet_rect(self) -> QRect:
        return QRect(self.x(), self.y(), self.width(), self.height())

    def _place_beside_pet(self, window, *, placement: str, gap: int) -> None:
        """把弹出窗口放在桌宠旁边，并保证完整落在所在屏幕的可用区域内。"""
        pet_rect = self._pet_rect()
        size = widget_size(window)
        area = available_geometry_for(pet_rect)
        if area is None:
            window.move(pet_rect.right() + 1 + gap, pet_rect.top())
            return
        window.move(
            calculate_popup_position(
                pet_rect, size, area, placement=placement, gap=gap
            )
        )

    def _place_dialog_near_pet(self, dialog) -> None:
        """对话框以桌宠为中心显示；桌宠贴边时夹回屏幕内，避免标题栏跑到屏幕外。"""
        pet_rect = self._pet_rect()
        area = available_geometry_for(pet_rect)
        if area is None:
            return
        dialog.move(
            calculate_centered_position(pet_rect, widget_size(dialog), area)
        )

    def _show_timeline(self) -> None:
        timeline = getattr(self, "_conversation_timeline", None)
        if timeline is None:
            self._show_bubble("还没有可查看的对话。", 3000, mood=None)
            return
        from meapet.desktop.timeline_viewer import TimelineDialog

        dialog = getattr(self, "_timeline_dialog", None)
        if dialog is None:
            dialog = TimelineDialog(timeline, None)
            self._timeline_dialog = dialog
            self._place_dialog_near_pet(dialog)
        else:
            move_within_screen(dialog, dialog.pos())
        dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_timeline_turn(self, turn_id: str) -> None:
        timeline = getattr(self, "_conversation_timeline", None)
        turn = timeline.find(turn_id) if timeline is not None else None
        if turn is None:
            self._show_bubble("这轮完整回复已不在最近缓存中。", 3500, mood=None)
            return
        from meapet.desktop.timeline_viewer import TurnDetailDialog

        dialog = TurnDetailDialog(turn, None)
        self._timeline_turn_dialog = dialog
        self._place_dialog_near_pet(dialog)
        dialog.show()
        dialog.raise_()

    def _reset_memory(self):
        llm = (getattr(self, "config", {}) or {}).get("llm") or {}
        if str(llm.get("mode") or "direct").strip().lower() == "agent":
            self._start_new_agent_session()
            return
        import random
        reply = show_message_dialog(
            self,
            title="确认重置",
            text="确定要让梅尔忘掉一切喵？\n\n聊天记录、好感度、记忆都会清空。",
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.memory.reset_all()
            timeline = getattr(self, "_conversation_timeline", None)
            if timeline is not None:
                timeline.clear()
            engine = getattr(self, "chat_engine", None)
            clear_history = getattr(engine, "clear_history", None)
            if callable(clear_history):
                clear_history()
            self._show_bubble(
                "-什么都没发生喵。" if random.random() < 0.1 else "……你是谁喵？",
                3000,
            )

    def _start_new_agent_session(self) -> None:
        reply = show_message_dialog(
            self,
            title="新建 Agent 会话",
            text=(
                "确定要开始一个新的 Agent 会话吗？\n\n"
                "当前会话将结束，MeaPet 中的旧时间线仍可只读查看。"
                "此操作不会删除 Agent 服务端的数据或长期记忆，也暂不支持切回旧会话。"
            ),
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        worker = getattr(self, "_chat_worker", None)
        if worker is not None and callable(getattr(worker, "terminate", None)):
            try:
                worker.terminate()
            except Exception:
                pass

        invalidate = getattr(self, "_invalidate_active_conversation", None)
        if callable(invalidate):
            invalidate()

        llm = self.config.setdefault("llm", {})
        agent = llm.setdefault("agent", {})
        agent["session_id"] = f"meapet-{uuid.uuid4().hex}"
        kind = str(agent.get("kind") or "hermes").strip().lower()
        if kind == "openclaw":
            # OpenClaw 的真正会话边界是 sessionKey；只换本地 session_id
            # 仍会继续原 Gateway 上下文。
            agent["session_key"] = (
                f"agent:main:meapet:{uuid.uuid4().hex}"
            )
            agent.pop("upstream_session_id", None)
        elif kind == "hermes":
            # Hermes 下次连接必须 session.create，而不是 resume 旧的持久会话。
            agent.pop("remote_session_id", None)
        self._persisted_agent_remote_session_id = ""
        self._agent_history = []
        self._active_agent_turn_id = ""
        self._agent_tts_workers = {}
        self._agent_bubbles = {}

        try:
            old_adapter = getattr(self, "agent_adapter", None)
            close = getattr(old_adapter, "close", None)
            if callable(close):
                from meapet.async_runtime import submit

                submit(close())
        except Exception:
            pass

        from meapet.agent.factory import create_agent_adapter_from_config

        try:
            self.agent_adapter = create_agent_adapter_from_config(self.config)
            if kind == "agent_link":
                attach = getattr(self, "_attach_agent_link_control", None)
                if callable(attach):
                    attach()
            self._save_config()
            refresh_key = getattr(self, "_refresh_conversation_key", None)
            if callable(refresh_key):
                refresh_key()
            self._show_bubble(
                "已开始新的 Agent 会话。旧时间线仍可查看。",
                4500,
                mood=None,
            )
        except Exception as exc:
            safe_print(f"[agent] 新建会话失败: {type(exc).__name__}: {exc}")
            self._show_bubble("新建 Agent 会话失败，请检查配置。", 8000, mood=None)

    def _copy_agent_control_token(self) -> None:
        from meapet.config.store import resolve_secret

        raw = str(
            (self.config.get("agent_control") or {}).get("auth_token") or ""
        ).strip()
        token = resolve_secret(raw, ("MEA_PET_CONTROL_TOKEN",))
        if not token:
            self._show_bubble("当前没有可复制的 Agent 控制令牌。", 3500, mood=None)
            return
        QApplication.clipboard().setText(token)
        self._show_bubble("Agent 控制令牌已复制。", 3000, mood=None)

    def _regenerate_agent_control_token(self) -> None:
        reply = show_message_dialog(
            self,
            title="重新生成 Agent 控制令牌",
            text="重新生成后，旧令牌会立即失效，当前 Agent 需要改用新令牌。继续吗？",
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
            default_button=QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        rotate = getattr(self, "_rotate_control_token", None)
        if not callable(rotate):
            self._show_bubble("令牌重新生成失败。", 5000, mood=None)
            return
        rotate()
        self._show_bubble("已重新生成 Agent 控制令牌，旧令牌已失效。", 4500, mood=None)

    def _reopen_setup_wizard(self):
        try:
            from wizard.app import SetupWizard

            preview = None
            capture_preview = getattr(
                self,
                "_capture_live2d_viewport_preview",
                None,
            )
            if callable(capture_preview):
                preview = capture_preview()
            wizard_args = {
                "config_path": getattr(self, "_config_path", None),
                "initial_config": getattr(self, "config", None),
            }
            if preview is not None:
                wizard_args["live2d_preview"] = preview
            self._setup_wizard = SetupWizard(
                **wizard_args,
            )
            apply_config = getattr(self, "_apply_runtime_config", None)
            if callable(apply_config):
                self._setup_wizard.config_saved.connect(apply_config)
            self._setup_wizard.show()
        except Exception as e:
            safe_print(f"[pet] 打开配置页失败: {e}")
            self._show_bubble(f"打开配置页失败喵: {e}", 3000)
