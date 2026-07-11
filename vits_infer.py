"""
VITS Fast Fine-Tuning 推理脚本（独立版）
全部依赖已内置在 vits_core/ 中
用法: python vits_infer.py --text "[JA]こんにちわ[JA]" --output output.wav
"""
import os, sys, json, argparse, subprocess

# 将 vits_core 加入模块搜索路径
BASE = os.path.dirname(os.path.abspath(__file__))
VITS_CORE = os.path.join(BASE, "vits_core")
if os.path.isdir(VITS_CORE):
    sys.path.insert(0, VITS_CORE)

# embeddable Python 的 ._pth 可能没正确引入 site-packages，
# 手动加上，确保 setuptools/pyopenjtalk 能导入
_py_home = os.path.dirname(sys.executable)
_sp = os.path.join(_py_home, "Lib", "site-packages")
if os.path.isdir(_sp) and _sp not in sys.path:
    sys.path.insert(0, _sp)

# 国内网络环境 SSL 证书可能验证失败，pyopenjtalk 首次加载会下载字典
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

# ═══ 修复 numpy 二进制兼容性（跨平台：Windows / Linux） ═══
# pyopenjtalk 的 htsengine.pyx C 扩展编译时链接了 numpy < 2.0 的 ABI。
# 若运行时 numpy >= 2.0，会报：
#   ValueError: numpy.dtype size changed, may indicate binary incompatibility.
# 在模块导入之前检查已安装的 numpy 版本，自动安装兼容版本。
_PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
_PIP_MIRROR_HOST = "pypi.tuna.tsinghua.edu.cn"

def _fix_numpy_if_needed():
    try:
        from importlib.metadata import version as _ver
        v = _ver("numpy")
        if v.startswith("2."):
            print("  ⚠ 检测到 numpy >= 2.0，pyopenjtalk 需要 numpy < 2.0，正在修复…", file=sys.stderr, flush=True)
            # 尝试先不带 mirror（Linux/macOS 直连）
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--force-reinstall", "numpy>=1.21,<2.0", "-q"],
                    timeout=120,
                )
            except (subprocess.CalledProcessError, Exception):
                # 直连失败 → 走清华镜像（中国大陆/Windows）
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install",
                     "--index-url", _PIP_MIRROR,
                     "--trusted-host", _PIP_MIRROR_HOST,
                     "--force-reinstall", "numpy>=1.21,<2.0", "-q"],
                    timeout=120,
                )
            print("  ✓ numpy 已降级至 <2.0，继续加载…", file=sys.stderr, flush=True)
            # 不需要 exit：实际 import numpy 发生在后面（torch → numpy），
            # pip 已安装新版本到 site-packages，后续 import 会加载正确版本。
        elif v.startswith("1."):
            pass  # 正常
    except (ImportError, subprocess.CalledProcessError, Exception):
        pass  # 没有 importlib.metadata 或 pip 不可用，让后续导入报错

_fix_numpy_if_needed()

# 预下载 pyopenjtalk 词典（优先用项目内 dic/，没有才网络下载）
import urllib.request, tarfile, io, os as _os
# 项目内置词典路径
_builtin_dic = _os.path.join(BASE, 'dic', 'open_jtalk_dic_utf_8-1.11')
print(f"  [debug] BASE={BASE}", file=sys.stderr, flush=True)
print(f"  [debug] _builtin_dic={_builtin_dic}", file=sys.stderr, flush=True)
print(f"  [debug] isdir={_os.path.isdir(_builtin_dic)}", file=sys.stderr, flush=True)
if _os.path.isdir(_builtin_dic):
    print(f"  ✓ 使用项目内置日语词典", file=sys.stderr, flush=True)
    # 设置环境变量让 pyopenjtalk 直接用项目词典，防止它自行下载
    _os.environ['OPEN_JTALK_DICT_DIR'] = _builtin_dic
else:
    _pyjt_dir = _os.path.join(_os.environ.get('APPDATA', _py_home), '.pyopenjtalk')
    _dic_dir = _os.path.join(_pyjt_dir, 'open_jtalk_dic_utf_8-1.11')
    if not _os.path.isdir(_dic_dir):
        _os.makedirs(_pyjt_dir, exist_ok=True)
        _dic_urls = [
            'https://github.com/r9y9/open_jtalk/releases/download/v1.11.1/open_jtalk_dic_utf_8-1.11.tar.gz',
            'https://downloads.sourceforge.net/project/open-jtalk/Open%20JTalk/open_jtalk_dic_utf_8-1.11.tar.gz',
        ]
        for _url in _dic_urls:
            try:
                print(f"  ↓ 下载日语词典 {_os.path.basename(_url)} …", file=sys.stderr, flush=True)
                _resp = urllib.request.urlopen(_url, timeout=30)
                _tar = tarfile.open(fileobj=io.BytesIO(_resp.read()))
                _tar.extractall(_pyjt_dir)
                print(f"  ✓ 日语词典已下载到 {_pyjt_dir}", file=sys.stderr, flush=True)
                break
            except Exception as _e:
                print(f"  ⚠ 下载失败: {_url[:50]}… {_e}", file=sys.stderr, flush=True)
                continue
        if not _os.path.isdir(_dic_dir):
            print("  ❌ 日语词典下载失败", file=sys.stderr, flush=True)
            print("     手动下载后解压到项目 dic/ 目录:", file=sys.stderr, flush=True)
            print("     https://github.com/r9y9/open_jtalk/releases/tag/v1.11.1", file=sys.stderr, flush=True)
    if _os.path.isdir(_dic_dir):
        _os.environ['OPEN_JTALK_DICT_DIR'] = _dic_dir

# 确保 pyopenjtalk 需要的 pkg_resources 可用
try:
    import pkg_resources
except ModuleNotFoundError:
    _pip = [sys.executable, "-m", "pip", "install",
            "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
            "--trusted-host", "pypi.tuna.tsinghua.edu.cn",
            "setuptools==69.5.1", "-q"]
    subprocess.run(_pip, capture_output=True, timeout=60)

import torch
import scipy.io.wavfile as wavf
from torch import no_grad, LongTensor
import commons
from text import text_to_sequence
from models import SynthesizerTrn
import utils

device = "cuda:0" if torch.cuda.is_available() else "cpu"

def get_text(text, hps, is_symbol=False):
    text_norm = text_to_sequence(text, hps.symbols, [] if is_symbol else hps.data.text_cleaners)
    if hps.data.add_blank:
        text_norm = commons.intersperse(text_norm, 0)
    text_norm = LongTensor(text_norm)
    return text_norm

def load_model(model_path, config_path):
    hps = utils.get_hparams_from_file(config_path)
    net_g = SynthesizerTrn(
        len(hps.symbols),
        hps.data.filter_length // 2 + 1,
        hps.train.segment_size // hps.data.hop_length,
        n_speakers=hps.data.n_speakers,
        **hps.model
    ).to(device)
    net_g.eval()
    utils.load_checkpoint(model_path, net_g, None)
    return hps, net_g

def infer(hps, net_g, text, speaker_name, output_path,
          noise_scale=0.667, noise_scale_w=0.6, length_scale=1.0):
    speaker_ids = hps.speakers
    if isinstance(speaker_ids, dict):
        speaker_id = speaker_ids.get(speaker_name, 0)
    else:
        speaker_id = 0
    
    stn_tst = get_text(text, hps, False)
    with no_grad():
        x_tst = stn_tst.unsqueeze(0).to(device)
        x_tst_lengths = LongTensor([stn_tst.size(0)]).to(device)
        sid = LongTensor([speaker_id]).to(device)
        audio = net_g.infer(
            x_tst, x_tst_lengths, sid=sid,
            noise_scale=noise_scale,
            noise_scale_w=noise_scale_w,
            length_scale=length_scale
        )[0][0, 0].data.cpu().float().numpy()
    
    wavf.write(output_path, hps.data.sampling_rate, audio)
    return output_path

# 模型缓存（避免每次推理都重新加载）
_model_cache = None

def get_cached_model():
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    
    model_path = os.path.join(BASE, "vits_models", "G_latest.pth")
    config_path = os.path.join(BASE, "vits_models", "finetune_speaker.json")
    
    if not os.path.exists(model_path) or not os.path.exists(config_path):
        return None, None
    
    hps, net_g = load_model(model_path, config_path)
    _model_cache = (hps, net_g)
    return hps, net_g

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VITS 语音合成")
    parser.add_argument("-t", "--text", required=True, help="合成文本（含语言标记）")
    parser.add_argument("-o", "--output", default="output.wav", help="输出音频路径")
    parser.add_argument("-s", "--speaker", default="Mea", help="说话人名称")
    parser.add_argument("--noise_scale", type=float, default=0.667)
    parser.add_argument("--noise_scale_w", type=float, default=0.6)
    parser.add_argument("--length_scale", type=float, default=1.0)
    parser.add_argument("--warmup", action="store_true", help="预热加载模型")
    args = parser.parse_args()
    
    hps, net_g = load_model(
        os.path.join(BASE, "vits_models", "G_latest.pth"),
        os.path.join(BASE, "vits_models", "finetune_speaker.json")
    )
    if args.warmup:
        print("OK:model_loaded")
        sys.exit(0)
    
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    result = infer(hps, net_g, args.text, args.speaker, args.output,
                   args.noise_scale, args.noise_scale_w, args.length_scale)
    print(f"OK:{result}")
