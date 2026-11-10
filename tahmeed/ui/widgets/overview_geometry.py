"""Shared Overview page canvas geometry (login motifs, top-right → empty)."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

_BG = "#F1F5F9"
_ACCENT = (0, 119, 197)  # brand blue #0077C5
_GLOW = (26, 140, 255)


def _geometry_radius(w: float) -> float:
    return max(w * 0.78, 460.0)


def _corner_fade(x: float, y: float, w: float, h: float) -> float:
    """1 at top-right, 0 outside a fixed corner blob (page height-independent)."""
    if w <= 0 or h <= 0:
        return 0.0
    t = math.hypot(w - x, y) / _geometry_radius(w)
    if t >= 1.0:
        return 0.0
    u = 1.0 - t
    return u * u


def paint_corner_geometry(p: QPainter, w: int, h: int) -> None:
    """Draw Overview corner lattice / arcs / sweeps. Caller fills the background."""
    if w <= 0 or h <= 0:
        return

    ar, ag, ab = _ACCENT
    gr, gg, gb = _GLOW
    radius = _geometry_radius(float(w))

    ambient = QRadialGradient(w * 0.94, 36.0, radius * 0.95)
    ambient.setColorAt(0.0, QColor(ar, ag, ab, 54))
    ambient.setColorAt(0.30, QColor(ar, ag, ab, 26))
    ambient.setColorAt(0.65, QColor(ar, ag, ab, 8))
    ambient.setColorAt(1.0, QColor(ar, ag, ab, 0))
    p.fillRect(0, 0, w, h, ambient)

    step = 24
    lattice_h = int(radius) + step
    lattice_x0 = max(-step, int(w - radius) - step)
    p.save()
    for y in range(-step, min(h, lattice_h), step):
        for x in range(lattice_x0, w + step, step):
            cx = x + (step // 2 if (y // step) % 2 else 0)
            cy = y
            fade = _corner_fade(float(cx), float(cy), float(w), float(h))
            if fade <= 0.02:
                continue
            alpha = int(58 * fade)
            if alpha < 4:
                continue
            pen = QPen(QColor(ar, ag, ab, alpha))
            pen.setWidthF(1.2)
            p.setPen(pen)
            p.drawLine(cx, cy - 8, cx + 10, cy)
            p.drawLine(cx + 10, cy, cx, cy + 8)
            p.drawLine(cx, cy + 8, cx - 10, cy)
            p.drawLine(cx - 10, cy, cx, cy - 8)
    p.restore()

    p.save()
    cx, cy = w * 0.90, 42.0
    for i, (r, base_a) in enumerate(
        ((90, 54), (140, 40), (195, 30), (255, 20), (320, 14))
    ):
        fade = _corner_fade(cx + r * 0.12, cy + r * 0.08, float(w), float(h))
        alpha = int(base_a * fade)
        if alpha < 4:
            continue
        pen = QPen(QColor(gr, gg, gb, alpha))
        pen.setWidthF(1.5 if i % 2 == 0 else 1.05)
        p.setPen(pen)
        p.drawArc(
            QRectF(cx - r, cy - r, r * 2, r * 2),
            int(140 * 16),
            int(115 * 16),
        )
    fade_ticks = _corner_fade(cx, cy + 50, float(w), float(h))
    if fade_ticks > 0.05:
        tick = QPen(QColor(gr, gg, gb, int(64 * fade_ticks)))
        tick.setWidthF(1.55)
        p.setPen(tick)
        for deg in range(145, 255, 12):
            rad = math.radians(deg)
            x0 = cx + math.cos(rad) * 248
            y0 = cy + math.sin(rad) * 248
            x1 = cx + math.cos(rad) * 264
            y1 = cy + math.sin(rad) * 264
            if _corner_fade(x0, y0, float(w), float(h)) <= 0.04:
                continue
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
    p.restore()

    p.save()
    for y0, length, alpha in (
        (28.0, w * 0.38, 44),
        (52.0, w * 0.30, 32),
        (78.0, w * 0.34, 20),
        (108.0, w * 0.24, 14),
    ):
        x0 = w - length - 16
        fade = _corner_fade(x0 + length * 0.5, y0, float(w), float(h))
        a = int(alpha * fade)
        if a < 4:
            continue
        grad = QLinearGradient(x0, y0, x0 + length, y0 + length * 0.05)
        grad.setColorAt(0.0, QColor(ar, ag, ab, 0))
        grad.setColorAt(0.4, QColor(ar, ag, ab, a))
        grad.setColorAt(1.0, QColor(ar, ag, ab, 0))
        pen = QPen(grad, 1.55)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x0, y0), QPointF(x0 + length, y0 + length * 0.05))
    p.restore()


class OverviewGeometryContent(QWidget):
    """Scroll content with quiet login-style geometry in the top-right."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewContent")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            "QWidget#overviewContent { background: transparent; border: none; }"
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        p.fillRect(self.rect(), QColor(_BG))
        paint_corner_geometry(p, w, h)
        p.end()
