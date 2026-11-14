"""语音输入模块 —— 录音 + sherpa-onnx 离线转文字（P1-3 起含 VAD 自动断句、唤醒词、按住说话）。

所有功能默认关闭（voice_input.enabled=false）。用户需在配置向导或右键菜单中主动开启；
唤醒词常驻监听还有一层**独立**开关（voice_input.wake_word.enabled），默认同样关闭。

本包按「能不能脱离 Qt 导入」分层：
- gate / vad / paths 相关几件是纯逻辑或纯第三方推理（sherpa），无 Qt 依赖，
  无头取证与单元测试直接驱动；
- engine / ptt / kws 依赖 PyQt5（QThread / 信号 / 原生事件过滤器），
  经 PEP 562 惰性导入——这样 import meapet.voice.gate 不会连带拖起 Qt。
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查器看；运行期走 __getattr__
    from .engine import VoiceEngine, create_voice_engine

__all__ = ["VoiceEngine", "create_voice_engine"]

_LAZY = {
    "VoiceEngine": ("meapet.voice.engine", "VoiceEngine"),
    "create_voice_engine": ("meapet.voice.engine", "create_voice_engine"),
}


def __getattr__(name: str):
    """PEP 562：把 engine 那两件推迟到真正用到时才导入。"""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value  # 缓存，后续访问不再走 __getattr__
    return value
