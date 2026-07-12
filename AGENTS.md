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
| 启动桌宠 | `python pet.py`（薄封装 → `meapet.desktop.app:main`） |
| Linux | `QT_QPA_PLATFORM=xcb python pet.py` |
| 配置向导 | `python setup_wizard.py` 或右键「打开配置页…」 |
| 用户配置 | `config.json`（**gitignore**；模板 `config.example.json`） |
| TTS 权重表 | `weight.json`（入库） |
| 气泡时长 | `config.json` → `bubble_duration_ms`（**不是** `config_settings.json`） |
| Python | **3.10–3.12**（`.python-version`）；VITS 路径注意 `numpy<2`、`setuptools==69.5.1` |

密钥优先级：**环境变量 > config 明文**（`meapet/config/store.py` 的 `resolve_*`）。

## Architecture

| Path | Role |
|------|------|
| `pet.py` | 兼容入口 |
| `meapet/desktop/app.py` | 主窗口 + mixin 组装 |
| `meapet/desktop/*` | 聊天流、输入框、气泡、渲染、托盘/菜单、观察控制 |
| `meapet/chat/engine.py` | LLM 引擎（async httpx） |
| `meapet/memory/db.py` | SQLite 记忆 / 好感（`RLock`） |
| `meapet/watcher/screen.py` | 截屏识图 `QThread` |
| `meapet/tts/` | GSV / VITS / MiMo |
| `meapet/ui_theme.py` | 语义色、间距、字号缩放、44px 触控下限 |
| `meapet/desktop/theme.py` | 桌面浮窗 QSS |
| `meapet/desktop/status_language.py` | 统一状态/菜单短文案 |
| `meapet/desktop/icons.py` | 桌面菜单/托盘标准图标 |
| `wizard/` | 配置中心（Tab：环境 / 对话 / 语音 / 屏幕识图） |
| `design-system/` | UI 设计源与页面补充说明 |

### Threading（勿随意改）

- `ChatWorker` / `TTSWorker`：普通 `threading.Thread` 包装，**不是** QThread
- `ScreenWatcher`：QThread
- 主线程用 `QTimer`（约 100ms）轮询 worker；**禁止**在 GUI 线程做阻塞网络/TTS

### Import / stdout 约束

- 在引入 PyQt 相关路径前保留 `socket` 导入习惯（QtNetwork 冲突历史问题）；`meapet/chat/engine.py` 与 desktop 入口已处理
- `ensure_utf8_stdout()` 只应在应用启动路径调用一次；其它模块勿重复包装 stdout

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

## Testing & quality

**有测试与 Ruff 配置；默认无强制 CI（本地可跑）。**

```bash
python -m pytest -q
python -m pytest tests/test_ui_refactor.py -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

- 测试目录：`tests/`
- 改 UI 后优先跑：`tests/test_ui_refactor.py`、`tests/test_live2d_startup.py`

## Key behaviors

- **聊天历史**：引擎侧裁剪为短窗口（system + 有限轮次）
- **记忆**：周期性或用户说「记住」类触发抽取
- **好感度**：有上下限与日 cap（见 `meapet/memory/db.py`）
- **屏幕观察**：随机间隔；待机或刚互动过会抑制；云端需 `allow_cloud` + 确认
- **语音缓存**：`voice_cache/`、`audio_cache/`（gitignore）
- **截图**：watcher 可能写入 `screenshots/`——勿提交真实截图

## Security / privacy checklist

- [ ] 不提交 `config.json`、`.env`、真实 API Key
- [ ] 日志经 `redact_text` / `safe_print`；调试正文仅 `MEAPET_DEBUG=1`
- [ ] 不把 `screenshots/`、`mea_memory.db`、日志打进发布物
- [ ] 云端识图路径保持确认门闩

## Commands (common)

```bash
python setup_wizard.py
python pet.py
QT_QPA_PLATFORM=xcb python pet.py
python -m pytest -q
python -m ruff check meapet wizard tests
pip install -r linux_requirements.txt
```

## Agent working notes

1. 改桌宠 UI 先读 `design-system/MASTER.md` 与 `meapet/ui_theme.py`
2. 改菜单文案时同步 `tests/test_ui_refactor.py` 中根菜单断言
3. 状态提示优先改 `status_language.py`
4. 大文件注意 LFS / 可选资源；勿提交私人运行数据
