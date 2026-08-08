"""Shared brand logo loading with light/dark background contrast for TAHMEED text."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent

# Cache: (resolved path, width, for_dark_bg) → pixmap
_LOGO_CACHE: dict[tuple[str, int, bool], QPixmap] = {}


def is_dark_theme() -> bool:
    app = QApplication.instance()
    return app is not None and app.palette().color(QPalette.Window).lightness() < 128


def _logo_candidates() -> list[Path]:
    paths: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        paths.extend(
            [
                meipass / "tahmeed" / "assets" / "logo_blue.png",
                meipass / "logo_blue.png",
                meipass / "logo.png",
                meipass / "tahmeed" / "assets" / "app_icon.png",
            ]
        )
    paths.extend(
        [
            _HERE.parent / "assets" / "logo_blue.png",
            _ROOT / "logo_blue.png",
            _ROOT / "logo.png",
            _HERE.parent / "assets" / "app_icon.png",
        ]
    )
    return paths


def _recolor_ink(pix: QPixmap, *, for_dark_bg: bool) -> QPixmap:
    """Make TAHMEED (and other neutral ink) readable on the target background.

    Uses scanline buffer access (not per-pixel QColor) so splash/login stay snappy.
    Dark background → near-white ink. Light background → near-black ink.
    Brand blues are preserved.
    """
    tr, tg, tb = (245, 245, 245) if for_dark_bg else (18, 18, 18)
    img = pix.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.bits()
    if ptr is None:
        return pix
    # PySide6 returns a memoryview / sip.array — treat as mutable bytes.
    buf = memoryview(ptr).cast("B")
    for y in range(h):
        row = y * bpl
        for x in range(w):
            i = row + x * 4
            # ARGB32 on little-endian: B, G, R, A
            b, g, r, a = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
            if a < 12:
                continue
            mx = r if r > g else g
            if b > mx:
                mx = b
            mn = r if r < g else g
            if b < mn:
                mn = b
            # saturation ~ (max-min)/max; value = max — all in 0..255
            if mx == 0:
                sat = 0
            else:
                sat = (mx - mn) * 255 // mx
            # Keep brand blues (high sat + blue dominant).
            if sat > 40 and b > r + 25 and b > g + 15:
                continue
            if sat < 55 and mx < 220:
                buf[i] = tb
                buf[i + 1] = tg
                buf[i + 2] = tr
                # alpha unchanged
    return QPixmap.fromImage(img)


def load_brand_logo(
    width: int = 200,
    *,
    for_dark_bg: bool | None = None,
) -> QPixmap:
    """Load the transparent blue logo, ink adjusted for the destination background.

    ``for_dark_bg``:
      - ``True``  — light (near-white) TAHMEED text for dark panels
      - ``False`` — dark (near-black) TAHMEED text for light panels (e.g. splash)
      - ``None``  — follow the OS / app palette via ``is_dark_theme()``
    """
    dark_bg = is_dark_theme() if for_dark_bg is None else for_dark_bg
    for path in _logo_candidates():
        if not path.is_file():
            continue
        key = (str(path.resolve()), int(width), bool(dark_bg))
        cached = _LOGO_CACHE.get(key)
        if cached is not None and not cached.isNull():
            return cached
        pix = QPixmap(str(path))
        if pix.isNull():
            continue
        scaled = pix.scaledToWidth(width, Qt.SmoothTransformation)
        result = _recolor_ink(scaled, for_dark_bg=dark_bg)
        _LOGO_CACHE[key] = result
        return result
    return QPixmap()
