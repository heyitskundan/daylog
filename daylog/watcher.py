"""Native activity watcher — the built-in replacement for ActivityWatch.

Samples the foreground window (app + title), how long the user has been idle, and the active
browser tab's URL, using nothing but the Windows APIs already on the machine. No external
service to install or keep running.

It writes the same `events` rows the segmenter reads, so the rest of the pipeline is unchanged:

  source='window'  ts_start..ts_end, app, title   -- one row per focused window stretch
  source='afk'     ts_start..ts_end, extra={"status": "afk"}  -- one row per away stretch
  source='web'     ts_start..ts_end, url, title   -- active browser tab, while it is focused

Rows are only written when a stretch *ends* (the window changed, the user went away or came
back, or the process is shutting down), so each row carries a real duration. `flush()` closes
whatever is still open — call it before reading the day, or a long final stretch is lost.

Everything degrades rather than fails: no foreground window yields (None, None), and URL
lookup is best-effort (see url_for_window).
"""

from __future__ import annotations

import ctypes
import sqlite3
from ctypes import wintypes
from dataclasses import dataclass
from datetime import timedelta

from .util import iso, parse_iso, utcnow

# Browser processes whose address bar we try to read.
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]


@dataclass
class CurrentState:
    """What the user is doing right now (same shape the old AW client returned)."""
    app: str | None = None
    title: str | None = None
    url: str | None = None
    afk: bool = False
    idle_secs: float = 0.0
    hwnd: int = 0


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input, system-wide."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # GetTickCount wraps every ~49.7 days and dwTime comes from the same clock, so the
    # unsigned difference stays correct across the wrap.
    elapsed_ms = (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def _process_name(pid: int) -> str | None:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value.rsplit("\\", 1)[-1]      # "…\\chrome.exe" -> "chrome.exe"
    finally:
        _kernel32.CloseHandle(handle)


def foreground_window() -> tuple[str | None, str | None, int]:
    """(app, title, hwnd) of the focused window. app is the exe name, e.g. 'chrome.exe'."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None, None, 0
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _process_name(pid.value), (buf.value or None), int(hwnd)


_url_cache: dict[tuple[int, str], str | None] = {}


def url_for_window(hwnd: int, app: str | None, title: str | None = None) -> str | None:
    """The active tab's URL, read from the browser's address bar via UI Automation.

    Best-effort by design: UI Automation is a cross-process call that can be slow or simply
    return nothing (the bar is virtualised, the window is mid-navigation, the browser isn't
    one we know). Any failure just means "no URL this sample" — the timeline still records
    the window, and the segmenter treats a missing host as "not research".

    The lookup costs ~150ms, so it is cached per (window, title): the title changes whenever
    the tab or page does, which is exactly when the URL can have changed.
    """
    if not hwnd or not app or app.lower() not in BROWSERS:
        return None
    key = (hwnd, title or "")
    if key in _url_cache:
        return _url_cache[key]
    if len(_url_cache) > 256:
        _url_cache.clear()
    url = _read_address_bar(hwnd)
    _url_cache[key] = url
    return url


def _read_address_bar(hwnd: int) -> str | None:
    try:
        import uiautomation as auto
    except Exception:
        return None
    try:
        control = auto.ControlFromHandle(hwnd)
        if control is None:
            return None
        # The address bar is the first edit control that exposes a value.
        edit = control.EditControl(searchDepth=12)
        if not edit.Exists(maxSearchSeconds=0.2):
            return None
        value = (edit.GetValuePattern().Value or "").strip()
    except Exception:
        return None
    if not value or " " in value:        # a search phrase, not a URL
        return None
    if not value.startswith(("http://", "https://")):
        if "." not in value.split("/", 1)[0]:
            return None
        value = "https://" + value        # chrome hides the scheme
    return value


def current_state(idle_threshold: float, track_urls: bool = True) -> CurrentState:
    """Snapshot of what the user is doing right now."""
    app, title, hwnd = foreground_window()
    idle = idle_seconds()
    return CurrentState(
        app=app,
        title=title,
        url=url_for_window(hwnd, app, title) if track_urls else None,
        afk=idle >= idle_threshold,
        idle_secs=idle,
        hwnd=hwnd,
    )


class Recorder:
    """Turns a stream of samples into `events` rows with real durations.

    Holds the currently-open window / afk / web stretch and closes it out the moment the
    sampled state changes. The AFK stretch is written with a "status" in `extra` because that
    is what segment._afk_intervals reads to subtract away-time from a segment.
    """

    # A stretch is cut and rewritten at least this often, so the timeline is never more than
    # a minute stale and a crash costs at most a minute. The segmenter re-merges consecutive
    # events with the same app + title, so cutting a long stretch changes nothing downstream.
    MAX_STRETCH_SECS = 60

    def __init__(self, conn: sqlite3.Connection, idle_threshold: float):
        self.conn = conn
        self.idle_threshold = idle_threshold
        self._window: dict | None = None      # {"app","title","start","end"}
        self._afk: dict | None = None         # {"start","end"}
        self._web: dict | None = None         # {"url","title","start","end"}

    # -- writing -------------------------------------------------------------
    def _insert(self, source: str, start: str, end: str, *, app=None, title=None,
                url=None, extra: str | None = None) -> None:
        if parse_iso(end) <= parse_iso(start):
            return                             # zero-length stretch: nothing to record
        self.conn.execute(
            "INSERT INTO events(ts_start, ts_end, source, app, title, url, extra) "
            "VALUES(?,?,?,?,?,?,?)",
            (start, end, source, app, title, url, extra),
        )

    def _close_window(self) -> None:
        if self._window:
            self._insert("window", self._window["start"], self._window["end"],
                         app=self._window["app"], title=self._window["title"])
            self._window = None

    def _close_web(self) -> None:
        if self._web:
            self._insert("web", self._web["start"], self._web["end"],
                         url=self._web["url"], title=self._web["title"])
            self._web = None

    def _close_afk(self) -> None:
        if self._afk:
            self._insert("afk", self._afk["start"], self._afk["end"],
                         extra='{"status": "afk"}')
            self._afk = None

    # -- sampling ------------------------------------------------------------
    def sample(self, state: CurrentState, now: str | None = None) -> None:
        """Fold one observation into the open stretches."""
        now = now or iso(utcnow())

        # AFK: an away stretch starts at the moment input stopped, not when we noticed.
        if state.afk:
            if self._afk is None:
                went_away = iso(parse_iso(now) - timedelta(seconds=state.idle_secs))
                self._afk = {"start": went_away, "end": now}
            else:
                self._afk["end"] = now
            # A window/tab that was open when the user walked away ends there — the AFK row
            # already accounts for the away time, and leaving it open would stretch the
            # window's duration across the whole break.
            self._close_window()
            self._close_web()
            return
        self._close_afk()

        if state.app is None and state.title is None:
            self._close_window()
            self._close_web()
            return

        key = (state.app, state.title)
        if (self._window is None
                or (self._window["app"], self._window["title"]) != key
                or self._stale(self._window, now)):
            self._close_window()
            self._window = {"app": state.app, "title": state.title, "start": now, "end": now}
        else:
            self._window["end"] = now

        if state.url:
            if (self._web is None or self._web["url"] != state.url
                    or self._stale(self._web, now)):
                self._close_web()
                self._web = {"url": state.url, "title": state.title, "start": now, "end": now}
            else:
                self._web["end"] = now
        else:
            self._close_web()

    def _stale(self, stretch: dict, now: str) -> bool:
        span = (parse_iso(now) - parse_iso(stretch["start"])).total_seconds()
        return span >= self.MAX_STRETCH_SECS

    def flush(self) -> None:
        """Close every open stretch and commit. Safe to call repeatedly."""
        self._close_window()
        self._close_web()
        self._close_afk()
        self.conn.commit()


def status(cfg=None) -> dict:
    """Health of the watcher, for `daylog status` and the Settings page."""
    app, title, _hwnd = foreground_window()
    urls = False
    try:
        import uiautomation  # noqa: F401
        urls = True
    except Exception:
        pass
    return {
        "ok": True,                       # the Windows APIs are always there
        "app": app,
        "title": title,
        "idle_secs": round(idle_seconds(), 1),
        "urls": urls,
        "detail": f"watching {app or 'nothing'}" + ("" if urls else " · no URL tracking"),
    }
