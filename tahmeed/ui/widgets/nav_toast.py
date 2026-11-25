"""Bottom-right nav toast — acknowledges tab opens while lag is unavoidable."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

_BLUE = "#0077C5"
_GREEN = "#059669"
_GREEN_BORDER = "#A7F3D0"
_GREEN_BG = "#ECFDF5"
_GREEN_TEXT = "#065F46"
_RED = "#DC2626"
_RED_BORDER = "#FECACA"
_RED_BG = "#FEF2F2"
_RED_TEXT = "#991B1B"
_TEXT = "#111827"
_MUTED = "#6B7280"
_DOT_MS = 380
_DOT_CYCLE = (".", "..", "...")


class NavToast(QFrame):
    """Compact floating toast anchored to the bottom-right of ``host``.

    Stays a child widget (never a top-level HWND). Solid chrome only — no
    ``rgba()`` / translucent attributes that flash a tiny Windows title bar.
    """

    MARGIN = 16
    STATUS_RESERVE = 28  # leave room above LiveStatusBar

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("navToast")
        self.setWindowFlags(Qt.Widget)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)
        self.setStyleSheet(
            "QFrame#navToast {"
            "  background: #FFFFFF;"
            "  border: 1px solid #DBE4EE;"
            "  border-radius: 10px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._base = "Working"
        self._dot_index = 0
        self._generation = 0
        self._kind = "busy"  # busy | success | error
        self._animate_dots = True
        self._accent = _BLUE

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 16, 10)
        root.setSpacing(10)

        self._pulse = QLabel("●")
        self._pulse.setFixedWidth(14)
        self._pulse.setAlignment(Qt.AlignCenter)
        self._pulse.setStyleSheet(
            f"color: {_BLUE}; font-size: 11px; background: transparent; border: none;"
        )
        root.addWidget(self._pulse)

        self._label = QLabel("Opening…")
        self._label.setStyleSheet(
            f"color: {_TEXT}; font-size: 12px; font-weight: 600;"
            f" font-family:'Segoe UI'; background: transparent; border: none;"
        )
        self._label.setMinimumWidth(140)
        root.addWidget(self._label)

        # Timers before hide() — hideEvent stops them and must not run mid-init.
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(_DOT_MS)
        self._dot_timer.timeout.connect(self._tick_dots)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timeout)
        self._hide_generation = 0

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(70)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_phase = 0

        self.hide()
        host.installEventFilter(self)

    # ── Public API ──────────────────────────────────────────────────────────

    def show_opening(self, title: str, *, safety_ms: int = 15000) -> None:
        # Sticky until Loading/dismiss, but never forever if a build stalls.
        self._show(
            f"Opening {self._clean(title)}",
            sticky=False,
            ms=max(3000, int(safety_ms)),
        )

    def show_loading(self, title: str, *, ms: int = 2800) -> None:
        self._show(f"Loading {self._clean(title)}", sticky=False, ms=ms)

    def show_message(self, message: str, *, sticky: bool = False, ms: int = 2500) -> None:
        self._show(self._clean(message) or "Working", sticky=sticky, ms=ms)

    def show_success(self, message: str, *, ms: int = 4500) -> None:
        """Green status toast (replaces the Table-page inline success pill)."""
        self._show_result(
            (message or "").strip() or "Done",
            kind="success",
            accent=_GREEN,
            label_color=_GREEN_TEXT,
            glyph="✓",
            ms=ms,
        )

    def show_error(self, message: str, *, ms: int = 6500) -> None:
        """Red failure toast."""
        self._show_result(
            (message or "").strip() or "Failed",
            kind="error",
            accent=_RED,
            label_color=_RED_TEXT,
            glyph="!",
            ms=ms,
        )

    def _show_result(
        self,
        message: str,
        *,
        kind: str,
        accent: str,
        label_color: str,
        glyph: str,
        ms: int,
    ) -> None:
        self._kind = kind
        self._animate_dots = False
        self._accent = accent
        self._apply_chrome()
        self._pulse.setText(glyph)
        self._pulse.setStyleSheet(
            f"color: {accent}; font-size: 12px; font-weight: 700;"
            " background: transparent; border: none;"
        )
        self._label.setStyleSheet(
            f"color: {label_color}; font-size: 12px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent; border: none;"
        )
        self._generation += 1
        gen = self._generation
        self._base = message
        self._dot_index = 0
        self._label.setText(message)
        self._reposition()
        self.show()
        self.raise_()
        self.adjustSize()
        self._reposition()
        try:
            self.repaint()
        except RuntimeError:
            pass
        self._dot_timer.stop()
        self._pulse_timer.stop()
        self._hide_timer.stop()
        self._hide_generation = gen
        self._hide_timer.start(max(1000, int(ms)))

    def dismiss(self) -> None:
        self._generation += 1
        self._hide_timer.stop()
        self._dot_timer.stop()
        self._pulse_timer.stop()
        self.hide()

    def is_showing(self) -> bool:
        return self.isVisible()

    def display_text(self) -> str:
        return (self._label.text() or "").strip()

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        t = (text or "").strip()
        while t.endswith(".") or t.endswith("…"):
            t = t[:-1].rstrip()
        return t

    def _apply_chrome(self) -> None:
        if self._kind == "success":
            bg, border = _GREEN_BG, _GREEN_BORDER
        elif self._kind == "error":
            bg, border = _RED_BG, _RED_BORDER
        else:
            bg, border = "#FFFFFF", "#DBE4EE"
        self.setStyleSheet(
            "QFrame#navToast {"
            f"  background: {bg};"
            f"  border: 1px solid {border};"
            "  border-radius: 10px;"
            "}"
        )

    def _show(self, base: str, *, sticky: bool, ms: int = 2500) -> None:
        self._kind = "busy"
        self._animate_dots = True
        self._accent = _BLUE
        self._apply_chrome()
        self._pulse.setText("●")
        self._pulse.setStyleSheet(
            f"color: {_BLUE}; font-size: 11px; background: transparent; border: none;"
        )
        self._label.setStyleSheet(
            f"color: {_TEXT}; font-size: 12px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent; border: none;"
        )
        self._generation += 1
        gen = self._generation
        self._base = base or "Working"
        self._dot_index = 0
        self._label.setText(f"{self._base}{_DOT_CYCLE[0]}")
        self._reposition()
        self.show()
        self.raise_()
        self.adjustSize()
        self._reposition()
        try:
            self.repaint()
        except RuntimeError:
            pass
        if not self._dot_timer.isActive():
            self._dot_timer.start()
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
        self._hide_timer.stop()
        if sticky:
            self._hide_generation = 0
        else:
            self._hide_generation = gen
            self._hide_timer.start(max(800, int(ms)))

    def _on_hide_timeout(self) -> None:
        if self._hide_generation and self._hide_generation != self._generation:
            return
        self.dismiss()

    def _tick_dots(self) -> None:
        if not self._animate_dots:
            return
        self._dot_index = (self._dot_index + 1) % len(_DOT_CYCLE)
        self._label.setText(f"{self._base}{_DOT_CYCLE[self._dot_index]}")

    def _tick_pulse(self) -> None:
        if self._kind in {"success", "error"}:
            return
        self._pulse_phase = (self._pulse_phase + 1) % 20
        # Soft brightness pulse on the blue dot (stylesheet only — no opacity effect).
        if self._pulse_phase < 10:
            color = _BLUE
        else:
            color = "#4AA3D9"
        self._pulse.setStyleSheet(
            f"color: {color}; font-size: 11px; background: transparent; border: none;"
        )

    def _reposition(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        self.adjustSize()
        w = max(self.sizeHint().width(), self.width(), 180)
        h = max(self.sizeHint().height(), self.height(), 40)
        self.resize(w, h)
        x = host.width() - w - self.MARGIN
        y = host.height() - self.STATUS_RESERVE - h - self.MARGIN
        self.move(max(self.MARGIN, x), max(self.MARGIN, y))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[name-defined]
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            if self.isVisible():
                self._reposition()
        return False

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        # Left accent strip (solid paint — no translucent stylesheet).
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(
            0,
            8,
            3,
            max(8, self.height() - 16),
            QColor(self._accent if self._accent else _BLUE),
        )
        painter.end()

    def hideEvent(self, event) -> None:  # noqa: N802
        dot = getattr(self, "_dot_timer", None)
        pulse = getattr(self, "_pulse_timer", None)
        if dot is not None:
            dot.stop()
        if pulse is not None:
            pulse.stop()
        super().hideEvent(event)
