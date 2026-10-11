"""Brand splash shown while the desktop app starts — same logo as login."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Property,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.config import APP_VERSION
from tahmeed.ui.branding import is_dark_theme, load_brand_logo
from tahmeed.ui.widgets.brand_loader import DOT_CYCLE, strip_loader_ellipsis

# Match LoginWindow logo width exactly.
_LOGIN_LOGO_WIDTH = 200

# Light mode — dark ink on a pale panel (won't wash out on light desktops).
_PANEL_LIGHT = QColor(255, 255, 255, 230)
_INK_LIGHT = QColor("#121212")
_MUTED_LIGHT = QColor("#64748B")

# Dark mode — light ink on a near-black panel.
_PANEL_DARK = QColor(18, 18, 20, 230)
_INK_DARK = QColor("#F5F5F5")
_MUTED_DARK = QColor("#94A3B8")


class SplashScreen(QWidget):
    """Frameless splash: login wordmark + status (no circular badge)."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("splashRoot")
        self.setWindowFlags(
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._status_base = "Starting"
        self._dot_index = 0
        self._mark_pulse = 0.0
        self._fade = 0.0
        # Logo ink + chrome follow OS / app light-dark palette (same as login).
        self._dark = is_dark_theme()
        self._logo = load_brand_logo(_LOGIN_LOGO_WIDTH, for_dark_bg=self._dark)
        if self._dark:
            self._panel = _PANEL_DARK
            self._ink = _INK_DARK
            self._muted = _MUTED_DARK
        else:
            self._panel = _PANEL_LIGHT
            self._ink = _INK_LIGHT
            self._muted = _MUTED_LIGHT

        # Room for logo + status + version; panel fills the widget bounds.
        logo_h = self._logo.height() if not self._logo.isNull() else 120
        self._width = max(320, _LOGIN_LOGO_WIDTH + 80)
        self._height = logo_h + 96
        self.setFixedSize(self._width, self._height)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self._tick_pulse)

        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(380)
        self._dot_timer.timeout.connect(self._tick_dots)

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

    def _status_text(self) -> str:
        return f"{self._status_base}{DOT_CYCLE[self._dot_index]}"

    def _tick_pulse(self) -> None:
        self._mark_pulse += (2.0 * math.pi) * (40 / 1600)
        if self._mark_pulse > 2.0 * math.pi:
            self._mark_pulse -= 2.0 * math.pi
        self.update()

    def _tick_dots(self) -> None:
        self._dot_index = (self._dot_index + 1) % len(DOT_CYCLE)
        self.update()

    def set_status(self, text: str) -> None:
        self._status_base = strip_loader_ellipsis(text or "Loading")
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
        self._pulse_timer.start()
        self._dot_timer.start()
        self._fade_anim.start()
        QApplication.processEvents()

    def finish(self, next_window: QWidget | None = None) -> None:
        self._pulse_timer.stop()
        self._dot_timer.stop()
        if next_window is not None:
            next_window.show()
            QApplication.processEvents()
        self.close()
        self.deleteLater()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        base_opacity = max(0.0, min(1.0, self._fade))
        painter.setOpacity(base_opacity)

        w = float(self._width)
        h = float(self._height)
        cx = w / 2.0

        # Themed panel so logo/text never sit raw on the desktop wallpaper.
        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 16.0, 16.0)
        painter.fillPath(panel, self._panel)
        painter.setPen(QColor(148, 163, 184, 70 if self._dark else 90))
        painter.drawPath(panel)

        top = 16.0
        logo = self._logo
        if not logo.isNull():
            wave = 0.5 + 0.5 * math.sin(self._mark_pulse)
            # Keep a higher floor so the wordmark stays readable while pulsing.
            mark_opacity = 0.72 + 0.28 * wave
            painter.save()
            painter.setOpacity(base_opacity * mark_opacity)
            mw, mh = logo.width(), logo.height()
            painter.drawPixmap(int(cx - mw / 2), int(top), logo)
            painter.restore()
            painter.setOpacity(base_opacity)
            below = top + mh + 14
        else:
            below = top + 40

        status_font = QFont("Segoe UI", 11)
        painter.setFont(status_font)
        painter.setPen(self._ink)
        status_rect = QRectF(24, below, w - 48, 24)
        painter.drawText(
            status_rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            self._status_text(),
        )

        ver_font = QFont("Segoe UI", 8)
        painter.setFont(ver_font)
        painter.setPen(self._muted)
        ver_rect = QRectF(24, below + 26, w - 48, 18)
        painter.drawText(
            ver_rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"v{APP_VERSION}",
        )
