"""Cross-platform global hotkey registration.

Prefers the `keyboard` library (Windows), falls back to `pynput` (macOS/Linux). Returns a
callable that unregisters the hotkey, or None if no backend could register it. Never raises.
"""

from __future__ import annotations

from typing import Callable, Optional


def _to_pynput(combo: str) -> str:
    """Convert 'ctrl+alt+s' to pynput's '<ctrl>+<alt>+s' format."""
    mods = {"ctrl", "alt", "shift", "cmd", "win", "super"}
    parts = []
    for p in combo.lower().split("+"):
        p = p.strip()
        parts.append(f"<{p}>" if p in mods else p)
    return "+".join(parts)


def start_hotkey(combo: str, callback: Callable[[], None]) -> Optional[Callable[[], None]]:
    # keyboard (great on Windows)
    try:
        import keyboard

        keyboard.add_hotkey(combo, callback)
        return lambda: keyboard.remove_hotkey(combo)
    except Exception:
        pass

    # pynput (macOS / Linux)
    try:
        from pynput import keyboard as pk

        listener = pk.GlobalHotKeys({_to_pynput(combo): callback})
        listener.start()
        return listener.stop
    except Exception:
        return None
