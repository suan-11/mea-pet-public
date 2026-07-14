# AGENTS.md — MeaPet 桌宠

Windows-first **PyQt5 透明桌宠**：Live2D / PNG 双渲染、多后端 AI 对话、云端/本地 TTS、SQLite 记忆与好感度、Companion MCP。

## Entry points

| What | Command |
|------|---------|
| 启动桌宠 | `python pet.py` 或 `python -m meapet` (→ `meapet.desktop.app:main`) |
| 配置向导 | `python setup_wizard.py` (或右键菜单「打开配置页…」 → `wizard.app:main`) |
| Linux | `QT_QPA_PLATFORM=xcb python pet.py`；niri: window-rule `title="mea-pet" open-floating true` |
| Fcitx5 | `QT_PLUGIN_PATH=/usr/lib/qt/plugins` |
| 唯一模板 | `config.example.json`；`config.json` 被 gitignore，不得提交 |

## Key commands

```bash
python -m pytest -q
python -m pytest tests/test_ui_refactor.py tests/test_live2d_startup.py -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

- Ruff: 仅 `E9` / `F63` / `F7` / `F82`；排除 `GPT-Sovits`, `live2d`, `models`, `vits_core`, `vits_models`
- Python **3.10–3.12** (`.python-version` = 3.12)
- VITS optional-deps: `numpy<2`, `setuptools==69.5.1`

## Architecture

**MRO** (`meapet/desktop/app.py:95`): 8 mixins + `QWidget`:

`PetAudioMixin` → `PetWatcherMixin` → `PetChatFlowMixin` → `PetControlBridgeMixin` → `PetInteractionMixin` → `PetWindowChromeMixin` → `PetRenderHostMixin` → `PetConfigBridgeMixin`

**Key modules:**

| Path | Role |
|------|------|
| `desktop/app.py` | 主窗口 + 启动生命周期 |
| `desktop/` | 聊天流、气泡、输入、渲染、托盘、窗口控制、桥接 |
| `agent/` | Hermes / OpenClaw 适配器与呈现状态机 |
| `direct/` | 四种直连协议 (ollama_chat/openai_chat/openai_responses/anthropic_messages) |
| `conversation/` | 分段输出协议、会话隔离、时间线 |
| `control/` | Companion MCP 服务 + 安全中间件 (速率/Origin/mTLS) |
| `chat/engine.py` | LLM 引擎 (async httpx)，角色提示词 |
| `memory/db.py` | SQLite 记忆/好感 (`RLock`, `SCHEMA_VERSION=3`) |
| `tts/` | MiMo HTTP / GSV+VITS subprocess |
| `vision/` | 视觉路由 (`disabled`/`inherit`/`relay`) |
| `watcher/screen.py` | 截屏识图 `QThread` + 隐私门闩 |
| `config/store.py` | 配置加载、规范化、环境变量解析 (`resolve_*`) |
| `async_runtime.py` | 单例 asyncio 守护线程 (`submit`, `run`, `get_loop`) |
| `http_async.py` | 共享 `httpx.AsyncClient` (跑在 async_runtime 的 loop 上) |
| `ui_theme.py` | 语义色 `PALETTE`、霞鹜文楷、字号缩放、44px 触控下限 |
| `desktop/status_language.py` | 统一状态/菜单短文案 (functions, not strings) |
| `desktop/theme.py` | 桌面浮窗 QSS |
| `wizard/` | 配置中心 (Tab: env/llm/tts/vision) |

## Threading — DO NOT CHANGE

- `ChatWorker` / `TTSWorker` → `async_runtime.submit(coro)` → singleton asyncio daemon thread
- Net I/O uses async httpx; blocking work (local TTS subprocess) → `asyncio.to_thread`
- `ScreenWatcher` is a `QThread`
- Main thread polls workers via `QTimer` (~100ms)
- **Never** block GUI thread with network I/O or TTS
- `ensure_utf8_stdout()` called once at boot; other modules must not re-initialize

## Window flags & lifecycle

- Main: `Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint` (no `SubWindow` — invisible on Windows)
- `WA_QuitOnClose = False`; exit through tray menu only
- `app.setQuitOnLastWindowClosed(False)` + offscreen keepalive `QWidget` prevent early exit
- Import `socket` **before** any PyQt path (QtNetwork hook conflict) — enforced in `app.py` and `engine.py`

## Config & secrets

- **Env var > config.json** (`store.py:resolve_secret`); supports `"$ENV_VAR"` / `${ENV_VAR}` placeholders
- Key env vars: `DEEPSEEK_API_KEY`, `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY`, `MEAPET_API_KEY` (fallback), `TRANSLATE_API_KEY` (falls to `DEEPSEEK_API_KEY`), `HERMES_API_SERVER_KEY` / `MEAPET_AGENT_TOKEN`, `MEAPET_CONTROL_TOKEN`, `GSV_PYTHON`, `MEAPET_FORCE_PNG`, `MEAPET_DEBUG`, `MEAPET_ALLOW_DOWNLOAD`, `MEAPET_REDUCED_MOTION`

## Key behaviors

- **Chat history**: max 16 msgs; keeps system + last 14 on overflow
- **Memory extraction**: immediate on keywords "记住 / 记下 / 别忘了 / 提醒我"; else every 3 turns
- **Affection**: 0–100, start=5. Per turn +1/2/3 by length, daily cap=15. Tiers in `AFFECTION_TIERS` (`memory/db.py`)
- **Screen watcher**: random interval `min_ms`/`max_ms` in config. Off by default. Cloud vision requires `allow_cloud=true` + per-run confirmation (timeout→cancel)
- **Bubble duration**: `bubble_duration_ms` keys `default/reply/watch/interaction/thinking`; sync_with_audio waits for TTS before showing

## UI conventions

- Design tokens: `meapet/ui_theme.py` (colors via `PALETTE`, font scale via `display.font_scale`)
- Before editing UI: read `design-system/MASTER.md`, `design-system/pages/desktop.md`, and `meapet/ui_theme.py`
- Bubble = character speech; input panel = operational surface — never mix
- Status text → `meapet/desktop/status_language.py` (functions, not raw strings)
- Menu: root = frequent, submenus = grouping. Dangerous actions isolated
- Motions 150–300ms; reduced when `MEAPET_REDUCED_MOTION=1` or `display.reduced_motion=true`
- Icons: system ops = text only; emoji = character/mood accent, never sole meaning

## Testing

- 26 test files in `tests/`. `pyproject.toml` has `[tool.pytest.ini_options]` (`addopts = "-ra"`)
- After UI changes: `tests/test_ui_refactor.py` (menu assertions) + `tests/test_live2d_startup.py`
- When changing menu text: update assertions in `test_ui_refactor.py`

## Agent working notes

1. Don't commit `config.json`, `.env`, `*.db`, `screenshots/`, `logs/`, `audio_cache/`, `voice_cache/`
2. Before editing pet UI, read `design-system/MASTER.md` + `design-system/pages/desktop.md` + `meapet/ui_theme.py`
3. Status prompts → edit `meapet/desktop/status_language.py`
4. Thread model → see Threading section; never block GUI thread
5. Cloud vision path: guard with `watcher.allow_cloud` + per-run confirmation, timeout → cancel
