"""daylog command-line entry point.

Usage:
    python -m daylog.cli init          # create the database
    python -m daylog.cli verify        # check the built-in watcher sees your desktop
    python -m daylog.cli run           # start the capture loop (Ctrl-C to stop)
    python -m daylog.cli web           # serve the dashboard
"""

from __future__ import annotations

import argparse
import sys
import time

from . import db
from .config import get_config


def cmd_status(_args) -> int:
    from . import autostart, llm, ocr, watcher

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    nshots = conn.execute("SELECT COUNT(*) c FROM screenshots WHERE deleted=0").fetchone()["c"]
    nseg = conn.execute("SELECT COUNT(*) c FROM segments").fetchone()["c"]
    ntasks = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]

    w = watcher.status(cfg)
    ai_ready, ai_reason = llm.availability(cfg)
    vp = cfg.obsidian.vault_path
    st = autostart.status()

    print("daylog status")
    print(f"  data dir      : {cfg.storage.data_dir}")
    print(f"  database      : {nshots} screenshots, {nseg} segments, {ntasks} tasks")
    print(f"  watcher       : OK  (focused: {w['app'] or '-'}, idle {w['idle_secs']}s, "
          f"browser URLs: {'yes' if w['urls'] else 'no'})")
    print(f"  AI ({cfg.ai.provider}){' ' * max(0, 9 - len(cfg.ai.provider))}: "
          f"{'OK' if ai_ready else 'not configured - ' + ai_reason + ' (falls back to the built-in labeler)'}")
    print(f"  notes         : {vp}  ({'exists' if vp and vp.exists() else 'MISSING'})")
    print(f"  OCR           : {'available' if ocr.available() else 'not available (text not searchable)'}")
    print(f"  screenshot hotkey : {cfg.capture.hotkey}")
    print(f"  autostart     : {'ENABLED' if st['enabled'] else 'disabled'}")
    return 0


def cmd_init(_args) -> int:
    cfg = get_config()
    db.init_db(cfg.db_path)
    print(f"Initialized database at {cfg.db_path}")
    print(f"Screenshots will be stored under {cfg.screenshots_dir}")
    return 0


def cmd_verify(_args) -> int:
    """Watch the desktop for a few seconds and show what the built-in watcher sees."""
    from . import ocr, watcher

    cfg = get_config()
    print("Sampling the foreground window for 5 seconds — switch windows to see it follow.\n")
    seen: set[tuple] = set()
    for _ in range(5):
        s = watcher.current_state(cfg.capture.idle_threshold_seconds)
        seen.add((s.app, s.title))
        flag = "AFK" if s.afk else f"idle {s.idle_secs:4.0f}s"
        print(f"  {flag}  {s.app or '-':22} {(s.title or '')[:48]:48} {s.url or ''}")
        time.sleep(1)

    apps = {a for a, _ in seen if a}
    print()
    ok = bool(apps)
    print(f"[{' OK ' if ok else 'FAIL'}] Active window + title   ({len(apps)} app(s) seen)")
    print(f"[ OK ] AFK / idle detection    (idle timer reads {watcher.idle_seconds():.0f}s)")
    urls = watcher.status(cfg)["urls"]
    print(f"[{' OK ' if urls else 'MISS'}] Browser tab URL         "
          f"{'' if urls else '-> uiautomation not installed; hosts will be missing'}")
    print(f"[{' OK ' if ocr.available() else 'MISS'}] Screenshot text (OCR)   "
          f"{'' if ocr.available() else '-> no OCR engine; screenshot search will be empty'}")
    if not ok:
        print("\nNo foreground window was readable. Capture will still run, but the timeline "
              "will be empty — check that daylog is not blocked from reading window titles.")
        return 1
    print("\nCapture is ready. Nothing else to install.")
    return 0


def cmd_run(_args) -> int:
    from .capture import run_capture

    return run_capture()


def cmd_shot(_args) -> int:
    from .capture import manual_shot

    return manual_shot()


def cmd_backfill_ocr(_args) -> int:
    from . import ocr

    cfg = get_config()
    if not ocr.available():
        print("No OCR backend installed. Install rapidocr-onnxruntime or pytesseract first.")
        return 1
    conn = db.init_db(cfg.db_path)
    rows = conn.execute(
        "SELECT id, path FROM screenshots WHERE ocr_done = 0 AND deleted = 0 ORDER BY id"
    ).fetchall()
    print(f"OCR backfill: {len(rows)} screenshot(s) to process...")
    for i, row in enumerate(rows, 1):
        text = ocr.ocr_image(row["path"])
        conn.execute(
            "UPDATE screenshots SET ocr_text = ?, ocr_done = 1 WHERE id = ?",
            (text, row["id"]),
        )
        if i % 25 == 0:
            conn.commit()
            print(f"  {i}/{len(rows)}")
    conn.commit()
    print("Done.")
    return 0


def cmd_segment(args) -> int:
    from .segment import segment_day
    from .util import human_duration, local_date_str

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    date_str = args.date or local_date_str()
    segments = segment_day(conn, cfg, date_str)
    print(f"{date_str}: {len(segments)} segment(s)")
    for s in segments:
        flag = "  <- struggle" if s.signals.get("struggle_score", 0) >= 2 else ""
        proj = f" [{s.project_guess}]" if s.project_guess else ""
        host = f" {{{s.url_host}}}" if s.url_host else ""
        print(
            f"  {s.ts_start[11:16]}  {human_duration(s.active_secs):>7}  "
            f"{(s.primary_app or '?'):<18}{proj}{host}{flag}"
        )
    return 0


def cmd_purge(_args) -> int:
    from .retention import purge

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    stats = purge(conn, cfg)
    print(
        f"Purge (keep raw {stats['retention_days']}d): "
        f"{stats['raw_purged']} image(s) thinned, {stats['deleted_cleaned']} deleted cleaned, "
        f"{stats['mb_freed']} MB freed, {stats['empty_dirs_removed']} empty folder(s) removed."
    )
    return 0


def cmd_ai_check(_args) -> int:
    from . import llm

    cfg = get_config()
    print(f"AI provider: {cfg.ai.provider}")
    ready, reason = llm.availability(cfg)
    if not ready:
        print(f"[FAIL] {reason}")
        return 1
    print(f"[ OK ] reachable ({reason}). Running a tiny test completion...")
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean"}, "model_said": {"type": "string"}},
        "required": ["ok", "model_said"],
    }
    try:
        out = llm.complete_json(
            cfg,
            "You are a connection test. Reply with valid JSON only.",
            "Set ok=true and model_said to a 3-word greeting.",
            schema,
        )
    except llm.LLMError as exc:
        print(f"[FAIL] completion failed: {exc}")
        return 1
    print(f"[ OK ] structured output works -> {out}")
    return 0


def cmd_label(args) -> int:
    from . import llm
    from .label import label_day
    from .util import human_duration, local_date_str

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    date_str = args.date or local_date_str()
    try:
        tasks = label_day(conn, cfg, date_str)
    except llm.LLMError as exc:
        print(f"[FAIL] {exc}")
        return 1
    if not tasks:
        print(f"{date_str}: no segments to label (run `daylog segment` first).")
        return 0
    print(f"{date_str}: proposed {len(tasks)} task(s) via {cfg.ai.provider}:")
    for t in tasks:
        marks = []
        if t.get("struggled"):
            marks.append("struggled")
        if t.get("learned"):
            marks.append("learned")
        tag = f"  [{', '.join(marks)}]" if marks else ""
        print(f"  - {t['name']}  ({human_duration(t['total_secs'])}){tag}")
    print("\nReview/edit in the dashboard, then `daylog publish` to write to Obsidian.")
    return 0


def cmd_publish(args) -> int:
    from .obsidian import PublishError, publish_day
    from .util import local_date_str

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    date_str = args.date or local_date_str()
    try:
        result = publish_day(conn, cfg, date_str)
    except PublishError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"Published {date_str} to Obsidian:")
    print(f"  daily note: {result['daily_note']}")
    print(f"  {result['tasks']} task note(s) in {result['vault']}")
    return 0


def cmd_summarize(args) -> int:
    """segment -> label -> publish, for a full end-of-day run."""
    from . import llm
    from .label import label_day
    from .obsidian import PublishError, publish_day
    from .segment import segment_day
    from .util import local_date_str

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    date_str = args.date or local_date_str()

    segs = segment_day(conn, cfg, date_str)
    print(f"{date_str}: {len(segs)} segment(s).")
    if not segs:
        return 0
    try:
        tasks = label_day(conn, cfg, date_str)
        print(f"Labeled {len(tasks)} task(s) via {cfg.ai.provider}.")
        result = publish_day(conn, cfg, date_str)
        print(f"Published to {result['daily_note']}")
    except (llm.LLMError, PublishError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


def cmd_web(args) -> int:
    from .web import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_tray(_args) -> int:
    from .tray import run_tray

    return run_tray()


def cmd_autostart(args) -> int:
    from . import autostart

    if args.launcher:
        p = autostart.write_launcher()
        print(f"Created double-click launcher:\n  {p}\nDouble-click it to start daylog.")
        return 0
    if args.enable:
        r = autostart.enable()
        print(f"Autostart enabled. daylog tray will launch on login.\n  {r['path']}")
    elif args.disable:
        r = autostart.disable()
        print(f"Autostart disabled.\n  {r['path']}")
    else:
        r = autostart.status()
        print(f"Autostart: {'ENABLED' if r['enabled'] else 'disabled'}\n  {r['path']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daylog")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)
    sub.add_parser("status", help="health check: data, watcher, AI, notes, autostart").set_defaults(
        func=cmd_status
    )
    sub.add_parser("verify", help="check the built-in watcher sees your desktop").set_defaults(
        func=cmd_verify
    )
    sub.add_parser("run", help="start the capture loop").set_defaults(func=cmd_run)
    sub.add_parser("shot", help="take one manual (badged) screenshot").set_defaults(func=cmd_shot)
    sub.add_parser("backfill-ocr", help="OCR any screenshots missing text").set_defaults(
        func=cmd_backfill_ocr
    )

    seg = sub.add_parser("segment", help="rebuild segments for a day")
    seg.add_argument("--date", help="YYYY-MM-DD (default: today)")
    seg.set_defaults(func=cmd_segment)

    sub.add_parser("ai-check", help="check the configured AI provider is reachable").set_defaults(
        func=cmd_ai_check
    )
    sub.add_parser("purge", help="apply screenshot retention (thin old raw images)").set_defaults(
        func=cmd_purge
    )

    lab = sub.add_parser("label", help="AI-label the day's tasks (opt-in)")
    lab.add_argument("--date", help="YYYY-MM-DD (default: today)")
    lab.set_defaults(func=cmd_label)

    pub = sub.add_parser("publish", help="publish the day to your Obsidian vault")
    pub.add_argument("--date", help="YYYY-MM-DD (default: today)")
    pub.set_defaults(func=cmd_publish)

    summ = sub.add_parser("summarize", help="segment + label + publish (end-of-day)")
    summ.add_argument("--date", help="YYYY-MM-DD (default: today)")
    summ.set_defaults(func=cmd_summarize)

    web = sub.add_parser("web", help="serve the dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.set_defaults(func=cmd_web)

    sub.add_parser("tray", help="run the system-tray desktop shell").set_defaults(func=cmd_tray)

    auto = sub.add_parser("autostart", help="launch the tray on login (enable/disable/status)")
    auto_grp = auto.add_mutually_exclusive_group()
    auto_grp.add_argument("--enable", action="store_true", help="enable autostart")
    auto_grp.add_argument("--disable", action="store_true", help="disable autostart")
    auto_grp.add_argument("--launcher", action="store_true",
                          help="create a double-click launcher in the project folder")
    auto.set_defaults(func=cmd_autostart)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
