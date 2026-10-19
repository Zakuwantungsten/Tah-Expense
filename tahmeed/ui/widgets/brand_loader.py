"""Branded loading panel: login horse mark with fade pulse + context text."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_BLUE = "#0077C5"
_FADE_MS = 40
_DOT_MS = 380
_FADE_PERIOD_MS = 1600  # full dim → full → dim cycle
DOT_CYCLE = (".", "..", "...")
# Login shows the full wordmark at 200px wide; the horse alone is ~160px.
LOGIN_HORSE_WIDTH = 160
_MARK_CACHE: dict[int, QPixmap] = {}


def strip_loader_ellipsis(message: str) -> str:
    """Remove trailing ASCII/unicode dots so the panel can animate them."""
    text = (message or "").rstrip()
    while text and text[-1] in ".…":
        text = text[:-1].rstrip()
    return text or "Loading"


def _asset_candidates(name: str) -> list[Path]:
    here = Path(__file__).resolve().parent
    root = here.parent.parent  # tahmeed/
    paths: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        paths.extend(
            [
                meipass / "tahmeed" / "assets" / name,
                meipass / name,
            ]
        )
    paths.extend(
        [
            root / "assets" / name,
            root.parent / name,
        ]
    )
    return paths


def _scale_mark(pix: QPixmap, mark_width: int) -> QPixmap:
    if pix.isNull() or mark_width <= 0:
        return pix
    return pix.scaledToWidth(mark_width, Qt.SmoothTransformation)


def _load_horse_mark(mark_width: int) -> QPixmap:
    """Load the transparent login horse only — no circle, badge, or wordmark."""
    for path in _asset_candidates("horse_mark.png"):
        if path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                return _scale_mark(pix, mark_width)

    # Fallback: crop the horse band from the login wordmark.
    for path in _asset_candidates("logo_blue.png"):
        if not path.is_file():
            continue
        logo = QPixmap(str(path))
        if logo.isNull():
            continue
        # Same crop band as scripts/extract_horse_mark.py (relative to 1280×754).
        w, h = logo.width(), logo.height()
        x0 = int(w * 100 / 1280)
        y0 = int(h * 60 / 754)
        x1 = int(w * 1130 / 1280)
        y1 = int(h * 400 / 754)
        cropped = logo.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        if not cropped.isNull():
            return _scale_mark(cropped, mark_width)

    # Last resort: small transparent blue horse-shaped stand-in (not a circle badge).
    fallback = QPixmap(mark_width, max(24, mark_width // 3))
    fallback.fill(Qt.transparent)
    painter = QPainter(fallback)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(_BLUE))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, fallback.width() - 4, fallback.height() - 4, 6, 6)
    painter.end()
    return fallback


def load_mark_pixmap(mark_width: int = LOGIN_HORSE_WIDTH) -> QPixmap:
    """Cached login horse mark (transparent, no circular badge)."""
    key = int(mark_width)
    cached = _MARK_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    mark = _load_horse_mark(key)
    _MARK_CACHE[key] = mark
    return mark


def load_gallop_frames(mark_width: int = LOGIN_HORSE_WIDTH) -> list[QPixmap]:
    """Compat shim — returns a single static mark (gallop removed)."""
    return [load_mark_pixmap(mark_width)]


class BrandLoaderPanel(QWidget):
    """Login horse fade pulse + context message with ``.`` →``..`` →``...`` dots.

    Never uses ``WA_TranslucentBackground`` or ``QGraphicsOpacityEffect`` — on
    Windows those force a native HWND and briefly flash a tiny top-level window
    titled like the app (``meedexpe``) whenever tab loaders show. Fade is done
    by re-compositing the horse pixmap. Overlays keep progress hidden; dialogs
    pass ``show_progress=True`` and ``opaque=True`` for UploadBusy.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        message: str = "Loading",
        *,
        mark_size: int = LOGIN_HORSE_WIDTH,
        mark_width: Optional[int] = None,
        show_progress: bool = False,
        opaque: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("brandLoaderPanel")
        self._opaque = bool(opaque)
        # Stay a child widget — never a native / top-level window.
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)
        if self._opaque:
            self.setStyleSheet(
                "QWidget#brandLoaderPanel {"
                "  background: #FFFFFF; border: none;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QWidget#brandLoaderPanel { background: transparent; border: none; }"
            )
        self._base_message = strip_loader_ellipsis(message)
        self._dot_index = 0
        self._fade_phase = 0.0
        self._fade_opacity = 0.45
        self._running = False
        width = int(mark_width if mark_width is not None else mark_size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)

        self._mark = load_mark_pixmap(width)
        self._horse = QLabel()
        self._horse.setAlignment(Qt.AlignCenter)
        self._horse.setFixedSize(self._mark.size())
        self._horse.setAttribute(Qt.WA_TranslucentBackground, False)
        self._horse.setAttribute(Qt.WA_NativeWindow, False)
        if self._opaque:
            self._horse.setStyleSheet("background: #FFFFFF; border: none;")
        else:
            self._horse.setStyleSheet("background: transparent; border: none;")
        self._apply_fade()
        layout.addWidget(self._horse, alignment=Qt.AlignCenter)

        self._label = QLabel(self._compose_text())
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            f"color: #111827; font-size: 13px; font-weight: 600;"
            f" font-family:'Segoe UI'; background: transparent; border: none;"
        )
        self._label.setMinimumWidth(280)
        layout.addWidget(self._label, alignment=Qt.AlignCenter)

        self._pct = QLabel("")
        self._pct.setAlignment(Qt.AlignCenter)
        self._pct.setStyleSheet(
            f"color: {_BLUE}; font-size: 20px; font-weight: 700;"
            f" font-family:'Segoe UI'; background: transparent; border: none;"
        )
        self._pct.setMinimumWidth(280)
        self._pct.hide()
        layout.addWidget(self._pct, alignment=Qt.AlignCenter)

        self._bar = QProgressBar()
        self._bar.setFixedWidth(280)
        self._bar.setFixedHeight(12)
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 0)
        self._bar.setStyleSheet(
            "QProgressBar {"
            "  background: #E5E7EB; border: none; border-radius: 6px;"
            "}"
            "QProgressBar::chunk {"
            f"  background: {_BLUE}; border-radius: 6px;"
            "}"
        )
        self._show_progress = show_progress
        self._bar.setVisible(bool(show_progress))
        layout.addWidget(self._bar, alignment=Qt.AlignCenter)

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(_FADE_MS)
        self._fade_timer.timeout.connect(self._tick_fade)

        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(_DOT_MS)
        self._dot_timer.timeout.connect(self._tick_dots)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def _compose_text(self) -> str:
        return f"{self._base_message}{DOT_CYCLE[self._dot_index]}"

    def display_text(self) -> str:
        """Current label including animated dots (for tests / status)."""
        return self._label.text()

    def base_message(self) -> str:
        return self._base_message

    def set_message(self, message: str) -> None:
        self._base_message = strip_loader_ellipsis(message)
        self._label.setText(self._compose_text())

    def set_progress(
        self,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        if not self._show_progress:
            self._bar.hide()
            self._pct.hide()
            return
        self._bar.show()
        if maximum is not None and maximum > 0:
            maximum = int(maximum)
            current = max(0, min(maximum, int(value or 0)))
            self._bar.setRange(0, maximum)
            self._bar.setValue(current)
            pct = int(round(100.0 * current / maximum)) if maximum else 0
            self._pct.setText(f"{pct}%")
            self._pct.show()
        elif value is not None and self._bar.maximum() > 0:
            maximum = self._bar.maximum()
            current = max(0, min(maximum, int(value)))
            self._bar.setValue(current)
            pct = int(round(100.0 * current / maximum)) if maximum else 0
            self._pct.setText(f"{pct}%")
            self._pct.show()
        elif maximum is not None:
            # Indeterminate busy — keep the bar visible so the dialog is never empty.
            self._bar.setRange(0, 0)
            self._pct.setText("")
            self._pct.hide()

    def reset_progress(self) -> None:
        self._bar.setRange(0, 0)
        self._pct.setText("")
        self._pct.hide()
        if self._show_progress:
            self._bar.show()
        else:
            self._bar.hide()

    def percent_text(self) -> str:
        return (self._pct.text() or "").strip()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._fade_phase = 0.0
        if not self._fade_timer.isActive():
            self._fade_timer.start()
        if not self._dot_timer.isActive():
            self._dot_timer.start()
        self._apply_fade()

    def fade_opacity(self) -> float:
        """Current horse fade opacity (for tests)."""
        return float(self._fade_opacity)

    def stop(self) -> None:
        self._running = False
        self._fade_timer.stop()
        self._dot_timer.stop()
        self._fade_opacity = 1.0
        self._horse.setPixmap(self._mark)

    def _apply_fade(self) -> None:
        # Smooth sine pulse: ~0.35 (faded) ↔ 1.0 (full color).
        # Re-composite the pixmap — never QGraphicsOpacityEffect (native HWND flash).
        wave = 0.5 + 0.5 * math.sin(self._fade_phase)
        opacity = 0.35 + 0.65 * wave
        self._fade_opacity = opacity
        faded = QPixmap(self._mark.size())
        faded.fill(Qt.transparent)
        painter = QPainter(faded)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, self._mark)
        painter.end()
        self._horse.setPixmap(faded)

    def _tick_fade(self) -> None:
        step = (2.0 * math.pi) * (_FADE_MS / _FADE_PERIOD_MS)
        self._fade_phase += step
        if self._fade_phase > 2.0 * math.pi:
            self._fade_phase -= 2.0 * math.pi
        self._apply_fade()

    def _tick_dots(self) -> None:
        self._dot_index = (self._dot_index + 1) % len(DOT_CYCLE)
        self._label.setText(self._compose_text())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)
