# Building the daylog Windows installer

This produces `daylog-setup.exe` — a double-click installer that puts daylog in the
Start Menu (with icon), optionally on the Desktop, and optionally starts it at login.
No terminal, cmd, Python, or `uv` is needed on the machine that *runs* the installer.

## One-shot build

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The installer lands at `dist\daylog-setup.exe` (all distributable artifacts live
under `dist\`: the app bundle in `dist\daylog\`, the installer next to it).

## What it does, step by step

1. `uv sync --extra tray --group build` — installs the tray deps and PyInstaller.
2. `packaging\make_icon.py` — renders `daylog.ico` from `daylog/icon.py`.
3. `pyinstaller packaging\daylog.spec` — bundles a **windowed** `daylog.exe`
   (no console window) plus its runtime into `dist\daylog\`.
4. Inno Setup (`packaging\daylog.iss`) — wraps `dist\daylog\` into the installer.

## Prerequisites (build machine only)

- [uv](https://docs.astral.sh/uv/) and Python 3.11+.
- [Inno Setup 6](https://jrsoftware.org/isdl.php): `winget install JRSoftware.InnoSetup`.

## How the installed app behaves

- **Per-user install** into `%LocalAppData%\Programs\daylog` — no admin prompt, and the
  folder is writable so the in-app Settings can save `config.toml` there.
- Launch from the Start Menu (or Desktop) icon → a **tray icon** appears and the
  dashboard opens in the browser. Everything (capture, screenshots, summarize + publish)
  is in the tray menu.
- Config lives in `%LocalAppData%\daylog\config.toml`; data (DB + screenshots) in
  `%USERPROFILE%\.daylog`.
- Uninstall via **Settings → Apps** or the Start Menu "Uninstall daylog" entry.

## Notes / caveats

- The installed app is self-contained: the activity watcher (Windows APIs), OCR (the OS OCR
  engine) and the task labeler all ship inside it. Nothing else has to be installed for
  capture, summarizing and publishing to work.
- An LLM is optional. Point daylog at LM Studio / Ollama (local) or Claude / OpenAI (cloud) in
  Settings for nicer prose; with no provider it falls back to the built-in labeler.
- Obsidian is optional too: with no vault configured, notes are written to `<data_dir>\notes`
  and can be read in the dashboard.
- The exe is unsigned, so Windows SmartScreen may show a "Windows protected your PC"
  prompt on first run → **More info → Run anyway**. To remove it, sign the exe and
  installer with a code-signing certificate.
