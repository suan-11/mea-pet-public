"""
GPT-SoVITS 推理脚本 — 直接调用官方整合包的 TTS 流水线（TTS_infer_pack）
"""
import sys, os, json, time

_LOG_FILE = None

def log(msg):
    """写日志到文件，避免管道阻塞导致死锁"""
    global _LOG_FILE
    if _LOG_FILE is None:
        _LOG_FILE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "audio_cache", "_gsv_infer.log"), "a", encoding="utf-8")
    ts = time.strftime("%H:%M:%S")
    _LOG_FILE.write(f"[{ts}] {msg}\n")
    _LOG_FILE.flush()
    # 同时也写到 stderr（有缓存风险但记录用）
    print(f"[gsv] {msg}", file=sys.stderr, flush=True)


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
        gsv_root = None
        py_exe = sys.executable
        log(f"python_exe={py_exe}")
        if py_exe:
            d = os.path.dirname(py_exe)
            if os.path.basename(d).lower() == "runtime":
                gsv_root = os.path.dirname(d)

        if gsv_root and os.path.isdir(gsv_root):
            os.chdir(gsv_root)
            sys.path.insert(0, gsv_root)
            sys.path.insert(0, os.path.join(gsv_root, "GPT_SoVITS"))
            log(f"CWD -> {gsv_root}")
        else:
            log(f"未检测到 GSV root")
            _emit_json({"ok": False, "error": "GSV root not found"})
            return

        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        t0 = time.time()
        log("import GPT_SoVITS.TTS_infer_pack.TTS …")
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        log(f"import ok ({time.time()-t0:.1f}s)")

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
