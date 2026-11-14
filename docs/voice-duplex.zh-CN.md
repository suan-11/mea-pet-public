# 语音输入全双工（P1-3）

> 状态：已落地并**经真机打包验证**（VAD 自动断句 / 唤醒词 / 按住说话 / 播音期门控 / 依赖打包）。
> 验收仪器：`scripts/voice_duplex_probe.py`；打包闸门：`scripts/verify_dist.py`。
> 隐私边界：**所有常驻监听默认关闭**，且唤醒词是独立于 `voice_input.enabled` 的第二道开关。

## 1. 三件能力与它们的开关

| 能力 | 配置键 | 默认 | 说明 |
| --- | --- | --- | --- |
| 点按切换录音 | `voice_input.mode = "toggle"` | ✅ 开 | 老行为，升级不改用户习惯 |
| 常驻收音 + 自动断句 | `voice_input.mode = "hands_free"` | 关 | VAD 断出一句就转写发一句 |
| 全局按住说话 | `voice_input.ptt_hotkey = "Ctrl+Shift+Space"` | 空（不注册） | 空串=不启用；非 Windows 自动回落 |
| 唤醒词 | `voice_input.wake_word.enabled` | ❌ 关 | 常驻监听，必须在向导里单独打开 |
| 播音期门控 | `voice_input.echo_gate_tail_ms` | 300 ms | 不是开关，是防自激的尾音留量 |

## 2. 为什么门控是这个形状

`_play_audio` 有三条播放路径（Qt 播放器 / Windows `startfile` / Linux `aplay`），
后两条**拿不到「播完」回调**。所以门控不能是引用计数，只能是「到期时间戳」：
每发播放把截止推到 `now + 时长 + 尾音`，自己能过期。

四条不变量（都有测试钉住）：

1. `hold()` **只叠 max**，不缩短已有窗口——逐句流水线下一句常在上一句尾音里就进队。
2. 时长读不出来时**仍要闭尾音那一格**，不能变成空操作。
   （实现里时长与尾音**分两个 try 解析**；合成一个的话，糟糕的时长会让整个 hold 变空操作——
   而那正是最该闭嘴的时刻。这条是测试第一次跑就抓出来的真 bug。）
3. 单一实例 `GLOBAL_ECHO_GATE`：播放侧推、录音侧读，两处各持一只就等于没做。
4. 门控生效期**整帧丢弃并 reset 检测器**，不是「暂停」——帧的时间轴要与真实时间对齐。

## 3. 实测读数（2026-11-14）

### 3.1 误唤醒验收

```
python scripts/voice_duplex_probe.py --hours 8
```

| 臂 | 断句数 | 说明 |
| --- | --- | --- |
| 底噪（8h） | **0** | 白噪声 + 50 Hz 漂移 |
| 自播音 · 门控开 | **0** | 丢帧 900,000 |
| 自播音 · 门控关 | **9172** | 仪器自检：必须 > 0 |

实耗 430 s，判据全过。第三条是关键：没有它，一个「永远不断句」的坏 VAD 也能拿满分。

### 3.2 真机打包（core 档）

```
python -m PyInstaller --noconfirm --distpath build/stage_dist --workpath build/stage_work MeaPet.spec
python scripts/verify_dist.py --dist build/stage_dist/MeaPet --platform windows --profile core
```

| 读数 | 值 |
| --- | --- |
| 包体积 | **321.4 MB**（上限 360） |
| PyInstaller 耗时 | 235 s |
| 闸门结果 | `ok: true`，failures/warnings 均空 |
| qtbin 旧 CRT | 0 |
| 泄露文件 | 无 |
| 包内 doctor | exit=0，16 项，`frozen=True` |

**包内语音运行时逐件确认**（`ls _internal/`）：

- `sherpa_onnx/lib/onnxruntime.dll`、`sherpa-onnx-c-api.dll`、`sherpa-onnx-cxx-api.dll`、`_sherpa_onnx.cp312-win_amd64.pyd`
- `pyaudio/_portaudio.cp312-win_amd64.pyd`

冻结版 doctor 的 `voice_duplex` 那一格实读：

```
模式=toggle · 库=(sherpa_onnx=在场 pyaudio=在场) · VAD模型=缺席 ·
唤醒词=关(默认)/模型=缺席 · PTT热键=未配 · 尾音=300ms · 门控=空闲
```

`input_devices` 那一格：**枚举到 24 台录音设备（10 台可用）** —— 冻结包内 pyaudio 真的能开到设备。

把模型放进 `_internal/voice_vad/` 后该格转为 `VAD模型=在场`；再用**包内那一个模型文件**
（`_internal/voice_vad/silero_vad.onnx`）实跑分段：真语音样本断出 **1 句 1.8 s**，静音 **0 段**。
这证明的是「包里的模型能加载、能断句」，不是「目录里有这个文件名」。

### 3.3 PTT 真机验证（2026-11-14，Windows）

```
python scripts/ptt_probe.py --hotkey "Ctrl+Alt+F9"
```

| 检查项 | 读数 |
| --- | --- |
| 解析 | `mods=0x0003 vk=0x78` |
| RegisterHotKey | **成功** |
| 投递 WM_HOTKEY | 成功 |
| nativeEventFilter 收到 pressed | **是** |
| 松开轮询已起 | 是（间隔 40 ms，与 `POLL_MS` 一致） |
| 卸载后 `armed` | False |
| **卸载后能再次注册** | **是（证明无残留）** |

最后一条是关键：RegisterHotKey 不会随进程结束自动释放，残留会让**下一次启动
静默注册失败**。探针用「先卸载、再注册同一个热键」来证伪残留，比只看
`armed=False` 强——后者只说明对象状态，前者说明系统状态真的回收了。

探针里「松开」这一格只验**轮询已启动**，不验「真松开能停」：`GetAsyncKeyState`
读的是物理键态，模拟不出来。这一格留给人手按一次。

### 3.4 KWS 唤醒词端到端（2026-11-14）

```
python scripts/kws_probe.py --model-dir <解压后的 KWS 模型目录>
```

| 检查项 | 读数 |
| --- | --- |
| 成套解析 | encoder/decoder/joiner 全为 `epoch-12-avg-2` int8 |
| 加载 | 成功 |
| 命中（3.wav） | `文森特卡索`、`法国` |
| 命中（4.wav） | `蒋友伯`、`女儿` |
| 命中（5.wav） | `见面会`、`落实` |
| 命中（6.wav） | `周望军`、`朱丽楠` |
| 合计 | **8 个关键词**，0.wav/1.wav/2.wav 无命中（它们不含词表里的词） |

**这里修掉一个真缺陷。** 上游 wenetspeech 官方包**一个目录里并排放着两套模型**
（`epoch-99-avg-1` 与 `epoch-12-avg-2`，各有 int8/非 int8 两档）。原来的
`find_voice_kws_files()` 是按件 `startswith` 挑第一个命中的——三件可能分属不同套，
拼出 encoder-99 + decoder-12 这种组合。实测那次侥幸全落在 12，但那是 `iterdir`
顺序给的运气，不是保证；混搭的失败也不会指向真因。

现在改成**按变体分组、成套取**：`_kws_variant_key()` 去掉前缀与精度后缀，
只有三件同键才算一套；同套内优先 int8。配套四条测试钉住（含「两套并存不许混搭」）。

**另一个坑**：词表必须与模型配套。mobile 版的 `keywords.txt` 是中文原文格式，
而模型要的是音素级 `test_wavs/test_keywords.txt`——用错词表时模型**照样加载成功**，
只是永远不命中。所以探针的判据里「能命中」必须独立于「能加载」。

## 4. 两个模型的取件

| 模型 | 盘上位置 | 体积 | 冻结版可下 |
| --- | --- | --- | --- |
| Silero VAD | `voice_vad/silero_vad.onnx` | ~630 KB | ✅ 纯 HTTPS |
| ASR（中英 zipformer） | `voice_asr/` | 198 MB (int8) | ⚠️ 需 pip 装 sherpa |
| KWS 唤醒（可选） | `voice_kws/` | ~15 MB | ⚠️ 同上 |

**冻结版的关键差别**：VAD 只是一个 HTTPS 文件，用 `requests` 直下即可，
所以向导里「下载 VAD 模型」按钮**不受冻结限制**；ASR 那一路要 pip，冻结版走不通
（这是既有 P0-2 的结论，未被本次改变）。

### 4.1 打包闸门补了一条硬断言

`verify_dist.py` 新增 `VOICE_RUNTIME_DIRS = ("sherpa_onnx", "pyaudio")` 包内断言。
理由是一次真踩：**第一次打包时 venv 里没有 pyaudio**，spec 里那段是 `pass` 静默跳过，
包照样出得来、闸门照样绿——只是录音那一路整个没了。「构建成功、功能缺失」比构建失败更危险。

两处都已收紧：

- `MeaPet.spec`：缺依赖时打印 `⚠ 语音输入依赖缺席，本包**不含**录音能力：<pkg>`，不再静默。
- `verify_dist.py`：包内缺 `_internal/sherpa_onnx` 或 `_internal/pyaudio` 直接判 **FAIL**。
  已用全空假包实测过，两条断言都真的会拦（`ok: false`）。

### 4.2 加闸门必须同步加它的夹具和红测试

第一次推上去 **CI 四个 job 全挂**，原因不是产品代码，而是我改了闸门却没改它的测试：

`tests/test_verify_dist.py` 用一棵**合成的假产物树**验证「干净产物应全绿」，
那棵树里没有 `_internal/sherpa_onnx` 与 `_internal/pyaudio`，于是 5 条用例当场转红。

教训（写进代码注释了）：**闸门新增一条硬断言时，三件套要一起改**——
断言本身、夹具（让「干净产物」名副其实）、以及一条**证明它真会拦的红测试**。
少了任何一件，要么 CI 红，要么断言形同虚设。

补上后该文件 24 项全过；不带语音依赖的环境下全量 1909 passed。

## 5. 已知边界

- **PTT 不碰低级键盘钩子**：`RegisterHotKey` 只报按下，松开靠轮询
  `GetAsyncKeyState`（40 ms）。被杀软按 keylogger 标的风险低得多，代价是松开最多一格的延迟。
  真机验证见 §3.3（脚本 `scripts/ptt_probe.py`；注册本身不进 CI，它会改系统状态）。
- **无修饰键的热键被拒**：单按一个键当全局 PTT 等于全系统录音，
  `hotkey_is_safe()` 直接挡掉，只写日志不弹窗（被占用是设备上的常态）。
- **非 Windows 没有全局 PTT**：`available()` 返回 False，回落点按/常驻模式。
- **KWS 已端到端测**（§3.4）：模型 31 MB，成套解析 → 加载 → 真命中 8 词。
  但**常驻监听线程本身**未跑过——探针验的是「喂一段音频能断出词」，
  不是「监听线程持续读麦克风」。后者要有麦克风才能验。
- **唤醒词词表需与模型配套**：中文原文词表（`keywords_raw.txt`）要转音素，
  直接用会导致「加载成功但永不命中」。产品里 `voice_input.wake_word.keywords_file`
  要指向音素级词表。
- **唤醒词命中且引擎已在听时不打扰**：hands_free 模式下唤醒词只是确认，不重启收音。

## 6. 测试

`tests/test_voice_duplex.py` 62 项，覆盖门控（含假钟注入）、VAD（真 sherpa 跑真样本）、
PTT 解析与安全判定、KWS 缺席路径、配置面收敛、hands_free 端到端发射、以及
「新键不许是死键」的逐格读取点断言。
