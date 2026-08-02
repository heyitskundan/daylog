"""System-tray shell (pystray) — the Phase 3 desktop front-end.

Runs entirely under the trusted Python interpreter (no compiled binaries, so it works under
Windows Smart App Control). Provides a tray icon with:
  - start/stop the capture loop (in a background thread),
  - open the dashboard (starts the local web server + browser),
  - take a manual (badged) screenshot,
  - summarize + publish today's notes to Obsidian,
  - quit.

Run with `daylog tray`. Requires the tray extra: `uv sync --extra tray`.
"""

from __future__ import annotations

import threading
import webbrowser

from . import capture, icon
from .config import Config, get_config


class CaptureController:
    """Starts/stops the capture loop on a background thread."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=capture.run_capture,
            kwargs={"cfg": self.cfg, "stop_event": self._stop},
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._stop:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self._stop = None


def run_tray(cfg: Config | None = None) -> int:
    try:
        import pystray
    except Exception:
        print("Tray support not installed. Run: uv sync --extra tray")
        return 1

    cfg = cfg or get_config()
    capture_ctl = CaptureController(cfg)
    web_state: dict = {"server": None, "url": None}

    def ensure_web() -> str:
        if web_state["server"] is None:
            from . import web

            server, url = web.serve_background(controller=capture_ctl)
            web_state["server"] = server
            web_state["url"] = url
        return web_state["url"]

    def notify(icon_obj, title, msg):
        try:
            icon_obj.notify(msg, title)
        except Exception:
            pass

    # --- menu actions ---
    def toggle_capture(icon_obj, _item):
        if capture_ctl.running:
            capture_ctl.stop()
            notify(icon_obj, "daylog", "Capture stopped.")
        else:
            capture_ctl.start()
            notify(icon_obj, "daylog", "Capture started.")
        icon_obj.update_menu()

    def open_dashboard(icon_obj, _item):
        webbrowser.open(ensure_web())

    def take_shot(icon_obj, _item):
        def _run():
            try:
                capture.manual_shot(cfg)
                notify(icon_obj, "daylog", "Manual screenshot saved (badged).")
            except Exception as exc:
                notify(icon_obj, "daylog", f"Screenshot failed: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def summarize(icon_obj, _item):
        def _run():
            from datetime import datetime

            from . import db, llm
            from .label import label_day
            from .obsidian import PublishError, publish_day
            from .segment import segment_day
            from .util import local_date_str

            notify(icon_obj, "daylog", "Summarizing today…")
            try:
                conn = db.init_db(cfg.db_path)
                date_str = local_date_str(datetime.now())
                segment_day(conn, cfg, date_str)
                label_day(conn, cfg, date_str)
                result = publish_day(conn, cfg, date_str)
                notify(icon_obj, "daylog", f"Published to Obsidian: {result['tasks']} task(s).")
            except (llm.LLMError, PublishError) as exc:
                notify(icon_obj, "daylog", f"Summarize failed: {exc}")
            except Exception as exc:
                notify(icon_obj, "daylog", f"Summarize error: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def quit_app(icon_obj, _item):
        capture_ctl.stop()
        if web_state["server"]:
            try:
                web_state["server"].shutdown()
            except Exception:
                pass
        icon_obj.stop()

    def capture_label(_item):
        return "Stop capture" if capture_ctl.running else "Start capture"

    menu = pystray.Menu(
        pystray.MenuItem(capture_label, toggle_capture),
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("Take screenshot", take_shot),
        pystray.MenuItem("Summarize + publish today", summarize),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )

    tray_icon = pystray.Icon("daylog", icon.render(64), "daylog", menu)

    # Register the global screenshot hotkey.
    from . import hotkey

    hk_stop = hotkey.start_hotkey(cfg.capture.hotkey, lambda: take_shot(tray_icon, None))
    if hk_stop:
        print(f"Global screenshot hotkey: {cfg.capture.hotkey}")
    else:
        print("Could not register global hotkey (use the tray menu to screenshot).")

    # Make quit also tear down the hotkey.
    original_quit = quit_app

    def quit_and_cleanup(icon_obj, item):
        if hk_stop:
            try:
                hk_stop()
            except Exception:
                pass
        original_quit(icon_obj, item)

    tray_icon.menu = pystray.Menu(
        pystray.MenuItem(capture_label, toggle_capture),
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("Take screenshot", take_shot),
        pystray.MenuItem("Summarize + publish today", summarize),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_and_cleanup),
    )

    # Start capturing and open the dashboard immediately — launching the app shows the UI.
    capture_ctl.start()
    try:
        webbrowser.open(ensure_web())
    except Exception:
        pass
    print("daylog tray running. The dashboard should open in your browser.")
    tray_icon.run()
    return 0
