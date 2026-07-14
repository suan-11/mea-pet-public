"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import time

from meapet.desktop import status_language
from meapet.desktop.chat_input import set_awaiting_reply_state
from meapet.utils import (
    cloud_vision_allowed,
    debug_enabled,
    is_loopback_url,
)
from meapet.desktop.workers import TTSWorker
from meapet.desktop.dialogs import (
    confirm_cloud_capture_scope,
    confirm_cloud_vision,
)
from meapet.config.store import (
    resolve_vision_api_base,
    resolve_vision_backend,
    resolve_vision_host,
)
from meapet.log import get_color_logger
from meapet.vision.policy import resolve_vision_route

log = get_color_logger("watch_ctrl")


def _log_private_text(label: str, text: str) -> None:
    """默认仅记录识图文本长度，调试模式才打印正文。"""
    value = str(text or "")
    if debug_enabled():
        log.debug(f"{label}: chars={len(value)}\n{value}")
    else:
        log.debug(f"{label}: chars={len(value)}")


class PetWatcherMixin:
    def _start_watcher_timer(self):
        """随机间隔，从配置读取范围"""
        interval = (self.config.get("watcher") or {}).get("interval", {"min_ms": 60000, "max_ms": 600000})
        min_ms = interval.get("min_ms", 60000)
        max_ms = interval.get("max_ms", 600000)
        if min_ms > max_ms:
            min_ms, max_ms = max_ms, min_ms  # 保证最小值不大于最大值
        ms = random.randint(min_ms, max_ms)
        self._watcher_timer.start(ms)


    def _vision_backend(self) -> str:
        vision_cfg = self.config.get("vision", {}) or {}
        llm_cfg = self.config.get("llm", {}) or {}
        return resolve_vision_backend(vision_cfg, llm_cfg)

    def _vision_route(self):
        return resolve_vision_route(
            self.config.get("vision", {}) or {},
            self.config.get("llm", {}) or {},
        )

    def _vision_endpoint(self) -> str:
        """返回识图请求的实际目标地址。"""
        vision_cfg = self.config.get("vision", {}) or {}
        llm_cfg = self.config.get("llm", {}) or {}
        if self._vision_route().mode == "inherit":
            if str(llm_cfg.get("mode") or "direct").lower() == "agent":
                agent = llm_cfg.get("agent") or {}
                return str(agent.get("base_url") or "")
            direct = llm_cfg.get("direct") or {}
            protocol = str(direct.get("protocol") or "").lower()
            if protocol == "ollama_chat":
                return str(
                    direct.get("host")
                    or llm_cfg.get("host")
                    or "http://127.0.0.1:11434"
                )
            return str(direct.get("api_base") or llm_cfg.get("api_base") or "")
        if self._vision_backend() == "mimo":
            return resolve_vision_api_base(vision_cfg, llm_cfg)
        return resolve_vision_host(vision_cfg, llm_cfg)

    def _is_cloud_vision(self) -> bool:
        """判断截图是否会离开本机；未知或远程目标按云端处理。"""
        if self._vision_route().mode == "inherit":
            return not is_loopback_url(self._vision_endpoint())
        backend = self._vision_backend()
        if backend == "mimo":
            return True
        if backend == "ollama":
            return not is_loopback_url(self._vision_endpoint())
        return True

    def _confirm_cloud_capture(self, force: bool = False) -> bool:
        """Gate before every cloud screenshot. Always ask; no session skip."""
        self.config.setdefault("watcher", {})
        if not self._is_cloud_vision():
            return True

        if not cloud_vision_allowed(self.config, True):
            log.info("[watcher] cloud vision disabled (allow_cloud=false)")
            self._show_bubble(status_language.cloud_vision_disabled(), 4000)
            return False

        approval = confirm_cloud_capture_scope(
            self,
            timeout_seconds=5,
        )
        if approval is None:
            log.info("[watcher] user denied cloud screenshot upload")
            self._show_bubble(status_language.watching_denied(), 2500)
            return False
        watcher = getattr(self, "_watcher", None)
        if watcher is not None:
            watcher.capture_scope = approval.scope
            watcher.capture_region = approval.region
            watcher.capture_application = approval.application
        log.info("[watcher] user allowed cloud vision for this capture only")
        return True

    def _do_screen_watch(self, force: bool = False):
        """Screenshot + vision roast. Cloud path must pass confirmation first."""
        watcher_cfg = self.config.get("watcher", {})
        route = self._vision_route()
        if route.mode == "disabled":
            return
        if not route.available:
            log.warning(f"[watcher] vision route unavailable: {route.reason}")
            self._show_bubble(
                status_language.vision_mode_unavailable(route.reason),
                5000,
            )
            if hasattr(self, "_watcher_timer"):
                self._start_watcher_timer()
            return
        if not watcher_cfg.get("enabled", False) and not force:
            return
        if self._standby and not force:
            return
        if self._awaiting_reply and not force:
            self._start_watcher_timer()
            return
        if self._watcher.isRunning():
            if force:
                if not self._watcher.stop():
                    log.warning("[watcher] previous capture did not stop in time")
                    set_awaiting_reply_state(self, False)
                    self._start_watcher_timer()
                    return
            else:
                return

        if self._is_cloud_vision():
            if not self._confirm_cloud_capture(force=force):
                set_awaiting_reply_state(self, False)
                self._start_watcher_timer()
                return
        else:
            log.info(f"[watcher] local vision backend={self._vision_backend()} (no upload)")

        if not self._watcher.prepare_start():
            log.warning("[watcher] capture thread is still running")
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()
            return

        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        idle_s = time.time() - self._last_interaction_time
        self._watcher.set_idle_minutes(idle_s / 60.0)
        reply_adapter = (
            getattr(self, "agent_adapter", None)
            if self._is_agent_mode()
            else getattr(self, "chat_engine", None)
        )
        self._watcher.configure_reply(
            reply_adapter,
            frontend_context=self._build_agent_frontend_context(),
            tts_enabled=bool(
                getattr(getattr(self, "tts", None), "enabled", False)
            ),
        )
        if self._is_cloud_vision():
            self._show_bubble("（已确认）梅尔酱偷看并上传识别中…", 30000)
        else:
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
            _log_private_text("[watch] _pending_reply 已设置", text)
            voice = ""
            voice_language = ""
            tts_style = ""
            try:
                w = getattr(self, "_watcher", None)
                if w is not None:
                    voice = str(getattr(w, "last_voice_text", "") or "").strip()
                    voice_language = str(
                        getattr(w, "last_voice_language", "") or ""
                    ).strip()
                    tts_style = str(
                        getattr(w, "last_tts_style", "") or ""
                    ).strip()
                    w.last_voice_text = ""
                    w.last_voice_language = ""
                    w.last_tts_style = ""
            except Exception as e:
                log.error(f"[watch] 取语音元数据失败: {type(e).__name__}")
            tts = getattr(self, "tts", None)
            if (
                tts is None
                or not bool(getattr(tts, "enabled", False))
                or not voice
                or not voice_language
            ):
                self.show_reply(
                    text,
                    mood,
                    duration_ms=self.config["bubble_duration_ms"]["watch"],
                )
                set_awaiting_reply_state(self, False)
                self._start_watcher_timer()
                return
            self._watch_tts_worker = TTSWorker(
                tts,
                voice,
                mood=mood,
                style=tts_style,
                language=voice_language,
            )
            self._watch_tts_worker.start()
            self._ensure_tts_poll()
        except Exception as e:
            log.error(f"[watch] _on_watch_result 异常: {type(e).__name__}" + (f": {e}" if debug_enabled() else ""))
            self.show_reply(text, mood, duration_ms=self.config["bubble_duration_ms"]["watch"])
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()

    def _on_watch_tts_and_show(self, raw: str, reply: str = None, mood: str = None):
        log.info(f"[watch] _on_watch_tts_and_show called, raw={raw is not None}, reply={reply is not None}")
        if raw is None or reply is None:
            log.warning("[TTS] watch tts returned None, skip audio")
            if reply and mood:
                self.show_reply(reply, mood, duration_ms=self.config["bubble_duration_ms"]["watch"])

            else:
                log.warning("[watch] _pending_reply 已丢失!")
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()
            return

        """屏幕吐槽：语音合成完成 → 显示文字 + 播放"""
        wav_path = raw.rsplit("|", 1)[0] if "|" in raw else raw
        # reply/mood 由调用方 _poll_tts 直接传入，不再从 _pending_reply 重复读取
        audio_duration_ms = self._get_wav_duration_ms(wav_path) if wav_path else 0
        bubble_ms = self.config["bubble_duration_ms"]["watch"]
        if self.config["tts"]["sync_with_audio"]:
            bubble_ms = max(audio_duration_ms + 500, bubble_ms)
        self.show_reply(reply, mood, duration_ms=bubble_ms)
        set_awaiting_reply_state(self, False)
        self._start_watcher_timer()
        if wav_path and os.path.exists(wav_path):
            self._play_audio(wav_path)

    def _on_watch_tts_error(self, err: str):
        """屏幕吐槽 TTS 合成失败 —— 至少显示文字，不卡死"""
        _log_private_text("[watch] TTS 合成失败", err)
        log.error(f"[watch] TTS 合成失败: error_chars={len(err or '')}")
        set_awaiting_reply_state(self, False)
        if hasattr(self, '_pending_reply'):
            reply, mood = self._pending_reply
            del self._pending_reply
            self.show_reply(reply, mood, duration_ms=5000)
        self._start_watcher_timer()

    def _on_watch_error(self, err: str):
        _log_private_text("[watch] 识图错误", err)
        # 显示简短提示，不打扰主人
        set_awaiting_reply_state(self, False)
        self._show_bubble(f"唔…看不清喵 ({err[:30]})", self.config["bubble_duration_ms"]["default"])

        self._start_watcher_timer()

    def _on_watch_silent(self):
        """视觉模型评估后决定不说话——安静恢复"""
        set_awaiting_reply_state(self, False)
        self._show_bubble("😼 没什么好说的喵…", self.config["bubble_duration_ms"]["default"])

        self._start_watcher_timer()

    def _on_watch_progress(self, msg: str):
        """显示识图/评估阶段状态"""
        self._show_bubble(msg, 0)  # 持久显示直到下一个阶段

    def _on_search_request(self, query: str):
        """处理 Web 搜索请求（来自 watcher）—— 暂无可用搜索后端"""
        result = f"（关于「{query}」的搜索结果暂时无法获取喵）"
        if hasattr(self, '_watcher') and self._watcher:
            self._watcher.set_search_result(result)

    def _toggle_watcher_enabled(self):
        """Right-click toggle for screen watch. Cloud needs explicit consent."""
        w = self.config.setdefault("watcher", {
            "enabled": False,
            "allow_cloud": False,
            "require_confirm": True,
        })
        turning_on = not w.get("enabled", False)

        if turning_on and self._is_cloud_vision():
            if not w.get("allow_cloud", False):
                q = "\n".join([
                    "当前识图后端会把截图发到云端。",
                    "",
                    "是否授权「允许云端识图」并开启屏幕观察？",
                    "之后每次自动偷看默认仍会再确认一次。",
                ])
                allowed = confirm_cloud_vision(
                    self,
                    title="开启云端屏幕观察？",
                    message=q,
                    timeout_seconds=5,
                    accept_text="允许并开启",
                )
                if not allowed:
                    self._show_bubble("未开启屏幕观察喵", 2500)
                    return
                w["allow_cloud"] = True
                w["require_confirm"] = True
            else:
                q = "\n".join([
                    "将定时截屏并上传到云端识别。",
                    "每次上传前默认仍会弹窗确认。",
                    "",
                    "继续开启？",
                ])
                allowed = confirm_cloud_vision(
                    self,
                    title="开启屏幕观察？",
                    message=q,
                    timeout_seconds=5,
                    accept_text="继续开启",
                )
                if not allowed:
                    self._show_bubble("未开启屏幕观察喵", 2500)
                    return

        w["enabled"] = turning_on
        # Always require per-capture confirm for cloud uploads
        w["require_confirm"] = True
        w["confirm_once_session"] = False
        self._cloud_watch_confirmed = False
        # 统一写入 config.json
        self._save_config()

        if w["enabled"]:
            if self._is_cloud_vision():
                self._show_bubble(status_language.watcher_enabled_cloud(), 3500)
            else:
                self._show_bubble(status_language.watcher_enabled_local(), 2500)
            self._start_watcher_timer()
        else:
            if hasattr(self, "_watcher_timer") and self._watcher_timer:
                self._watcher_timer.stop()
            self._show_bubble("屏幕观察已关闭喵", 2500)
