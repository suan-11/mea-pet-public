# MeaPet — Desktop Companion

[简体中文](README.md) | **English**

A PyQt5 transparent desktop companion for Windows and Linux alike. It integrates character sprites, AI conversation, speech synthesis, screen vision, SQLite memory, and affection into a single desktop frontend, supporting Live2D / PNG dual rendering.

MeaPet draws a clear boundary: character presentation, chat bubbles, TTS, screenshot authorization, and local state are managed by MeaPet. When using an Agent as the reply backend, the model, long-term memory, and internal tools are managed by the Agent.

**Mobile Version**: [mea-pet-mobile](https://github.com/llz121517/mea-pet-mobile) (Lightweight mobile client with core features)

## Current Capabilities

| Capability | Description |
|------------|-------------|
| Reply backends | Direct model API or Agent — one active at a time, no automatic fallback |
| Direct protocol | Ollama Chat, OpenAI Chat/Responses, Anthropic Messages over HTTP streaming |
| Agent | Hermes TUI Gateway, OpenClaw Gateway v4, or custom Agent Link v1 over WebSocket |
| Display | Streaming text bubble without TTS; waits for audio then shows bubble + plays in sync |
| Multi-segment replies | Each segment has its own bubble, mood, voice text, language, and TTS style |
| Speech | MiMo cloud TTS, local GPT-SoVITS, local VITS; GPT-SoVITS supports per-language reference audio |
| Vision | Disabled, inherit main model, or relay via independent vision model |
| Reverse control | Shared frontend tools over Companion MCP or the same Agent Link connection |
| Local data | SQLite memory, affection, per-backend/per-session conversation timeline |
| Rendering | Live2D dynamic model and PNG diff sprites, switchable at runtime |

## Quick Start

The companion requires Python 3.10+ (project defaults to 3.12). For local VITS, Python 3.10–3.12 is recommended due to dependency compatibility.

### Windows

Double-click `启动桌宠.bat`. The script reuses `.venv` if available, creates the environment and installs core dependencies when needed, and opens the configuration wizard on first run.

Or run manually:

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

`live2d-py` is optional; falls back to PNG when unavailable. Prebuilt packages are available at [EasyLive2D/live2d-py](https://github.com/EasyLive2D/live2d-py).

The configuration wizard can also be reopened from the pet's right-click menu ("Open Settings…") at any time. After saving, the new backend takes effect immediately; any in-flight generations from the old backend are cancelled.

For Chinese troubleshooting steps, see [`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md).

### Windows binary (PyInstaller onedir)

Build from a venv that has app deps + PyInstaller (VITS needs `numpy<2` and `setuptools==69.5.1` if you ship local VITS):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

Output: `dist/MeaPet/MeaPet.exe` + `dist/MeaPet/_internal/`.

The `mea-pet` wheel is a Python package component for development and dependency
distribution; it does not contain the complete desktop asset tree or a
standalone launcher. Use the source checkout or the PyInstaller onedir bundle
to run the full companion.

Portable layout (user-writable data lives under `_internal` with the bundle):

| Path under `_internal` | Role |
|------------------------|------|
| `config.json` / `config.example.json` | User config (example is the template) |
| `mea_memory.db` | SQLite memory / affection |
| `audio_cache/`, `logs/` | Runtime cache and logs |
| `sprites/`, `live2d/`, `vits_models/`, `vits_core/`, `dic/` | Bundled assets |

Speech engines in the packaged build:

- **VITS**: in-process (torch + `vits_core` inside the app). No external Python required when models are present.
- **MiMo**: cloud TTS (API key).
- **GPT-SoVITS**: still needs a separate GPT-SoVITS runtime `python.exe`; never point it at `MeaPet.exe`.

Do not ship a developer `config.json` that contains API keys. Only `config.example.json` is packaged as a template. Do not commit `dist/` or `build/`.

## Reply Backends

### Direct Model API

Direct mode lets MeaPet manage character prompts, recent context, SQLite memory, and output constraints. It is the only mode that connects directly to an LLM HTTP API.

You configure three things in the wizard: the API base URL, the model name (with optional auto-discovery via "Fetch Models"), and your API key. The saved `provider` is always `custom`. Transport is decided by `protocol` + `api_base` (Ollama local URLs auto-select `ollama_chat`; everything else defaults to OpenAI-compatible chat). URL hints only affect which environment variable is probed for the key, and MiMo/Ollama linkage for TTS/vision.

| API Base URL Example | Saved provider | Auto protocol | Environment Variable |
|----------------------|----------------|---------------|---------------------|
| `https://api.deepseek.com/v1` | `custom` | `openai_chat` | `DEEPSEEK_API_KEY` (also `MEAPET_API_KEY`) |
| `https://api.xiaomimimo.com/v1` | `custom` | `openai_chat` | `MIMO_API_KEY` |
| `http://localhost:11434` | `custom` | `ollama_chat` | (none — key optional) |
| Other OpenAI-compatible endpoint | `custom` | `openai_chat` | `MEAPET_API_KEY` / `OPENAI_API_KEY` |

### Agent

Agent mode treats MeaPet as a pure desktop frontend:

- MeaPet calls the Agent to generate replies, providing mood, action, TTS language, and character state as context.
- The Agent uses its own model, memory, and internal tools; MeaPet does not require the Agent to implement additional "memory query" capabilities.
- Internal tool names and raw parameters are not displayed in character bubbles. Safe status (start, complete, failure) enters the timeline; diagnostic details go to logs only.
- Agent mode uses WebSocket only. Hermes connects to the native TUI Gateway exposed by `hermes serve`; OpenClaw connects to Gateway v4; a custom backend can implement Agent Link v1. HTTP/SSE model endpoints belong exclusively to direct mode.
- Connections are reused across turns and carry bidirectional streaming, cancellation, ping/pong, and protocol-specific reconnect recovery. OpenClaw retries with the same idempotency key; Hermes resumes the stored session and reconciles history instead of blindly resubmitting a possibly executed tool turn.
- Agent Link uses one outbound connection for chat and frontend tool calls. MeaPet publishes typed tool schemas after the handshake, so the third-party connector can register them in its own Agent loop and can proactively call `meapet.say`.
- Agent responses must follow the MeaPet segmented output format. Malformed output raises an explicit format-repair state; no Agent path silently falls back to an HTTP model endpoint.

The local `session_id` identifies MeaPet's timeline scope. OpenClaw additionally persists its Gateway `session_key`; Hermes persists the server-returned `remote_session_id` and uses `session.resume` after restart or reconnect. "Clear memory" in Agent mode explicitly starts a new upstream Agent session. Old timelines remain readable.

The complete custom backend contract is documented in [`docs/agent-link-v1.md`](docs/agent-link-v1.md). A generic WebSocket endpoint is not sufficient: the backend must implement its envelope, handshake, tool snapshot, request correlation, idempotency, cancellation, offline, and reconnect semantics.

## Reply, Bubble, and TTS Timing

Each reply segment from the model or Agent contains:

- `display_text`: text shown in the bubble
- `voice_text`: text sent to TTS
- `voice_language`: TTS language
- `mood`: character expression; unsupported values normalize to `neutral`
- `tts_style`: tone description passed to TTS engines that support it

Rendering rules:

1. TTS disabled: bubbles appear on first text delta and grow incrementally; finalized when the segment completes.
2. TTS enabled: the full segment is collected and audio is generated first; the bubble and audio play appear simultaneously once audio is ready.
3. Bubble duration is `max(configured min duration, audio duration + 500ms)`.
4. If TTS fails to start, the language is unsupported, or audio generation fails, the reply falls back to a text-only bubble immediately — no blocking.
5. Multiple segments are not merged; they play sequentially. Clicking a bubble or opening "Conversation Timeline…" shows the full turn.

Both direct and Agent modes share the same presentation layer, so switching backends does not change bubble or TTS timing.

## Multi-language Speech

GPT-SoVITS can be configured with a fixed reference audio per language in `tts.reference_audios`. Each entry must specify the language; paths can be project-relative or absolute. Reference text can be left empty:

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

Legacy `gsv_ref_wav` + `gsv_ref_lang` fields are read-only migrated to a single reference audio entry. A `.txt` file with the same name as the WAV is used as reference text.

Speech translation uses MeaPet's built-in non-LLM machine translation service pool, rotating services on single-segment failure (max 3 total attempts). When "prefer model voice translation" is enabled, the model's returned `voice_language` and `voice_text` are checked against the configured target language — if inconsistent, they are translated to the target. A separate "translate when unsupported" toggle controls fallback for unsupported output languages. When translation ultimately fails, the voice segment is skipped and the text bubble is preserved.

## Screen Vision

The vision link has three modes configured in the wizard:

| Mode | Behavior |
|------|----------|
| `disabled` | Screenshots and vision turned off |
| `inherit` | When the main reply model supports images, screenshots are included in the same multimodal request |
| `relay` | An independent vision model generates a caption first, then passes it to the reply backend |

`inherit` is suitable for direct models or Agents that natively support images. `relay` can select Ollama or MiMo as the vision model in direct mode. Agent mode should use the Agent's own vision capability, or disable vision — do NOT route through a separate vision model on the MeaPet side. TTS machine translation does not participate in the vision pipeline.

Privacy rules are enforced: screen observation is off by default; each screenshot requires local confirmation, and the authorization is valid for one shot only. Confirmation defaults to full-screen but can be scoped to a region or specific application. Screenshots are passed in memory only and are never written to disk by new links. Cloud vision additionally requires explicit `watcher.allow_cloud` consent.

## Companion MCP: Agent Control over MeaPet

For Hermes and OpenClaw, Agent mode can expose an optional standard MCP Streamable HTTP endpoint:

```text
http(s)://<listen_host>:<port>/mcp
```

Only four tools are exposed:

| Tool | Capability |
|------|-----------|
| `meapet.say` | Queue one or more complete reply segments; does not preempt pending user replies |
| `meapet.express` | Request a mood or action the frontend explicitly supports; no implicit mapping |
| `meapet.get_state` | Read rendering, TTS capability, character state, and affection level summary; does not return paths, secrets, memory, or full chat history |
| `meapet.capture_screen` | Request a screenshot (fullscreen, region, or application); requires local per-shot confirmation; results never written to disk |

Security constraints:

- Defaults to listening on `127.0.0.1` only, with a single allowed Agent IP.
- Every request must carry a Bearer Token; viewable, copyable, and rotatable from the wizard or right-click menu. Rotating invalidates the old token immediately.
- LAN listening requires HTTPS by default. Plain HTTP can be explicitly allowed on trusted networks; the UI shows a persistent risk warning. When a client CA is configured, the Agent must present a client certificate signed by that CA (mTLS).
- Does not modify Windows Firewall; remote access requires manually opening the chosen port.
- The service also validates source IP, Host, Origin, request size, and rate.

Custom Agent Link does not start this second listener. It projects the same capability registry over the existing WebSocket, so chat, proactive messages, and tool calls remain on one connection.

## Configuration

The only user configuration file is `config.json` in the project root. The only template is `config.example.json`. Do not edit or commit real secrets; `config.json` is gitignored.

Minimal example:

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

See `config.example.json`, `docs/backend-and-control.md`, and `docs/agent-link-v1.md` for the full schema, Agent, MCP, and custom Agent Link examples.

### Secrets and Environment Variables

Secret priority: environment variable > `config.json` plaintext. Config values also support `$ENV_VAR` or `${ENV_VAR}` placeholders.

| Environment Variable | Purpose |
|---------------------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek direct connection |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo conversation, vision, or TTS |
| `MEAPET_API_KEY` | Custom direct connection fallback |
| `HERMES_DASHBOARD_SESSION_TOKEN` | Hermes `hermes serve` WebSocket token |
| `OPENCLAW_GATEWAY_TOKEN` / `MEAPET_AGENT_TOKEN` | OpenClaw Gateway token |
| `AGENT_LINK_TOKEN` / `MEAPET_AGENT_TOKEN` | Custom Agent Link token |
| `MEAPET_CONTROL_TOKEN` | Companion MCP Bearer Token |
| `GSV_PYTHON` | GPT-SoVITS environment `python.exe` |
| `MEAPET_PIP_INDEX_URL` / `PIP_INDEX_URL` | Python package index override; defaults to Tsinghua TUNA |
| `MEAPET_TORCH_INDEX_URL` / `TORCH_INDEX_URL` | Optional PyTorch wheel index override |
| `MEAPET_HF_ENDPOINT` / `HF_ENDPOINT` | Optional Hugging Face endpoint override |
| `MEAPET_TRANSLATORS_REGION` | `translators` import region (`CN` or `EN`); defaults to `CN` |
| `MEAPET_FORCE_PNG` | Force PNG rendering when set to a non-empty value |
| `MEAPET_DEBUG=1` | Additional protocol-level diagnostic output; leave off by default |

If a real key has ever entered a repository or public log, rotate it immediately at the provider — do not just delete the local text.

## Local Cache and Privacy

- Defaults to retaining the last 5 timeline turns; configurable 0–100 in the wizard.
- Timelines are isolated by direct provider / Agent type + Agent session; switching backends will not cross streams.
- SQLite memory (direct mode) and Agent-owned memory are separate boundaries. MeaPet does not replicate the Agent's long-term memory.
- New config apply, session switches, and token rotation all invalidate old async results — late replies, TTS, or screenshots do not enter the new session.
- Runtime data lives in `mea_memory.db`, `logs/`, `audio_cache/`, `voice_cache/`, and `voice_asr/`; none should be distributed. Shipped interaction clips are package assets under `meapet/assets/interaction_voices/`.
- Logs record metadata such as lengths and statuses by default, rotated daily and retained for 7 days. Payload text is limited to explicit TRACK/debug diagnostics. API keys, auth headers, reasoning, internal tool parameters/results, and screenshot content must NOT be written to normal logs.

## Controls

| Action | Effect |
|--------|--------|
| Left-click drag | Move the pet |
| Double-click | Open chat input |
| Drag on head area | Trigger headpat reaction |
| Right-click | Open settings, timeline, status, rendering, idle, and exit menu |
| Click a reply bubble | Open that turn's full reply (while still in recent cache) |
| `Esc` | Close input or panel |

Closing the main window only hides it; use the tray menu to exit.

## Project Structure

```text
mea-pet/
├── pet.py / meapet/__main__.py       Entry points
├── meapet/
│   ├── agent/                         Hermes / OpenClaw / Agent Link and presentation state machine
│   ├── direct/                        Direct protocol client with unified stream events
│   ├── conversation/                  Segmented output protocol, session isolation, timeline
│   ├── control/                       Companion MCP and security middleware
│   ├── chat/                          Direct-mode character prompts, history, and memory coordination
│   ├── desktop/                       PyQt5 main window, bubbles, input, rendering, and bridges
│   ├── memory/                        SQLite memory, affection, timeline persistence
│   ├── tts/                           MiMo / GPT-SoVITS / VITS
│   ├── vision/                        Vision routing and screen watcher coordination
│   ├── watcher/                       Screenshot thread and privacy latch
│   └── config/                        Config normalization and secret resolution
├── wizard/                            Configuration wizard
├── config.example.json                Single config template
├── design-system/                     UI design constraints
└── tests/                             pytest / unittest regression tests
```

## Development and Validation

```bash
python -m pytest -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

Before editing the pet UI, read `design-system/MASTER.md`, `design-system/pages/desktop.md`, and `meapet/ui_theme.py`. Repository hygiene, testing, and distribution rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md); backend, threading, and privacy contracts are in [`docs/backend-and-control.md`](docs/backend-and-control.md).

## Custom Character and Sprites

- Character prompt: `SYSTEM_PROMPT` in `meapet/chat/engine.py` and its persona references.
- Expression mapping: `EXPRESSION_MAP` and `MOOD_TO_EXPRESSION` in `meapet/desktop/renderer.py`.
- PNG resource naming: `sprites/mea{outfit_id}{direction}_{expression}.png`.
- Live2D model directory: `live2d.model_dir` in `config.json`.
- Live2D visual viewport: the legacy `live2d.window_mask` ellipse parameters define a rectangular outer bound used to crop transparent canvas margins; set `enabled` to `false` to expose the complete model canvas. The configuration center can edit this bound by dragging a rectangle or entering its four edge percentages. A separate `live2d.placement_anchor` target can be placed between the model's feet so that the same full-canvas point stays fixed on screen while the viewport or model scale changes. Saving keeps the complete model canvas and interaction zones unchanged while applying both settings immediately.

## FAQ

<details>
<summary>Replies still come from the old backend after saving</summary>

Saving cancels the old backend and builds the new one immediately. If old content still appears, check the timeline tab: old session timelines are retained read-only, but late events do not enter the new session. Search logs for "New config applied" and the backend initialization status.
</details>

<details>
<summary>Text bubbles show but there is no voice</summary>

Check whether TTS is enabled, the engine health check passes, the reply's `voice_language` is supported, and the language has a valid reference audio (for GPT-SoVITS). When a language is unsupported and translation is not configured, the voice is skipped and the text bubble is preserved by design.
</details>

<details>
<summary>Hermes, OpenClaw, Agent Link, or remote MCP cannot connect</summary>

For Hermes, start `hermes serve --host 127.0.0.1 --port 9119` with a fixed `HERMES_DASHBOARD_SESSION_TOKEN`, then configure `ws://127.0.0.1:9119/api/ws`; port 8642 is the separate HTTP API Server and is not the Agent WebSocket endpoint. Current Hermes public binds use short-lived login tickets rather than the static loopback token, so connect a remote Hermes through an SSH tunnel to its loopback port. Remote OpenClaw and Agent Link should use WSS; plain remote WS requires explicit opt-in. An Agent Link server must return a matching `control.ready` and support dynamic tool calls as described in `docs/agent-link-v1.md`. The Companion MCP listen IP must be a concrete local interface IP, and the allowed IP must match the Agent host. LAN HTTP also requires explicit opt-in; check Windows Firewall for the port.
</details>

<details>
<summary>Live2D does not render, or PNG switch results in wrong size</summary>

Verify that `live2d.model_dir` contains a `.model3.json` file and check `meapet_boot.log` / `meapet_fault.log`. Set `MEAPET_FORCE_PNG=1` to test the PNG path. Runtime switching resyncs window geometry; if still broken, attach logs and display scale info.
</details>

## License

Project code is MIT Licensed. Live2D Cubism Core is a proprietary component of Live2D Inc. and requires compliance with its own software license agreement. Character, model, and voice resource copyrights belong to their respective authors. Dependencies such as GPT-SoVITS, VITS, and Ollama are governed by their respective licenses.
