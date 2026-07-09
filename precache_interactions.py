"""
预生成互动语音缓存（日语）
运行一次后，摸头/摸尾巴等交互的语音秒出
"""
import os, sys, json, time

PET_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PET_DIR, "voice_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 需要缓存的固定文本
TEXTS = [
    "……别摸我头发。",
    "……有事吗？",
    "哼。",
    "别摸了……",
    "尾巴……不许碰喵！！",
    "……你想死一次吗？",
    "变态。",
    "……尾巴是很敏感的不知道吗。",
]

# 日语翻译（机翻）
JP_MAP = {
    "……别摸我头发。": "……髪を触らないで。",
    "……有事吗？": "……何か用？",
    "哼。": "ふん。",
    "别摸了……": "もう触らないで……",
    "尾巴……不许碰喵！！": "しっぽ……触るなニャ！！",
    "……你想死一次吗？": "……一回死んでみる？",
    "变态。": "変態。",
    "……尾巴是很敏感的不知道吗。": "……しっぽは敏感なんだ、知らないのか。",
}

def make_cache_filename(text, lang):
    """生成缓存文件名，去除特殊字符"""
    safe = text.replace("……", "").replace("（", "").replace("）", "").replace(" ", "_").strip()
    return f"{lang}_{safe}.wav"

def generate_for_text(tts, text, lang):
    """生成一条语音缓存"""
    cache_path = os.path.join(CACHE_DIR, make_cache_filename(text, lang))
    if os.path.exists(cache_path):
        print(f"  [SKIP] {lang}: {text} (已有缓存)")
        return cache_path
    
    speak_text = JP_MAP.get(text, text)

    print(f"  [GEN] {lang}: {text} → {speak_text}")
    try:
        result = tts.speak(speak_text, mood="neutral")
        wav = result[0] if result else None
        if wav and os.path.exists(wav):
            import shutil
            shutil.copy2(wav, cache_path)
            print(f"    OK: {cache_path}")
            return cache_path
        else:
            print(f"    FAIL: TTS 返回空 (result={result})")
            return None
    except Exception as e:
        print(f"    ERROR: {e}")
        return None

def main():
    with open(os.path.join(PET_DIR, "config.json"), "r", encoding="utf-8") as f:
        config = json.load(f)

    print(f"生成日语语音缓存…\n")

    from tts import MeaTTS
    tts = MeaTTS(config)
    if not tts.enabled:
        print("TTS 未启用，跳过")
        return

    for text in TEXTS:
        generate_for_text(tts, text, "jp")
        time.sleep(0.5)

    print(f"\n完成！共 {len(TEXTS)} 条缓存 → {CACHE_DIR}")

if __name__ == "__main__":
    main()
