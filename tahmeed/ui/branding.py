"""Shared brand logo loading (transparent blue logo for light/dark UI)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent


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


def _lighten_ink_for_dark(pix: QPixmap) -> QPixmap:
    """Turn near-black logo ink white so text reads on dark backgrounds."""
    img = pix.toImage().convertToFormat(QImage.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() < 12:
                continue
            # Keep brand blues; only lift neutral dark ink (TAHMEED text / swoosh).
            if c.saturation() < 45 and c.value() < 100:
                img.setPixelColor(x, y, QColor(245, 245, 245, c.alpha()))
    return QPixmap.fromImage(img)


def load_brand_logo(width: int = 200) -> QPixmap:
    """Load the transparent blue logo, theme-adjusted for light/dark mode."""
    for path in _logo_candidates():
        if not path.is_file():
            continue
        pix = QPixmap(str(path))
        if pix.isNull():
            continue
        scaled = pix.scaledToWidth(width, Qt.SmoothTransformation)
        if is_dark_theme():
            return _lighten_ink_for_dark(scaled)
        return scaled
    return QPixmap()
