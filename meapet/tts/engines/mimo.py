"""TTS 引擎 mixin（从 tts.py 拆出）"""
from __future__ import annotations

import os
from typing import Optional
from meapet.paths import project_path
from meapet.log import get_color_logger
from meapet.utils import debug_enabled

log = get_color_logger("tts")


_MIMO_CLONE_MIME_BY_EXTENSION = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}
_MIMO_MAX_CLONE_VOICE_URI_BYTES = 10_000_000


class TtsMimoMixin:
    def _speak_mimo(
        self,
        tts_text: str,
        output_wav: str,
        mood: str = "neutral",
        lang_tag: str = "zh",
        style: str = "",
    ) -> Optional[tuple[str, str]]:
        """同步入口：内部走 httpx 异步客户端。"""
        from meapet.async_runtime import run as _arun
        return _arun(
            self._speak_mimo_async(
                tts_text,
                output_wav,
                mood=mood,
                lang_tag=lang_tag,
                style=style,
            ),
            timeout=max(float(getattr(self, "timeout", 60)), 150),
        )


    async def _speak_mimo_async(
        self,
        tts_text: str,
        output_wav: str,
        mood: str = "neutral",
        lang_tag: str = "zh",
        style: str = "",
    ) -> Optional[tuple[str, str]]:
        """MiMo TTS — 真异步 HTTP（httpx）。"""
        import base64
        import time
        from meapet.http_async import post_json

        if not self.mimo_api_key:
            log.error("MiMo TTS: 无 API Key")
            return None, ""

        style_prompt = self._mimo_style_for_mood(mood, style=style)
        url = f"{self.mimo_api_base.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "api-key": self.mimo_api_key,
        }

        voice_field = self.mimo_voice
        model_name = self.mimo_model
        ref = None
        if self._mimo_voiceclone:
            ref = self._pick_clone_ref_wav(mood)
            if not ref:
                log.warn(
                    "voice-clone 未找到参考音频。"
                    "请把 wav/mp3 放到 voice_cache/，或在 config 设 tts.clone_ref"
                )
                return None, ""
            uri = self._build_clone_voice_uri(ref)
            if not uri:
                return None, ""
            voice_field = uri
            model_name = "mimo-v2.5-tts-voiceclone"

        payload = {
            "model": model_name,
            "messages": [
                {"role": "user", "content": style_prompt if style_prompt else ""},
                {"role": "assistant", "content": tts_text},
            ],
            "audio": {"format": "wav", "voice": voice_field},
            "stream": False,
        }
        voice_log = (
            f"clone:{os.path.basename(ref)}"
            if self._mimo_voiceclone and ref
            else str(self.mimo_voice)
        )
        log.info(f"MiMo TTS 请求(async) model={model_name} voice={voice_log}")
        t1 = time.time()
        try:
            timeout = max(float(self.timeout), 120.0 if self._mimo_voiceclone else float(self.timeout))
            resp = await post_json(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            log.error(f"MiMo TTS 网络错误: {e}")
            return None, ""

        elapsed = time.time() - t1
        if resp.status_code != 200:
            log.warn(
                f"MiMo TTS HTTP {resp.status_code} ({elapsed:.1f}s) "
                f"body_len={len(resp.text or '')}"
            )
            if debug_enabled():
                log.debug(f"MiMo TTS error body [debug]: {(resp.text or '')[:300]}")
            return None, ""
        try:
            data = resp.json()
        except Exception as e:
            log.error(f"MiMo TTS JSON 解析失败: {e}")
            return None, ""

        message = (data.get("choices") or [{}])[0].get("message") or {}
        audio_obj = message.get("audio") or {}
        b64 = audio_obj.get("data") or message.get("audio_data") or ""
        if not b64:
            b64 = (data.get("audio") or {}).get("data") or ""
        if not b64:
            log.warn(
                f"MiMo TTS 响应无 audio.data keys={list(message.keys())}"
            )
            return None, ""
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            log.error(f"MiMo TTS base64 解码失败: {e}")
            return None, ""
        try:
            with open(output_wav, "wb") as f:
                f.write(raw)
        except Exception as e:
            log.error(f"MiMo TTS 写文件失败: {e}")
            return None, ""
        if not os.path.exists(output_wav) or os.path.getsize(output_wav) < 44:
            log.warn("MiMo TTS 输出文件异常")
            return None, ""
        log.info(
            f"✓ MiMo TTS output: {os.path.basename(output_wav)} "
            f"({elapsed:.1f}s, {os.path.getsize(output_wav)} bytes) lang={lang_tag}"
        )
        return output_wav, lang_tag

    def _mimo_style_for_mood(self, mood: str, style: str = "") -> str:
        """根据桌宠情绪生成 MiMo TTS 风格提示（user 消息）"""
        fixed_style = (self.mimo_style or "").strip()
        generated_style = " ".join((style or "").split())[:400]
        if fixed_style and generated_style:
            return f"{fixed_style}\n本句表演：{generated_style}"
        if generated_style:
            return generated_style
        if fixed_style:
            return fixed_style
        styles = {
            "happy": "明亮、雀跃的少女声线，语速稍快，句尾上扬，带一点撒娇感。",
            "excited": "兴奋、元气满满的少女声，语速快，像在分享好消息。",
            "angry": "生气但不失可爱的少女声，语气短促，略带嗔怒。",
            "annoyed": "不耐烦的少女声，轻声抱怨，略带毒舌。",
            "sad": "低落、轻柔的少女声，语速偏慢，带着一点委屈。",
            "melancholy": "淡淡忧伤的少女声，语速慢，气息轻。",
            "shy": "害羞的少女声，音量偏小，语速稍慢，带点犹豫。",
            "embarrassed": "不好意思的少女声，轻声、略结巴、带笑意。",
            "teary": "带着哭腔的少女声，轻声、断断续续。",
            "soft": "温柔、贴耳的少女声，语速适中，像在哄人。",
            "wistful": "若有所思的少女声，轻柔缓慢。",
            "neutral": "自然、亲切的少女声，语速适中，吐字清晰。",
        }
        return styles.get(mood or "neutral", styles["neutral"])

    def _normalize_voice_lang(self, lang: str = "") -> str:
        """统一语言代码：jp / zh / en。"""
        raw = (lang or getattr(self, "voice_lang", "") or "zh").strip().lower()
        if raw in ("jp", "ja", "jpn", "japanese", "日文", "日语"):
            return "jp"
        if raw in ("en", "eng", "english", "英文", "英语"):
            return "en"
        if raw in ("zh", "cn", "zh-cn", "zh_cn", "chinese", "中文", "汉语"):
            return "zh"
        return raw or "zh"

    def _detect_lang_from_path(self, path: str) -> str:
        """从文件名/路径推断参考音频语言（jp_/zh_/en_ 前缀）。"""
        if not path:
            return ""
        name = os.path.basename(path).lower()
        norm = path.replace("\\", "/").lower()
        if name.startswith("jp_") or name.startswith("ja_") or "/jp_" in norm:
            return "jp"
        if name.startswith("zh_") or name.startswith("cn_") or "/zh_" in norm:
            return "zh"
        if name.startswith("en_") or "/en_" in norm:
            return "en"
        return ""

    def _pick_clone_ref_wav(self, mood: str = "neutral") -> Optional[str]:
        """
        选择 voice-clone 参考音频（语言与 voice_lang 一致）：
        1) 显式 clone_ref / voice_ref
        2) 优先同语言样本：voice_cache + GPT-Sovits（zh_* / jp_*）
        3) 再回退其它语言样本
        """
        want = self._normalize_voice_lang(getattr(self, "voice_lang", "zh"))

        if self.mimo_clone_ref and os.path.isfile(self.mimo_clone_ref):
            ref_lang = self._detect_lang_from_path(self.mimo_clone_ref)
            if ref_lang and ref_lang != want:
                log.warn(
                    f"clone_ref 语言={ref_lang} 与 voice_lang={want} 不一致，"
                    f"仍使用显式路径: {os.path.basename(self.mimo_clone_ref)}"
                )
            return self.mimo_clone_ref

        candidates = []

        def _add_candidate(path: str, base_score: int = 0) -> None:
            if not path or not os.path.isfile(path):
                return
            name = os.path.basename(path)
            low = name.lower()
            if low.startswith("mea_"):
                return
            try:
                size = os.path.getsize(path)
            except OSError:
                return
            if size < 8 * 1024:
                return
            score = base_score + size
            ref_lang = self._detect_lang_from_path(path)
            if ref_lang == want:
                score += 50_000_000
            elif ref_lang:
                # 明确其它语言：大幅降权，避免 zh 合成却 clone 到 jp 样本
                score -= 40_000_000
            if "normal" in low:
                score += 10_000_000
            if mood and mood.lower() in low:
                score += 5_000_000
            candidates.append((score, path, ref_lang or "?"))

        for d in (
            self.mimo_clone_dir,
            project_path("voice_cache"),
        ):
            if not d or not os.path.isdir(d):
                continue
            try:
                for name in os.listdir(d):
                    if os.path.splitext(name)[1].lower() not in _MIMO_CLONE_MIME_BY_EXTENSION:
                        continue
                    _add_candidate(os.path.join(d, name))
            except OSError:
                continue

        # GPT-Sovits 参考：按 voice_lang 选 zh_* / jp_*
        try:
            ref_wav, _, _ = self._get_ref_paths(mood or "neutral")
            if ref_wav:
                _add_candidate(ref_wav, base_score=2_000_000)
        except Exception as e:
            if debug_enabled():
                log.debug(f"  MiMo clone 获取 GPT-SoVITS 参考失败 [debug]: {e}")

        # 再扫 GPT-Sovits 各情绪目录，避免只命中旧的 jp 样本
        try:
            ref_dir = getattr(self, "ref_dir", "") or project_path("GPT-Sovits")
            for sub in ("normal", "soft", "clam"):
                folder = os.path.join(ref_dir, sub)
                if not os.path.isdir(folder):
                    continue
                for name in os.listdir(folder):
                    if not name.lower().endswith(".wav") or "~" in name:
                        continue
                    _add_candidate(os.path.join(folder, name), base_score=1_000_000)
        except OSError:
            pass

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0]
        if best[0] < 0:
            log.warn(
                f"未找到 voice_lang={want} 的 clone 样本，"
                f"回退 {os.path.basename(best[1])} (lang={best[2]})"
            )
        else:
            log.info(
                f"clone 选用与 voice_lang={want} 一致: "
                f"{os.path.basename(best[1])} (lang={best[2]})"
            )
        return best[1]

    def _build_clone_voice_uri(self, ref_path: str) -> Optional[str]:
        """把参考音频编码为 data:{mime};base64,...（MiMo voiceclone 要求）"""
        import base64

        if not ref_path or not os.path.isfile(ref_path):
            return None

        ext = os.path.splitext(ref_path)[1].lower()
        mime = _MIMO_CLONE_MIME_BY_EXTENSION.get(ext)
        if not mime:
            log.warn(
                "clone 参考音频格式不支持；MiMo VoiceClone 仅支持 WAV/MP3"
            )
            return None

        uri_prefix = f"data:{mime};base64,"
        # 缓存：同一文件不重复读
        try:
            stat = os.stat(ref_path)
            cache_key = (
                f"{os.path.abspath(ref_path)}|{stat.st_mtime_ns}|{stat.st_size}"
            )
            if (
                getattr(self, "_mimo_clone_voice_uri", None)
                and getattr(self, "_mimo_clone_cache_key", None) == cache_key
            ):
                return self._mimo_clone_voice_uri
        except OSError:
            cache_key = ref_path

        try:
            raw_size = os.path.getsize(ref_path)
        except OSError as e:
            log.error(f"读取 clone 参考音频大小失败: {e}")
            return None

        # Base64 长度可由原始长度精确预计算；先拒绝超限文件，避免无谓读入内存。
        encoded_size = 4 * ((raw_size + 2) // 3)
        uri_size = len(uri_prefix) + encoded_size
        if uri_size > _MIMO_MAX_CLONE_VOICE_URI_BYTES:
            log.warn(
                f"clone 参考音频编码后过大 ({uri_size} bytes)，"
                f"上限为 {_MIMO_MAX_CLONE_VOICE_URI_BYTES} bytes"
            )
            return None

        try:
            with open(ref_path, "rb") as f:
                raw = f.read()
        except Exception as e:
            log.error(f"读取 clone 参考音频失败: {e}")
            return None

        b64 = base64.b64encode(raw).decode("ascii")
        uri = f"{uri_prefix}{b64}"
        # 文件可能在 stat 与读取之间变化，因此对最终请求值再次校验。
        if len(uri) > _MIMO_MAX_CLONE_VOICE_URI_BYTES:
            log.warn(
                f"clone 参考音频编码后过大 ({len(uri)} bytes)，"
                f"上限为 {_MIMO_MAX_CLONE_VOICE_URI_BYTES} bytes"
            )
            return None

        self._mimo_clone_voice_uri = uri
        self._mimo_clone_cache_key = cache_key
        log.info(
            f"  → clone 参考: {os.path.basename(ref_path)} "
            f"({len(raw)} bytes, {mime})"
        )
        return uri