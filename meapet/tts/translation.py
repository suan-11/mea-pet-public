"""基于 ``translators`` 的非 LLM 机器翻译服务。"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
from collections.abc import Callable, Iterable

from meapet.config.normalizers import canonical_tts_language
from meapet.log import get_color_logger
from meapet.tts.language_policy import voice_text_language_relation


log = get_color_logger("translation")

DEFAULT_TRANSLATION_PROVIDERS = (
    "alibaba",
    "iflytek",
    "sogou",
    "bing",
    "google",
)


def _provider_language(value: object, *, fallback: str) -> str:
    language = canonical_tts_language(value)
    if language == "jp":
        return "ja"
    if language in {"zh", "en"}:
        return language
    return language or fallback


class TranslationService:
    """在固定非 LLM 服务池内轮换，单次翻译最多发起三个请求。"""

    def __init__(
        self,
        *,
        translate_func: Callable[..., object] | None = None,
        providers: Iterable[str] = DEFAULT_TRANSLATION_PROVIDERS,
        max_attempts: int = 3,
    ) -> None:
        self.providers = tuple(
            str(provider or "").strip()
            for provider in providers
            if str(provider or "").strip()
        )
        self.max_attempts = max(1, min(int(max_attempts), 3))
        self._state_lock = threading.RLock()
        self._translate_func = translate_func if callable(translate_func) else None
        self._load_attempted = self._translate_func is not None
        self._dependency_present = (
            self._translate_func is not None or self._translation_package_present()
        )
        self._last_successful_provider = ""
        self._next_provider_index = 0

    @staticmethod
    def _translation_package_present() -> bool:
        """只检查模块规格，避免在 TTS 启动阶段执行第三方包代码。"""
        try:
            return importlib.util.find_spec("translators") is not None
        except (ImportError, ValueError):
            return False

    @staticmethod
    def _load_translate_func() -> Callable[..., object] | None:
        # translators 默认在 import 阶段访问 OneTrust 推断地区。固定地区可
        # 避免一次与实际翻译无关的网络请求；用户仍可显式覆盖为 EN。
        region = str(os.environ.get("translators_default_region") or "").upper()
        if region not in {"CN", "EN"}:
            os.environ["translators_default_region"] = "CN"
        try:
            import translators as translators_package
        except ImportError:
            log.error("翻译组件 translators 未安装；本轮无法进行机器翻译")
            return None
        except Exception as exc:
            log.warning(
                "[translate] 翻译组件加载失败；不影响 TTS 启动，"
                f"本轮跳过翻译 error={type(exc).__name__}"
            )
            return None
        translate_func = getattr(translators_package, "translate_text", None)
        if not callable(translate_func):
            log.error("翻译组件 translators 缺少 translate_text")
            return None
        return translate_func

    def _ensure_translate_func(self) -> Callable[..., object] | None:
        """首次真正需要翻译时才导入第三方后端，且隔离其全部异常。"""
        with self._state_lock:
            if self._translate_func is not None:
                return self._translate_func
            if self._load_attempted or not self._dependency_present:
                return None
            self._load_attempted = True
            try:
                loaded = self._load_translate_func()
            except Exception as exc:
                # 防止测试替身、第三方更新或 import hook 绕过加载器自身保护。
                log.warning(
                    "[translate] 翻译组件初始化异常；不影响 TTS，"
                    f"本轮跳过翻译 error={type(exc).__name__}"
                )
                loaded = None
            if callable(loaded):
                self._translate_func = loaded
            else:
                self._dependency_present = False
            return self._translate_func

    @property
    def available(self) -> bool:
        if not self.providers:
            return False
        with self._state_lock:
            return bool(
                self._translate_func is not None
                or (self._dependency_present and not self._load_attempted)
            )

    @property
    def last_successful_provider(self) -> str:
        with self._state_lock:
            return self._last_successful_provider

    def _attempt_order(self) -> tuple[tuple[int, str], ...]:
        if not self.providers:
            return ()
        with self._state_lock:
            if self._last_successful_provider in self.providers:
                start = self.providers.index(self._last_successful_provider)
            else:
                start = self._next_provider_index % len(self.providers)
        count = min(self.max_attempts, len(self.providers))
        return tuple(
            (
                (start + offset) % len(self.providers),
                self.providers[(start + offset) % len(self.providers)],
            )
            for offset in range(count)
        )

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        value = str(text or "").strip()
        target = canonical_tts_language(target_language)
        if not value or not target or not self.providers:
            return ""
        translate_func = self._ensure_translate_func()
        if translate_func is None:
            return ""

        source = _provider_language(source_language, fallback="auto")
        target_code = _provider_language(target, fallback=target)
        for index, provider in self._attempt_order():
            with self._state_lock:
                self._next_provider_index = (index + 1) % len(self.providers)
            try:
                result = translate_func(
                    value,
                    translator=provider,
                    from_language=source,
                    to_language=target_code,
                )
                translated = str(result or "").strip()
            except Exception as exc:
                log.warning(
                    f"[translate] provider={provider} failed={type(exc).__name__}"
                )
                continue

            relation = voice_text_language_relation(translated, target)
            if not translated or relation == "mismatch":
                log.warning(
                    f"[translate] provider={provider} invalid_result "
                    f"relation={relation} chars={len(translated)}\n{translated}"
                )
                continue

            with self._state_lock:
                self._last_successful_provider = provider
                self._next_provider_index = index
            log.info(
                f"[translate] provider={provider} source={source} target={target_code} "
                f"chars={len(translated)}\n{translated}"
            )
            return translated

        return ""

    async def translate_async(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        return await asyncio.to_thread(
            self.translate,
            text,
            source_language,
            target_language,
        )


__all__ = ["DEFAULT_TRANSLATION_PROVIDERS", "TranslationService"]
