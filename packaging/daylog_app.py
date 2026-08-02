"""Frozen-app entry point for the packaged Windows build.

The installed daylog.exe is a windowed (no-console) app whose job is to run the
system-tray shell. Everything the end user needs — start/stop capture, open the
dashboard, screenshots, summarize + publish — lives in that tray menu.
"""
from __future__ import annotations

import multiprocessing
import sys

from daylog.cli import main


def run() -> int:
    # PyInstaller-frozen apps that may spawn child processes need this.
    multiprocessing.freeze_support()
    # Default to the tray; still allow `daylog.exe <command>` for advanced use.
    argv = sys.argv[1:] or ["tray"]
    return main(argv)


if __name__ == "__main__":
    sys.exit(run())
