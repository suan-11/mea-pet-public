# TTS 引擎插拔与轻量 ONNX 引擎（P1-4）

> 状态：协议层 + 注册表 + 能力矩阵已落地；轻量 ONNX 引擎经真机验证。
> 向导收尾（2026-11-14）：引擎选择 + 模型一键下载已落地，见 §8。
> 验收仪器：`scripts/tts_engine_probe.py`。
> 边界：**没有重写既有三引擎**（GSV / VITS / MiMo）的合成路径——路线图 §9 的纪律是
> 「不单开脱离功能的纯架构重构」，这一轮只把**新增引擎**放进协议层。

## 1. 为什么要有协议层

重构前引擎是这么接的：

```python
class MeaTTS(TtsMimoMixin, TtsGsvMixin, TtsVitsMixin):
    self._vits_mode = engine == "vits"
    self._mimo_mode = engine == "mimo"
```

判断靠两个布尔，分支散在 `speak()` / `speak_async()` / `health_check()` /
`supported_languages()` / `_ensure_deps()` 五处。加第 N 个引擎要改这五处，
**漏一处的症状各不相同**：

| 漏在哪 | 症状 |
| --- | --- |
| `speak` | 静默不出声 |
| `health_check` | doctor 恒绿（谎报） |
| `supported_languages` | 语言闸误杀正常文本 |
| `_ensure_deps` | 被拖去装 torch |

协议层把「一个引擎要回答什么」写成契约，加引擎只剩两件事：写实现、注册。

## 2. 能力矩阵

```python
@dataclass(frozen=True)
class EngineCapabilities:
    languages: tuple = ()          # 支持的语言
    capabilities: frozenset = ()   # streaming / voice_clone / emotion / offline
    size_tier: str = "heavy"       # tiny(<100MB) / medium / heavy
    latency: str = "slow"          # fast(<1s) / medium / slow
    config_keys: tuple = ()        # 它认的配置键
```

三个设计决定，各有理由：

1. **全部是固有属性**，与当前配置无关。「这个引擎能不能流式」换配置也不会变；
   把它和「现在配成什么」混在一格里，两边都会失真。
2. **默认值取最保守档**（heavy/slow/无能力）。没写能力的引擎不该被当成
   「又小又快又全能」。
3. **`size_tier` 决定能否进 core**。这是 P1-4 的核心商业判断：ONNX 引擎是
   `tiny`，所以它能进 core；GSV/VITS 是 `heavy`，所以它们只能外置。

## 3. 轻量 ONNX 引擎：解决什么问题

core 包 321 MB，但**本地离线发声**仍要外置 VITS 扩展包（便携 Python + torch +
权重，1 GB 级）。用户只是想听她说句话，却要下 1 GB。

sherpa-onnx 的 `OfflineTts` 跑 ONNX 时**不需要 torch**——而 P1-3 已经把
sherpa-onnx 装进 core 了，所以这个引擎的**净增量只有模型体积**。

实测选型：`vits-piper-zh_CN-chaowen-medium-int8`

| 读数 | 值 |
| --- | --- |
| 模型目录 | **20.0 MB**（上限 150） |
| 模型载入 | 2545 ms（仅首次） |
| 合成 16 字 | 1047 ms → 2.94 s 音频 |
| 复读漂移（同句三次） | 0.021 |
| 采样率 | 22050 Hz 单声道 |

载入 4 秒是**首次成本**，之后常驻。所以引擎是**懒建**的：构造 `MeaTTS` 不载模型，
第一次 `speak`/`health_check` 才载。把 4 秒塞进启动期是不可接受的。

## 4. 验收：新增引擎真的不用改主干吗

```
python scripts/tts_engine_probe.py --model-dir models/voice_onnx
```

| 判据 | 读数 |
| --- | --- |
| ① 分发点硬编码引擎名 | `service.py` **0** / `chat_flow.py` **0** |
| ② 轻量包增量 | 20.0 MB < 150 MB |
| ③ 真合成 + 复读 | 2.94 s，漂移 0.021 |

**判据①的口径说明**（这一条我改过一次，值得记下来）：

第一版探针在整个文件里搜 `engine == "xxx"`，报出 2 处，判「未过」。但那 2 处都在
`__init__` 里，是**把配置名翻成布尔**的历史接缝，不是分发点——它们不随引擎数量增长。
真正的判据是「加第 N 个引擎要不要动 `speak`/`health_check` 这些方法」，所以探针
改成**只在分发方法体内**搜。调宽判据不是为了让红灯变绿，而是原来那条量的不是它想量
的东西；改完后既有的布尔分支仍在，如实记录在文档里。

## 5. 接进 MeaTTS 的四处改动

都是**并列新增**，不改既有分支：

| 位置 | 改动 |
| --- | --- |
| `__init__` 末尾 | `_init_plugin_engine()`：名字在注册表里才建，懒建 |
| `health_check` | 最前面加插件分支，其余不动 |
| `speak` | 语言处理之后、MiMo 分支之前插一段 |
| `_ensure_deps` | 插件引擎走自己的 health_check，不被拖去装 torch |

### 5.1 踩到的两个坑（都写进注释了）

**坑一：把代码插进 `__init__` 中间，会把构造函数拦腰截断。**
我第一次把 `_plugin_engine` 的创建和 `capabilities` property 一起插在
`__init__` 中间，于是构造函数从那里结束，后半段（`voice_lang` 等赋值）全部
落进新方法里变成**不可达死代码**。症状：

```
AttributeError: 'MeaTTS' object has no attribute 'voice_lang'
```

正确做法：新方法放在所有既有方法之后，`__init__` 的调用放它**最末尾**。

**坑二：`_plugin_engine` 必须有类级默认值。**
既有取证测试用 `MeaTTS.__new__(MeaTTS)` 绕开构造直接拼桩
（`tests/test_review_regressions.py` 的 GSV 那两条）。只写 `self._plugin_engine`
的话，那些桩在 `health_check` 第一行就 `AttributeError`。类级 `_plugin_engine = None`
是**契约**，不是冗余。

### 5.2 冻结版实测暴露的第三个坑：探针读到空注册表

真机打包后跑冻结版 doctor，`tts_engines` 那格报 **「已注册=无」**，而产品侧一切正常。

原因：注册写在 `meapet.tts.service` 的**模块级**（这样任何走 MeaTTS 的路径都不会漏），
而探针原来只 import 了 `protocol`。表是空的，不是注册失败——**读数没让注册发生**。

修法是在探针里显式 `import meapet.tts.service`。这条值得记：注册「发生在导入时」
这种设计，任何读取方都得先触发导入，否则拿到的是空表而不是错误。

### 5.3 第四个坑：问能力不该先造实例

修好上一条后，读数变成 **「能力读取失败=TypeError」**。

`EngineRegistry.capabilities_of()` 第一版无条件 `self.create(name)`，
而 ONNX 的工厂签名是 `create_onnx_engine(config)`——需要参数。
**这是接口形状错了，不是调用方写错了**：问一句「你支持什么」，不该要求先凑齐构造实参，
更不该为此去载模型（4 秒）。

改成：优先读**工厂函数自身**的 `capabilities` 属性（能力矩阵本来就写在类上，
挂到工厂是一行赋值），只有工厂没登记能力时才退回「造实例读」。配了两条测试，
快路径与兜底路径各一条。

## 6. 配置

```json
"tts": {
  "engine": "onnx",
  "onnx_model_dir": "./models/voice_onnx",
  "onnx_speaker": 0,
  "onnx_speed": 1.0,
  "onnx_num_threads": 2
}
```

`normalize_config` 里收敛：`onnx_speed` 夹到 0.3–3.0，`onnx_num_threads` 夹到
1–16，`onnx_speaker` 非负。空 `onnx_model_dir` 表示「没配」，引擎会报模型缺席而不是崩。

## 7. 已知边界

- **既有三引擎没有注册进注册表**。它们仍走 `_vits_mode`/`_mimo_mode` 布尔分发。
  这一轮只把新增引擎放进协议层；把三条跑通的路径全部改写风险高于收益，
  应随下次触达它们的功能顺带收敛（路线图 §9 的「随功能触达顺带抽离」）。
- **ONNX 引擎不支持情绪与音色克隆**，它也不声称支持——`capabilities` 里没有那两项。
  收到 `mood`/`style` 参数直接忽略，不假装。
- **模型不进版本控制**（`models/voice_onnx/` 在 .gitignore 里），与
  `voice_asr`/`voice_vad` 同纪律。打包时若目录不存在，spec 会打警告说明本包缺本地声音。
- **向导 UI 已落地**（2026-11-14 收尾）：引擎下拉 + 模型目录 + 一键下载，
  见 §8。P1-5 角色包的选择器可以复用这一段的形状。

## 8. 向导与模型取件（2026-11-14 收尾）

上一版这里写着「未做向导 UI，要手改配置文件切引擎」——招牌能力「开箱离线发声」
卡在最后一公里。本轮把它补齐：

**入口**：配置向导 → 语音设置 → 引擎下拉选「ONNX 轻量离线」→「下载语音模型（约 14 MB）」。
下载成功自动把落盘目录填进输入框，保存配置即生效；目录留空时保存流回落默认目录
`models/voice_onnx`（冻结版即 `_internal/models/voice_onnx`，与 spec 随包收编同一处），
所以「选引擎 + 保存」对随包带模型的构建开箱即用。

**取件通道** `meapet/onnx_voice_pack.py`（纯标准库，向导线程直接调，与
`meapet/vits_pack.py` 同形）：

| 环节 | 形状 |
| --- | --- |
| 身份 | 上游不发 .sha256 边车 ⇒ 直接钉档案 sha256（fidus expected 同款）：**身份由哈希把关，不由路由保证** |
| 路由 | fidus 双路由（API 资产地址优先、直链兜底）→ gh-proxy 镜像兜底 → URL 可覆盖成任意 https 直链（仍须 .tar.bz2、仍验哈希） |
| 解压 | 逐件预检越界/外链 + 新解释器叠 tarfile data filter；官方包的一层包装目录被剥掉，模型文件直接落在模型目录（引擎只认这一层） |
| 换入 | 整目录 .new → 正式 → 清 .old，失败回滚，不做"覆盖到一半" |
| 判据 | 与引擎 `_resolve_model_files` 同一套：恰好一个 .onnx + tokens.txt；多份拒绝挑 |

**实测读数（2026-11-14，Windows 本机）**：

| 项 | 值 |
| --- | --- |
| 档案 | `vits-piper-zh_CN-chaowen-medium-int8.tar.bz2`，14,011,298 B |
| sha256 | `f5f7c8628427fbb259ea4b7ec1a9a822a0c04e3f267071f0abfa0610371d9e0c` |
| 内容核对 | 解包 8 件与 P1-4 真机验收用的模型副本**逐字节 sha256 一致** |
| 真机取件 | 39.9 s（API 路由撞限流后走直链兜底成功），阶段：下载/核验/解压/就绪 |
| 取回即出声 | 新下载模型载入 2078 ms，合成 16 字 → 2.93 s 音频（129 KB wav） |

**测试**：`tests/test_onnx_voice_pack.py`（假 opener + 内存 tar.bz2：四条路由、
哈希不符、tar 越界、缺 tokens、多份 onnx、重装覆盖、staging 清理）与
`tests/test_wizard_tts_onnx.py`（下拉/显隐/回填/状态读数/下载接线/保存流）。
真网络取件不进 CI，由上表的真机读数留档。

