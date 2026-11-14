"""Config load/save and vision backend switching."""
from __future__ import annotations

import json

from meapet.config.defaults import DEFAULT_OLLAMA_VISION_MODEL
from meapet.config.store import (
    load_config as store_load_config,
    save_config as store_save_config,
    normalize_config,
    normalize_live2d_placement_anchor,
    normalize_live2d_window_mask,
    normalize_live2d_window_shape,
    resolve_writable_config_path,
)
from meapet.log import get_color_logger

log = get_color_logger("config_bridge")


class PetConfigBridgeMixin:
    def _load_config(self, path: str) -> dict:
        # 记住读取路径；保存时映射到可写 config.json（example → config.json）
        self._config_path = resolve_writable_config_path(path)
        try:
            return store_load_config(path)
        except Exception:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

    def _save_config(self):
        try:
            self.config = normalize_config(self.config)
            target = getattr(self, "_config_path", None) or resolve_writable_config_path()
            self._config_path = target
            store_save_config(self.config, target)
        except Exception as e:
            log.error(f"[config] 保存失败: {e}")

    def _apply_runtime_config(self, config: dict) -> bool:
        """在主线程切换活动后端；失败时不自动回退另一后端。

        停止旧 worker / MCP / watcher 时只做 cancel / should_exit，
        不在 GUI 线程 ``Future.result`` / ``QThread.wait``，避免配置保存
        路径把窗口卡成“未响应”。
        """
        current_live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        old_window_mask = normalize_live2d_window_mask(
            current_live2d.get("window_mask")
            if isinstance(current_live2d, dict)
            else None
        )
        old_placement_anchor = normalize_live2d_placement_anchor(
            current_live2d.get("placement_anchor")
            if isinstance(current_live2d, dict)
            else None,
            old_window_mask,
        )
        old_window_shape = normalize_live2d_window_shape(
            current_live2d.get("window_shape")
            if isinstance(current_live2d, dict)
            else None
        )

        worker = getattr(self, "_chat_worker", None)
        if worker is not None:
            try:
                terminate = getattr(worker, "terminate", None)
                if callable(terminate):
                    terminate()
                delete_later = getattr(worker, "deleteLater", None)
                if callable(delete_later):
                    delete_later()
            except Exception:
                pass
        self._chat_worker = None
        for name in ("_chat_poll", "_chat_timeout"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

        invalidate = getattr(self, "_invalidate_active_conversation", None)
        if callable(invalidate):
            invalidate()
        stop_control = getattr(self, "_stop_control", None)
        if callable(stop_control):
            stop_control()
        stop_onebot = getattr(self, "_stop_onebot", None)
        if callable(stop_onebot):
            stop_onebot()
        self._disconnect_watcher_signals()
        stop_voice = getattr(self, "_stop_voice_engine", None)
        if callable(stop_voice):
            stop_voice()
        # P1-3：全局热键与常驻监听是**系统级**资源，重载配置时必须一起收，
        # 否则 _init_voice 会再注册一次热键（第二次必然被占用失败）并在后台
        # 叠一条没人持有引用的监听线程。
        teardown_ptt = getattr(self, "_teardown_ptt", None)
        if callable(teardown_ptt):
            teardown_ptt()
        stop_wake = getattr(self, "_stop_wake_listener", None)
        if callable(stop_wake):
            stop_wake()

        old_adapter = getattr(self, "agent_adapter", None)
        close = getattr(old_adapter, "close", None)
        if callable(close):
            try:
                result = close()
                if result is not None:
                    from meapet.async_runtime import submit

                    submit(result)
            except Exception:
                pass

        self.config = normalize_config(config or {})
        new_live2d = self.config.get("live2d") or {}
        new_window_mask = normalize_live2d_window_mask(
            new_live2d.get("window_mask")
            if isinstance(new_live2d, dict)
            else None
        )
        new_placement_anchor = normalize_live2d_placement_anchor(
            new_live2d.get("placement_anchor")
            if isinstance(new_live2d, dict)
            else None,
            new_window_mask,
        )
        new_window_shape = normalize_live2d_window_shape(
            new_live2d.get("window_shape")
            if isinstance(new_live2d, dict)
            else None
        )
        viewport_changed = (
            old_window_mask != new_window_mask
            or old_placement_anchor != new_placement_anchor
            or old_window_shape != new_window_shape
        )
        try:
            apply_motion = getattr(self, "_apply_motion_preference", None)
            if callable(apply_motion):
                apply_motion()
            apply_display = getattr(self, "_apply_display_preference", None)
            if callable(apply_display):
                # 窗口大小（display.size_factor）保存后立即生效，不必重启桌宠。
                apply_display()
            if viewport_changed:
                apply_viewport = getattr(
                    self,
                    "_apply_live2d_viewport_preference",
                    None,
                )
                if callable(apply_viewport):
                    # 视觉视口与站立锚点只改变窗口几何；互动坐标仍属完整画布。
                    apply_viewport()
            self._init_tts()
            self._init_chat()
            self._init_watcher()
            self._init_control()
            init_onebot = getattr(self, "_init_onebot", None)
            if callable(init_onebot):
                init_onebot()
            self._init_voice()
        except Exception as exc:
            log.error(
                f"[config] 运行时应用失败: {type(exc).__name__}: {exc}"
            )
            show = getattr(self, "_show_bubble", None)
            if callable(show):
                show("新配置未能启动，请检查配置。", 8000, mood=None)
            return False

        show = getattr(self, "_show_bubble", None)
        if callable(show):
            show("新配置已应用。", 3500, mood=None)
        return True

    def _disconnect_watcher_signals(self):
        if not hasattr(self, "_watcher") or self._watcher is None:
            return
        for sig_name in (
            "result_ready",
            "error",
            "silent",
            "progress",
            "search_request",
        ):
            try:
                getattr(self._watcher, sig_name).disconnect()
            except Exception:
                pass
        try:
            # 默认非阻塞：只置停止标志，不 join QThread。
            self._watcher.stop(timeout_ms=0)
        except TypeError:
            try:
                self._watcher.stop()
            except Exception:
                pass
        except Exception:
            pass

    def _set_vision_backend(self, backend: str):
        backend = (backend or "ollama").lower()
        v = self.config.setdefault("vision", {})
        v["backend"] = backend
        if backend == "mimo":
            if not v.get("model") or v.get("model") in (DEFAULT_OLLAMA_VISION_MODEL,):
                v["model"] = "mimo"
        else:
            if not v.get("model") or v.get("model") in ("mimo",):
                v["model"] = DEFAULT_OLLAMA_VISION_MODEL
        self._save_config()
        self._disconnect_watcher_signals()
        self._init_watcher()
        self._show_bubble(f"识图后端切换为 {backend}", 2000)

    def _set_vision_model(self, model_name: str):
        self.config.setdefault("vision", {})["model"] = model_name
        if model_name and model_name not in ("mimo",) and "mimo" not in model_name.lower():
            self.config["vision"]["backend"] = "ollama"
        self._save_config()
        self._disconnect_watcher_signals()
        self._init_watcher()
        short = model_name.split(":")[0]
        self._show_bubble(f"识图模型切换为 {short}", 2000)
