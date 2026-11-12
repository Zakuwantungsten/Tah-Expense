"""Custom-painted 0–100% progress bar — never QProgressBar (Windows HWND flash)."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

_BLUE = QColor("#0077C5")
_BLUE_SOFT = QColor("#4AA3D9")
_TRACK = QColor("#E5EAF0")
_TRACK_EDGE = QColor("#D5DCE5")
_ANIM_MS = 28
_LERP = 0.22


class SleekProgressBar(QWidget):
    """Sleek determinate / indeterminate progress with a live 0–100% label.

    Painted entirely in ``paintEvent`` so Windows never promotes a native
    progress HWND (the tiny title-bar flash that used to appear before loaders).
    """

    BAR_WIDTH = 300
    BAR_HEIGHT = 10
    CORNER = 5.0

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        show_percent: bool = True,
        bar_width: int = BAR_WIDTH,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sleekProgressBar")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)
        self.setWindowFlags(Qt.Widget)
        self.setStyleSheet(
            "QWidget#sleekProgressBar { background: transparent; border: none; }"
        )

        self._show_percent = bool(show_percent)
        self._value = 0
        self._maximum = 100
        self._indeterminate = True
        self._display = 0.0
        self._target = 0.0
        self._phase = 0.0
        self._running = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignCenter)

        self._pct = QLabel("")
        self._pct.setAlignment(Qt.AlignCenter)
        self._pct.setStyleSheet(
            f"color: {_BLUE.name()}; font-size: 22px; font-weight: 700;"
            f" font-family:'Segoe UI'; background: transparent; border: none;"
            " letter-spacing: 0.5px;"
        )
        self._pct.setFixedWidth(bar_width)
        self._pct.hide()
        root.addWidget(self._pct, alignment=Qt.AlignCenter)

        self._track = _TrackCanvas(self, bar_width=bar_width)
        root.addWidget(self._track, alignment=Qt.AlignCenter)

        self._timer = QTimer(self)
        self._timer.setInterval(_ANIM_MS)
        self._timer.timeout.connect(self._tick)

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedWidth(bar_width)

    # ── Public API ──────────────────────────────────────────────────────────

    def percent_label(self) -> QLabel:
        return self._pct

    def percent_text(self) -> str:
        return (self._pct.text() or "").strip()

    def percent(self) -> int:
        if self._indeterminate or self._maximum <= 0:
            return 0
        return int(round(100.0 * self._value / self._maximum))

    def value(self) -> int:
        return int(self._value)

    def maximum(self) -> int:
        return int(self._maximum)

    def is_indeterminate(self) -> bool:
        return bool(self._indeterminate)

    def set_progress(
        self,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        if maximum is not None and maximum > 0:
            self._maximum = int(maximum)
            self._value = max(0, min(self._maximum, int(value or 0)))
            self._indeterminate = False
            self._target = self._value / self._maximum
            self._sync_percent()
            self._ensure_anim()
            self._track.set_state(
                fraction=self._display,
                indeterminate=False,
                phase=self._phase,
            )
            return

        if value is not None and self._maximum > 0 and not self._indeterminate:
            self._value = max(0, min(self._maximum, int(value)))
            self._target = self._value / self._maximum
            self._sync_percent()
            self._ensure_anim()
            self._track.set_state(
                fraction=self._display,
                indeterminate=False,
                phase=self._phase,
            )
            return

        if maximum is not None:
            # Indeterminate busy — keep the bar alive, hide fake %.
            self._indeterminate = True
            self._maximum = 0
            self._value = 0
            self._target = 0.0
            self._pct.setText("")
            self._pct.hide()
            self._ensure_anim()
            self._track.set_state(
                fraction=0.0,
                indeterminate=True,
                phase=self._phase,
            )

    def reset(self) -> None:
        self._value = 0
        self._maximum = 100
        self._indeterminate = True
        self._display = 0.0
        self._target = 0.0
        self._phase = 0.0
        self._pct.setText("")
        self._pct.hide()
        self._track.set_state(fraction=0.0, indeterminate=True, phase=0.0)
        self._stop_anim()

    def start(self) -> None:
        self._ensure_anim()

    def stop(self) -> None:
        self._stop_anim()
        if not self._indeterminate and self._maximum > 0:
            self._display = self._target
            self._track.set_state(
                fraction=self._display,
                indeterminate=False,
                phase=self._phase,
            )

    # ── Internals ───────────────────────────────────────────────────────────

    def _sync_percent(self) -> None:
        if not self._show_percent or self._indeterminate or self._maximum <= 0:
            self._pct.setText("")
            self._pct.hide()
            return
        pct = int(round(100.0 * self._value / self._maximum))
        pct = max(0, min(100, pct))
        self._pct.setText(f"{pct}%")
        self._pct.show()

    def _ensure_anim(self) -> None:
        if self._running:
            return
        self._running = True
        if not self._timer.isActive():
            self._timer.start()

    def _stop_anim(self) -> None:
        self._running = False
        self._timer.stop()

    def _tick(self) -> None:
        if self._indeterminate:
            self._phase = (self._phase + 0.045) % (2.0 * math.pi)
            self._track.set_state(
                fraction=0.0,
                indeterminate=True,
                phase=self._phase,
            )
            return

        # Smooth fill toward target.
        delta = self._target - self._display
        if abs(delta) < 0.002:
            self._display = self._target
        else:
            self._display += delta * _LERP
        self._track.set_state(
            fraction=self._display,
            indeterminate=False,
            phase=self._phase,
        )
        # Keep a soft shine drifting on the filled portion.
        self._phase = (self._phase + 0.03) % (2.0 * math.pi)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_anim()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_anim()
        super().hideEvent(event)


class _TrackCanvas(QWidget):
    """The painted track + fill (child of SleekProgressBar)."""

    def __init__(self, parent: SleekProgressBar, *, bar_width: int) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)
        self.setFixedSize(bar_width, SleekProgressBar.BAR_HEIGHT + 2)
        self.setStyleSheet("background: transparent; border: none;")
        self._fraction = 0.0
        self._indeterminate = True
        self._phase = 0.0

    def set_state(
        self,
        *,
        fraction: float,
        indeterminate: bool,
        phase: float,
    ) -> None:
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._indeterminate = bool(indeterminate)
        self._phase = float(phase)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = float(self.width())
        h = float(SleekProgressBar.BAR_HEIGHT)
        y = (self.height() - h) / 2.0
        track = QRectF(0.5, y, w - 1.0, h)
        radius = SleekProgressBar.CORNER

        # Track
        path = QPainterPath()
        path.addRoundedRect(track, radius, radius)
        painter.fillPath(path, _TRACK)
        painter.setPen(_TRACK_EDGE)
        painter.drawPath(path)

        if self._indeterminate:
            # Soft sliding capsule — never a native busy indicator.
            span = w * 0.32
            travel = w - span
            # Ease the slide with a sine so it breathes at the ends.
            t = 0.5 + 0.5 * math.sin(self._phase)
            x = track.x() + t * travel
            pill = QRectF(x, y, span, h)
            grad = QLinearGradient(pill.left(), 0, pill.right(), 0)
            c0 = QColor(_BLUE)
            c0.setAlpha(40)
            c1 = QColor(_BLUE)
            c1.setAlpha(220)
            c2 = QColor(_BLUE_SOFT)
            c2.setAlpha(40)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(0.5, c1)
            grad.setColorAt(1.0, c2)
            clip = QPainterPath()
            clip.addRoundedRect(track, radius, radius)
            painter.setClipPath(clip)
            painter.fillRect(pill, grad)
            painter.end()
            return

        fill_w = max(0.0, (w - 1.0) * self._fraction)
        if fill_w <= 0.5:
            painter.end()
            return

        fill = QRectF(track.x(), y, fill_w, h)
        # Keep round caps even at low % by clamping radius to half fill width.
        r = min(radius, fill_w / 2.0)
        fill_path = QPainterPath()
        fill_path.addRoundedRect(fill, r, r)

        grad = QLinearGradient(fill.left(), 0, fill.right(), 0)
        grad.setColorAt(0.0, _BLUE)
        grad.setColorAt(1.0, _BLUE_SOFT)
        painter.fillPath(fill_path, grad)

        # Soft moving sheen over the filled portion.
        if fill_w > 16:
            sheen_w = max(12.0, fill_w * 0.28)
            t = 0.5 + 0.5 * math.sin(self._phase)
            sx = fill.x() + t * max(0.0, fill_w - sheen_w)
            sheen = QRectF(sx, y, sheen_w, h)
            sg = QLinearGradient(sheen.left(), 0, sheen.right(), 0)
            a0 = QColor(255, 255, 255, 0)
            a1 = QColor(255, 255, 255, 55)
            sg.setColorAt(0.0, a0)
            sg.setColorAt(0.5, a1)
            sg.setColorAt(1.0, a0)
            clip = QPainterPath()
            clip.addPath(fill_path)
            painter.setClipPath(clip)
            painter.fillRect(sheen, sg)

        painter.end()
