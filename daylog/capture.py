"""The always-on capture loop.

Responsibilities:
  - sample the foreground window + idle time with the built-in watcher (no external service),
  - record window / afk / browser-tab stretches into the events timeline,
  - take screenshots on window-switch (debounced) and on an interval while active,
  - skip excluded apps and skip while the user is AFK,
  - drop visually-unchanged frames,
  - run OCR in a background thread.

Stop with Ctrl-C.
"""

from __future__ import annotations

import signal
import threading
import time

from . import db, ocr, screenshot, watcher
from .config import Config, get_config

UNCHANGED_HAMMING = 4  # avg-hash distance below which a frame is "the same"


def _excluded(app: str | None, cfg: Config) -> bool:
    if not app:
        return False
    low = app.lower()
    return any(x.lower() in low for x in cfg.capture.exclude_apps)


def capture_once(cfg, conn, source: str = "manual") -> int | None:
    """Capture a single screenshot tagged with the current app/title. Returns its id."""
    state = watcher.current_state(cfg.capture.idle_threshold_seconds, cfg.watcher.track_urls)
    if source == "manual" or not _excluded(state.app, cfg):
        shot_id, _ = screenshot.capture(cfg, conn, state.app, state.title, source=source)
        return shot_id
    return None


def run_capture(cfg: Config | None = None, stop_event: threading.Event | None = None) -> int:
    """Run the capture loop. If `stop_event` is given (e.g. from the tray), the loop is
    controlled by it and no SIGINT handler is installed (safe to run off the main thread)."""
    cfg = cfg or get_config()
    embedded = stop_event is not None
    conn = db.init_db(cfg.db_path)
    recorder = watcher.Recorder(conn, cfg.capture.idle_threshold_seconds)

    if not ocr.available():
        print("(OCR backend not installed - screenshots will be captured without text. "
              "Install rapidocr-onnxruntime or pytesseract later, then run backfill-ocr.)")

    # Apply retention once on startup.
    try:
        from . import retention

        stats = retention.purge(conn, cfg)
        if stats["raw_purged"] or stats["mb_freed"]:
            print(f"Retention: thinned {stats['raw_purged']} old image(s), "
                  f"freed {stats['mb_freed']} MB.")
    except Exception as exc:
        print(f"  retention error: {exc}")

    stop = stop_event or threading.Event()

    # OCR worker gets its own connection (sqlite connections are per-thread).
    ocr_thread = threading.Thread(
        target=ocr.run_worker,
        args=(lambda: db.connect(cfg.db_path), stop),
        daemon=True,
    )
    ocr_thread.start()

    if not embedded:
        def handle_sigint(_sig, _frame):
            stop.set()

        signal.signal(signal.SIGINT, handle_sigint)

    last_key: tuple | None = None          # (app, title) of last seen window
    last_switch_ts = 0.0
    last_interval_ts = 0.0
    last_tail_ts = 0.0
    last_purge_ts = time.monotonic()      # purged once already above
    last_hash: int | None = None
    shots = 0

    print(f"daylog capturing. Screenshots -> {cfg.screenshots_dir}")
    if not embedded:
        print("Press Ctrl-C to stop.\n")

    while not stop.is_set():
        now = time.monotonic()
        state = watcher.current_state(cfg.capture.idle_threshold_seconds, cfg.watcher.track_urls)

        # Fold this observation into the open window/afk/web stretches, then persist any
        # that just closed — so a reader (dashboard, summarize) sees the timeline as it is.
        recorder.sample(state)
        conn.commit()

        # Tail shell history periodically.
        if now - last_tail_ts >= cfg.watcher.poll_seconds:
            try:
                from . import terminal

                terminal.tail(conn, cfg)
            except Exception as exc:
                print(f"  terminal tail error: {exc}")
            last_tail_ts = now

        # Apply retention once a day.
        if now - last_purge_ts >= 86400:
            try:
                from . import retention

                retention.purge(conn, cfg)
            except Exception as exc:
                print(f"  retention error: {exc}")
            last_purge_ts = now

        if state.afk:
            stop.wait(2.0)
            continue

        key = (state.app, state.title)
        switched = key != last_key
        last_key = key

        want_shot = False
        source = "auto"
        if switched and (now - last_switch_ts) >= cfg.capture.switch_debounce_seconds:
            want_shot, source, last_switch_ts = True, "switch", now
        elif (now - last_interval_ts) >= cfg.capture.interval_seconds:
            want_shot, source = True, "auto"

        if want_shot and not _excluded(state.app, cfg):
            try:
                shot_id, ahash = screenshot.capture(
                    cfg, conn, state.app, state.title, source=source
                )
                # Drop the row if it is a near-duplicate of the previous frame.
                if last_hash is not None and screenshot.hamming(ahash, last_hash) <= UNCHANGED_HAMMING:
                    screenshot.delete_screenshot(conn, shot_id)
                else:
                    shots += 1
                    last_interval_ts = now
                    last_hash = ahash
            except Exception as exc:
                print(f"  capture error: {exc}")

        stop.wait(2.0)

    # Close the stretch that was open when we were told to stop, or the last window of the
    # session (often the longest) never lands in the timeline.
    recorder.flush()
    print(f"\nStopped. Captured {shots} screenshot(s) this session.")
    conn.commit()
    return 0


def manual_shot(cfg: Config | None = None) -> int:
    """Take one manual (badged) screenshot immediately. Used by the hotkey / `shot` cmd."""
    cfg = cfg or get_config()
    conn = db.init_db(cfg.db_path)
    shot_id = capture_once(cfg, conn, source="manual")
    if shot_id:
        print(f"Manual screenshot saved (id {shot_id}) -> badged in the timeline.")
        return 0
    print("Could not capture screenshot.")
    return 1
