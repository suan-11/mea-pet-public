# MeaPet - 桌面宠物

**简体中文** | [English](README.en.md)

MeaPet 是一款 Windows 与 Linux 并重的 PyQt5 透明桌面宠物。它将角色立绘、AI 对话、语音合成、屏幕视觉、SQLite 记忆和好感度整合在一个桌面前端中，同时支持 Live2D 和 PNG 两种渲染方式。

MeaPet 有清晰的职责边界：角色呈现、聊天气泡、TTS、截图授权和本地状态由 MeaPet 管理；使用 Agent 作为回复后端时，模型、长期记忆和内部工具由 Agent 管理。

**移动版**：[mea-pet-mobile](https://github.com/llz121517/mea-pet-mobile)（包含核心功能的轻量移动客户端）

## 当前功能

| 功能 | 说明 |
|------|------|
| 回复后端 | 直连模型 API 或 Agent；同时只启用一种，不自动回退 |
| 直连协议 | 通过 HTTP 流式传输支持 Ollama Chat、OpenAI Chat/Responses、Anthropic Messages |
| Agent | 通过 WebSocket 支持 Hermes TUI Gateway、OpenClaw Gateway v4 或自定义 Agent Link v1 |
| 显示 | 未启用 TTS 时流式显示文本气泡；启用 TTS 时等待音频生成，再同步显示气泡并播放 |
| 多段回复 | 每段回复分别包含气泡、情绪、朗读文本、语言和 TTS 风格 |
| 语音 | 支持 MiMo 云端 TTS、本地 GPT-SoVITS、本地 VITS；GPT-SoVITS 支持按语言配置参考音频 |
| 视觉 | 可禁用、继承主模型，或通过独立视觉模型中继 |
| 反向控制 | 通过 Companion MCP 或同一 Agent Link 连接共享前端工具 |
| 本地数据 | SQLite 记忆、好感度，以及按后端和会话隔离的对话时间线 |
| 渲染 | Live2D 动态模型与 PNG 差分立绘，可在运行时切换 |

## 快速开始

桌宠需要 Python 3.10 或更高版本（项目默认使用 3.12）。由于依赖兼容性限制，使用本地 VITS 时建议使用 Python 3.10-3.12。

### Windows

双击 `启动桌宠.bat`。脚本会复用已有的 `.venv`；必要时会创建环境并安装核心依赖，首次运行时会打开配置向导。

也可以手动运行：

```bat
python setup_wizard.py
python pet.py
```

### Linux

```bash
pip install -r linux_requirements.txt
python setup_wizard.py
python pet.py
```

`live2d-py` 是可选依赖；不可用时会回退到 PNG。预编译包可从 [EasyLive2D/live2d-py](https://github.com/EasyLive2D/live2d-py) 获取。

随时可以通过桌宠右键菜单中的“打开设置...”再次打开配置向导。保存后，新后端会立即生效，旧后端仍在进行的生成任务会被取消。

中文故障排查步骤见 [`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md)。

### Windows 二进制包（PyInstaller onedir）

请在已安装应用依赖和 PyInstaller 的虚拟环境中构建。如果发布包包含本地 VITS，还需要 `numpy<2` 和 `setuptools==69.5.1`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

输出：`dist/MeaPet/MeaPet.exe` 和 `dist/MeaPet/_internal/`。

### Linux 二进制包（同一份 spec）

Wayland 点击穿透依赖仓库根的 `liblayer_shell_shim.so`（ctypes 直读的裸 `.so`，PyInstaller 的依赖分析看不见它），所以打包前先构建它：

```bash
bash build_layer_shell.sh
pyinstaller MeaPet.spec
```

缺这个产物时 Linux 上的打包会直接中止并提示上面第一条命令——带着缺件打出来的包没有穿透模式，而这件事只在真机上看得见。

`mea-pet` wheel 只是用于开发和依赖分发的 Python 包组件，不包含完整桌面资源目录或独立启动器。请使用源码检出目录或 PyInstaller onedir 包运行完整桌宠。

便携版目录结构（用户可写数据与程序包一起放在 `_internal` 下）：

| `_internal` 下的路径 | 用途 |
|----------------------|------|
| `config.json` / `config.example.json` | 用户配置（example 文件是模板） |
| `mea_memory.db` | SQLite 记忆和好感度 |
| `audio_cache/`、`logs/` | 运行时缓存和日志 |
| `sprites/`、`live2d/`、`vits_models/`、`vits_core/`、`dic/` | 随包资源 |

打包版支持的语音引擎：

- **VITS**：在应用进程内运行（应用内包含 torch 和 `vits_core`）。模型存在时不需要外部 Python。
- **MiMo**：云端 TTS（需要 API Key）。
- **GPT-SoVITS**：仍需要独立 GPT-SoVITS 运行环境中的 `python.exe`；不要将路径指向 `MeaPet.exe`。

不要发布包含 API Key 的开发者 `config.json`。打包时只应将 `config.example.json` 作为模板。不要提交 `dist/` 或 `build/`。

## 回复后端

### 直连模型 API

直连模式由 MeaPet 管理角色提示词、近期上下文、SQLite 记忆和输出约束。这是唯一会直接连接 LLM HTTP API 的模式。

在向导中需要配置三项内容：API 基础 URL、模型名称（可以通过“获取模型”自动发现）和 API Key。保存后的 `provider` 始终为 `custom`。传输方式由 `protocol` 和 `api_base` 决定：Ollama 本地 URL 会自动选择 `ollama_chat`，其他地址默认使用 OpenAI 兼容的 Chat 协议。URL 提示只影响从哪个环境变量读取 Key，以及 TTS/视觉与 MiMo/Ollama 的联动。

| API 基础 URL 示例 | 保存的 provider | 自动选择的协议 | 环境变量 |
|-------------------|-----------------|----------------|----------|
| `https://api.deepseek.com/v1` | `custom` | `openai_chat` | `DEEPSEEK_API_KEY`（也支持 `MEAPET_API_KEY`） |
| `https://api.xiaomimimo.com/v1` | `custom` | `openai_chat` | `MIMO_API_KEY` |
| `http://localhost:11434` | `custom` | `ollama_chat` | 无（Key 可选） |
| 其他 OpenAI 兼容端点 | `custom` | `openai_chat` | `MEAPET_API_KEY` / `OPENAI_API_KEY` |

### Agent

Agent 模式将 MeaPet 作为纯桌面前端：

- MeaPet 调用 Agent 生成回复，并提供情绪、动作、TTS 语言和角色状态等上下文。
- Agent 使用自己的模型、记忆和内部工具；MeaPet 不要求 Agent 额外实现“查询记忆”能力。
- 内部工具名称和原始参数不会显示在角色气泡中。安全的状态（开始、完成、失败）会进入时间线，诊断详情只写入日志。
- Agent 模式只使用 WebSocket。Hermes 连接 `hermes serve` 暴露的原生 TUI Gateway，OpenClaw 连接 Gateway v4，自定义后端可以实现 Agent Link v1。HTTP/SSE 模型端点只属于直连模式。
- 连接会跨轮次复用，支持双向流式传输、取消、ping/pong 以及各协议自己的断线恢复。OpenClaw 使用相同的幂等键重试；Hermes 会恢复已保存的会话并核对历史，而不是盲目重新提交可能已经执行过工具调用的轮次。
- Agent Link 使用同一个出站连接完成聊天和前端工具调用。握手后 MeaPet 会发布带类型的工具 schema，第三方连接器可以在自己的 Agent 循环中注册这些工具，并主动调用 `meapet.say`。
- Agent 回复必须遵循 MeaPet 的分段输出格式。格式错误会进入明确的格式修复状态；任何 Agent 路径都不会静默回退到 HTTP 模型端点。

本地 `session_id` 用于标识 MeaPet 的时间线范围。OpenClaw 还会持久化其 Gateway `session_key`；Hermes 会持久化服务器返回的 `remote_session_id`，并在重启或重连后使用 `session.resume`。在 Agent 模式中，“清除记忆”会明确启动一个新的上游 Agent 会话。旧时间线仍可读取。

完整的自定义后端契约见 [`docs/agent-link-v1.md`](docs/agent-link-v1.md)。普通 WebSocket 端点并不足够；后端必须实现其中定义的信封格式、握手、工具快照、请求关联、幂等、取消、离线和重连语义。

## 回复、气泡与 TTS 时序

模型或 Agent 返回的每个回复分段都包含：

- `display_text`：气泡中显示的文本
- `voice_text`：发送给 TTS 的文本
- `voice_language`：TTS 语言
- `mood`：角色情绪；不支持的值会归一化为 `neutral`
- `tts_style`：传给支持该字段的 TTS 引擎的语气描述

渲染规则：

1. 未启用 TTS：收到第一个文本增量时显示气泡，随后逐步增长，分段完成时定稿。
2. 已启用 TTS：先收集完整分段并生成音频，音频就绪后同时显示气泡并开始播放。
3. 气泡持续时间为 `max(配置的最短时长, 音频时长 + 500ms)`。
4. 如果 TTS 无法启动、语言不受支持或音频生成失败，会立即回退为纯文本气泡，不会阻塞。
5. 多个分段不会合并，而是依次播放。点击气泡或打开“对话时间线...”可以查看这一轮的完整内容。

直连和 Agent 模式共用同一套呈现层，因此切换后端不会改变气泡或 TTS 时序。

## 多语言语音

GPT-SoVITS 可以通过 `tts.reference_audios` 为每种语言配置固定参考音频。每一项都必须指定语言；路径可以相对于项目，也可以使用绝对路径。参考文本可以留空：

```json
{
  "tts": {
    "engine": "gpt_sovits",
    "enabled": true,
    "reference_audios": {
      "ja": {"path": "./voice_cache/mea-ja.wav", "text": ""},
      "zh": {"path": "./voice_cache/mea-zh.wav", "text": ""},
      "en": {"path": "./voice_cache/mea-en.wav", "text": ""}
    }
  }
}
```

旧版 `gsv_ref_wav` 和 `gsv_ref_lang` 字段只会被读取，并迁移为一条参考音频配置。与 WAV 同名的 `.txt` 文件会作为参考文本。

语音翻译使用 MeaPet 内置的非 LLM 机器翻译服务池。单个分段失败时会轮换服务，总尝试次数最多为 3 次。启用“优先使用模型语音翻译”后，模型返回的 `voice_language` 和 `voice_text` 会与配置的目标语言核对；若不一致，则翻译到目标语言。单独的“不支持时翻译”开关控制输出语言不受支持时的回退策略。翻译最终失败时会跳过该语音分段，但保留文本气泡。

## 屏幕视觉

视觉链路可在向导中配置为三种模式：

| 模式 | 行为 |
|------|------|
| `disabled` | 关闭截图和视觉 |
| `inherit` | 主回复模型支持图片时，在同一多模态请求中附带截图 |
| `relay` | 先由独立视觉模型生成描述，再传给回复后端 |

`inherit` 适用于原生支持图片的直连模型或 Agent。直连模式下，`relay` 可以选择 Ollama 或 MiMo 作为视觉模型。Agent 模式应使用 Agent 自己的视觉能力，或关闭视觉；不要在 MeaPet 侧通过另一个视觉模型中继。TTS 机器翻译不参与视觉链路。

隐私规则会被强制执行：屏幕观察默认关闭；每张截图都需要本地确认，授权仅对当前一次截图有效。确认范围默认为全屏，也可以限定为区域或特定应用。新链路只在内存中传递截图，绝不会将其写入磁盘。使用云端视觉还必须明确设置 `watcher.allow_cloud` 同意项。

## Companion MCP：Agent 控制 MeaPet

对于 Hermes 和 OpenClaw，Agent 模式可以选择暴露标准 MCP Streamable HTTP 端点：

```text
http(s)://<listen_host>:<port>/mcp
```

只暴露四个工具：

| 工具 | 能力 |
|------|------|
| `meapet.say` | 将一个或多个完整回复分段加入队列；不会抢占等待中的用户回复 |
| `meapet.express` | 请求前端明确支持的情绪或动作；不做隐式映射 |
| `meapet.get_state` | 读取渲染、TTS 能力、角色状态和好感度摘要；不返回路径、密钥、记忆或完整聊天历史 |
| `meapet.capture_screen` | 请求截取全屏、区域或应用；每次都需要本地确认，结果绝不写入磁盘 |

安全约束：

- 默认只监听 `127.0.0.1`，并只允许一个 Agent IP。
- 每个请求都必须携带 Bearer Token；可在向导或右键菜单中查看、复制和轮换。轮换后旧 Token 立即失效。
- LAN 监听默认要求 HTTPS。可信网络可以明确允许纯 HTTP，界面会持续显示风险警告。配置客户端 CA 后，Agent 必须提供由该 CA 签发的客户端证书（mTLS）。
- 不会修改 Windows 防火墙；远程访问需要手动放行对应端口。
- 服务还会验证来源 IP、Host、Origin、请求大小和速率。

自定义 Agent Link 不会启动第二个监听器。它会在现有 WebSocket 上投影相同的能力注册表，使聊天、主动消息和工具调用保持在同一个连接中。

## 配置

项目根目录中唯一的用户配置文件是 `config.json`，唯一的模板是 `config.example.json`。不要编辑或提交真实密钥；`config.json` 已被 gitignore。

最小配置示例：

```json
{
  "llm": {
    "mode": "direct",
    "direct": {
      "provider": "openai",
      "protocol": "openai_chat",
      "api_base": "https://api.openai.com/v1",
      "model": "gpt-4o",
      "api_key": "$MEAPET_API_KEY",
      "temperature": 0.7,
      "max_tokens": 4096
    }
  },
  "vision": {
    "mode": "disabled"
  },
  "tts": {
    "enabled": false
  },
  "ui": {
    "timeline_turns": 5
  }
}
```

完整 schema、Agent、MCP 和自定义 Agent Link 示例见 `config.example.json`、`docs/backend-and-control.md` 和 `docs/agent-link-v1.md`。

### 密钥与环境变量

密钥优先级：环境变量高于 `config.json` 明文。配置值也支持 `$ENV_VAR` 或 `${ENV_VAR}` 占位符。

| 环境变量 | 用途 |
|----------|------|
| `DEEPSEEK_API_KEY` | DeepSeek 直连 |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo 对话、视觉或 TTS |
| `MEAPET_API_KEY` | 自定义直连的备用变量 |
| `HERMES_DASHBOARD_SESSION_TOKEN` | Hermes `hermes serve` WebSocket Token |
| `OPENCLAW_GATEWAY_TOKEN` / `MEAPET_AGENT_TOKEN` | OpenClaw Gateway Token |
| `AGENT_LINK_TOKEN` / `MEAPET_AGENT_TOKEN` | 自定义 Agent Link Token |
| `MEAPET_CONTROL_TOKEN` | Companion MCP Bearer Token |
| `GSV_PYTHON` | GPT-SoVITS 环境中的 `python.exe` |
| `MEAPET_PIP_INDEX_URL` / `PIP_INDEX_URL` | Python 包索引覆盖；默认使用清华 TUNA |
| `MEAPET_TORCH_INDEX_URL` / `TORCH_INDEX_URL` | 可选的 PyTorch wheel 索引覆盖 |
| `MEAPET_HF_ENDPOINT` / `HF_ENDPOINT` | 可选的 Hugging Face 端点覆盖 |
| `MEAPET_TRANSLATORS_REGION` | `translators` 导入地区（`CN` 或 `EN`）；默认为 `CN` |
| `MEAPET_FORCE_PNG` | 设置为非空值时强制使用 PNG 渲染 |
| `MEAPET_DEBUG=1` | 输出额外的协议级诊断信息；默认应保持关闭 |

如果真实 Key 曾经进入代码仓库或公开日志，请立即前往服务提供商处轮换；不要只删除本地文本。

## 本地缓存与隐私

- 默认保留最近 5 轮时间线，可在向导中配置为 0-100。
- 时间线按直连 provider / Agent 类型和 Agent 会话隔离；切换后端不会串线。
- SQLite 记忆（直连模式）与 Agent 自己的记忆边界彼此独立。MeaPet 不会复制 Agent 的长期记忆。
- 应用新配置、切换会话和轮换 Token 都会使旧异步结果失效；迟到的回复、TTS 或截图不会进入新会话。
- 运行时数据位于 `mea_memory.db`、`logs/`、`audio_cache/`、`voice_cache/` 和 `voice_asr/`，这些内容都不应发布。随包分发的互动音频位于包资源 `meapet/assets/interaction_voices/`。
- 普通日志默认只记录长度、状态等元数据，按天轮换并保留 7 天。正文只会进入明确的 TRACK/debug 诊断。API Key、认证头、推理内容、内部工具参数/结果和截图内容都不得写入普通日志。

## 操作

| 操作 | 效果 |
|------|------|
| 按住左键拖动 | 移动桌宠 |
| 双击 | 打开聊天输入框 |
| 拖动头部区域 | 触发摸头互动 |
| 右键 | 打开设置、时间线、状态、渲染、待机和退出菜单 |
| 点击回复气泡 | 在内容仍处于近期缓存时打开该轮完整回复 |
| `Esc` | 关闭输入框或面板 |

关闭主窗口只会隐藏桌宠；请使用托盘菜单退出程序。

## 项目结构

```text
mea-pet/
├── pet.py / meapet/__main__.py       入口
├── meapet/
│   ├── agent/                         Hermes / OpenClaw / Agent Link 与呈现状态机
│   ├── direct/                        使用统一流事件的直连协议客户端
│   ├── conversation/                  分段输出协议、会话隔离和时间线
│   ├── control/                       Companion MCP 与安全中间件
│   ├── chat/                          直连模式的角色提示词、历史和记忆协调
│   ├── desktop/                       PyQt5 主窗口、气泡、输入、渲染和桥接
│   ├── memory/                        SQLite 记忆、好感度和时间线持久化
│   ├── tts/                           MiMo / GPT-SoVITS / VITS
│   ├── vision/                        视觉路由与屏幕观察协调
│   ├── watcher/                       截图线程和隐私授权锁
│   └── config/                        配置归一化和密钥解析
├── wizard/                            配置向导
├── config.example.json                唯一配置模板
├── design-system/                     UI 设计约束
└── tests/                             pytest / unittest 回归测试
```

## 开发与验证

```bash
python -m pytest -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

编辑桌宠 UI 前，请先阅读 `design-system/MASTER.md`、`design-system/pages/desktop.md` 和 `meapet/ui_theme.py`。仓库卫生、测试和分发规则见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；后端、线程和隐私契约见 [`docs/backend-and-control.md`](docs/backend-and-control.md)。

## 自定义角色与立绘

- 角色提示词：`meapet/chat/engine.py` 中的 `SYSTEM_PROMPT` 及其角色设定引用。
- 表情映射：`meapet/desktop/renderer.py` 中的 `EXPRESSION_MAP` 和 `MOOD_TO_EXPRESSION`。
- PNG 资源命名：`sprites/mea{outfit_id}{direction}_{expression}.png`。
- Live2D 模型目录：`config.json` 中的 `live2d.model_dir`。
- Live2D 可视视口：旧版 `live2d.window_mask` 椭圆参数定义了用于裁剪透明画布边缘的矩形外边界；将 `enabled` 设为 `false` 可以显示完整模型画布。配置中心支持拖动矩形或输入四边百分比来编辑该边界。还可以在模型两脚之间放置单独的 `live2d.placement_anchor` 锚点，使视口或模型缩放变化时，同一个完整画布坐标仍固定在屏幕上。保存后会立即应用两项设置，同时保持完整模型画布和交互区域不变。

## 常见问题

<details>
<summary>保存设置后，回复仍来自旧后端</summary>

保存会取消旧后端并立即创建新后端。如果仍看到旧内容，请检查时间线标签页：旧会话时间线会以只读方式保留，但迟到的事件不会进入新会话。可以在日志中搜索“New config applied”和后端初始化状态。
</details>

<details>
<summary>有文本气泡，但没有语音</summary>

请检查是否启用了 TTS、引擎健康检查是否通过、回复中的 `voice_language` 是否受支持，以及该语言是否配置了有效参考音频（GPT-SoVITS）。如果语言不受支持且未配置翻译，按设计会跳过语音并保留文本气泡。
</details>

<details>
<summary>Hermes、OpenClaw、Agent Link 或远程 MCP 无法连接</summary>

对于 Hermes，请使用固定的 `HERMES_DASHBOARD_SESSION_TOKEN` 运行 `hermes serve --host 127.0.0.1 --port 9119`，然后配置 `ws://127.0.0.1:9119/api/ws`；8642 端口属于单独的 HTTP API Server，并不是 Agent WebSocket 端点。当前 Hermes 的公开地址绑定使用短期登录票据，而非静态回环 Token，因此远程 Hermes 应通过 SSH 隧道连接其回环端口。远程 OpenClaw 和 Agent Link 应使用 WSS；远程明文 WS 需要明确选择启用。Agent Link 服务器必须返回匹配的 `control.ready`，并支持 [`docs/agent-link-v1.md`](docs/agent-link-v1.md) 所述的动态工具调用。Companion MCP 的监听 IP 必须是具体的本地接口 IP，允许的 IP 必须与 Agent 主机一致。LAN HTTP 也需要明确选择启用，并请检查 Windows 防火墙是否已放行端口。
</details>

<details>
<summary>Live2D 不渲染，或切换 PNG 后尺寸错误</summary>

请确认 `live2d.model_dir` 中包含 `.model3.json` 文件，并检查 `meapet_boot.log` / `meapet_fault.log`。可以设置 `MEAPET_FORCE_PNG=1` 测试 PNG 路径。运行时切换会重新同步窗口几何信息；如果问题仍然存在，请附上日志和显示缩放信息。
</details>

## 许可证

项目代码采用 MIT 许可证。Live2D Cubism Core 是 Live2D Inc. 的专有组件，使用时必须遵守其软件许可协议。角色、模型和语音资源的版权归各自作者所有。GPT-SoVITS、VITS 和 Ollama 等依赖遵循各自的许可证。
