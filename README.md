# MeaPet — Desktop Companion

A Windows-first (Linux-compatible) PyQt5 transparent desktop companion. It integrates character sprites, AI conversation, speech synthesis, screen vision, SQLite memory, and affection into a single desktop frontend, supporting Live2D / PNG dual rendering.

MeaPet draws a clear boundary: character presentation, chat bubbles, TTS, screenshot authorization, and local state are managed by MeaPet. When using an Agent as the reply backend, the model, long-term memory, and internal tools are managed by the Agent.

## Current Capabilities

| Capability | Description |
|------------|-------------|
| Reply backends | Direct model API or Agent — one active at a time, no automatic fallback |
| Direct protocol | OpenAI Chat Completions (always used) — unified across all providers |
| Agent | Hermes API Server, OpenClaw Gateway WebSocket v4 |
| Display | Streaming text bubble without TTS; waits for audio then shows bubble + plays in sync |
| Multi-segment replies | Each segment has its own bubble, mood, voice text, language, and TTS style |
| Speech | MiMo cloud TTS, local GPT-SoVITS, local VITS; GPT-SoVITS supports per-language reference audio |
| Vision | Disabled, inherit main model, or relay via independent vision model |
| Reverse control | Limited Companion MCP: speak, express, read-only state, per-shot screenshot confirmation |
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
QT_QPA_PLATFORM=xcb python pet.py
```

`live2d-py` is optional; falls back to PNG when unavailable. Prebuilt packages are available at [EasyLive2D/live2d-py](https://github.com/EasyLive2D/live2d-py).

The configuration wizard can also be reopened from the pet's right-click menu ("Open Settings…") at any time. After saving, the new backend takes effect immediately; any in-flight generations from the old backend are cancelled.

## Reply Backends

### Direct Model API

Direct mode lets MeaPet manage character prompts, recent context, SQLite memory, and output constraints. All providers connect via the unified OpenAI-compatible interface.

You configure three things in the wizard: the API base URL, the model name (with optional auto-discovery via "Fetch Models"), and your API key. The provider is auto-detected from the URL (deepseek.com → DeepSeek, xiaomimimo.com → MiMo, localhost → Ollama, otherwise custom) for environment variable resolution.

| API Base URL Example | Provider | Environment Variable |
|----------------------|----------|---------------------|
| `https://api.deepseek.com/v1` | DeepSeek | `DEEPSEEK_API_KEY` |
| `https://api.xiaomimimo.com/v1` | MiMo | `MIMO_API_KEY` |
| `http://localhost:11434` | Ollama | (none — key optional) |
| Custom endpoint | Custom | `MEAPET_API_KEY` |

### Agent

Agent mode treats MeaPet as a pure desktop frontend:

- MeaPet calls the Agent to generate replies, providing mood, action, TTS language, and character state as context.
- The Agent uses its own model, memory, and internal tools; MeaPet does not require the Agent to implement additional "memory query" capabilities.
- Internal tool names and raw parameters are not displayed in character bubbles. Safe status (start, complete, failure) enters the timeline; diagnostic details go to logs only.
- Hermes connects via OpenAI Chat Completions + SSE; OpenClaw connects via Gateway WebSocket v4.
- Agent responses must follow the MeaPet segmented output format. Severely malformed fields trigger a single isolated format-repair request; unparseable responses are treated as safe errors.

The current session's `session_id` and long-term scope `session_key` are saved in the local config. Restart defaults to restoring the current session. "Clear memory" in Agent mode explicitly ends the current Agent session and creates a new identity. Old timelines remain readable.

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
      "ja": {"path": "./voices/mea-ja.wav", "text": ""},
      "zh": {"path": "./voices/mea-zh.wav", "text": ""},
      "en": {"path": "./voices/mea-en.wav", "text": ""}
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

When Agent mode is active, an optional standard MCP Streamable HTTP endpoint can be enabled:

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

See `config.example.json` and `docs/backend-and-control.md` for the full schema, Agent, and MCP examples.

### Secrets and Environment Variables

Secret priority: environment variable > `config.json` plaintext. Config values also support `$ENV_VAR` or `${ENV_VAR}` placeholders.

| Environment Variable | Purpose |
|---------------------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek direct connection |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo conversation, vision, or TTS |
| `MEAPET_API_KEY` | Custom direct connection fallback |
| `HERMES_API_SERVER_KEY` / `MEAPET_AGENT_TOKEN` | Hermes / OpenClaw Agent auth |
| `MEAPET_CONTROL_TOKEN` | Companion MCP Bearer Token |
| `GSV_PYTHON` | GPT-SoVITS environment `python.exe` |
| `MEAPET_FORCE_PNG` | Force PNG rendering when set to a non-empty value |
| `MEAPET_DEBUG=1` | Additional protocol-level diagnostic output; leave off by default |

If a real key has ever entered a repository or public log, rotate it immediately at the provider — do not just delete the local text.

## Local Cache and Privacy

- Defaults to retaining the last 5 timeline turns; configurable 0–100 in the wizard.
- Timelines are isolated by direct provider / Agent type + Agent session; switching backends will not cross streams.
- SQLite memory (direct mode) and Agent-owned memory are separate boundaries. MeaPet does not replicate the Agent's long-term memory.
- New config apply, session switches, and token rotation all invalidate old async results — late replies, TTS, or screenshots do not enter the new session.
- Runtime data lives in `mea_memory.db`, `logs/`, `audio_cache/`, `voice_cache/`; none should be distributed.
- Logs record user input, model-visible replies, and TTS translation text by default, rotated daily and retained for 7 days. API keys, auth headers, reasoning, internal tool parameters/results, and screenshot content must NOT be written to logs. `MEAPET_DEBUG=1` is for protocol diagnostics only.

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
│   ├── agent/                         Hermes / OpenClaw and presentation state machine
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

Before editing the pet UI, read `design-system/MASTER.md`, `design-system/pages/desktop.md`, and `meapet/ui_theme.py`. Threading model, window lifecycle, privacy, and distribution constraints are in `AGENTS.md`.

## Custom Character and Sprites

- Character prompt: `SYSTEM_PROMPT` in `meapet/chat/engine.py` and its persona references.
- Expression mapping: `EXPRESSION_MAP` and `MOOD_TO_EXPRESSION` in `meapet/desktop/renderer.py`.
- PNG resource naming: `sprites/mea{outfit_id}{direction}_{expression}.png`.
- Live2D model directory: `live2d.model_dir` in `config.json`.

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
<summary>OpenClaw or remote MCP cannot connect</summary>

Remote OpenClaw should use WSS; plain WS requires explicit opt-in. The Companion MCP listen IP must be a concrete local interface IP, and the allowed IP must match the Agent host. LAN HTTP also requires explicit opt-in; check Windows Firewall for the port.
</details>

<details>
<summary>Live2D does not render, or PNG switch results in wrong size</summary>

Verify that `live2d.model_dir` contains a `.model3.json` file and check `meapet_boot.log` / `meapet_fault.log`. Set `MEAPET_FORCE_PNG=1` to test the PNG path. Runtime switching resyncs window geometry; if still broken, attach logs and display scale info.
</details>

## License

Project code is MIT Licensed. Live2D Cubism Core is a proprietary component of Live2D Inc. and requires compliance with its own software license agreement. Character, model, and voice resource copyrights belong to their respective authors. Dependencies such as GPT-SoVITS, VITS, and Ollama are governed by their respective licenses.
