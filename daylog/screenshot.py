"""Screenshot capture + storage.

Grabs the screen with mss, downscales/compresses with Pillow, writes a full image plus a
thumbnail under the data dir, and records a row in the screenshots table. A cheap average
hash lets the caller skip frames that are visually unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Config
from .util import iso, utcnow

THUMB_WIDTH = 360


def _lazy_imports():
    import mss  # noqa: F401
    from PIL import Image  # noqa: F401

    return mss, Image


def average_hash(image, size: int = 16) -> int:
    """64+ bit average hash for cheap unchanged-frame detection."""
    _lazy_imports()  # ensure PIL is loaded; `image` is already a PIL Image
    small = image.convert("L").resize((size, size))
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px >= avg else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _grab_primary(mss_mod):
    with mss_mod.mss() as sct:
        # monitors[0] is the full virtual screen; monitors[1] is the primary display.
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        raw = sct.grab(monitor)
        return raw


def capture(
    cfg: Config,
    conn: sqlite3.Connection,
    app: str | None,
    title: str | None,
    source: str = "auto",
) -> tuple[int, int]:
    """Capture one screenshot. Returns (screenshot_id, avg_hash)."""
    mss_mod, Image = _lazy_imports()
    raw = _grab_primary(mss_mod)
    img = Image.frombytes("RGB", raw.size, raw.rgb)

    # Downscale to max_width before storing.
    if img.width > cfg.capture.max_width:
        ratio = cfg.capture.max_width / img.width
        img = img.resize((cfg.capture.max_width, int(img.height * ratio)))

    ahash = average_hash(img)

    now = utcnow()
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")[:-3]
    day_dir = cfg.screenshots_dir / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    full_path = day_dir / f"{stamp}.jpg"
    thumb_path = day_dir / f"{stamp}.thumb.jpg"

    img.save(full_path, "JPEG", quality=cfg.capture.jpeg_quality)
    thumb = img.copy()
    if thumb.width > THUMB_WIDTH:
        r = THUMB_WIDTH / thumb.width
        thumb = thumb.resize((THUMB_WIDTH, int(thumb.height * r)))
    thumb.save(thumb_path, "JPEG", quality=70)

    cur = conn.execute(
        "INSERT INTO screenshots(ts, path, thumb_path, app, title, source, ocr_done) "
        "VALUES(?,?,?,?,?,?,0)",
        (iso(now), str(full_path), str(thumb_path), app, title, source),
    )
    conn.commit()
    return cur.lastrowid, ahash


def delete_screenshot(conn: sqlite3.Connection, shot_id: int, remove_files: bool = True) -> None:
    row = conn.execute(
        "SELECT path, thumb_path FROM screenshots WHERE id = ?", (shot_id,)
    ).fetchone()
    if row and remove_files:
        for p in (row["path"], row["thumb_path"]):
            if p:
                Path(p).unlink(missing_ok=True)
    conn.execute("UPDATE screenshots SET deleted = 1 WHERE id = ?", (shot_id,))
    conn.commit()
