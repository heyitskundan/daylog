"""Launch the daylog tray on login, cross-platform.

Windows: a .vbs in the Startup folder that runs the venv's pythonw (no console window).
macOS:   a LaunchAgent plist in ~/Library/LaunchAgents.
Linux:   a .desktop file in ~/.config/autostart.

All run the tray under the current Python interpreter, so no compiled binaries are involved
(works under Smart App Control).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import PROJECT_ROOT

APP_ID = "com.kundan.daylog"


def _python_windowless() -> str:
    """pythonw.exe next to the active interpreter (falls back to python.exe)."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def _windows_target() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return startup / "daylog.vbs"


def _mac_target() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{APP_ID}.plist"


def _linux_target() -> Path:
    return Path.home() / ".config/autostart/daylog.desktop"


def target_path() -> Path:
    if sys.platform == "win32":
        return _windows_target()
    if sys.platform == "darwin":
        return _mac_target()
    return _linux_target()


def status() -> dict:
    p = target_path()
    return {"enabled": p.exists(), "path": str(p)}


def enable() -> dict:
    p = target_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    proj = str(PROJECT_ROOT)

    if sys.platform == "win32":
        pyw = _python_windowless()
        # Hidden window (0), don't wait (False); run from the project dir.
        vbs = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{proj}"\r\n'
            f'sh.Run """{pyw}"" -m daylog.cli tray", 0, False\r\n'
        )
        p.write_text(vbs, encoding="utf-8")
    elif sys.platform == "darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{APP_ID}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>-m</string><string>daylog.cli</string><string>tray</string>
  </array>
  <key>WorkingDirectory</key><string>{proj}</string>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
"""
        p.write_text(plist, encoding="utf-8")
    else:
        desktop = (
            "[Desktop Entry]\nType=Application\nName=daylog\n"
            f"Exec={sys.executable} -m daylog.cli tray\nPath={proj}\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        p.write_text(desktop, encoding="utf-8")

    return {"enabled": True, "path": str(p)}


def disable() -> dict:
    p = target_path()
    p.unlink(missing_ok=True)
    return {"enabled": False, "path": str(p)}


def write_launcher() -> Path:
    """Write a double-clickable launcher into the project root (no terminal needed)."""
    proj = Path(PROJECT_ROOT)
    if sys.platform == "win32":
        pyw = _python_windowless()
        p = proj / "Start daylog.vbs"
        p.write_text(
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{proj}"\r\n'
            f'sh.Run """{pyw}"" -m daylog.cli tray", 0, False\r\n',
            encoding="utf-8",
        )
        return p
    # macOS / Linux: a shell script.
    p = proj / "start-daylog.sh"
    p.write_text(
        f'#!/bin/sh\ncd "{proj}"\nexec "{sys.executable}" -m daylog.cli tray\n',
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p
