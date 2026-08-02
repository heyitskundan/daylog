"""Render the daylog motif (from daylog.icon) to a multi-resolution Windows .ico.

Run:  python packaging/make_icon.py
Writes packaging/daylog.ico (16, 24, 32, 48, 64, 128, 256 px).
"""
from __future__ import annotations

from pathlib import Path

from daylog import icon

OUT = Path(__file__).resolve().parent / "daylog.ico"
SIZES = [256, 128, 64, 48, 32, 24, 16]


def main() -> None:
    base = icon.render(256)
    imgs = [base.resize((s, s)) for s in SIZES]
    imgs[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {OUT} ({', '.join(str(s) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
