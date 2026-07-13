"""Config load/save and vision backend switching."""
from __future__ import annotations

import json

from meapet.config.store import (
    load_config as store_load_config,
    save_config as store_save_config,
    normalize_config,
)
from meapet.log import get_color_logger

log = get_color_logger("config_bridge")


class PetConfigBridgeMixin:
    def _load_config(self, path: str) -> dict:
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
            store_save_config(self.config)
        except Exception as e:
            log.error(f"[config] 保存失败: {e}")

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
            self._watcher.stop()
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