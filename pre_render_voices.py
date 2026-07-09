"""
预合成摸头等固定语音到 voice_cache/
运行一次即可：python pre_render_voices.py
"""
import json, os, sys

# 确保 stdio 不被 GUI 环境影响
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

from tts import MeaTTS
tts = MeaTTS(cfg)

if not tts.health_check():
    print("❌ GPT-SoVITS model not found!")
    sys.exit(1)

# 所有需要预合成的固定文本 (text, mood)
fixed_items = [
    # 摸头反应
    ("哼，别随便碰我……",   "annoyed"),
    ("……你干嘛",           "annoyed"),
    ("喵~",                "happy"),
    ("烦死了",              "annoyed"),
    ("瞪",                 "annoyed"),
    ("别摸了……",           "annoyed"),
]

print(f"🎤 Pre-rendering {len(fixed_items)} phrases...")
results = tts.pre_render_batch(fixed_items)

print(f"\n✅ Done! {len(results)}/{len(fixed_items)} cached:")
for text, path in results.items():
    size_kb = os.path.getsize(path) / 1024
    print(f"  [{size_kb:.0f}KB] {text!r}")
