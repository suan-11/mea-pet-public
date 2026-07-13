# MeaPet — 梅尔桌宠

一个 Windows-first、同时支持 Linux 的 PyQt5 透明桌宠。它把角色立绘、AI 对话、语音合成、屏幕识图、SQLite 记忆与好感度放在同一个桌面前端中，并支持 Live2D / PNG 双渲染。

MeaPet 的边界很明确：角色表现、气泡、TTS、截图授权和本地状态由 MeaPet 负责；选择 Agent 作为回复后端时，模型、长期记忆和内部工具由 Agent 负责。

## 当前能力

| 能力 | 说明 |
|------|------|
| 回复后端 | 模型服务商直连或 Agent，二选一运行，不自动切换兜底 |
| 直连协议 | Ollama Chat、OpenAI Chat Completions、OpenAI Responses、Anthropic Messages |
| Agent | Hermes API Server、OpenClaw Gateway WebSocket v4 |
| 呈现 | 无语音时流式增长文字；有语音时等待音频就绪，再同步显示气泡并播放 |
| 多段回复 | 每段独立气泡、情绪、语音文本、语言和 TTS 风格；可从时间线查看本轮全文 |
| 语音 | MiMo 云端 TTS、本地 GPT-SoVITS、本地 VITS；GPT-SoVITS 支持每种语言一条固定参考音频 |
| 视觉 | 关闭、继承主回复模型、独立视觉模型中转三种链路 |
| 反向控制 | 受限 Companion MCP：说话、表情/动作、只读状态、逐次确认截图 |
| 本地数据 | SQLite 记忆、好感度、按后端与 Agent 会话隔离的最近对话时间线 |
| 渲染 | Live2D 动态模型与 PNG 差分立绘，可在运行时切换 |

## 快速开始

需要 Python 3.10–3.12；项目默认版本为 3.12。

### Windows

双击 `启动桌宠.bat`。脚本会优先复用 `.venv`，在需要时创建环境、安装基础依赖，并在首次运行时打开配置向导。

也可以手动运行：

```bat
python setup_wizard.py
python pet.py
```

### Linux

```bash
pip install -r linux_requirements.txt
python setup_wizard.py
QT_QPA_PLATFORM=xcb python pet.py
```

`live2d-py` 是可选依赖；不可用时会回退 PNG。预编译包可从 [EasyLive2D/live2d-py](https://github.com/EasyLive2D/live2d-py) 获取。

配置向导也可从桌宠右键菜单的「打开配置页…」随时重开。保存后新后端会直接生效；正在生成的旧回复会取消，不会同时运行两个后端。

## 两类回复后端

### 模型服务商直连

直连模式由 MeaPet 维护角色提示词、近期上下文、SQLite 记忆和输出约束。OpenAI-compatible 接口属于模型服务商直连，不作为 Agent。

| 协议值 | 请求形式 | 常见用途 |
|--------|----------|----------|
| `ollama_chat` | `POST /api/chat`，NDJSON 流 | 本机 Ollama |
| `openai_chat` | `POST /v1/chat/completions`，SSE | DeepSeek、MiMo、自建兼容服务 |
| `openai_responses` | `POST /v1/responses`，SSE | Responses-compatible 服务 |
| `anthropic_messages` | `POST /v1/messages`，SSE | Anthropic Messages-compatible 服务 |

向导内置 Ollama、DeepSeek、MiMo 的常用值，也允许手工填写实际协议、API 地址、模型 ID 和 API Key。接口解析按所选协议执行，不会仅凭服务商名字猜测响应格式。

### Agent

Agent 模式只把 MeaPet 当成桌面前端：

- MeaPet 调用 Agent 生成回复，并把前端支持的情绪、动作、TTS 语言和角色状态摘要一并提供。
- Agent 继续使用自己的模型、记忆和内部工具；MeaPet 不要求 Agent 实现额外的“查询记忆”能力。
- 内部工具名称和原始参数不直接显示在角色气泡中；安全的开始、完成、失败状态会进入时间线，原始诊断信息只写日志。
- Hermes 通过 OpenAI Chat Completions + SSE 接入；OpenClaw 通过官方 Gateway WebSocket v4 接入。
- Agent 返回内容遵循 MeaPet 分段输出约束。字段严重缺失时会在隔离请求中尝试一次纯格式修复；仍不可解析时按安全错误处理。

当前会话的 `session_id` 与长期作用域 `session_key` 会保存在本机配置中。重启默认恢复当前会话；「清除记忆」在 Agent 模式下会明确提醒它将结束当前 Agent 会话，并创建新标识。旧时间线仍可只读查看。

## 回复、气泡与 TTS 时序

模型或 Agent 的每个回复分段都包含：

- `display_text`：气泡显示文本
- `voice_text`：交给 TTS 的文本
- `voice_language`：TTS 语言
- `mood`：角色表情；前端不支持的值会规范为 `neutral`
- `tts_style`：传给支持该能力的 TTS 引擎的语气描述

呈现规则如下：

1. 未启用 TTS：收到流式文本增量后立即创建气泡并持续增长；完成后定稿。
2. 启用 TTS：先收齐分段并生成音频，音频就绪后才同时显示气泡与播放语音。
3. 气泡持续时间取配置的最小时长与“音频时长 + 500ms”的较大值。
4. TTS 启动失败、语言不支持或音频生成失败时，不阻塞回复，立即回退为文字气泡并记录日志。
5. 多段回复不合并，按顺序生成和播放；点击对应气泡或打开「对话时间线…」可查看本轮完整回复。

直连与 Agent 共用这套呈现层，因此切换后端不会改变气泡和语音时序。

## 多语言语音

GPT-SoVITS 可在 `tts.reference_audios` 中为每种语言配置一条固定参考音频。每项必须表明语言，路径可为项目相对路径或绝对路径，参考文本可留空：

```json
{
  "tts": {
    "engine": "gpt_sovits",
    "enabled": true,
    "reference_audios": {
      "ja": {"path": "./voices/mea-ja.wav", "text": ""},
      "zh": {"path": "./voices/mea-zh.wav", "text": ""},
      "en": {"path": "./voices/mea-en.wav", "text": ""}
    }
  }
}
```

兼容字段 `gsv_ref_wav` + `gsv_ref_lang` 会只读迁移为对应语言的一条参考音频。固定 WAV 旁的同名 `.txt` 可作为参考文本。

翻译 API 不是模型后端的失败兜底。仅当回复语言不受当前 TTS 支持、用户显式开启翻译且翻译 API 已配置时，才翻译到受支持语言后合成；否则跳过该段语音并保留原文气泡。

## 屏幕识图

视觉链路在配置向导中有三种模式：

| 模式 | 行为 |
|------|------|
| `disabled` | 关闭截屏与识图 |
| `inherit` | 主回复模型支持图片时，把截图与用户请求放进同一次多模态请求 |
| `relay` | 先由独立视觉模型生成摘要，再把摘要交给回复后端 |

`inherit` 适合本身支持图片的直连模型或 Agent；`relay` 可在直连模式下选择 Ollama 或 MiMo 作为视觉模型。Agent 模式应继承 Agent 自身的视觉能力，否则关闭识图，不在 MeaPet 侧另接一个视觉模型。翻译 API 不参与视觉链路。

隐私规则是强制的：屏幕观察默认关闭；每次截图都要在本机确认，授权仅本次有效。确认时默认全屏，也可以改为框定区域或指定应用。截图只在内存中传递，不由新链路写入磁盘；云端识图还必须显式允许 `watcher.allow_cloud`。

## Companion MCP：Agent 主动控制 MeaPet

Agent 模式可选开启标准 MCP Streamable HTTP 端点：

```text
http(s)://<listen_host>:<port>/mcp
```

只暴露四个工具：

| 工具 | 能力 |
|------|------|
| `meapet.say` | 排队一个或多个完整回复分段；不会抢占正在等待的用户回复 |
| `meapet.express` | 请求前端明确支持的情绪或动作，不做隐式映射 |
| `meapet.get_state` | 读取渲染、TTS 能力、角色状态与好感度等级摘要；不返回路径、密钥、记忆或聊天全文 |
| `meapet.capture_screen` | 请求一次截图；全屏、区域或应用均需本机逐次确认，结果不落盘 |

安全约束：

- 默认只监听 `127.0.0.1`，且只允许一个明确的 Agent IP。
- 每个请求必须携带 Bearer Token；可在向导或右键菜单查看、复制、轮换。轮换后旧 Token 立即失效。
- 局域网监听默认要求 HTTPS。没有内部证书时，可以在可信内网显式允许明文 HTTP；界面会持续显示风险提示。配置客户端 CA 后还会要求 Agent 提供由该 CA 签发的客户端证书（mTLS）。
- 不会自动修改 Windows 防火墙；远程访问需自行放行所选端口。
- 服务还校验来源 IP、Host、Origin、请求大小与速率。

## 配置

唯一用户配置是项目根目录的 `config.json`，唯一模板是 `config.example.json`。不要编辑或提交真实密钥；`config.json` 已被 gitignore。

最小示例：

```json
{
  "llm": {
    "mode": "direct",
    "backend": "ollama",
    "direct": {
      "provider": "ollama",
      "protocol": "ollama_chat",
      "host": "http://127.0.0.1:11434",
      "api_base": "",
      "model": "qwen3.5:4b",
      "api_key": "",
      "temperature": 0.7,
      "max_tokens": 512
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

完整字段、Agent 与 MCP 示例见 `config.example.json` 和 `docs/backend-and-control.md`。

### 密钥与环境变量

配置密钥的优先级为“环境变量 > `config.json` 明文”。配置值也支持 `$ENV_VAR` 或 `${ENV_VAR}` 占位符。

| 环境变量 | 用途 |
|----------|------|
| `DEEPSEEK_API_KEY` | DeepSeek 直连 |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo 对话、识图或 TTS |
| `MEAPET_API_KEY` | 自定义直连接口兜底 |
| `HERMES_API_SERVER_KEY` / `MEAPET_AGENT_TOKEN` | Hermes / OpenClaw Agent 认证 |
| `MEAPET_CONTROL_TOKEN` | Companion MCP Bearer Token |
| `TRANSLATE_API_KEY` | 显式启用的 TTS 翻译 |
| `GSV_PYTHON` | GPT-SoVITS 环境的 `python.exe` |
| `MEAPET_FORCE_PNG` | 非空真值时强制 PNG |
| `MEAPET_DEBUG=1` | 允许载荷级调试日志；默认不要开启 |

如果真实 Key 曾进入仓库或公开日志，应立即在服务商侧轮换，而不只是删除本地文本。

## 本地缓存与隐私

- 默认保留最近 5 轮时间线，可在配置页设为 0–100。
- 时间线按“直连服务商/Agent 类型 + Agent 会话”隔离，切换后端不会串线。
- 直连模式的 SQLite 记忆与 Agent 自有记忆是两套边界；MeaPet 不复制 Agent 的长期记忆。
- 新配置应用、会话切换和 Token 轮换都会使旧异步结果失效，迟到的回复、TTS 或截图不会进入新会话。
- 运行数据位于 `mea_memory.db`、`logs/`、`audio_cache/`、`voice_cache/`；均不应进入发布物。
- 日志默认只记录长度、状态和脱敏后的错误；完整载荷仅在显式设置 `MEAPET_DEBUG=1` 时输出。

## 操作

| 操作 | 效果 |
|------|------|
| 左键拖拽 | 移动桌宠 |
| 双击 | 打开聊天输入框 |
| 头部区域左右拖拽 | 触发摸头反应 |
| 右键 | 打开配置、时间线、状态、渲染、待机和退出菜单 |
| 点击回复气泡 | 打开该轮完整回复（仍在最近缓存中时） |
| `Esc` | 关闭输入框或面板 |

关闭主窗只会隐藏；真正退出请使用托盘菜单。

## 项目结构

```text
mea-pet/
├── pet.py / meapet/__main__.py       启动入口
├── meapet/
│   ├── agent/                         Hermes / OpenClaw 与呈现状态机
│   ├── direct/                        四种直连协议与统一流事件
│   ├── conversation/                  分段输出协议、会话隔离、时间线
│   ├── control/                       Companion MCP 与安全中间件
│   ├── chat/                          直连角色提示词、历史和记忆协调
│   ├── desktop/                       PyQt5 主窗、气泡、输入、渲染与桥接
│   ├── memory/                        SQLite 记忆、好感度、时间线持久化
│   ├── tts/                           MiMo / GPT-SoVITS / VITS
│   ├── vision/                        视觉路由与截图观察协调
│   ├── watcher/                       截图线程与隐私门闩
│   └── config/                        配置规范化与密钥解析
├── wizard/                            配置中心
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

改桌宠 UI 前请先阅读 `design-system/MASTER.md`、`design-system/pages/desktop.md` 与 `meapet/ui_theme.py`。线程模型、窗口生命周期、隐私和发布约束见 `AGENTS.md`。

## 自定义角色与立绘

- 角色提示词：`meapet/chat/engine.py` 的 `SYSTEM_PROMPT` 及其引用的 persona。
- 表情映射：`meapet/desktop/renderer.py` 的 `EXPRESSION_MAP` 与 `MOOD_TO_EXPRESSION`。
- PNG 资源命名：`sprites/mea{服装编号}{朝向}_{表情}.png`。
- Live2D 模型目录：`config.json` 的 `live2d.model_dir`。

## 常见问题

<details>
<summary>保存配置后回复仍来自旧后端</summary>

保存会取消旧生成并重建当前唯一后端。如果仍看到旧内容，请检查时间线标签；旧会话时间线会保留只读，但迟到事件不会进入新会话。日志中搜索 `新配置已应用` 和后端初始化状态。
</details>

<details>
<summary>有文字但没有语音</summary>

检查 TTS 是否启用、引擎健康检查、回复的 `voice_language` 是否受支持，以及该语言是否有有效参考音频。语言不支持且未显式配置翻译时，会按设计保留文字并跳过语音。
</details>

<details>
<summary>OpenClaw 或远程 MCP 无法连接</summary>

远程 OpenClaw 应使用 WSS；明文 WS 需要显式允许。Companion MCP 的监听 IP 必须是本机具体接口 IP，允许 IP 必须与 Agent 主机一致；局域网 HTTP 还需显式允许，并检查 Windows 防火墙端口。
</details>

<details>
<summary>Live2D 不显示或切换 PNG 后尺寸异常</summary>

确认 `live2d.model_dir` 内存在 `.model3.json`，并查看 `meapet_boot.log` / `meapet_fault.log`。可设置 `MEAPET_FORCE_PNG=1` 验证 PNG 路径；运行时切换会重新同步窗口几何，若仍异常请附带日志和显示缩放比例。
</details>

## 许可

项目代码采用 MIT License。Live2D Cubism Core 属于 Live2D Inc. 的专有组件，使用时还需遵守其软件许可协议；角色、模型和语音资源版权归各自作者所有。GPT-SoVITS、VITS、Ollama 等依赖遵循其各自许可证。
