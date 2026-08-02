"""Terminal-history tailer.

Reconstructs the commands you ran by tailing shell history files. PSReadLine (PowerShell)
history has no per-line timestamps, so newly-appended commands are stamped with the time we
observe them (accurate while `daylog run` is live). zsh "extended history" lines carry an
epoch we parse when present.

On first sight of a history file we record the current line count as a baseline so we only
capture commands typed from now on, not your entire shell history.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import db
from .config import Config
from .util import iso, utcnow


def detect_history_files(cfg: Config) -> list[tuple[str, Path]]:
    """Return [(shell, path), ...] for history files that exist on this machine."""
    files: list[tuple[str, Path]] = []

    # PowerShell (Windows) PSReadLine
    ps = cfg.terminal.powershell_history
    if ps is None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            ps = Path(appdata) / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"
    if ps and Path(ps).exists():
        files.append(("powershell", Path(ps)))

    # zsh / bash (macOS, Linux)
    for shell, name in (("zsh", ".zsh_history"), ("bash", ".bash_history")):
        p = Path.home() / name
        if p.exists():
            files.append((shell, p))

    return files


def _parse_zsh_line(line: str) -> tuple[str | None, str]:
    """zsh extended history: ': <epoch>:<elapsed>;<command>'. Returns (ts_iso|None, command)."""
    if line.startswith(": ") and ";" in line:
        try:
            meta, cmd = line.split(";", 1)
            epoch = int(meta.split(":")[1].strip())
            from datetime import datetime, timezone

            return iso(datetime.fromtimestamp(epoch, tz=timezone.utc)), cmd.strip()
        except (ValueError, IndexError):
            return None, line.strip()
    return None, line.strip()


def tail(conn: sqlite3.Connection, cfg: Config) -> int:
    """Insert commands appended to history files since we last looked. Returns count."""
    if not cfg.terminal.enabled:
        return 0

    inserted = 0
    now = iso(utcnow())
    for shell, path in detect_history_files(cfg):
        cursor_key = f"term_lines:{path}"
        seen = int(db.get_sync_state(conn, cursor_key) or -1)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue

        if seen < 0:
            # First sight: baseline at current length, capture nothing retroactively.
            db.set_sync_state(conn, cursor_key, str(len(lines)))
            continue

        if len(lines) <= seen:
            # File rotated/truncated (e.g. cleared) — reset baseline.
            if len(lines) < seen:
                db.set_sync_state(conn, cursor_key, str(len(lines)))
            continue

        new_lines = lines[seen:]
        with db.transaction(conn):
            for raw in new_lines:
                if not raw.strip():
                    continue
                if shell == "zsh":
                    ts, command = _parse_zsh_line(raw)
                    ts = ts or now
                else:
                    ts, command = now, raw.strip()
                if not command:
                    continue
                try:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO terminal_cmds(ts, shell, command) VALUES(?,?,?)",
                        (ts, shell, command),
                    )
                    inserted += cur.rowcount if cur.rowcount > 0 else 0
                except sqlite3.Error:
                    pass
        db.set_sync_state(conn, cursor_key, str(len(lines)))

    return inserted
