"""TTS 引擎协议与注册表（P1-4）。

为什么要有这一层
----------------
重构前引擎是这么接的：`MeaTTS(TtsMimoMixin, TtsGsvMixin, TtsVitsMixin)`，
判断靠 `self._mimo_mode` / `self._vits_mode` 两个布尔，分支散在
`speak()` / `speak_async()` / `health_check()` / `supported_languages()` /
`_ensure_deps()` 五处。加第 N 个引擎要改这五处，漏一处的症状各不相同：
漏 speak 是静默不出声，漏 health_check 是 doctor 恒真，漏
supported_languages 是语言闸误杀。

这一层把「一个引擎需要回答什么」写成协议，让加引擎只做两件事：
写一个类实现协议、把它注册进去。调度主干不再出现 `if engine == ...`。

契约的边界
----------
**这里不接管合成本身**。协议描述的是「能力与入口」，具体怎么合成仍由各引擎
自己决定（GSV 走子进程、MiMo 走 HTTP、VITS 进程内或外置解释器）——
路线图 §9 的纪律是「不单开脱离功能的纯架构重构」，所以这一层只做插拔面，
不重写三条已经跑通的合成路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: 引擎能力矩阵里的六个维度。问的是「这个引擎能不能」，不是「现在配置成什么」——
#: 前者是引擎的固有属性（换配置也不会变），后者是运行时状态，别混在一格里。
CAPABILITY_STREAMING = "streaming"
CAPABILITY_VOICE_CLONE = "voice_clone"
CAPABILITY_EMOTION = "emotion"
CAPABILITY_OFFLINE = "offline"
CAPABILITY_ALL = (
    CAPABILITY_STREAMING,
    CAPABILITY_VOICE_CLONE,
    CAPABILITY_EMOTION,
    CAPABILITY_OFFLINE,
)

#: 体积档：决定它能不能进 core 包。数来自真机实测（见 docs/tts-engine-plugins.zh-CN.md）。
SIZE_TIER_TINY = "tiny"      # < 100 MB，可与 core 同包
SIZE_TIER_MEDIUM = "medium"  # 100 MB - 1 GB，按需下载
SIZE_TIER_HEAVY = "heavy"    # >= 1 GB，外置扩展包（当前 GSV/VITS 都在这一档）
SIZE_TIER_ALL = (SIZE_TIER_TINY, SIZE_TIER_MEDIUM, SIZE_TIER_HEAVY)

#: 延迟档：首音时间数量级。决策用（要不要为了低延迟牺牲音质），不是精确 SLA。
LATENCY_FAST = "fast"        # < 1 s
LATENCY_MEDIUM = "medium"    # 1 - 5 s（首次含模型加载另计）
LATENCY_SLOW = "slow"        # > 5 s（子进程冷启动那一类）
LATENCY_ALL = (LATENCY_FAST, LATENCY_MEDIUM, LATENCY_SLOW)


@dataclass(frozen=True)
class EngineCapabilities:
    """一个引擎的能力矩阵。全部字段都是**固有属性**，与当前配置无关。"""

    #: 支持的语言标签（`zh` / `jp` / `en` ...）。空元组表示"不做限制，由文本路由决定"。
    languages: tuple = ()
    #: 能力集合，取值见 CAPABILITY_*。
    capabilities: frozenset = field(default_factory=frozenset)
    #: 体积档，取值见 SIZE_TIER_*。
    size_tier: str = SIZE_TIER_HEAVY
    #: 延迟档，取值见 LATENCY_*。
    latency: str = LATENCY_SLOW
    #: 这个引擎要知道的那几个配置键（给人看的能力清单，也给向导排 UI 用）。
    config_keys: tuple = ()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def describe(self) -> str:
        """一行读数，给 doctor 与日志用。"""
        langs = "/".join(self.languages) if self.languages else "不限"
        caps = ",".join(sorted(self.capabilities)) or "无"
        return (
            f"语言={langs} 能力={caps} "
            f"体积档={self.size_tier} 延迟档={self.latency}"
        )


@runtime_checkable
class TtsEngine(Protocol):
    """TTS 引擎必须回答的问题。

    只放**调度主干真正需要的**那几件。多放一格就等于要求每个引擎都得实现它，
    而其中大多数引擎根本用不上——那不是协议，是把实现细节泄漏成义务。
    """

    #: 注册表里的名字，也是配置里 `tts.engine` 的取值。
    name: str
    #: 能力矩阵。
    capabilities: EngineCapabilities

    def speak(self, text: str, output_wav: str, **kwargs):
        """合成一段文本到 `output_wav`，返回 `(wav_path, lang)` 或 None。"""

    def health_check(self) -> bool:
        """前置条件齐不齐（模型在不在、依赖装没装）。doctor 与 speak 之前都问它。"""


class EngineRegistry:
    """引擎注册表：名字 → 工厂。

    工厂而不是实例：引擎有状态（模型句柄、HTTP 连接），一个进程里只该有一份，
    但**建它的时机**由调用方定（配置加载后、语言确定后）。存工厂才不逼着
    注册期就去加载模型。
    """

    def __init__(self) -> None:
        self._factories: dict = {}
        self._order: list = []

    def register(self, name: str, factory, *, override: bool = False) -> None:
        """注册一个引擎工厂。

        `override=False` 时重名会抛——同一个名字被两处定义，运行时用哪个取决于
        导入顺序，这种不确定性在引擎选择上是要命的。
        """
        key = str(name or "").strip().lower()
        if not key:
            raise ValueError("引擎名不能为空")
        if key in self._factories and not override:
            raise ValueError(f"引擎 {key!r} 已注册（要覆盖请显式 override=True）")
        self._factories[key] = factory
        if key not in self._order:
            self._order.append(key)

    def unregister(self, name: str) -> None:
        """摘掉一个引擎（插件卸载用）。不存在就静默——卸载幂等比报错有用。"""
        key = str(name or "").strip().lower()
        self._factories.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    def create(self, name: str, *args, **kwargs):
        """按名字造一个引擎实例；名字不在表里抛 KeyError。"""
        key = str(name or "").strip().lower()
        if key not in self._factories:
            raise KeyError(f"未知 TTS 引擎: {name!r}（已注册: {', '.join(self._order)}）")
        return self._factories[key](*args, **kwargs)

    def names(self) -> tuple:
        return tuple(self._order)

    def __contains__(self, name: object) -> bool:
        return str(name or "").strip().lower() in self._factories

    def __len__(self) -> int:
        return len(self._factories)

    def capabilities_of(self, name: str, *args, **kwargs) -> EngineCapabilities:
        """取某个引擎的能力矩阵。

        优先读**工厂函数自身**的 `capabilities` 属性（`@register_engine` 与
        `create_onnx_engine` 都挂在它上面）——读能力不该需要先凑齐构造实参，
        更不该为了问一句「你支持什么」去载模型。只有工厂没登记能力时才退回
        「造一个实例读它的」。

        实测踩过：第一版无条件 `self.create(name)`，而 `create_onnx_engine`
        需要一个 `config` 参数，于是 doctor 报「能力读取失败=TypeError」——
        问一句话却要先编一个对象出来，这个接口形状本身就是错的。
        """
        key = str(name or "").strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(
                f"未知 TTS 引擎: {name!r}（已注册: {', '.join(self._order)}）"
            )
        declared = getattr(factory, "capabilities", None)
        if isinstance(declared, EngineCapabilities):
            return declared
        engine = factory(*args, **kwargs) if (args or kwargs) else factory()
        return getattr(engine, "capabilities", EngineCapabilities())


#: 全局注册表。插件在 import 时把自己加进来；产品侧只从这里取。
ENGINE_REGISTRY = EngineRegistry()


def register_engine(name: str, *, override: bool = False):
    """装饰器形式：`@register_engine("piper")` 挂在引擎工厂上。"""

    def decorate(factory):
        ENGINE_REGISTRY.register(name, factory, override=override)
        return factory

    return decorate
