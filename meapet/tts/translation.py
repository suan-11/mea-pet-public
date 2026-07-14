"""基于 ``translators`` 的非 LLM 机器翻译服务。"""

from __future__ import annotations

import asyncio
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
        self._translate_func = translate_func or self._load_translate_func()
        self._last_successful_provider = ""
        self._next_provider_index = 0
        self._state_lock = threading.RLock()

    @staticmethod
    def _load_translate_func() -> Callable[..., object] | None:
        try:
            import translators as translators_package
        except ImportError:
            log.error("翻译组件 translators 未安装；本轮无法进行机器翻译")
            return None
        return translators_package.translate_text

    @property
    def available(self) -> bool:
        return bool(self._translate_func and self.providers)

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
        if not value or not target or not self.available:
            return ""

        source = _provider_language(source_language, fallback="auto")
        target_code = _provider_language(target, fallback=target)
        for index, provider in self._attempt_order():
            with self._state_lock:
                self._next_provider_index = (index + 1) % len(self.providers)
            try:
                result = self._translate_func(
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
