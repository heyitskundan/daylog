# daylog

Local-first activity tracker. Watches what you do all day on Windows and, at the end of the
day, shows you the tasks you performed, where your time went, the sub-steps inside each task,
with screenshots, so you can see where you struggled, where you learned something new, and
document it properly.

Everything stays on your machine. Nothing else to install: no tracker service, no OCR binary,
no LLM required.

## How it works

```
built-in watcher (app, window title, browser URL, idle)  ──┐
screenshot + OCR (the OS OCR engine)                       ├─▶  SQLite timeline
shell history (PSReadLine / zsh / bash)                   ──┘        │
                                                                     ▼
                                            deterministic segmenter (time blocks)
                                                                     │
                                    labeler: built-in, or your LLM (local or cloud)
                                                                     │
                              review UI  +  publish the day (daily note → task notes)
```

- **Capture** is built in: the foreground window, its title and your idle time come straight
  from the Windows APIs, and the active browser tab's URL is read from the address bar via UI
  Automation. There is no separate tracker to install or keep running.
- **Screenshot text** is extracted with the OCR engine that ships with Windows, so what was on
  screen is searchable without any external binary or model download.
- **Segmentation** is deterministic (no LLM): it splits the day into blocks whenever the app,
  window title, URL host, or idle state changes, and tags each block with struggle/learn
  signals.
- **Labeling** groups those blocks into named tasks. It works with no AI at all (a built-in
  labeler names tasks from projects, hosts and apps), and uses an LLM when you point it at one
  — local (LM Studio, Ollama) or cloud (Claude, OpenAI). Durations are always computed from
  the segments, never invented by a model.
- **Output** is plain Markdown, written into daylog's own data folder — and, if you have one,
  into your Obsidian vault as well. Notes are readable in the app.

## Status

Capture, timeline, dashboard (day journal + archive + search), labeling, terminal capture, the
markdown publisher, screenshot retention, and a desktop shell (system tray, global screenshot
hotkey, autostart, in-dashboard task edit/publish). See `docs/SETUP.md`.

Quick start: `uv sync --extra tray` then `uv run daylog tray`.

## Setup

This project uses [uv](https://docs.astral.sh/uv/). See [docs/SETUP.md](docs/SETUP.md).
Short version:

1. `uv sync` (installs deps into `.venv` from `pyproject.toml`).
2. `uv run daylog verify` — confirms the watcher sees your desktop and OCR works.
3. `uv run daylog run` to start tracking; `uv run daylog web` for the dashboard.

Optional: copy `config.example.toml` to `config.toml` to move the data folder, point at an
Obsidian vault, or choose an LLM provider. The dashboard's Settings page does all of this too.
