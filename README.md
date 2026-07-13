# 🐱 MeaPet — 梅尔桌宠

一个会说话、会吐槽、会记住你的桌面宠物。

**立绘（Live2D + PNG 差分双引擎）+ 语音合成 + AI 对话 + 记忆养成** 全都有；界面与角色资源随项目提供，大型模型和离线字典按需下载。

### 下载策略

- **启动脚本会装基础依赖**：首次无 `.venv` 或依赖缺失时，`启动桌宠.bat` 会创建虚拟环境并 `pip/uv install -r linux_requirements.txt`（PyQt5 等）。
- **不默认安装 uv**：本机没有 uv 时，脚本优先用系统 Python 建 `.venv`；只有设置 `MEAPET_ALLOW_DOWNLOAD=1` 才会尝试联网安装 uv。
- **大件仍按需**：Ollama / 模型 / 词典 / TTS 重依赖在配置向导里点「安装」并二次确认后再下载；`config.json` 可设 `tts.auto_install_deps: true`。
- **屏幕观察默认关闭**（隐私）：右键菜单手动开启；云端识图需 `watcher.allow_cloud: true`。
- **Git LFS**：TTS 大模型权重使用 LFS 指针，克隆后需 `git lfs pull`（需本机安装 git-lfs）。

---

## 🚀 打开就玩

**Windows 用户** → 双击 **`启动桌宠.bat`**

它会自动帮你搞定大部分事情：

| 阶段 | 自动做什么 | 需要你做什么 |
|------|-----------|------------|
| ① 环境 | 用 uv 或系统 Python 创建 `.venv`，安装基础依赖 | 等几分钟（首次） |
| ② 配置向导 | 无 `config.json` 时弹出设置窗口 | 选 AI 大脑、设语音 |
| ③ 启动桌宠 | 向导完成后自动运行 `pet.py` | 🐱 开玩 |

> 推荐安装 [uv](https://docs.astral.sh/uv/) 或 [Python 3.10~3.12](https://www.python.org/downloads/)（勾选 "Add Python to PATH"）。  
> 有 uv 时可不预装 Python（uv 可按需拉取解释器）；没有 uv 则需要本机 Python。

> pip / uv 默认用**清华镜像**，失败会回落官方源。

配置向导里只用选两样东西：

1. **AI 大脑** — 推荐选「Ollama」（免费，不需要任何 Key）
   - 向导可以帮你下载安装 Ollama + 拉取模型
   - 对话+识图用 `qwen3.5:4b`（多模态）
2. **语音** — 开/关
   - 语音引擎使用 **GPT-SoVITS** 本地推理
   - 梅尔说日语（中文回复会自动翻译成日语后合成）
   - 如果没装语音环境，向导会教你装

> 不想用图形界面？复制 `config.example.json` 为 `config.json` 后手动编辑也一样。

### 配置向导随时可以重开

桌宠右键菜单 → **`⚙ 再次配置`** → 即可重新弹出配置向导，改后端、改语音、重新检测环境。

---

## ✨ 它能做什么

| 功能 | 说明 |
|------|------|
| 💬 **聊天** | 双击桌宠打开输入框，AI 会回复你。支持 Ollama / DeepSeek / MiMo V2.5 三种后端 |
| 🎤 **说话** | 支持 **MiMo 云端 TTS** / 本地 VITS / GPT-SoVITS；云端无需本地语音环境 |
| 👀 **偷看屏幕** | 它会定时看看你在干嘛，偶尔吐槽一句 |
| 🖱️ **摸头** | 鼠标在头部左右拖拽，会有反应 |
| 🎭 **换表情** | 右键菜单切换心情，立绘会变 |
| 📝 **记性** | 它记得你和它说过的话，好感度会涨 |
| 📊 **养成面板** | 右键打开面板，看好感度、心情、回忆 |
| 😴 **待机** | 右键设待机，它会闭眼睡觉，鼠标穿透 |
| 🔄 **双渲染** | Live2D 动态模型 或 PNG 差分立绘，右键切换 |

### 屏幕观察的聪明之处

观察模块不是简单的截图→回复，而是**三层决策**：

1. **截图** → 截取当前屏幕
2. **场景摘要** → 视觉 AI 用一句话描述屏幕内容（不超过 30 字）
3. **策略评估** → 考虑冷落时长（>10 分钟主动搭话，>30 分钟表达在意，<3 分钟保持沉默）+ 屏幕内容，决定：**说/不说**、什么**策略**（毒舌吐槽/关心进度/轻松陪聊/好奇询问）、是否需**搜索**补充信息

---

## 📋 配置

编辑 `config.json`（完整字段请参考 `config.example.json`）：

```json
{
  "llm": {
    "backend": "ollama",
    "host": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",
    "api_key": "",
    "api_base": "https://api.deepseek.com"
    <!-- ollama: 本地聊天+识图; deepseek: 在线聊天; mimo: 在线聊天+识图（不需要Ollama） -->
  },
  "vision": {
    "model": "qwen3.5:4b"
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
| `DEEPSEEK_API_KEY` | DeepSeek 对话 / 翻译（**优先于** config.json 明文） |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | 小米 MiMo 对话 / 识图 / TTS（优先于 config） |
| `MEAPET_API_KEY` | 通用兜底密钥 |
| `TRANSLATE_API_KEY` | 仅 TTS 日语翻译（可选） |

### 密钥怎么存更安全？

**推荐：`config.json` 里 `api_key` 留空，用环境变量注入。**

优先级：**环境变量 > config.json 明文**。也支持占位 `"api_key": "$ENV"` 或 `"${DEEPSEEK_API_KEY}"`。

```bash
# Linux / macOS / WSL
export DEEPSEEK_API_KEY="sk-xxxx"
python pet.py
```

```bat
REM Windows
set DEEPSEEK_API_KEY=sk-xxxx
python pet.py
```

> 若曾经把真实 Key 写进 `config.json`：请到服务商控制台**轮换作废**旧 Key，并清空文件中的 `api_key`（该文件已 gitignore，但仍在你本机磁盘上）。


### 运行

**🪟 Windows 用户** → 双击 `启动桌宠.bat`：
- 无 `.venv` 时用 **uv**（或系统 Python）创建虚拟环境并安装基础依赖
- 第一次运行（无 `config.json`）会打开配置向导，完成后**自动**启动桌宠
- 环境就绪后再次双击直接开玩

**或者手动运行：**

第一次使用，先运行配置向导：
```bash
python setup_wizard.py
```

配置完成后，直接启动桌宠：
```bash
python pet.py
```



Linux部分


依赖安装
```bash
linux_requirements.txt
# live2d-py在https://github.com/EasyLive2D/live2d-py，建议下载预编译好的
```

创建配置文件
```bash
python setup_wizard.py
```

启动桌宠
```bash
QT_QPA_PLATFORM=xcb python pet.py
```

如果你使用niri
```KDL
window-rule {
     match title="mea-pet"
     open-floating true
     focus-ring { }
     border { }
 }
```

如果你使用Fcitx5
```
QT_PLUGIN_PATH=/usr/lib/qt/plugins
# 实际位置视优先级决定
```


启动后，桌面宠物会出现在屏幕右下角。（Linux可能不会）

---

## 🔑 API Key 清单

本项目在以下功能中可能需要 API Key（部分功能可选）：

| # | 配置项 / 环境变量 | 所属功能 | 是否需要 | 用途说明 |
|---|------------------|---------|---------|---------|
| 1 | `config.json` → `llm.api_key` | **AI 对话**（DeepSeek 后端） | 可选 | LLM 对话密钥。如果 `backend` 设为 `"deepseek"` 则需要；设为 `"ollama"` 则不需要 |
| 2 | `config.json` → `tts.translate_api_key` | **TTS 日语翻译** | 可选 | 将 AI 回复翻译成日语再合成语音时使用。如果 AI 后端本身就是 DeepSeek，则自动共用同一个 Key，无需额外填写 |
| 3 | `config.json` → `llm.api_base` | **AI 对话** | 可选 | API 地址。默认 `https://api.deepseek.com/v1`，MiMo 后端默认 `https://api.deepinfra.com/v1` |

| 后端模式 | `config.json` 设置 | 需要什么 | 说明 |
|---------|-------------------|---------|------|
| **Ollama**（默认） | `"backend": "ollama"` | 不需要 API Key | 本地运行，免费，推荐 |
| **DeepSeek API** | `"backend": "deepseek"` | DeepSeek API Key | 需要 `api_key`，填入 `config.json` 或设置环境变量 |
| **MiMo V2.5 API** | `"backend": "mimo"` | 第三方平台 API Key | 小米多模态模型，聊天+识图一体，不需要 Ollama |

> 👀 **关于屏幕识图**：可在 `config.json` 的 **`vision`** 里**单独配置**，不必与对话 `llm` 相同。未写 `vision.backend` 时回退到 `llm.backend`。  
> - 对话 MiMo、识图 Ollama 本地：`vision.backend=ollama` + `vision.model=qwen3.5:4b`（或其它视觉/多模态模型）  
> - 对话 Ollama、识图 MiMo 云端：`vision.backend=mimo` + `watcher.allow_cloud=true` + Key  
> - DeepSeek 对话时，识图通常仍用 Ollama 多模态模型

### 快速判断

```
只用 Ollama（本地）+ 不开语音     → 不需要任何 API Key ✅
只用 Ollama（本地）+ 日语语音     → 只需要 translate_api_key（翻译用）
用 DeepSeek 对话 + 不开语音       → 只需要 DEEPSEEK_API_KEY
用 DeepSeek 对话 + 日语语音       → 只需要 DEEPSEEK_API_KEY（翻译自动共用）
用 MiMo 对话 + MiMo 云端 TTS      → 只需要小米 MiMo API Key（聊天+识图+语音同一 Key，无需 Ollama / 本地 TTS）

👀 屏幕识图功能：Ollama 和 MiMo 后端自带识图；DeepSeek 需要额外装 Ollama + 多模态模型（qwen3.5:4b）
```

---

## 🎮 操作指南

| 操作 | 效果 |
|------|------|
| 左键拖拽 | 移动桌宠 |
| 双击 | 打开聊天输入框（Galgame 风格） |
| 头部区域左右拖拽 | 触发摸头反应 |
| 右键 | 弹出菜单（切换表情、⚙ 再次配置、待机、渲染切换、养成面板、退出） |
| `ESC` | 关闭输入框 / 状态面板 |

---

## 🧩 项目结构

```
mea-pet/
├── pet.py                   # 桌宠入口（兼容）→ meapet.desktop.app
├── setup_wizard.py          # 配置向导入口（兼容）→ wizard/
├── meapet/                  # 主程序包
│   ├── config/              # config 加载 / 密钥
│   ├── chat/                # LLM 对话
│   ├── memory/              # SQLite 养成记忆
│   ├── tts/                 # 语音合成（common + engines）
│   ├── desktop/             # 桌宠 UI / mixin / workers
│   ├── watcher/             # 屏幕观察
│   ├── tools/               # gsv_infer / vits_infer / 预缓存脚本
│   ├── utils.py
│   └── paths.py             # PROJECT_ROOT
├── wizard/                  # 配置向导 UI 分包
├── config.example.json      # 唯一配置模板
├── tests/                   # 单元测试
├── models/ · live2d/ · sprites/ · GPT-Sovits/ · vits_*
└── 启动桌宠.bat
```

启动：
```bash
python pet.py
# 或
python -m meapet
python setup_wizard.py
```



---

## 🎨 表情与立绘

### 18 种表情映射

| 编号 | 名字 | 说明 |
|------|------|------|
| 001 | default | 默认表情（含泪光特色） |
| 002 | melancholy | 忧郁 / 略带悲伤 |
| 011 | content | 满足 / 眯眼微笑 |
| 012 | peaceful | 安宁 / 幸福微笑 |
| 101 | curious | 好奇 / 内省 |
| 102 | innocent | 天真 / 微羞好奇 |
| 171 | teary | 泪眼 / 失望 / 悲伤 |
| 181 | shy_a | 害羞 / 别扭 A |
| 182 | shy_b | 害羞 / 别扭 B |
| 191 | intrigued | 感兴趣 / 挑眉 |
| 192 | surprised | 惊讶 / 好奇瞪眼 |
| 301 | sad_a | 悲伤 / 梦幻落寞 |
| 302 | sad_b | 悲伤 / 忧郁 B |
| 601 | gentle | 温柔好奇 / 微担忧 |
| 611 | annoyed_a | 不耐烦 / 烦躁 A |
| 612 | annoyed_b | 不耐烦 / 烦躁 B |
| 701 | wistful | 沉思 / 温柔悲伤 |
| 702 | pensive | 忧愁 / 更深沉思 |

横跨 **5 套服装**（01/02/11/12）、**2 个朝向**（A/B）、部分表情带 `_a` 眨眼变体，总计 **180+ 张 PNG 差分立绘**，全部已打包。

### 双渲染引擎切换

- **Live2D**（默认）：动态模型，呼吸/眨眼动画，WebGL 硬件加速
- **PNG 差分**：无需 GPU，高性能，覆盖所有表情

右键菜单可一键切换渲染模式。

---

## 🛠️ 技术细节

### Python / 虚拟环境（Windows，推荐 uv）

启动脚本 `启动桌宠.bat` 用 **[uv](https://docs.astral.sh/uv/)** 管理项目环境：

1. **项目 `.venv`** 存在则直接用
2. 不存在时：`uv venv --python 3.12 .venv`（Python 版本见 `.python-version`，uv 可自动下载）
3. 依赖：`uv pip install -r linux_requirements.txt`（默认清华镜像，失败回落官方源）
4. 可选安装 `live2d-py`（失败则 PNG 模式）
5. 本机没有 uv 时：设置 `MEAPET_ALLOW_DOWNLOAD=1` 后重跑，脚本会尝试安装 uv

手动等价命令：

```bat
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install -r linux_requirements.txt --python .venv\Scripts\python.exe
.venv\Scripts\python.exe setup_wizard.py
.venv\Scripts\python.exe pet.py
```

### MiMo 云端 TTS（推荐，无需本地语音环境）

- `tts.engine: "mimo"`，调用小米 [MiMo Speech Synthesis](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/speech-synthesis-v2.5)
- 默认模型 `mimo-v2.5-tts`，内置音色如 `冰糖` / `茉莉` / `Chloe`
- **声音克隆**：`tts.voice_clone: true` 或 `model: "mimo-v2.5-tts-voiceclone"`，会把 `voice_cache/`（或 `tts.clone_ref` 指定 wav）以 `data:audio/wav;base64,...` 发给 API
- 与对话共用 `llm.api_key` / `llm.api_base`（也可单独写 `tts.api_key`）
- 默认**中文直出**（`translate_to_jp: false`，`voice_lang: "zh"`）；若仍想先译日语再合成可改配置
- **不需要** PyTorch / VITS / GPT-SoVITS / `git lfs pull`

### VITS 语音引擎

- 模型基于 **VITS-fast-fine-tuning** 训练，内置日语词典（`dic/`），首次使用免下载
- 对话回复自动翻译为日语后合成（通过 DeepSeek API 翻译）
- 配置向导优先使用已有 Python（已有 PyTorch 则直接复用），否则创建 venv 从清华镜像安装
- Python 3.12+ 兼容：固定 `setuptools==69.5.1` + `numpy<2` 解决 C 扩展兼容问题

### GPT-SoVITS 引擎

- 通过子进程调用独立整合包，不污染主进程依赖
- 支持多参考音频目录（`clam`/`normal`/`soft`）
- 可用 `tts.gsv_ref_wav` 指定固定 WAV，并用 `tts.gsv_ref_lang` 标明参考语言（`jp` / `zh` / `en`）；留空路径时仍按情绪自动选择
- 固定 WAV 旁的同名 `.txt` 会作为参考文本，例如 `custom.wav` 搭配 `custom.txt`
- 高还原力，适合需要丰富情感的语音场景

### 国内加速

- 所有 `pip install` 默认用 `pypi.tuna.tsinghua.edu.cn` 镜像
- PyTorch 用 `mirrors.tuna.tsinghua.edu.cn/pytorch/whl/cpu`
- 镜像挂了自动回落官方源

### 离线安装

把 PyTorch、PyQt5 等难下载的 `.whl` 文件放进 `wheels\` 目录，配置向导会自动使用本地文件，跳过网络下载。

---

## 🔧 自定义

### 更换 Live2D 模型

1. 将模型文件放入 `live2d/model/` 目录
2. 更新 `config.json` 中的 `live2d.model_dir` 路径
3. 重启应用

### 修改角色设定

编辑 `chat.py` 中的 `SYSTEM_PROMPT` 即可修改角色的性格、说话风格和行为规则。

### 添加新情绪 / 表情

在 `renderer.py` 的 `EXPRESSION_MAP` 和 `MOOD_TO_EXPRESSION` 中添加映射，然后在 `sprites/` 中放置对应编号的 PNG 文件。

### 添加新服装

按命名规则在 `sprites/` 中放置文件：`mea{服装编号}{朝向}_{表情}.png`，并在 `config.json` 的 `character.default_outfit` 中设置默认服装。

---

## 🔍 常见问题

<details>
<summary><b>双击启动桌宠.bat 后窗口一闪而过</b></summary>

打开命令提示符，手动运行 `启动桌宠.bat` 查看错误信息。常见原因：
- 网络问题导致依赖安装失败（重试或手动配置镜像）
- 未安装 uv 且本机没有 Python 3.10+（安装其一后重试）
- 也可手动：`.venv\Scripts\python.exe -u pet.py` 看完整报错；日志见 `meapet_boot.log`
</details>

<details>
<summary><b>Ollama 连接失败</b></summary>

1. 确认 Ollama 已启动（任务栏有 Ollama 图标）
2. 确认 `config.json` 中 `llm.host` 为 `http://127.0.0.1:11434`
3. 运行 `ollama list` 检查模型是否已拉取
</details>

<details>
<summary><b>语音合成没有声音</b></summary>

1. 确认 `config.json` 中 `tts.enabled` 为 `true`
2. VITS 引擎：检查 `vits_models/` 下是否有 `G_latest.pth`
3. GPT-SoVITS 引擎：检查模型路径是否正确
4. 用 `python vits_infer.py --text "测试" --output test.wav` 单独测试
</details>

<details>
<summary><b>屏幕观察不吐槽</b></summary>

1. 确认已安装 Ollama 并拉取了多模态模型（`qwen3.5:4b`）
2. 观察模块使用冷落感知：最近 3 分钟内说过话则保持沉默
3. 检查 `config.json` 中 `vision.model` 是否正确
</details>

<details>
<summary><b>Live2D 不显示</b></summary>

1. 确认 `live2d/model/mea_live2d/` 下有 `.model3.json` 文件
2. 确认 `config.json` 中 `live2d.enabled` 为 `true`
3. 尝试右键切换为 PNG 渲染模式
</details>

<details>
<summary><b>Windows 中文乱码</b></summary>

启动脚本已自动设置 `chcp 65001`（UTF-8）和 `PYTHONIOENCODING=utf-8`。如果仍有乱码，检查系统区域设置是否支持 UTF-8。
</details>

---

## ⚠️ 已知限制

- Live2D 渲染需要支持 OpenGL 的显卡
- GPT-SoVITS 引擎需要单独下载整合包（~2GB），VITS 引擎已内置
- 屏幕观察：Ollama 后端需额外下载多模态模型（如 qwen3.5:4b）；MiMo 后端云端自带识图能力，无需下载
- 首次创建 `.venv` / 安装依赖需要联网（默认清华镜像，失败回落官方源）

---

## 📝 许可说明

> **注意**：本项目使用 **Live2D Cubism Core** 进行 WebGL 渲染，该 SDK 属于 [Live2D Inc.](https://www.live2d.com/) 的专有软件。
> 使用 Live2D Cubism SDK 需要遵守 Live2D 的 [软件许可协议](https://www.live2d.com/legal/license/)。

- 项目代码：MIT License
- Live2D 模型资源：版权归原作者所有
- GPT-SoVITS：遵循其开源许可证
- VITS：遵循其开源许可证

---

## 🙏 致谢

- [Live2D Cubism](https://www.live2d.com/) - Live2D 渲染引擎
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) - 语音合成
- [Ollama](https://ollama.ai/) - 本地 LLM 运行
- [DeepSeek](https://deepseek.com/) - 对话 API
- [MiMo V2.5](https://deepinfra.ai/XiaomiMiMo/MiMo-V2.5/api) - 小米多模态模型
