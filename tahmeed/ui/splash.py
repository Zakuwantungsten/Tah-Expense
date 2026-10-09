"""Modern circular brand splash shown while the desktop app starts."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Property,
)
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.config import APP_NAME, APP_VERSION
from tahmeed.ui.branding import load_brand_logo

_BLUE = QColor("#0077C5")
_BLUE_DEEP = QColor("#005EA3")
_NAVY = QColor("#0F1C33")
_WHITE = QColor("#FFFFFF")
_MUTED = QColor("#94A3B8")


def _asset_candidates(name: str) -> list[Path]:
    here = Path(__file__).resolve().parent
    root = here.parent
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


def _load_splash_mark(size: int) -> QPixmap:
    for path in _asset_candidates("splash_mark.png"):
        if path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                return pix.scaled(
                    size,
                    size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
    # Fallback: brand wordmark logo.
    return load_brand_logo(size, for_dark_bg=False)


class SplashScreen(QWidget):
    """Circular, Material-style brand splash (icon → brand moment → login)."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("splashRoot")
        self.setWindowFlags(
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._diameter = 360
        self.setFixedSize(self._diameter, self._diameter)

        self._status = "Starting…"
        self._spin = 0.0
        self._fade = 0.0
        self._mark = _load_splash_mark(148)

        # True circular hit-test / silhouette (not a square chrome frame).
        pad = 10
        self.setMask(
            QRegion(
                pad,
                pad,
                self._diameter - 2 * pad,
                self._diameter - 2 * pad,
                QRegion.Ellipse,
            )
        )

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

        self._fade_anim = QPropertyAnimation(self, b"fade", self)
        self._fade_anim.setDuration(480)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_fade(self) -> float:
        return self._fade

    def _set_fade(self, value: float) -> None:
        self._fade = float(value)
        self.update()

    fade = Property(float, _get_fade, _set_fade)

    def _tick_spin(self) -> None:
        self._spin = (self._spin + 3.2) % 360.0
        self.update()

    def set_status(self, text: str) -> None:
        self._status = text or "Loading…"
        self.update()
        QApplication.processEvents()

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
        self.show()
        self._spin_timer.start()
        self._fade_anim.start()
        QApplication.processEvents()

    def finish(self, next_window: QWidget | None = None) -> None:
        self._spin_timer.stop()
        if next_window is not None:
            next_window.show()
            QApplication.processEvents()
        self.close()
        self.deleteLater()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(max(0.0, min(1.0, self._fade)))

        d = float(self._diameter)
        pad = 10.0
        disk = QRectF(pad, pad, d - 2 * pad, d - 2 * pad)
        cx, cy = disk.center().x(), disk.center().y()

        # Soft outer glow (premium depth).
        for i, alpha in ((18, 28), (10, 40), (4, 55)):
            glow = disk.adjusted(-i, -i, i, i)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 119, 197, alpha))
            painter.drawEllipse(glow)

        # Main disk — cool brand wash (not flat purple / cream AI clichés).
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#F4F8FC"))
        painter.drawEllipse(disk)

        # Inner vignette ring.
        ring = QPen(QColor(0, 119, 197, 55))
        ring.setWidthF(2.0)
        painter.setPen(ring)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(disk.adjusted(1.5, 1.5, -1.5, -1.5))

        # Brand mark.
        if not self._mark.isNull():
            mw, mh = self._mark.width(), self._mark.height()
            painter.drawPixmap(
                int(cx - mw / 2),
                int(cy - mh / 2 - 28),
                self._mark,
            )

        # Title.
        title_font = QFont("Segoe UI", 15, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(_NAVY)
        title_rect = QRectF(pad + 24, cy + 52, d - 2 * pad - 48, 28)
        painter.drawText(title_rect, Qt.AlignHCenter | Qt.AlignVCenter, APP_NAME)

        # Status.
        status_font = QFont("Segoe UI", 10)
        painter.setFont(status_font)
        painter.setPen(_BLUE)
        status_rect = QRectF(pad + 28, cy + 82, d - 2 * pad - 56, 22)
        painter.drawText(status_rect, Qt.AlignHCenter | Qt.AlignVCenter, self._status)

        # Indeterminate arc (modern spinner on the circle rim).
        arc_rect = disk.adjusted(14, 14, -14, -14)
        track = QPen(QColor(0, 119, 197, 35))
        track.setWidthF(3.0)
        track.setCapStyle(Qt.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(arc_rect)

        sweep = 72.0
        grad = QConicalGradient(QPointF(cx, cy), -self._spin)
        grad.setColorAt(0.0, QColor(0, 119, 197, 0))
        grad.setColorAt(0.35, _BLUE)
        grad.setColorAt(0.7, _BLUE_DEEP)
        grad.setColorAt(1.0, QColor(0, 119, 197, 0))
        arc = QPen(grad, 3.4)
        arc.setCapStyle(Qt.RoundCap)
        painter.setPen(arc)
        # Qt angles: 16ths of a degree, 0 = 3 o'clock, counter-clockwise.
        start = int((-self._spin - sweep / 2) * 16)
        span = int(-sweep * 16)
        painter.drawArc(arc_rect, start, span)

        # Version tucked at the bottom of the disk.
        ver_font = QFont("Segoe UI", 8)
        painter.setFont(ver_font)
        painter.setPen(_MUTED)
        ver_rect = QRectF(pad + 40, d - pad - 36, d - 2 * pad - 80, 18)
        painter.drawText(
            ver_rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"v{APP_VERSION}",
        )

        # Clip hint for window shape on platforms that ignore translucent corners.
        path = QPainterPath()
        path.addEllipse(disk)
        painter.setClipPath(path)
