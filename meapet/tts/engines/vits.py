"""TTS engine mixin (extracted from tts.py)."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from meapet.paths import project_path, project_root
from meapet.log import get_color_logger
from meapet.tts.common import (
    DEFAULT_VITS_CONFIG_NAME,
    DEFAULT_VITS_MODEL_NAME,
    DEFAULT_VITS_SPEAKER,
    VitsRoute,
    hidden_subprocess_kwargs,
    resolve_external_python,
    resolve_vits_route,
)

log = get_color_logger("tts")


def vits_frontend_text(text: str, language: Optional[str]) -> str:
    """按整句语言给 VITS 前端套语言标记。

    ``cjke_cleaners2`` 只音素化**包在标记里**的文本，裸中日韩文会被逐字符丢掉
    （vits_core/text/cleaners.py:88-99）。中文有自己那一支 ``[ZH]`` →
    ``chinese_to_ipa``（拼音），而 ``[JA]`` 那一支对汉字只给音读——人工
    2026-10-12 听过后点了「[ZH] 能听」，中文才换标记。

    整句只选一支，不逐 run 分块：实测 ``[ZH]打开[ZH] [EN]TCP[EN] [ZH]端口[ZH]``
    清出 31 音素却只剩 30（大写缩写被符号表丢），而中文串里外扩英文进 ``[ZH]``
    是 37→37 零丢；更关键的是日文句子里的汉字本来就多，按 run 标 ``[ZH]``
    会把音读念碎。所以判据取"这句是什么语言"，不取"这段是什么字符"。
    """
    marker = "ZH" if str(language or "").lower().startswith("zh") else "JA"
    return f"[{marker}]{text}[{marker}]"


class TtsVitsMixin:
    def _vits_external_python(self) -> str:
        return resolve_external_python(
            getattr(self, "_vits_python", None)
            or getattr(self, "python_exe", None)
        )

    def _vits_pack_dir(self) -> str:
        """外置运行时包目录（P0-2）：配置了且在盘上才算数。"""
        raw = str(getattr(self, "_vits_pack_dir", "") or "").strip()
        return raw if raw and os.path.isdir(raw) else ""

    def _vits_script_path(self) -> str:
        """推理脚本落点：包内镜像副本优先（P0-2）。

        包内 vits_infer.py 的 _bootstrap 按 parents[2] 解析 base=包根，
        vits_core/权重/dic 全用包内的；_internal 那份只作源码态/full 档兜底。
        """
        pack = TtsVitsMixin._vits_pack_dir(self)
        if pack:
            pack_script = os.path.join(pack, "meapet", "tools", "vits_infer.py")
            if os.path.isfile(pack_script):
                return pack_script
        return project_path("meapet", "tools", "vits_infer.py")

    def _vits_route(self) -> VitsRoute:
        """本实例的 VITS 选路结果。

        健康检查与 speak 都取这一处，两处口径不可能再分叉。宿主若自己实现了
        ``_vits_route``（MeaTTS 就是），用宿主的；否则按同类口径就地判定。
        """
        host_route = getattr(self, "_vits_route_decision", None)
        if callable(host_route):
            return host_route()

        external_py = TtsVitsMixin._vits_external_python(self)
        inprocess_pref = getattr(self, "_vits_inprocess", None)
        if inprocess_pref is not None:
            inprocess_pref = bool(inprocess_pref)
        return resolve_vits_route(
            external_python=external_py,
            inprocess_pref=inprocess_pref,
            fallback_python=getattr(self, "python_exe", None),
        )

    def _vits_knobs(self) -> tuple[str, str, str]:
        """模型 / 配置 / 说话人 —— 两条路共用的取值口径。

        三处 ``getattr`` 以前只有进程内那条路在用，于是"健康检查验 A、
        一条路合成 B"是默认状态；现在两路都从这里取，并且都透传下去。
        """
        pack = TtsVitsMixin._vits_pack_dir(self)
        default_model = (
            os.path.join(pack, "vits_models", DEFAULT_VITS_MODEL_NAME)
            if pack else project_path("vits_models", DEFAULT_VITS_MODEL_NAME)
        )
        default_config = (
            os.path.join(pack, "vits_models", DEFAULT_VITS_CONFIG_NAME)
            if pack else project_path("vits_models", DEFAULT_VITS_CONFIG_NAME)
        )
        model = getattr(self, "_vits_model", None) or default_model
        config = getattr(self, "_vits_config", None) or default_config
        speaker = (
            getattr(self, "_vits_speaker", None) or DEFAULT_VITS_SPEAKER
        )
        return str(model), str(config), str(speaker)

    @staticmethod
    def _vits_extra_args(argv: list[str], values: list[tuple[str, object]]) -> None:
        """按 ``vits_infer.py --help`` 逐条把非空旋钮追加进 argv。

        不能再现"引擎以为传了、脚本其实没这个参数"的静默失败，所以这里只
        追加用户真的配了的值：脚本自带同名默认，不传就是走默认。
        """
        for flag, value in values:
            if value is None or value == "":
                continue
            argv.extend([flag, str(value)])

    def _speak_vits(
        self, tts_text: str, output_wav: str, language: str = "jp"
    ) -> Optional[tuple[str, str]]:
        """VITS backend inference.

        Prefer a configured external Python + ``vits_infer.py`` when available
        (most reliable on Windows frozen builds). Fall back to in-process torch
        when no external interpreter is configured, or when the subprocess path
        is unavailable.

        ``language`` 只决定前端标记（``vits_frontend_text``），不改变合成文本本身：
        VITS 那一路不做语言校验，句子的语言由选路阶段带下来。

        The route is decided once, in ``resolve_vits_route``. An explicit
        ``tts.vits_inprocess: true`` does **not** outrank a real external
        interpreter, but that override is reported instead of being swallowed.
        """
        # 内部辅助一律经类限定调用：保证以鸭子类型宿主（仅有配置属性、
        # 不继承本 mixin）非绑定调用 _speak_vits 时同样可用（测试契约）。
        route = TtsVitsMixin._vits_route(self)
        if route.ignored_inprocess_pref:
            log.warning(
                "VITS: vits_inprocess=true 被外部解释器优先级覆盖，实际走子进程 "
                "(python=%s)。这条优先级是有意的（打包版自带 torch 可能加载不了）；"
                "要去掉它请清空 tts.vits_python。",
                os.path.basename(route.external_python),
            )
        log.info("VITS route: %s", route.describe())

        external_py = route.external_python
        if not route.inprocess and not external_py:
            # 既没有外部解释器、又不走进程内 —— 打包版下本进程解释器就是 pet
            # exe，拿它跑 vits_infer.py 只会再开一个桌宠实例。
            log.error(
                "VITS: 没有可用的外部解释器，且进程内推理被显式关闭"
                "（tts.vits_inprocess=false）。请配置 tts.vits_python，"
                "或删掉 tts.vits_inprocess 让打包版回落到进程内。"
            )
            return None, ""

        if external_py:
            # 外部解释器可用：先子进程，失败再退进程内（打包版自带 torch 可能
            # 加载不了，这条兜底是原有行为）。
            result = TtsVitsMixin._speak_vits_subprocess(
                self, tts_text, output_wav, external_py, language=language
            )
            if result[0]:
                return result
            log.warning(
                "VITS subprocess failed; trying in-process torch as fallback"
            )
            return TtsVitsMixin._speak_vits_inprocess(
                self, tts_text, output_wav, language=language
            )

        # 进程内单路：没有可退的外部解释器（上面已挡掉"两路都没有"）。
        return TtsVitsMixin._speak_vits_inprocess(
            self, tts_text, output_wav, language=language
        )

    def _vits_get_serve_worker(self, python_exe, script, model, config):
        """P0-4：按 (解释器, 脚本, 模型, 配置) 缓存常驻 worker。"""
        try:
            from meapet.tts.engines.vits_worker import VitsServeWorker

            cache = getattr(self, "_vits_serve_workers", None)
            if cache is None:
                cache = {}
                self._vits_serve_workers = cache
            key = (python_exe, script, model, config)
            worker = cache.get(key)
            if worker is None:
                worker = VitsServeWorker(python_exe, script, model=model, config=config)
                cache[key] = worker
            return worker
        except Exception as exc:
            log.warning(
                "VITS serve worker 初始化失败: %s: %s", type(exc).__name__, exc
            )
            return None

    def _speak_vits_subprocess(
        self,
        tts_text: str,
        output_wav: str,
        vits_python: str,
        knobs: Optional[tuple[str, str, str]] = None,
        language: str = "jp",
    ) -> Optional[tuple[str, str]]:
        log.info("VITS inference (subprocess)...")
        t1 = time.time()
        vits_script = TtsVitsMixin._vits_script_path(self)
        if not os.path.isfile(vits_script):
            log.error(f"VITS script missing: {vits_script}")
            return None, ""

        model_path, config_path, speaker = (
            knobs if knobs is not None else TtsVitsMixin._vits_knobs(self)
        )

        # P0-4：常驻热驻留优先——句级流水线下每句都冷启动 torch 会把首音
        # 延迟反而恶化（10s+ 税）。serve 失败响亮降级回一次性子进程。
        if bool(getattr(self, "_vits_serve", True)):
            serve_worker = TtsVitsMixin._vits_get_serve_worker(
                self, vits_python, vits_script, model_path, config_path
            )
            if serve_worker is not None:
                ok = serve_worker.synthesize(
                    vits_frontend_text(tts_text, language),
                    output_wav,
                    speaker=speaker,
                )
                if ok:
                    log.info(
                        "VITS output: %s (%.1fs, serve)",
                        os.path.basename(output_wav), time.time() - t1,
                    )
                    return output_wav, language
                log.warning("VITS serve 不可用，回落一次性子进程")
        argv = [
            vits_python,
            vits_script,
            "--text",
            vits_frontend_text(tts_text, language),
            "--output",
            output_wav,
            "--noise_scale",
            "0.667",
            "--noise_scale_w",
            "0.6",
            "--length_scale",
            "1.0",
        ]
        # 旋钮透传：argv 里以前没有 --model/--config/--speaker，脚本只能回落
        # 硬编码默认值，与进程内那条路的口径不同。
        TtsVitsMixin._vits_extra_args(
            argv,
            [
                ("--model", model_path),
                ("--config", config_path),
                ("--speaker", speaker),
            ],
        )

        try:
            env = os.environ.copy()
            # Windows consoles often default to GBK; vits_core prints phonemes
            # that include IPA symbols and crash with UnicodeEncodeError.
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=project_root(),
                env=env,
                **hidden_subprocess_kwargs(),
            )
            elapsed = time.time() - t1
            log.info(f"VITS 返回 (rc={proc.returncode}, {elapsed:.1f}s)")

            if proc.returncode != 0:
                log.warning(
                    f"VITS failed: rc={proc.returncode} stderr_chars={len(proc.stderr or '')}"
                )
                log.trace(
                    lambda: f"VITS stderr [debug]: {(proc.stderr or '')[-400:]}"
                )
                if proc.stderr:
                    log.warning(f"VITS stderr tail: {(proc.stderr or '')[-300:]}")
                return None, ""

            if not os.path.exists(output_wav):
                log.warning("VITS: 输出文件不存在")
                return None, ""

            log.info(f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s)")
            return output_wav, language
        except Exception as e:
            log.error(f"VITS exception: {type(e).__name__}: {e}")
            return None, ""

    def _speak_vits_inprocess(
        self, tts_text: str, output_wav: str, language: str = "jp"
    ) -> Optional[tuple[str, str]]:
        log.info("VITS inference (in-process)...")
        t1 = time.time()
        try:
            from meapet.tts.engines.vits_runtime import synthesize_vits

            model_path, config_path, speaker = TtsVitsMixin._vits_knobs(self)
            synthesize_vits(
                vits_frontend_text(tts_text, language),
                output_wav,
                model_path=model_path,
                config_path=config_path,
                speaker=speaker,
            )
            elapsed = time.time() - t1
            if not os.path.exists(output_wav):
                log.warning("VITS in-process: 输出文件不存在")
                return None, ""
            log.info(
                f"VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s, in-process)"
            )
            return output_wav, language
        except Exception as e:
            log.error(f"VITS in-process exception: {type(e).__name__}: {e}")
            return None, ""
