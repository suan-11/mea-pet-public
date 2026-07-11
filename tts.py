"""
梅尔桌宠 - TTS 语音合成模块
通过子进程调用 GPT-SoVITS v2pro runtime Python 进行端到端合成
参考音频按情感分类：soft / normal / clam
"""
import os
import sys
import time
import json
import shutil
import subprocess
import importlib
import unicodedata
from typing import Optional


def _safe_print(*args, **kwargs):
    """GUI 环境下安全打印 —— 写入日志文件 + stderr（终端可见）"""
    now = time.strftime("%H:%M:%S")
    try:
        msg = ' '.join(str(a) for a in args)
        # 日志文件
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tts_debug.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'[{now}] {msg}\n')
        # 同时输出到 stderr（终端/ Hermes 可见）
        print(f'[TTS][{now}] {msg}', file=sys.stderr, flush=True)
    except Exception:
        pass


# ═══════════════════════════════════════════
# GSV 子进程依赖自动安装
# ═══════════════════════════════════════════

# pip 包名 → Python import 名映射（两者不一致时）
GSV_MODULE_MAP = {
    "PyYAML": "yaml",
    "split-lang": "split_lang",
    "jieba_fast": "jieba_fast",
}


def _get_import_name(pkg_name: str) -> str:
    """从 pip 包名推导 Python import 名"""
    base = pkg_name.split(">")[0].split("<")[0].split("=")[0].strip()
    return GSV_MODULE_MAP.get(base, base.replace("-", "_"))


# GPT-SoVITS-CPUFast 推理所需的基础 Python 包
GSV_REQUIRED_PACKAGES = [
    "torch",
    "torchaudio",
    "soundfile",
    "numpy<2.0",
    "einops",
    "PyYAML",
    "tqdm",
    "pypinyin",
    "av",
    "fast_langdetect>=0.3.1",
    "split-lang",
    "wordsegment",
    "tokenizers",
    "transformers",
    "gradio",
    "pydantic<=2.10.6",
    "jieba",
]

# 可选包（部分可降级/跳过）
GSV_OPTIONAL_PACKAGES = [
    "jieba_fast",        # 有 C++ 编译要求，装不上用 jieba + 垫片
    "pyopenjtalk>=0.4.1", # 日语文本前端
    "g2p_en",            # 英文音素
    "g2pk2",             # 韩语音素
    "ko_pron",           # 韩语处理
    "ToJyutping",        # 粤语拼音
]

GSV_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"


def _has_module(py_exe: str, module_name: str) -> bool:
    """检查指定 Python 能否 import 某模块"""
    try:
        r = subprocess.run(
            [py_exe, "-c", f"import {module_name}; print('ok')"],
            capture_output=True, text=True, timeout=15
        )
        return r.returncode == 0 and 'ok' in r.stdout
    except Exception:
        return False


def _install_modules(py_exe: str, packages: list[str],
                     extra_index: str = None) -> bool:
    """pip install 包列表到指定 Python，返回是否全部成功"""
    cmd = [py_exe, "-m", "pip", "install", "--timeout", "120"]
    if extra_index:
        # 有专用 index（如 PyTorch）时用它做主源，清华做备用
        cmd.extend(["--index-url", extra_index])
        cmd.extend(["--extra-index-url", GSV_PIP_INDEX])
    else:
        cmd.extend(["-i", GSV_PIP_INDEX])
    cmd.extend(packages)
    try:
        _safe_print(f"  pip install {len(packages)} 个包 …")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0:
            _safe_print(f"  pip 失败: {r.stderr[-200:]}")
            return False
        _safe_print(f"  pip 成功")
        return True
    except Exception as e:
        _safe_print(f"  pip 异常: {e}")
        return False


def auto_install_gsv_deps(py_exe: str) -> bool:
    """检查并一键安装所有依赖到指定 Python"""
    _safe_print(f"  → 检查 GSV 依赖 (Python: {py_exe})")

    # 快速检查缺了哪些
    missing = []
    for pkg in GSV_REQUIRED_PACKAGES:
        mod = _get_import_name(pkg)
        if not _has_module(py_exe, mod):
            missing.append(pkg)

    if not missing:
        _safe_print(f"  ✓ 所有 GSV 依赖已安装")
        return True

    _safe_print(f"  ⚠ 缺少 {len(missing)} 个依赖，一次性安装 …")
    # 拆成两批：torch 系用 PyTorch 官方源，其余用清华源
    torch_pkgs = [p for p in missing if p in ("torch", "torchaudio")]
    other_pkgs = [p for p in missing if p not in ("torch", "torchaudio")]

    ok = True
    if torch_pkgs:
        ok = _install_modules(py_exe, torch_pkgs, extra_index=TORCH_INDEX_URL) and ok
    if other_pkgs:
        ok = _install_modules(py_exe, other_pkgs) and ok

    if not ok:
        _safe_print(f"  ❌ pip 安装失败，请检查网络或手动安装")
        return False

    # 最终验证
    still = [p for p in GSV_REQUIRED_PACKAGES
             if not _has_module(py_exe, _get_import_name(p))]
    if still:
        _safe_print(f"  ⚠ 仍有 {len(still)} 个包未装: {still}")
        return False

    _safe_print(f"  ✓ 所有依赖安装完成")
    return True


# ========================
# 情感 → 参考音频映射
# ========================
MOOD_TO_REF = {
    # 平静/正面 → normal
    "neutral":      "normal",
    "happy":        "normal",
    "curious":      "normal",
    "surprised":    "normal",
    "talking":      "normal",
    "intrigued":    "normal",
    # 悲伤/忧郁/害羞 → soft
    "sad":          "soft",
    "melancholy":   "soft",
    "shy":          "soft",
    "embarrassed":  "soft",
    "teary":        "soft",
    "wistful":      "soft",
    # 恼怒 → clam
    "annoyed":      "clam",
}

# 语言常量（始终使用日语合成）
LANG_TTS = "日文"


class MeaTTS:
    """梅尔语音合成：通过子进程调用 GPT-SoVITS v2pro"""

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        tts_cfg = cfg.get("tts", {})

        self.enabled = tts_cfg.get("enabled", True)

        # ═══ GPT-SoVITS runtime Python 路径 ═══
        # 优先使用配置文件或环境变量；若均未设置，自动检测常见安装路径
        self.python_exe = tts_cfg.get("python_exe", "") or \
            os.environ.get("GSV_PYTHON", "")

        if not self.python_exe or not os.path.isfile(self.python_exe):
            # 自动检测常见安装路径（引导安装解压的 GPT-SoVITS 整合包）
            _home = os.path.expanduser("~")
            _candidates = [
                # GPT-SoVITS v2pro 整合包（解压后常见位置）
                r"D:\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\runtime\python.exe",
                r"D:\GPT-SoVITS-v2pro\GPT-SoVITS-v2pro\runtime\python.exe",
                r"C:\Program Files\GPT-SoVITS\runtime\python.exe",
                # 用户目录下的解压版
                os.path.join(_home, "GPT-SoVITS-v2pro", "runtime", "python.exe"),
                # 当前脚本的解释器（GUI 同进程）
                sys.executable,
            ]
            # 扫描常见 conda 环境（setup_wizard 创建或手动安装的）
            for _conda_root in (_home, r"C:\ProgramData", r"C:\Users"):
                for _maybe in (
                    os.path.join(_conda_root, "miniconda3", "envs", "GPTSoVits", "python.exe"),
                    os.path.join(_conda_root, "miniconda3", "python.exe"),
                    os.path.join(_conda_root, "anaconda3", "envs", "GPTSoVits", "python.exe"),
                    os.path.join(_conda_root, "anaconda3", "python.exe"),
                ):
                    _candidates.append(_maybe)
            # 去重 + 按存在过滤
            _seen = set()
            for _p in _candidates:
                _rp = os.path.realpath(_p) if os.path.isfile(_p) else None
                if _rp and _rp not in _seen:
                    _seen.add(_rp)
                    self.python_exe = _p
                    _safe_print(f"  → 检测到 GPT-SoVITS: {_p}")
                    break
            if not self.python_exe:
                self.python_exe = sys.executable
                _safe_print(f"  ⚠ 未找到 GPT-SoVITS，降级至当前解释器: {self.python_exe}")

        # 验证 python_exe 是否存在
        if not os.path.isfile(self.python_exe):
            _safe_print(f"  ⚠ python_exe 不存在: {self.python_exe}")
            self.python_exe = sys.executable

        # 推理脚本路径
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.infer_script = tts_cfg.get(
            "infer_script",
            os.path.join(base_dir, "gsv_infer.py")
        )

        # 模型路径（从配置读取，无默认硬编码路径）
        self.gpt_weights_dir = tts_cfg.get("gpt_weights_dir", "./models/GPT_weights")
        self.sovits_weights_dir = tts_cfg.get("sovits_weights_dir", "./models/SoVITS_weights")

        # 具体模型文件
        self.gpt_model = tts_cfg.get("gpt_model", "mea_pro-e50.ckpt")
        self.sovits_model = tts_cfg.get("sovits_model", "mea_pro_e24_s13704.pth")
        # 模型路径转绝对（子进程会切换目录，相对路径会失效）
        gpt_dir = tts_cfg.get("gpt_weights_dir", "./models/GPT_weights")
        sv_dir = tts_cfg.get("sovits_weights_dir", "./models/SoVITS_weights")
        self.gpt_path = os.path.normpath(os.path.join(base_dir, gpt_dir, self.gpt_model))
        self.sovits_path = os.path.normpath(os.path.join(base_dir, sv_dir, self.sovits_model))

        # 参考音频目录（转绝对路径）
        ref_dir_raw = tts_cfg.get("ref_dir", "GPT-Sovits")
        self.ref_dir = os.path.normpath(ref_dir_raw if os.path.isabs(ref_dir_raw) else os.path.join(base_dir, ref_dir_raw))

        # 合成参数（平衡稳定性和完整性）
        # top_k/top_p/temperature 太低会导致 GPT 提前截断（只输出语气词）
        # 太高会导致乱说/重复，在两者间取平衡
        self.top_k = tts_cfg.get("top_k", 15)
        self.top_p = tts_cfg.get("top_p", 0.8)
        self.temperature = tts_cfg.get("temperature", 0.6)
        self.repetition_penalty = tts_cfg.get("repetition_penalty", 1.35)
        self.speed = tts_cfg.get("speed", 1.0)
        self.sample_steps = tts_cfg.get("sample_steps", 8)

        # 输出目录
        self.output_dir = tts_cfg.get(
            "output_dir",
            os.path.join(base_dir, "audio_cache")
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # 子进程超时（秒）
        self.timeout = tts_cfg.get("timeout", 60)

        # 翻译配置（中文 → 日语，使用翻译 API）
        self.translate_enabled = tts_cfg.get("translate_to_jp", True)
        self.translate_api_key = tts_cfg.get("translate_api_key", "")
        self.translate_model = tts_cfg.get("translate_model", "deepseek-chat")

        # ═══ VITS 后端配置 ═══
        engine = tts_cfg.get("engine", "gpt_sovits")
        self._vits_mode = engine == "vits" or tts_cfg.get("vits_mode", False)
        self._vits_python = tts_cfg.get("vits_python", "") or self.python_exe

        # 自检依赖（首次自动安装）
        self._deps_ready = False
        self._deps_attempted = False

        _safe_print(
            f"🎤 MeaTTS v2 (subprocess) | "
            f"python={os.path.basename(self.python_exe)} | "
            f"GPT={self.gpt_model} | SoVITS={self.sovits_model} | "
            f"top_k={self.top_k} top_p={self.top_p} temp={self.temperature}"
        )

    def health_check(self) -> bool:
        """检查关键文件是否存在，并确保依赖已安装"""
        gpt_ok = os.path.exists(self.gpt_path)
        s2_ok = os.path.exists(self.sovits_path)
        python_ok = os.path.exists(self.python_exe)
        script_ok = os.path.exists(self.infer_script)
        ref_ok = all(
            os.path.exists(os.path.join(self.ref_dir, t))
            for t in ["normal", "soft", "clam"]
        )
        _safe_print(
            f"Health: python={python_ok} script={script_ok} "
            f"GPT={gpt_ok} SoVITS={s2_ok} Refs={ref_ok}"
        )
        all_ok = all([python_ok, script_ok, gpt_ok, s2_ok, ref_ok])

        # 自动安装缺失的 GSV 依赖（仅在首次 speak 前尝试一次）
        if all_ok and not self._deps_attempted:
            self._deps_attempted = True
            if auto_install_gsv_deps(self.python_exe):
                self._deps_ready = True
            else:
                _safe_print("  ⚠ GSV 依赖安装不完全，TTS 可能失败")

        return all_ok

    def _ensure_deps(self):
        """speak 前确保依赖就绪（self._deps_ready 由 health_check 或显式调用设置）"""
        if self._deps_ready:
            return True
        # VITS 模式不需要 GSV 依赖
        if self._vits_mode:
            self._deps_ready = True
            return True
        if not self._deps_attempted:
            self._deps_attempted = True
            if auto_install_gsv_deps(self.python_exe):
                self._deps_ready = True
                return True
            _safe_print("  ⚠ GSV 依赖未就绪")
        return False

    # 日语后处理：替换不常见/粗俗词为 GPT-SoVITS 模型更友好的表达
    JP_CLEAN_MAP = {
        "クソ": "だめ",
        "糞": "ごみ",
        "死ね": "やめて",
        "うざい": "いや",
        "うるせえ": "うるさい",
        "ダセえ": "ださい",
        "ムカつく": "いらいらする",
    }

    def _translate_to_jp(self, text: str) -> str:
        """将中文翻译成日语：依次尝试多个免费翻译源，最后回退 DeepSeek API"""
        # 1) 本地翻译库（依次尝试多个源）
        try:
            import translators as ts
        except ImportError:
            _safe_print(f"  translate: 安装 translators …")
            import subprocess, sys
            subprocess.run([sys.executable, "-m", "pip", "install", "translators",
                          "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
                          "--trusted-host", "pypi.tuna.tsinghua.edu.cn", "-q"],
                          capture_output=True, timeout=120)
            try:
                import translators as ts
            except ImportError:
                _safe_print(f"  translate: translators 安装失败")
                ts = None

        if ts is not None:
            # 国内可用翻译源（按成功率排序）
            for svc in ("alibaba", "iflytek", "sogou", "bing", "google"):
                try:
                    jp = ts.translate_text(text, translator=svc,
                                           from_language='zh', to_language='ja')
                    if jp and len(jp) >= 2:
                        jp = self._clean_jp(jp)
                        _safe_print(f"  translate ({svc}): {jp[:80]}")
                        return jp
                except Exception as e:
                    _safe_print(f"  translate ({svc}) failed: {e}")
                    continue

        # 2) 回退：DeepSeek API
        if not self.translate_api_key:
            _safe_print(f"  translate: no API key, local also failed")
            return ""
        import urllib.request
        prompt = (
            "Translate this Chinese cat-girl line to natural Japanese. "
            "End sentences with 'nya'. Output ONLY the translation.\n\n"
            f"Chinese: {text}\n"
            "Japanese:"
        )
        payload = json.dumps({
            "model": self.translate_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 128,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.translate_api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                jp = result["choices"][0]["message"]["content"].strip()
                jp = jp.replace("\n", "").strip()
                jp = self._clean_jp(jp)
                if jp and len(jp) >= 2:
                    _safe_print(f"  translate (api): {jp[:80]}")
                    return jp
        except Exception as e:
            _safe_print(f"  translate (api) failed: {e}")
        return ""

    def _clean_jp(self, text: str) -> str:
        """后处理日语文本，替换模型训练数据中不常见的粗俗/敏感词"""
        import re
        for bad, good in self.JP_CLEAN_MAP.items():
            text = text.replace(bad, good)
        # 压缩连续重复的 にゃ/ニャ（娇喘数据会导致模型哼唧而非清晰发音）
        text = re.sub(r'(にゃ){2,}', 'にゃ', text)
        text = re.sub(r'(ニャ){2,}', 'ニャ', text)
        return text

    def _get_ref_paths(self, mood: str) -> tuple:
        """
        根据情绪获取 (参考音频路径, 参考文本, 语言)
        返回 (wav_path, ref_text, lang) 或 (None, None, None)
        根据 voice_lang 选择对应语言的参考文件（zh_* / jp_*）
        """
        ref_type = MOOD_TO_REF.get(mood, "normal")
        ref_folder = os.path.join(self.ref_dir, ref_type)
        wav_file = None
        txt_file = None
        try:
            for f in os.listdir(ref_folder):
                if f.startswith('jp_') and f.lower().endswith('.wav') and '~' not in f:
                    wav_file = os.path.join(ref_folder, f)
                elif f.startswith('jp_') and f.lower().endswith('.txt'):
                    txt_file = os.path.join(ref_folder, f)
        except FileNotFoundError:
            _safe_print(f"Ref folder not found: {ref_folder}")
            return None, None, None

        if not wav_file or not txt_file:
            _safe_print(f"No ref audio for mood={mood} type={ref_type}")
            return None, None, None

        # 读取参考文本
        with open(txt_file, 'r', encoding='utf-8') as f:
            ref_text = f.read().strip()

        # 检测语言：有假名=日文，否则=中文
        # （日文汉字也在CJK范围内，为避免误判，优先检测假名）
        lang = LANG_TTS

        return wav_file, ref_text, lang

    def speak(self, text: str, mood: str = "neutral") -> Optional[tuple[str, str]]:
        """
        文字 → 梅尔语音（始终合成日语），返回 (wav_path, lang)
        """
        if not self.enabled:
            return None

        # 【新增】检查 GPT-SoVITS 模型文件是否存在
        if not os.path.exists(self.gpt_path):
            _safe_print(f"❌ TTS: GPT 模型文件不存在，跳过合成: {self.gpt_path}")
            return None
        if not os.path.exists(self.sovits_path):
            _safe_print(f"❌ TTS: SoVITS 模型文件不存在，跳过合成: {self.sovits_path}")
            return None

        if not text or not text.strip():
            return None

        # 确保 GSV 依赖已安装
        if not self._ensure_deps():
            _safe_print(f"TTS: 依赖未就绪，跳过合成")
            return None, ""

        # 去除表情标记、动作括号、对话标记
        import re
        clean = re.sub(r'【.*?】', '', text).strip()
        clean = re.sub(r'\[.*?\]', '', clean).strip()
        # 去掉小动作括号（如「（伸懒腰）」「（尾巴晃了晃）」）
        clean = re.sub(r'（[^）]*）', '', clean).strip()
        clean = re.sub(r'\([^)]*\)', '', clean).strip()
        # 去掉结尾的"喵"后面多余的标点（保留喵字）
        clean = clean.strip()

        if len(clean) < 1:
            return None, ""
        # 检查文本是否包含任何可发音内容（字母、数字、汉字等）
        # 只有纯标点/符号/空白才跳过
        if not any(unicodedata.category(c).startswith(('L', 'N')) for c in clean):
            _safe_print(f"  TTS: 跳过无实际内容的文本: {clean[:40]}")
            return None, ""

        _safe_print(f"🔊 TTS: {clean[:60]}...  mood={mood}")
        t0 = time.time()

        # 获取参考音频
        ref_wav, ref_text, ref_lang = self._get_ref_paths(mood)
        if not ref_wav:
            _safe_print(f"TTS: no ref for mood={mood}")
            return None, ""

        # 输出文件
        timestamp = int(time.time() * 1000) % 1000000
        output_wav = os.path.join(self.output_dir, f"mea_{timestamp}.wav")

        # 翻译：中文 → 日语
        # 如果文本已包含假名（LLM 直接输出日语），跳过翻译
        has_kana = any(
            c in clean for c in
            "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
            "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゃゅょっ"
            "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
            "ニャにゃ"
        )
        if not has_kana and self.translate_enabled:
            _safe_print(f"  → 翻译中…")
            jp = self._translate_to_jp(clean)
            if jp and len(jp) >= 2:
                tts_text = jp
            else:
                tts_text = clean
        else:
            tts_text = clean

        _safe_print(f"  → 合成: [日文] {tts_text[:60]}")

        # 判断后端：VITS vs GPT-SoVITS
        tts_backend = self.__class__.__name__  # MeaTTS = VITS, 否则走原逻辑
        if hasattr(self, '_vits_mode') and self._vits_mode:
            return self._speak_vits(tts_text, output_wav)
        else:
            return self._speak_gsv(tts_text, output_wav, mood, ref_wav, ref_text, ref_lang)

    def _speak_vits(self, tts_text: str, output_wav: str) -> Optional[tuple[str, str]]:
        """VITS 后端推理"""
        _safe_print(f"  ▶ VITS 推理…")
        t1 = time.time()
        vits_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vits_infer.py")
        vits_python = getattr(self, '_vits_python', self.python_exe)

        try:
            proc = subprocess.run(
                [vits_python, vits_script, "--text", f"[JA]{tts_text}[JA]", "--output", output_wav,
                 "--noise_scale", "0.667", "--noise_scale_w", "0.6", "--length_scale", "1.0"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout,
            )
            elapsed = time.time() - t1
            _safe_print(f"  ◀ VITS 返回 (rc={proc.returncode}, {elapsed:.1f}s)")

            if proc.returncode != 0:
                _safe_print(f"VITS failed: {proc.stderr[-200:]}")
                return None, ""

            if not os.path.exists(output_wav):
                _safe_print(f"VITS: 输出文件不存在")
                return None, ""

            _safe_print(f"✓ VITS output: {os.path.basename(output_wav)} ({elapsed:.1f}s)")
            return output_wav, "jp"
        except Exception as e:
            _safe_print(f"VITS exception: {e}")
            return None, ""

    def _speak_gsv(self, tts_text: str, output_wav: str, mood: str,
                    ref_wav: str, ref_text: str, ref_lang: str) -> Optional[tuple[str, str]]:
        """GPT-SoVITS 后端推理（原逻辑）"""
        # 获取参考音频
        payload = {
            "ref_wav": ref_wav,
            "prompt_text": ref_text,
            "prompt_language": LANG_TTS,
            "text": tts_text,
            "text_language": ref_lang,
            "gpt_path": self.gpt_path,
            "sovits_path": self.sovits_path,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed": self.speed,
            "sample_steps": self.sample_steps,
            "output_wav": output_wav,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)

        _safe_print(f"  ref={os.path.basename(ref_wav)} lang={ref_lang}")
        _safe_print(f"  text_len={len(tts_text)} chars payload_size={len(payload_json)} bytes")

        try:
            cmd = [self.python_exe, self.infer_script]
            _safe_print(f"  ▶ 启动推理子进程…")
            t1 = time.time()

            proc = subprocess.run(
                cmd,
                input=payload_json.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
            )
            elapsed = time.time() - t1
            # 用 replace 忽略无法解码的字节（GPT-SoVITS 可能会输出 GBK 编码的中文日志）
            stdout_text = proc.stdout.decode('utf-8', errors='replace')
            stderr_text = proc.stderr.decode('utf-8', errors='replace')

            _safe_print(f"  ◀ 子进程返回 (rc={proc.returncode}, {elapsed:.1f}s)")
            if stderr_text.strip():
                _safe_print(f"  ⚠ stderr: {stderr_text.strip()[-200:]}")

            if proc.returncode != 0:
                _safe_print(f"TTS subprocess failed: rc={proc.returncode}")
                if stderr_text.strip():
                    _safe_print(f"  stderr: {stderr_text[:300]}")
                return None, ""

            # 取最后一行非空 JSON
            lines = [l.strip() for l in stdout_text.split('\n') if l.strip()]
            if not lines:
                _safe_print(f"TTS: 子进程无输出")
                return None, ""
            last_line = lines[-1]
            result = json.loads(last_line)

            if not result.get("ok"):
                err = result.get('error', 'unknown')
                _safe_print(f"TTS subprocess error: {err}")
                if result.get("captured"):
                    _safe_print(f"  captured: {result['captured'][:300]}")
                return None, ""

            duration = result.get("duration", 0)
            result_lang = "jp"
            _safe_print(f"✓ SoVITS output: {os.path.basename(output_wav)} ({duration}s) lang={result_lang}")
            return output_wav, result_lang

        except subprocess.TimeoutExpired:
            _safe_print(f"TTS timeout ({self.timeout}s)")
            return None, ""
        except json.JSONDecodeError as e:
            _safe_print(f"TTS JSON parse error: {e}")
            _safe_print(f"  last_line: {last_line[:200] if 'last_line' in dir() else 'N/A'}")
            return None, ""
        except Exception as e:
            _safe_print(f"TTS subprocess error: {e}")
            import traceback
            _safe_print(traceback.format_exc())
            return None, ""

    def pre_render_batch(
        self, texts_with_moods: list[tuple[str, str]], cache_dir: str = None
    ) -> dict[str, str]:
        """
        预合成一批文本 → {text: wav_path}
        texts_with_moods: [(text, mood), ...]
        缓存文件命名: {lang}_{safe}.wav
        """
        if cache_dir is None:
            cache_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "voice_cache"
            )
        os.makedirs(cache_dir, exist_ok=True)

        results = {}
        for text, mood in texts_with_moods:
            if not text or not text.strip():
                continue
            safe = text.replace("……", "").replace("（", "").replace("）", "")
            safe = safe.replace(" ", "_").strip()
            if not safe:
                continue
            # 先合成才能知道语言——暂用临时名，合成完再改名
            _safe_print(f"[prerender] {text!r} mood={mood} ...")
            result = self.speak(text, mood)
            wav, tts_lang = result if result else (None, "")
            if wav and tts_lang:
                cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                if os.path.exists(cache_path):
                    _safe_print(f"[cache] {text!r} already cached (overwriting)")
                shutil.move(wav, cache_path)
                results[text] = cache_path
                _safe_print(f"[prerender] {text!r} -> {cache_path}")
            else:
                _safe_print(f"[prerender] {text!r} FAILED")
        return results

    def get_cached(self, text: str, cache_dir: str = None) -> Optional[str]:
        """获取已缓存语音路径（始终 jp_ 前缀）"""
        if cache_dir is None:
            cache_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "voice_cache"
            )
        safe = text.replace("……", "").replace("（", "").replace("）", "")
        safe = safe.replace(" ", "_").strip()
        if not safe:
            return None
        cache_path = os.path.join(cache_dir, f"jp_{safe}.wav")
        if os.path.exists(cache_path):
            return cache_path
        # 回退无前缀旧缓存
        legacy = os.path.join(cache_dir, f"{safe}.wav")
        if os.path.exists(legacy):
            return legacy
        return None


if __name__ == "__main__":
    tts = MeaTTS()
    print(f"Enabled: {tts.enabled}")
    print(f"Models exist: {tts.health_check()}")
    result = tts.speak("主人，语音测试成功啦喵！", mood="happy")
    if result and result[0]:
        wav, lang = result
        print(f"Output: {wav}  lang={lang}")
    else:
        print("TTS failed")
