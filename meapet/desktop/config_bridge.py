"""Config load/save and vision backend switching."""
from __future__ import annotations

import json

from meapet.config.store import (
    load_config as store_load_config,
    save_config as store_save_config,
    normalize_config,
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
        self._disconnect_watcher_signals()

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
        try:
            apply_motion = getattr(self, "_apply_motion_preference", None)
            if callable(apply_motion):
                apply_motion()
            self._init_tts()
            self._init_chat()
            self._init_watcher()
            self._init_control()
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
            if not v.get("model") or v.get("model") in ("qwen3.5:4b",):
                v["model"] = "mimo"
        else:
            if not v.get("model") or v.get("model") in ("mimo",):
                v["model"] = "qwen3.5:4b"
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
