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
        w_enabled = self.config.get("watcher", {}).get("enabled", False)
        watch_text = (
            status_language.menu_watch_disable()
            if w_enabled
            else status_language.menu_watch_enable()
        )
        watch_action = QAction(watch_text, self)
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
        menu.addMenu(vision_menu)

        voice_enabled = self.config.get("voice_input", {}).get("enabled", False)
        voice_text = (
            status_language.menu_voice_input_disable()
            if voice_enabled
            else status_language.menu_voice_input_enable()
        )
        voice_action = QAction(voice_text, self)
        voice_action.setToolTip("开启后输入框出现麦克风按钮，点击录音转文字（本地离线）")
        voice_action.triggered.connect(self._toggle_voice_input)
        menu.addAction(voice_action)
        menu.addSeparator()

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
        menu.addMenu(display_menu)

        settings_menu = QMenu("设置与数据", self)
        settings_menu.setIcon(standard_icon("settings"))
        settings_menu.setObjectName("SettingsAndDataMenu")
        apply_named_style(settings_menu, "MENU_STYLE")
        settings_menu.setAccessibleName("设置与数据")
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

    def _show_about(self):
        """打开"关于"页面（版本信息与 Live2D 模型来源致谢）。"""
        from meapet.desktop.dialogs import AboutDialog
        AboutDialog(self).exec_()

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
