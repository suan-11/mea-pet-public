# AGENTS.md — MeaPet 桌宠

面向自动化助手与协作者的项目说明。实现细节以代码为准；本文件描述约束、入口与约定。

## Project overview

Windows-first（Linux 支持）的 **PyQt5 透明桌宠**：

- Live2D / PNG 双渲染
- 多后端 AI 对话（Ollama / DeepSeek / MiMo）
- 本地或云端 TTS
- SQLite 记忆与好感度
- 可选屏幕观察（默认关闭，云端需确认）

气质：**角色在前、系统在后**；暗色陪伴风，见 `design-system/MASTER.md` 与 `meapet/ui_theme.py`。

## Entry points & config

| 用途 | 命令 / 文件 |
|------|-------------|
| 启动桌宠 | `python pet.py` 或 `python -m meapet` → `meapet.desktop.app:main` |
| Linux | `QT_QPA_PLATFORM=xcb python pet.py` |
| 配置向导 | `python setup_wizard.py` 或右键「打开配置页…」 |
| 用户配置 | `config.json`（**gitignore**；唯一模板 `config.example.json`） |
| TTS 权重表 | `weight.json`（入库） |
| 气泡时长 | `config.json` → `bubble_duration_ms`（**不是** `config_settings.json`） |
| Python | **3.10–3.12**（`.python-version` 为 3.12）；VITS 路径注意 `numpy<2`、`setuptools==69.5.1` |

密钥优先级：**环境变量 > config 明文**（`meapet/config/store.py` 的 `resolve_*`，含 `"$ENV"` / `${ENV_VAR}` 占位符）。

## Architecture

`meapet/desktop/app.py` — `MeaPet` 以 7 个 mixin + `QWidget` 组成 MRO：

`PetAudioMixin` → `PetWatcherMixin` → `PetChatFlowMixin` → `PetInteractionMixin` → `PetWindowChromeMixin` → `PetRenderHostMixin` → `PetConfigBridgeMixin`

| Path | Role |
|------|------|
| `pet.py` / `meapet/__main__.py` | 兼容入口 / 模块入口 |
| `meapet/desktop/app.py` | 主窗口与启动生命周期 |
| `meapet/desktop/*` | 聊天流、输入框、气泡、渲染、托盘/菜单、观察控制、splash |
| `meapet/config/store.py` | 配置加载、规范化、环境变量解析 |
| `meapet/chat/engine.py` | LLM 引擎（async httpx） |
| `meapet/http_async.py` | 后台 asyncio loop 共用的 `httpx.AsyncClient` |
| `meapet/async_runtime.py` | 单例 asyncio 事件循环及守护线程 |
| `meapet/desktop/workers.py` | Chat / TTS 任务投递与兼容轮询接口 |
| `meapet/memory/db.py` | SQLite 记忆 / 好感（`RLock`，`SCHEMA_VERSION=3`） |
| `meapet/watcher/screen.py` | 截屏识图 `QThread` 与隐私门闩 |
| `meapet/tts/service.py` | TTS — MiMo（云端 HTTP）或本地 GSV/VITS（`subprocess.run`） |
| `meapet/tts/engines/` | `gsv.py` / `mimo.py` / `vits.py` |
| `meapet/log.py` | `get_color_logger(name)`：控制台颜色 + 按日滚动文件（7 天） |
| `meapet/ui_theme.py` | 语义色、间距、字号缩放、44px 触控下限、捆绑霞鹜文楷 |
| `meapet/desktop/theme.py` | 桌面浮窗 QSS |
| `meapet/desktop/status_language.py` | 统一状态/菜单短文案 |
| `meapet/desktop/icons.py` | 桌面菜单/托盘标准图标 |
| `meapet/paths.py` | `PROJECT_ROOT` / `project_path()` |
| `wizard/` | 配置中心（Tab：环境 / 对话 / 语音 / 屏幕识图） |
| `meapet/tools/` | `vits_infer.py`、`gsv_infer.py`、`precache_interactions.py`、`pre_render_voices.py` |
| `design-system/` | UI 设计源与页面补充说明（`MASTER.md`、`pages/*`） |

### Threading（勿随意改）

- `ChatWorker` / `TTSWorker` 将协程提交到 `async_runtime.py` 的单例 asyncio 守护线程；网络 I/O 使用 async httpx，本地 TTS 等阻塞工作经 `asyncio.to_thread` 执行。
- `ScreenWatcher` 是 `QThread`。
- 主线程用 `QTimer`（约 100ms）轮询 worker；**禁止**在 GUI 线程做阻塞网络/TTS。

### Import / stdout 约束

- 在引入 PyQt 相关路径前保留 `socket` 导入习惯（QtNetwork 冲突历史问题）；`meapet/chat/engine.py` 与 desktop 入口已处理。
- `ensure_utf8_stdout()` 由应用启动路径统一调用。实现本身可安全重入，但其它模块不要重复承担启动初始化职责。

### Window flags & lifecycle

- 主窗使用 `Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint`；不要改成 Windows 下可能不可见的 `SubWindow`。
- `WA_QuitOnClose = False`：关闭窗口只隐藏，真正退出走托盘菜单。
- `app.setQuitOnLastWindowClosed(False)` 与离屏 keepalive 窗口共同避免进程被过早结束。

### TTS subprocess model

- GSV / VITS 使用带超时的 `subprocess.run()`；子进程解释器必须安装各自依赖。
- `GSV_PYTHON` 指向 GPT-SoVITS 环境的 `python.exe`。
- MiMo 语音使用云端 HTTP；语音缓存位于 `voice_cache/`，临时音频位于 `audio_cache/`。

### Optional dependencies

`pyproject.toml` 定义 `opengl`、`vits`、`webengine`、`win32`、`linux` extras。`live2d-py` 是可选依赖；不可用时应回退到 PNG 渲染。预编译包见 <https://github.com/EasyLive2D/live2d-py>。

## Config environment variables

环境变量会覆盖 `config.json`；配置值也支持 `"$ENV_VAR"` / `${ENV_VAR}` 占位符。

| Variable | Override for |
|----------|--------------|
| `DEEPSEEK_API_KEY` | DeepSeek 对话密钥 |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo 对话 / TTS / 识图密钥 |
| `MEAPET_API_KEY` | 对话密钥兜底 |
| `TRANSLATE_API_KEY` | 日语语音翻译密钥（可回退 `DEEPSEEK_API_KEY`） |
| `GSV_PYTHON` | GPT-SoVITS Python 路径 |
| `MEAPET_FORCE_PNG` | 非空真值时强制 PNG 渲染 |
| `MEAPET_DEBUG` | `=1` 时启用载荷级调试日志 |
| `MEAPET_ALLOW_DOWNLOAD` | `=1` 时允许自动下载或安装可选依赖 |
| `MEAPET_REDUCED_MOTION` | `=1` 时减少动效 |
| `QT_MULTIMEDIA_PREFERRED_PLUGINS` | Qt 多媒体后端；Windows 默认 `windowsmediafoundation` |
| `QTWEBENGINE_DISABLE_SANDBOX` | Linux 下建议 `=1`（`start.sh` 已设） |

## UI / UX conventions

设计源：`design-system/MASTER.md`，实现令牌：`meapet/ui_theme.py`。

- **风格**：Modern Dark + soft companion；非纯黑；主色粉/暖橙（`primary` / `secondary`）
- **字体**：捆绑霞鹜文楷（LXGW WenKai）；`display.font_scale` 控制 UI 缩放
- **组件**：气泡 = 角色语音；输入框 = 操作面板——框体样式不要混用
- **状态文案**：统一走 `meapet/desktop/status_language.py`
- **菜单**：根层高频 + 子菜单分组；危险操作（重置记忆、退出）隔离
- **隐私**：屏幕观察默认关；云端识图每次确认，超时默认取消
- **动效**：150–300ms；`MEAPET_REDUCED_MOTION=1` 时减少淡入淡出
- **图标**：系统操作以文字为主；emoji 仅作角色/心情点缀，不作唯一含义

### UI 实施路线

1. **体验债**：菜单信息架构、统一状态语言、输入忙态反馈、硬编码色回收
2. **精致度**：气泡情绪微样式、系统图标一致、向导 progressive disclosure、减少动画
3. **陪伴感**：新手一次性提示（`ui.first_run_hint_shown`）、养成空状态、待机文案
4. **动效**：`display.reduced_motion` → `MEAPET_REDUCED_MOTION`；气泡情绪描边

页面补充：`design-system/pages/desktop.md`、`design-system/pages/wizard.md`。

## Key behaviors

- **聊天历史**：超过 16 条时保留 system 消息与最近 14 条；同步 / 异步路径应一致。
- **记忆提取**：用户说「记住 / 记下 / 别忘了 / 提醒我」时立即触发，否则每 3 轮触发；成功回复后还会 `store_chat_exchange`。
- **好感度**：0–100，初始 5；当前聊天流程按用户消息长度每轮 +1 / +2 / +3，每日最多增加 15；等级阈值见 `AFFECTION_TIERS`。
- **屏幕观察**：随机间隔；待机或刚互动过会抑制；默认关闭，云端需 `allow_cloud` + 确认。
- **气泡时长**：`config.json` 的 `bubble_duration_ms`（default / reply / watch / interaction / thinking）。
- **回复与 TTS**：最终回复气泡应等 TTS 音频就绪后再显示；TTS 关闭或启动失败时回退为立即显示文字。
- **语音缓存**：`voice_cache/`、`audio_cache/`（gitignore）。
- **截图**：watcher 可能写入 `screenshots/`——勿提交真实截图。

## Testing & quality

**有测试与 Ruff 配置；默认无强制 CI。**

```bash
python -m pytest -q
python -m pytest tests/test_ui_refactor.py tests/test_live2d_startup.py -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

- 测试目录：`tests/`（`unittest` / pytest；`pyproject.toml` 有 `[tool.pytest.ini_options]`）
- Ruff 只启用 `E9`、`F63`、`F7`、`F82`；排除 `GPT-Sovits`、`live2d`、`models`、`vits_core`、`vits_models`
- 改 UI 后优先跑 `tests/test_ui_refactor.py`、`tests/test_live2d_startup.py`

## Security / privacy checklist

- [ ] 不提交 `config.json`、`.env`、真实 API Key
- [ ] 日志经 `redact_text` / `safe_print`；调试正文仅 `MEAPET_DEBUG=1`
- [ ] 不把 `screenshots/`、`mea_memory.db`、日志打进发布物
- [ ] 云端识图路径保持确认门闩

## Commands (common)

```bash
python setup_wizard.py
python pet.py
python -m meapet
QT_QPA_PLATFORM=xcb python pet.py
python -m pytest -q
python -m ruff check meapet wizard scripts tests
pip install -r linux_requirements.txt
# live2d-py: https://github.com/EasyLive2D/live2d-py （推荐预编译）
```

## Logs & diagnostics

- `logs/`：按日滚动日志（保留 7 天）；控制台经 `get_color_logger` 着色（Windows 开 VT）
- `meapet_boot.log`：启动摘要
- `meapet_fault.log`：`faulthandler` 捕获的原生崩溃信息（含 C++/OpenGL）
- `chat_errors.log`：LLM / TTS 错误（自动脱敏）
- `MEAPET_DEBUG=1`：把完整调试载荷输出到 stderr；不得作为默认值

## Windows bat scripts

- `启动桌宠.bat`：创建 `.venv`、安装依赖、运行配置向导与桌宠

## Agent working notes

1. 改桌宠 UI 先读 `design-system/MASTER.md` 与 `meapet/ui_theme.py`
2. 改菜单文案时同步 `tests/test_ui_refactor.py` 中根菜单断言
3. 状态提示优先改 `status_language.py`
4. 大文件注意 LFS / 可选资源；勿提交私人运行数据
