"""语音输入 Mixin — 管理 VoiceEngine 生命周期和 UI 交互。

三种收音方式（P1-3 补齐后）：
- **点按切换**（toggle，默认）：点一下开始录，再点一下停 → 转文字 → 填入输入框。
- **常驻收音 + 自动断句**（hands_free）：引擎一直开着，VAD 断出一句就发一句。
- **按住说话**（PTT，可选）：全局热键按住即录、松开即停；热键绑不上时回落点按。

唤醒词（KWS）是**独立开关**，默认关闭：开了才常驻监听，喊到词就进收音。
隐私边界与 watcher 同档——开语音输入不等于授权常驻监听，必须是用户单独点头。
"""
from __future__ import annotations

from typing import Optional

from meapet.log import get_color_logger

log = get_color_logger("voice")


class PetVoiceMixin:
    """语音输入功能 Mixin。

    需挂载到 MeaPet 主窗口（QWidget 子类），要求 self 具备:
    - self.config (dict): 全局配置
    - self._save_config() 方法
    - self._show_bubble(text, mood=None) 方法
    """

    _voice_engine = None
    _voice_listening = False
    _ptt = None
    _wake_listener = None

    # ------------------------------------------------------------------
    # 初始化 / 销毁
    # ------------------------------------------------------------------

    def _init_voice(self) -> None:
        """在 MeaPet.__init__ 的 _init_watcher 之后调用。"""
        cfg = self.config.get("voice_input") or {}
        if cfg.get("enabled", False):
            self._start_voice_engine(cfg)
            self._setup_ptt(cfg)
            self._sync_wake_word(cfg)

    def _start_voice_engine(self, cfg: Optional[dict] = None) -> None:
        """创建 VoiceEngine（不开始录音，仅准备模型）。"""
        from meapet.voice.engine import VoiceEngine

        cfg = cfg or (self.config.get("voice_input") or {})

        self._voice_engine = VoiceEngine(
            language=cfg.get("language", "zh"),
            precision=cfg.get("precision"),
            input_device_index=cfg.get("input_device_index"),
            mode=cfg.get("mode"),
            vad_config=cfg.get("vad"),
        )
        self._voice_engine.recording_started.connect(self._on_voice_recording_started)
        self._voice_engine.recording_stopped.connect(self._on_voice_recording_stopped)
        self._voice_engine.result_ready.connect(self._on_voice_result)
        self._voice_engine.error.connect(self._on_voice_error)

    def _stop_voice_engine(self) -> None:
        if self._voice_engine is not None:
            try:
                self._voice_engine.stop(0)
            except Exception:
                pass
            self._voice_engine = None
        self._voice_listening = False

    # ------------------------------------------------------------------
    # 全局按住说话（PTT）
    # ------------------------------------------------------------------

    def _setup_ptt(self, cfg: Optional[dict] = None) -> bool:
        """按配置注册全局 PTT；没配热键或平台不支持就静默保持点按模式。

        静默不是装死：热键绑不上在设备上极常见（被别的软件占了），为此弹一窗
        会变成每次启动都打扰一次；日志里点名，用户查得见。
        """
        cfg = cfg or (self.config.get("voice_input") or {})
        hotkey = str(cfg.get("ptt_hotkey") or "").strip()
        self._teardown_ptt()
        if not hotkey:
            return False
        from meapet.voice.ptt import GlobalPushToTalk, hotkey_is_safe

        if not hotkey_is_safe(hotkey):
            log.warning(f"[voice] PTT 热键 {hotkey} 不安全（缺修饰键或解析不过），不注册")
            return False
        self._ptt = GlobalPushToTalk(self)
        if not self._ptt.register(hotkey):
            self._ptt = None
            return False
        self._ptt.pressed.connect(self._on_ptt_pressed)
        self._ptt.released.connect(self._on_ptt_released)
        return True

    def _teardown_ptt(self) -> None:
        if self._ptt is not None:
            try:
                self._ptt.unregister()
            except Exception as exc:  # noqa: BLE001 - 卸载失败最坏是热键残留到进程退出
                # 记一笔而不是静默吞：热键残留会让「改了热键却还是旧的生效」这种
                # 怪现象无从追查，日志里留痕至少能对上号。
                log.warning(f"[voice] 全局热键卸载失败：{type(exc).__name__}: {exc}")
            self._ptt = None


    def _on_ptt_pressed(self) -> None:
        """按下即开录；已经在录就不再叠（连按只会被 MOD_NOREPEAT 挡掉，这里兜一道）。"""
        if self._voice_engine is None:
            return
        if not self._voice_engine.isRunning():
            self._voice_engine.toggle()

    def _on_ptt_released(self) -> None:
        """松开即停：录音线程读到 _toggle 就收口，转写照常走。"""
        if self._voice_engine is not None and self._voice_engine.isRunning():
            self._voice_engine.toggle()

    # ------------------------------------------------------------------
    # 唤醒词（KWS，默认关闭）
    # ------------------------------------------------------------------

    def _sync_wake_word(self, cfg: Optional[dict] = None) -> None:
        """按配置开/关常驻唤醒监听。"""
        cfg = cfg or (self.config.get("voice_input") or {})
        wake = cfg.get("wake_word") if isinstance(cfg.get("wake_word"), dict) else {}
        want = bool(wake.get("enabled", False))
        if not want:
            self._stop_wake_listener()
            return
        if self._wake_listener is not None and self._wake_listener.isRunning():
            return
        from meapet.voice.kws import WakeWordListener

        listener = WakeWordListener(
            keywords_file=str(wake.get("keywords_file") or "").strip() or None,
            input_device_index=cfg.get("input_device_index"),
            parent=self,
        )
        listener.detected.connect(self._on_wake_word)
        listener.error.connect(self._on_wake_error)
        listener.start()
        self._wake_listener = listener
        log.info("[voice] 唤醒词监听已启动")

    def _stop_wake_listener(self) -> None:
        if self._wake_listener is not None:
            try:
                self._wake_listener.stop(0)
            except Exception as exc:  # noqa: BLE001 - 停不掉最坏是线程活到进程退出
                log.warning(f"[voice] 唤醒词监听停止失败：{type(exc).__name__}: {exc}")
            self._wake_listener = None

    def _on_wake_word(self, keyword: str) -> None:
        """喊到词：引擎没开就开一轮收音，开了就不打断（它已经在听了）。"""
        if self._voice_engine is None:
            self._show_bubble_edge(f"听到「{keyword}」")
            return
        if not self._voice_engine.isRunning():
            self._voice_engine.toggle()
        self._show_bubble_edge(f"听到「{keyword}」")

    def _on_wake_error(self, message: str) -> None:
        log.warning(f"[voice] 唤醒词监听失败：{message}")
        self._show_bubble_edge(message)

    # ------------------------------------------------------------------
    # 开关 + UI 刷新
    # ------------------------------------------------------------------

    def _toggle_voice_input(self) -> None:
        """右键菜单回调：开启/关闭语音输入，并立即刷新按钮可见性。"""
        # 默认值只由 config.store.normalize_config 一处给，这里不再抄一份
        vi = self.config.setdefault("voice_input", {})
        turning_on = not vi.get("enabled", False)
        vi["enabled"] = turning_on
        self._save_config()

        if turning_on:
            self._start_voice_engine(vi)
            self._setup_ptt(vi)
            self._sync_wake_word(vi)
            self._show_bubble_edge("语音输入已开启")
        else:
            self._stop_voice_engine()
            self._teardown_ptt()
            self._stop_wake_listener()
            self._show_bubble_edge("语音输入已关闭")

        self._refresh_voice_button()

    @property
    def voice_enabled(self) -> bool:
        return bool(
            (self.config.get("voice_input") or {}).get("enabled", False)
        )

    def _refresh_voice_button(self) -> None:
        """强制刷新输入框语音按钮的可见性。在 _start_chat 创建输入框后调用。"""
        composer = getattr(self, "_chat_input", None)
        if composer is None:
            return
        try:
            show = self.voice_enabled
            if hasattr(composer, "show_voice_button"):
                composer.show_voice_button(show)
                log.info(f"[voice] button visibility refreshed: show={show}")
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # 录音切换
    # ------------------------------------------------------------------

    def _voice_toggle_recording(self) -> str:
        """点击麦克风按钮。返回状态描述字符串。"""
        if self._voice_engine is None:
            return "off"

        if not self._voice_engine.isRunning():
            ok = self._voice_engine.toggle()
            return "recording" if ok else "off"
        else:
            self._voice_engine.toggle()
            return "processing"

    # ------------------------------------------------------------------
    # 信号回调
    # ------------------------------------------------------------------

    def _on_voice_recording_started(self) -> None:
        self._voice_listening = True
        self._refresh_voice_button()
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("recording")

    def _on_voice_recording_stopped(self) -> None:
        self._voice_listening = False

    def _on_voice_result(self, text: str) -> None:
        if not text:
            return
        auto_send = bool(
            (self.config.get("voice_input") or {}).get("auto_send", False)
        )
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("done")
        if auto_send and hasattr(self, "_on_input_submit"):
            self._on_input_submit(text)
        elif composer:
            try:
                composer.input.setText(text)
            except RuntimeError:
                pass
        else:
            self._show_bubble_edge(
                f"识别结果：{text[:50]}{'…' if len(text) > 50 else ''}"
            )

    def _on_voice_error(self, message: str) -> None:
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("error")
        self._show_bubble_edge(message)

    # ------------------------------------------------------------------
    # 便捷气泡
    # ------------------------------------------------------------------

    def _show_bubble_edge(self, text: str):
        try:
            self._show_bubble(text, mood=None)
        except Exception:
            pass
