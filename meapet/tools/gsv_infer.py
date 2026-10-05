"""
GPT-SoVITS 推理脚本 — 直接调用官方整合包的 TTS 流水线（TTS_infer_pack）
"""
import sys, os, json, time

_LOG_FILE = None

def log(msg):
    """写日志到文件，避免管道阻塞导致死锁"""
    global _LOG_FILE
    if _LOG_FILE is None:
        # 本脚本由 GSV 整合包自带的 Python 运行，那个解释器里没有 meapet 包，
        # 绝不能 import meapet.*（否则启动即 ImportError）。
        # audio_cache 相对脚本位置定位：meapet/tools/ 上两级即项目/便携数据根。
        try:
            root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            cache_dir = os.path.join(root, "audio_cache")
            os.makedirs(cache_dir, exist_ok=True)
            _LOG_FILE = open(
                os.path.join(cache_dir, "_gsv_infer.log"), "a", encoding="utf-8"
            )
        except OSError:
            _LOG_FILE = sys.stderr  # 目录不可写时只保留 stderr 输出
    ts = time.strftime("%H:%M:%S")
    _LOG_FILE.write(f"[{ts}] {msg}\n")
    _LOG_FILE.flush()
    # 同时也写到 stderr（有缓存风险但记录用）
    print(f"[gsv] {msg}", file=sys.stderr, flush=True)


def _has_gsv_tree(directory: str) -> bool:
    return bool(directory) and os.path.isdir(
        os.path.join(directory, "GPT_SoVITS", "TTS_infer_pack")
    )


def find_gsv_root(explicit: str, py_exe: str) -> str:
    """按标记认根，不认目录名。

    Windows 整合包是 ``<root>/runtime/python.exe``，POSIX venv 是 ``<root>/runtime/bin/python``，
    conda env 干脆在树外——解释器离根的层数不固定，而 ``GPT_SoVITS/TTS_infer_pack`` 三者共有。
    """
    if explicit:
        if _has_gsv_tree(explicit):
            return explicit
        log(f"配置给的 gsv_root 没有 GPT_SoVITS/TTS_infer_pack：{explicit}")
    current = os.path.dirname(os.path.abspath(py_exe)) if py_exe else ""
    for _ in range(5):
        if not current or _has_gsv_tree(current):
            break
        parent = os.path.dirname(current)
        if parent == current:
            current = ""
            break
        current = parent
    return current if _has_gsv_tree(current) else ""


def prefer_non_sox_backend(windows=None):
    """POSIX 上把 torchaudio 的 sox 后端摘掉——上游 TTS.py 调的是无参 torchaudio.load。

    ``libtorchaudio_sox.so`` 按上游 sox 14.4.2 的 ABI 编译，而 Arch 等发行版的
    ``libsox`` 实为 sox_ng 14.8：命中它是 SIGSEGV，不是可捕获的异常。后端表在
    import 期被 lru_cache 固化进 load/info/save 的闭包，所以摘掉一项就得用同模块
    的工厂把这三个函数重建一遍。只在 soundfile 也在场时动手——否则会把
    真 sox 14.4.2 的正常环境一起带走。
    """
    if (os.name == "nt") if windows is None else windows:
        return "windows-untouched"
    try:
        import torchaudio
        from torchaudio._backend import utils as _backend_utils

        backends = _backend_utils.get_available_backends()
    except Exception as exc:  # 版本形状不认识就别动它
        log(f"读不到 torchaudio 后端表，保持原样：{exc}")
        return "unknown-api"
    if "sox" not in backends:
        return "no-sox"
    if "soundfile" not in backends:
        log("torchaudio 没有 soundfile 后端，sox 留着不动")
        return "no-soundfile"
    backends.pop("sox")
    torchaudio.info = _backend_utils.get_info_func()
    torchaudio.load = _backend_utils.get_load_func()
    torchaudio.save = _backend_utils.get_save_func()
    return "sox-dropped"


def main():
    log("=== gsv_infer 启动 ===")
    log(f"sys.argv={sys.argv}")

    try:
        # 从 stdin 读取 payload（避免命令行参数编码问题）
        payload_line = sys.stdin.buffer.read().decode("utf-8").strip()
        log(f"payload_line len={len(payload_line)}")
        if not payload_line:
            _emit_json({"ok": False, "error": "No stdin payload"})
            return
        args = json.loads(payload_line)
        output_wav = args["output_wav"]
        py_exe = sys.executable
        log(f"python_exe={py_exe}")
        gsv_root = find_gsv_root(str(args.get("gsv_root") or ""), py_exe)

        if gsv_root:
            os.chdir(gsv_root)
            sys.path.insert(0, gsv_root)
            sys.path.insert(0, os.path.join(gsv_root, "GPT_SoVITS"))
            log(f"CWD -> {gsv_root}")
        else:
            log(f"未检测到 GSV root")
            _emit_json({"ok": False, "error": "GSV root not found"})
            return

        # Keep Hugging Face on its normal endpoint unless the user explicitly
        # selects a mirror. This subprocess inherits HF_ENDPOINT unchanged.
        if not os.environ.get("HF_ENDPOINT"):
            configured_endpoint = os.environ.get("MEA_PET_HF_ENDPOINT", "").strip()
            if configured_endpoint:
                os.environ["HF_ENDPOINT"] = configured_endpoint
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        t0 = time.time()
        log("import GPT_SoVITS.TTS_infer_pack.TTS …")
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        log(f"import ok ({time.time()-t0:.1f}s)")
        log(f"torchaudio 后端处置：{prefer_non_sox_backend()}")

        config_path = args.get("tts_config",
            os.path.join("GPT_SoVITS", "configs", "tts_infer.yaml"))
        log(f"config_path={config_path}")
        tts_config = TTS_Config(config_path)
        tts_config.t2s_weights_path = args["gpt_path"]
        tts_config.vits_weights_path = args["sovits_path"]
        tts_config.device = args.get("device", "cpu")
        tts_config.is_half = args.get("is_half", False)
        log("TTS_Config done")

        log("初始化 TTS 流水线…")
        tts_pipeline = TTS(tts_config)
        log(f"模型加载完成 ({time.time()-t0:.1f}s)")

        _lang_map = {
            "中文": "all_zh", "zh": "all_zh", "all_zh": "all_zh",
            "日文": "all_ja", "ja": "all_ja", "all_ja": "all_ja",
            "英文": "en", "en": "en",
            "粤语": "all_yue", "yue": "all_yue",
            "韩文": "all_ko", "ko": "all_ko",
            "auto": "auto",
        }
        text_lang = _lang_map.get(args.get("text_language", "auto"), "auto")
        prompt_lang = _lang_map.get(args.get("prompt_language", "auto"), "auto")

        _split_methods = {
            "不切": "cut0", "cut0": "cut0",
            "凑四句一切": "cut1", "cut1": "cut1",
            "凑50字一切": "cut2", "cut2": "cut2",
            "按中文句号。切": "cut3", "cut3": "cut3",
            "按英文句号.切": "cut4", "cut4": "cut4",
            "按标点符号切": "cut5", "cut5": "cut5",
        }
        text_split_method = _split_methods.get(args.get("text_split_method", "cut1"), "cut1")

        log("开始合成…")
        t1 = time.time()
        audio_chunks = []
        for result in tts_pipeline.run({
            "text": args["text"],
            "text_lang": text_lang,
            "ref_audio_path": args["ref_wav"],
            "prompt_text": args.get("prompt_text", ""),
            "prompt_lang": prompt_lang,
            "top_k": args.get("top_k", 15),
            "top_p": args.get("top_p", 0.8),
            "temperature": args.get("temperature", 0.6),
            "text_split_method": text_split_method,
            "speed_factor": args.get("speed", 1.0),
            "sample_steps": args.get("sample_steps", 8),
            "batch_size": 1,
            "batch_threshold": 0.75,
            "split_bucket": True,
            "return_fragment": False,
            "streaming_mode": False,
        }):
            chunk_sr, chunk_audio = result
            sr = chunk_sr
            audio_chunks.append(chunk_audio)
            log(f"收到音频片段: {len(chunk_audio)} samples")

        t2 = time.time()
        import numpy as np
        if len(audio_chunks) > 1:
            audio = np.concatenate(audio_chunks)
        elif len(audio_chunks) == 1:
            audio = audio_chunks[0]
        else:
            raise RuntimeError("TTS 未返回任何音频")

        log(f"合成完成 ({t2-t1:.1f}s, {len(audio)/sr:.1f}s 音频)")

        import soundfile as sf
        sf.write(output_wav, audio, sr)
        log(f"✓ 已保存: {output_wav}")

        _emit_json({"ok": True, "output_wav": output_wav,
                     "duration": round(len(audio)/sr, 2), "sample_rate": sr})

    except Exception as e:
        import traceback
        log(f"ERROR: {e}")
        log(traceback.format_exc())
        _emit_json({"ok": False, "error": str(e), "captured": ""})


def _emit_json(data):
    line = json.dumps(data, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
