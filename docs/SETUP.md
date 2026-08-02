# daylog setup

daylog is self-contained: the activity watcher, OCR, segmenter, labeler, dashboard and
markdown publisher are all part of the app. There is **nothing external to install** â€” no
tracker service, no OCR binary, no LLM (one is used only if you want nicer prose).

> **Run daylog natively on Windows, not in WSL.** daylog captures the screen and active
> window of the OS it runs on, using the Windows APIs directly. In WSL it would see the Linux
> side. Use a Windows shell (Windows Terminal â†’ PowerShell). If you accidentally ran
> `uv sync` in WSL, delete the Linux venv first: `Remove-Item .venv -Recurse -Force`, then
> `uv sync` on Windows.

## 1. Install daylog's Python deps with uv â€” (you run)

daylog uses [uv](https://docs.astral.sh/uv/). If you don't have it:
```
winget install astral-sh.uv
```
Then, from wherever you cloned the project (e.g. `cd daylog`):
```
uv sync
```
This creates `.venv` and installs everything from `pyproject.toml` â€” including the built-in
watcher's UI Automation support and the Windows OCR bindings. uv manages the venv for you, so
prefix commands with `uv run` (no manual activate needed).

Optional add-ons:
```
uv sync --extra tray       # system tray + global screenshot hotkey
uv sync --extra ai         # Ollama / Claude SDKs (only if you use those providers)
```
For Claude, set `ANTHROPIC_API_KEY` in your environment. LM Studio and any OpenAI-compatible
server need no extra package â€” they are plain HTTP.

## 2. Configure (optional)

```
copy config.example.toml config.toml
```
Everything has a working default: data lives in `~/.daylog`, notes are written inside that
folder, and no LLM is required. Use `config.toml` (or the dashboard's **Settings** page) to
move the data folder, point at an Obsidian vault, or choose an LLM provider.

## 3. Verify capture

```
uv run daylog verify
```
It samples your desktop for five seconds and reports what it sees. You want `Active window +
title` and `AFK / idle detection` to show `OK`. `Browser tab URL` and `Screenshot text (OCR)`
should show `OK` too â€” both are built in.

## 4. Run it

Start the capture loop (leave it running while you work):
```
uv run daylog run
```
In another terminal, build the day's segments and open the dashboard:
```
uv run daylog segment
uv run daylog web
```
Then open http://127.0.0.1:8765/

## Commands

| Command | What it does |
|---|---|
| `uv run daylog verify` | Check the built-in watcher sees your desktop (and OCR works) |
| `uv run daylog run` | Start the always-on capture loop (Ctrl-C to stop) |
| `uv run daylog shot` | Take one manual screenshot (badged in the UI) |
| `uv run daylog segment [--date YYYY-MM-DD]` | Rebuild segments for a day |
| `uv run daylog web [--port 8765]` | Serve the dashboard |
| `uv run daylog backfill-ocr` | OCR any screenshots captured before OCR was available |
| `uv run daylog label [--date ...]` | Group the day into named tasks (LLM if configured, else built-in) |
| `uv run daylog publish [--date ...]` | Write the day's markdown notes |
| `uv run daylog summarize [--date ...]` | segment + label + publish in one go (end-of-day) |
| `uv run daylog ai-check` | check the configured AI provider is reachable |
| `uv run daylog purge` | apply screenshot retention (thin old raw images) |
| `uv run daylog tray` | run the system-tray desktop shell (needs `uv sync --extra tray`) |
| `uv run daylog status` | health check: data, watcher, AI, notes, autostart |
| `uv run daylog autostart --enable` | launch the tray on login (`--disable` to undo) |

(`daylog` is a console script defined in `pyproject.toml`; `uv run` runs it inside the venv.)

## End-of-day: AI summary + Obsidian (Phase 2)

Once you've captured a day and built segments, turn it into documented notes:

```
uv run daylog summarize        # segment -> AI label -> publish to Obsidian
```

Pick an AI provider in `config.toml` under `[ai] provider`:

- **LM Studio (local, currently configured):** open LM Studio, load a model, and enable the
  local server (Developer tab). daylog talks to `http://localhost:1234/v1`. Leave
  `lmstudio_model = ""` to auto-use the loaded model, or pin a stronger one (e.g.
  `qwen/qwen3-30b-a3b`). No API key, nothing leaves your machine.
- **Ollama (local):** install [Ollama](https://ollama.com), `ollama pull llama3.1`,
  set `provider = "ollama"`. (`uv sync --extra ai` for the Ollama client.)
- **Claude (cloud, highest quality):** `provider = "claude"`, export `ANTHROPIC_API_KEY`
  (`uv sync --extra ai`). Uses `claude-opus-4-8`.
- **OpenAI / any OpenAI-compatible:** `provider = "openai"`, set `openai_base_url` +
  `openai_model`, export `OPENAI_API_KEY`.

Verify the connection any time with:
```
uv run daylog ai-check
```

The labeler proposes named tasks with sub-steps and flags where you struggled/learned (every
duration is computed deterministically from segments, not by the LLM). When you work in
**Claude Code**, daylog reads your session transcripts (`~/.claude/projects/**`) as the primary
signal â€” your prompts become the task intent and Claude's actions/errors become the evidence,
so tasks are named for the real work ("Deploy ValueLyne backend") instead of "Code.exe". Idle
time is subtracted, so durations reflect actual active time. `publish` then writes a daily
note that links to one note per task, with screenshots embedded and your terminal commands
folded in â€” into your Obsidian vault if you configured one, otherwise into `<data_dir>/notes`.
Either way you can read it in the app. Re-running overwrites the same day's files.

## Desktop tray shell (Phase 3, in progress)

daylog can run from the system tray instead of a terminal (no compiled binaries, so it works
under Windows Smart App Control). One-time:

```
uv sync --extra tray
uv run daylog autostart --launcher    # makes "Start daylog.vbs" in the project folder
uv run daylog autostart --enable      # optional: also launch on login
```

After that you never need the terminal: **double-click `Start daylog.vbs`** (or just log in,
if autostart is enabled). The tray icon appears and the dashboard opens in your browser.
Everything is controlled from there â€” Start/Stop capture, a **Settings** page (AI provider,
vault, hotkey, retention, autostart), task editing, and Publish to Obsidian.

The tray menu starts/stops capture, opens the dashboard, takes a manual screenshot, and runs
summarize+publish. A global hotkey (`ctrl+alt+s` by default, configurable) takes a badged
screenshot from anywhere. Enable launch-on-login with `uv run daylog autostart --enable`.

In the dashboard, the **Tasks** section lets you rename tasks, toggle struggled/learned,
confirm them, and **Publish to Obsidian** â€” or **Label with AI** if a day isn't labeled yet.

## Run on a second machine

Because daylog runs under signed Python (no compiled binaries), setup on a new machine is:

1. Install [uv](https://docs.astral.sh/uv/).
2. Copy/clone the project, then `uv sync --extra tray`.
3. Optionally copy `config.toml` and set the Obsidian vault â€” or rely on auto-detect, or skip
   it entirely and let notes live in the data folder.
4. `uv run daylog status` to confirm everything's connected, then
   `uv run daylog autostart --enable`.
