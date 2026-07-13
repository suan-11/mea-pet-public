# AGENTS.md — MeaPet 桌宠

## Entry points
- `pet.py` / `python -m meapet` → `meapet.desktop.app:main` (desktop pet)
- `python setup_wizard.py` → `wizard.app:main` (GUI config, first-run)
- `config.example.json` is the **only template** (tracked); `config.json` is gitignored
- Python `>=3.10,<3.13` (`.python-version` = 3.12). 3.12+ needs `numpy<2` + `setuptools==69.5.1` for VITS.

## Architecture

| Path | Role |
|------|------|
| `pet.py` / `meapet/__main__.py` / `meapet/desktop/app.py` | Entry + main window (`MeaPet` via 7 mixins + `QWidget`) |
| `meapet/config/store.py` | Config loading + env-var resolution (`resolve_*_api_key`) |
| `meapet/chat/engine.py` | LLM engine (Ollama / DeepSeek / MiMo), async httpx |
| `meapet/http_async.py` | Shared `httpx.AsyncClient` (runs on asyncio loop) |
| `meapet/memory/db.py` | SQLite memory + affection (`mea_memory.db`, `RLock`) |
| `meapet/watcher/screen.py` | Screen watch `QThread` + privacy gates |
| `meapet/tts/service.py` | TTS — MiMo (cloud HTTP) or local (GSV/VITS via `subprocess.run`) |
| `meapet/desktop/workers.py` | Chat / TTS async tasks via single asyncio loop |
| `meapet/async_runtime.py` | Singleton `asyncio` event loop in a daemon thread |
| `meapet/log.py` | Daily rolling file logger (`logs/`, 7-day retention) |
| `meapet/ui_theme.py` | Font loading + scaling |
| `meapet/paths.py` | `PROJECT_ROOT` path helper |
| `wizard/` | Setup wizard pages (separate package) |
| `meapet/tools/` | `vits_infer.py`, `gsv_infer.py`, `precache_interactions.py` |

## Threading model
- **ChatWorker / TTSWorker** submit coroutines to the singleton asyncio daemon thread (`async_runtime.py`). Network I/O uses `httpx.AsyncClient`; blocking calls (local TTS subprocess) use `asyncio.to_thread`.
- **ScreenWatcher** is a `QThread` subclass.
- Main thread polls workers via `QTimer` (100ms); **no blocking calls in the main event loop**.

## TTS engines (local = subprocess)
- **GSV** / **VITS** → `subprocess.run()` with timeout. Does **not** run in main process.
- **MiMo** → cloud HTTP `POST`, uses `httpx.AsyncClient`.
- Voice cache: `voice_cache/{jp|zh}_{safe_name}.wav`.
- Audio cache: `audio_cache/` auto-cleaned at startup (max 40 files, 48h TTL).

## Config: environment variable > config.json
Override keys via env vars (supports `"$ENV_VAR"` placeholder in config.json too):

| Variable | Override for |
|----------|-------------|
| `DEEPSEEK_API_KEY` | `llm.api_key` (DeepSeek) |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo LLM / TTS / vision keys |
| `MEAPET_API_KEY` | Fallback catch-all |
| `TRANSLATE_API_KEY` | TTS Japanese translation |
| `GSV_PYTHON` | GPT-SoVITS conda `python.exe` path |
| `MEAPETFORCE_PNG` | Set to any value → force PNG rendering |
| `MEAPET_DEBUG` | `=1` enables payload-level debug logging |
| `MEAPET_ALLOW_DOWNLOAD` | `=1` allows `启动桌宠.bat` to auto-install uv |
| `QT_MULTIMEDIA_PREFERRED_PLUGINS` | Defaults to `windowsmediafoundation` in `app.py:57` |

## Critical gotchas

### Import order: `socket` before PyQt5
`app.py:10` and `engine.py:9` import `socket` before any PyQt5 import to avoid QtNetwork hook conflicts. Do not reorganize imports.

### Window flags & lifecycle
- `Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint` — not `SubWindow` (invisible on Windows)
- `WA_QuitOnClose = False` — close hides, does not quit. Real exit via tray menu.
- `app.setQuitOnLastWindowClosed(False)` + off-screen `keepalive` widget prevents early process reaping.

### TTS subprocess model
Local engines (GSV, VITS) use `subprocess.run()`. The spawned Python process must have its own dependencies (conda env for GSV, system or venv Python for VITS). `GSV_PYTHON` env var points to the correct interpreter.

### Optional dependencies
`pyproject.toml` defines extras: `opengl`, `vits`, `webengine`, `win32`, `linux`. Only core deps (PyQt5, Pillow, requests, httpx) are installed by default. `live2d-py` is optional; falls back to PNG rendering if missing.

### `ensure_utf8_stdout()` is safe for multiple calls
Uses `sys.stdout.reconfigure(encoding="utf-8")` (not re-wrapping). Re-entry safe.

## Key behaviors
- **Chat history**: max 8 messages (system prompt + system prompt + 6 pairs). Trimmed in `quick_chat` and `quick_chat_async` — look for `len(history) > 8` + `history[-6:]` pattern.
- **Memory extraction**: triggers on keywords `记住`/`记下`/`别忘了`/`提醒我` or every 3rd message. Works with all backends (Ollama `/api/generate`; DeepSeek/MiMo via `/chat/completions`).
- **Affection**: range 0–100, starts at 5, daily cap of 15, +1 per chat. Tiers at 0/10/30/50/70/85/95.
- **Screen watching**: timer fires every 3–6 min randomly. Suppressed if user interacted <3 min ago or standby mode is on. Off by default.
- **Bubble durations**: `config.json` `bubble_duration_ms` (default/reply/watch/interaction/thinking).

## Tests & lint
```bash
python -m pytest                     # or: python -m unittest discover tests
python -m ruff check                 # select = E9, F63, F7, F82 only
```
- 10 test files in `tests/`, standard `unittest`.
- No CI, no formatter, no type checker, no pre-commit hooks.
- `pyproject.toml` has `[tool.pytest.ini_options]` with `addopts = "-ra"`.

## Commands
```bash
# Run
python pet.py                        # Windows
QT_QPA_PLATFORM=xcb python pet.py    # Linux (X11)
python -m meapet                     # module entry

# First-time setup
python setup_wizard.py               # GUI config wizard
# or: copy config.example.json → config.json, edit manually

# TTS test
python meapet/tools/vits_infer.py --text "测试" --output test.wav

# Package release (output: dist/mea-pet-*.zip + SHA-256)
python scripts/package_release.py
python scripts/package_release.py --dry-run
python scripts/package_release.py --include-optional-assets

# Linux deps
pip install -r linux_requirements.txt
# live2d-py must be installed separately from https://github.com/EasyLive2D/live2d-py
```

## Logs & diagnostics
- **Console**: colored logging via `meapet/log.py` `get_color_logger()`
- **Files**: `logs/` directory with daily rolling files (7-day retention)
- **Boot log**: `meapet_boot.log` (startup summary)
- **Fault log**: `meapet_fault.log` (native crashes via `faulthandler`, C++/OpenGL segfaults)
- **Chat errors**: `chat_errors.log` (LLM/TTS errors, auto-redacted)
- `MEAPET_DEBUG=1` enables full payload dumps to stderr

## Windows bat scripts
- `启动桌宠.bat` — auto-setup: create `.venv`, install deps, run config wizard + pet
- `打包发布.bat` — calls `python scripts/package_release.py`
