# 🐱 MeaPet — 梅尔桌宠

一个会说话、会吐槽、会记住你的桌面宠物。

**立绘 + 语音 + AI 对话 + 记忆养成** 全都有，模型和图片都已打包，下载就能用。

---

## 🚀 打开就玩

**Windows 用户** → 双击 **`启动桌宠.bat`**

它会自动帮你搞定大部分事情：

| 阶段 | 自动做什么 | 需要你做什么 |
|------|-----------|------------|
| ① 装依赖 | 自动 pip install PyQt5 等 | 等几分钟 |
| ② 配置向导 | 弹出图形化设置窗口 | 选 AI 大脑、设语音 |
| ③ 启动桌宠 | 自动运行 pet.py | 🐱 开玩 |

> ⚠️ Python 需要自行安装（启动脚本检测不到时会提示下载）。  
> 推荐 [python.org](https://www.python.org/downloads/) 下载 Python 3.10~3.12，安装时勾选"Add Python to PATH"。

配置向导里只用选两样东西：

1. **AI 大脑** — 推荐选「Ollama」（免费，不需要任何 Key）
   - 向导可以帮你下载安装 Ollama + 拉取模型
   - 对话用 `qwen2.5:7b`，识图用 `minicpm-v`
2. **语音** — 开/关
   - 语音引擎使用 **GPT-SoVITS** 本地推理
   - 梅尔说日语（中文回复会自动翻译成日语后合成）
   - 如果没装语音环境，向导会教你装

> 不想用图形界面？复制 `config.example.json` 为 `config.json` 后手动编辑也一样。

---

## ✨ 它能做什么

| 功能 | 说明 |
|------|------|
| 💬 **聊天** | 双击桌宠打开输入框，AI 会回复你。支持 Ollama / DeepSeek 两种后端 |
| 🎤 **说话** | 文字回复会合成日语语音读出来（模型已打包，中文会自动翻译后合成） |
| 👀 **偷看屏幕** | 它会定时看看你在干嘛，偶尔吐槽一句 |
| 🖱️ **摸头** | 鼠标在头部左右拖拽，会有反应 |
| 🎭 **换表情** | 右键菜单切换心情，立绘会变 |
| 📝 **记性** | 它记得你和它说过的话，好感度会涨 |
| 📊 **养成面板** | 右键打开面板，看好感度、心情、回忆 |
| 😴 **待机** | 右键设待机，它会闭眼睡觉，鼠标穿透 |
| 🔄 **双渲染** | Live2D 动态模型 或 PNG 差分立绘，右键切换 |

### 配置

编辑 `config.json`（完整字段请参考 `config.example.json`）：

```json
{
  "llm": {
    "backend": "ollama",
    "host": "http://127.0.0.1:11434",
    "model": "qwen2.5:7b",
    "api_key": "",
    "api_base": "https://api.deepseek.com"
  },
  "vision": {
    "model": "minicpm-v"
  },
  "tts": {
    "engine": "gpt_sovits",
    "enabled": true,
    "translate_api_key": "your-api-key-here"
  },
  "character": {
    "name": "梅尔",
    "default_outfit": "01",
    "default_direction": "A"
  }
}
```

**环境变量**（可选）：

| 变量 | 用途 |
|------|------|
| `GSV_PYTHON` | GPT-SoVITS conda 环境的 python.exe 路径 |

### 运行

**🪟 Windows 用户** → 双击 `启动桌宠.bat`：
- 第一次运行会自动打开配置向导
- 配置完成后自动启动桌宠

**或者手动运行：**

第一次使用，先运行配置向导：
```bash
python setup_wizard.py
```

配置完成后，启动桌宠：
```bash
python pet.py
```

启动后，桌面宠物会出现在屏幕右下角。

## 🔑 API Key 清单

本项目在以下功能中需要使用 API Key（部分功能可选）：

| # | 配置项 / 环境变量 | 所属功能 | 是否需要 | 用途说明 |
|---|------------------|---------|---------|---------|
| 1 | `config.json` → `llm.api_key` | **AI 对话**（DeepSeek 后端） | 可选 | LLM 对话密钥。如果 `backend` 设为 `"deepseek"` 则需要；设为 `"ollama"` 则不需要 |
| 2 | `config.json` → `tts.translate_api_key` | **TTS 日语翻译** | 可选 | 将 AI 回复翻译成日语再合成语音时使用。如果 AI 后端本身就是 DeepSeek，则自动共用同一个 Key，无需额外填写 |
| 3 | `config.json` → `llm.api_base` | **AI 对话** | 可选 | API 地址。默认 `https://api.deepseek.com/v1`，可改为其他 OpenAI 兼容 API |

| 后端模式 | `config.json` 设置 | 需要什么 | 说明 |
|---------|-------------------|---------|------|
| **Ollama**（默认） | `"backend": "ollama"` | 不需要 API Key | 本地运行，免费，推荐 |
| **DeepSeek API** | `"backend": "deepseek"` | DeepSeek API Key | 需要 `api_key`，填入 `config.json` 或设置环境变量 |

> 👀 **关于屏幕识图**：偷看屏幕功能**始终使用 Ollama**（需要视觉模型如 minicpm-v），与 LLM 后端无关。
> 即使 AI 对话选了 DeepSeek，想要识图功能也需要安装 Ollama + 视觉模型。

### 快速判断

```
只用 Ollama（本地）+ 不开语音     → 不需要任何 API Key ✅
只用 Ollama（本地）+ 日语语音     → 只需要 translate_api_key（翻译用）
用 DeepSeek 对话 + 不开语音       → 只需要 DEEPSEEK_API_KEY
用 DeepSeek 对话 + 日语语音       → 只需要 DEEPSEEK_API_KEY（翻译自动共用）

👀 屏幕识图功能：无论选什么后端，都需要 Ollama + 视觉模型（minicpm-v）
```

## 🎮 操作指南

| 操作 | 效果 |
|------|------|
| 左键拖拽 | 移动桌宠 |
| 双击 | 打开聊天输入框 |
| 头部区域左右拖拽 | 触发摸头反应 |
| 右键 | 弹出菜单（切换表情、设置、退出） |
| `ESC` | 关闭输入框/状态面板 |

## 🧩 项目结构

```
mea-pet/
├── setup_wizard.py          # 🎯 一键配置向导（推荐先运行）
├── pet.py                   # 主程序入口
├── config.json              # 用户配置（不会提交到 Git）
├── config.example.json      # 配置模板
├── chat.py                  # LLM 对话引擎（Ollama / DeepSeek）
├── tts.py                   # GPT-SoVITS 语音合成
├── gsv_infer.py             # GPT-SoVITS 推理子进程
├── live2d_widget.py         # Live2D OpenGL 渲染
├── pet_live2d.py            # Live2D WebEngine 版
├── renderer.py              # PNG 差分立绘渲染
├── memory.py                # SQLite 记忆与养成系统
├── watcher.py               # 屏幕观察模块
├── status_panel.py          # 养成状态面板
├── chat_input.py            # Galgame 风格输入框
├── utils.py                 # 工具函数
├── precache_interactions.py # 预生成互动语音缓存
├── pre_render_voices.py     # 预合成语音
├── weight.json              # TTS 模型权重注册表
├── live2d/                  # Live2D 模型与 JS 资源
│   ├── index.html
│   ├── model/mea_live2d/    # 默认 Live2D 模型
│   └── js/                  # Cubism SDK 与渲染库
├── models/                  # TTS 模型权重
│   ├── GPT_weights/         # GPT 模型（mea_pro-e50.ckpt）
│   └── SoVITS_weights/      # SoVITS 模型（mea_pro_e24_s13704.pth）
├── GPT-Sovits/              # TTS 参考音频（日语，normal/clam/soft 三种情绪）
├── sprites/                 # PNG 差分立绘（已包含梅尔全套）
└── .gitignore
```

## 🔧 自定义

### 更换 Live2D 模型

1. 将模型文件放入 `live2d/model/` 目录
2. 更新 `config.json` 中的 `live2d.model_dir` 路径
3. 重启应用

### 修改角色设定

编辑 `chat.py` 中的 `SYSTEM_PROMPT` 即可修改角色的性格、说话风格和行为规则。

### 添加新情绪/表情

在 `renderer.py` 的 `EXPRESSION_MAP` 和 `MOOD_TO_EXPRESSION` 中添加映射。

## 📝 许可说明

> **注意**：本项目使用 **Live2D Cubism Core** 进行 WebGL 渲染，该 SDK 属于 [Live2D Inc.](https://www.live2d.com/) 的专有软件。
> 使用 Live2D Cubism SDK 需要遵守 Live2D 的 [软件许可协议](https://www.live2d.com/legal/license/)。

- 项目代码：MIT License
- Live2D 模型资源：版权归原作者所有
- GPT-SoVITS：遵循其开源许可证

## 🙏 致谢

- [Live2D Cubism](https://www.live2d.com/) - Live2D 渲染引擎
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) - 语音合成
- [Ollama](https://ollama.ai/) - 本地 LLM 运行
- [DeepSeek](https://deepseek.com/) - 对话 API
