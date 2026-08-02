"""The daylog icon, drawn in-memory with Pillow (used by the tray)."""

from __future__ import annotations

BG = (14, 17, 22, 255)        # dark slate
ACCENT = (47, 129, 247, 255)  # blue
LIGHT = (139, 148, 158, 255)  # muted grey


def render(size: int = 64):
    """Return a PIL.Image of the daylog motif (three timeline bars + a 'now' dot)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 6
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG)
    pad = size * 0.22
    bar_h = size * 0.10
    gap = size * 0.10
    y = pad
    for w, c in zip((0.56, 0.40, 0.30), (ACCENT, LIGHT, LIGHT)):
        d.rounded_rectangle(
            [pad, y, pad + (size - 2 * pad) * w, y + bar_h], radius=bar_h / 2, fill=c
        )
        y += bar_h + gap
    dot = size * 0.07
    d.ellipse(
        [size - pad - dot, pad - dot * 0.2, size - pad + dot, pad + dot * 1.8], fill=ACCENT
    )
    return img
