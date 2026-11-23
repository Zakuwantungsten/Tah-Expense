"""Hover tooltips + right-click full-view dialog for Analytics charts."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolTip, QMenu, QFrame, QSizePolicy,
)


def open_chart_full_view(
    parent: Optional[QWidget],
    title: str,
    factory: Callable[[], QWidget],
) -> None:
    """Show a large modal dialog with a fresh clone of the chart."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title or "Chart")
    dlg.setModal(True)
    dlg.setMinimumSize(900, 620)
    dlg.resize(1100, 720)
    dlg.setStyleSheet(
        "QDialog { background: #F1F5F9; }"
        "QLabel { border: none; }"
    )
    root = QVBoxLayout(dlg)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(10)

    header = QHBoxLayout()
    title_lbl = QLabel(title or "Chart")
    title_lbl.setStyleSheet(
        "QLabel { color: #C45C5C; font-size: 16px; font-weight: 600;"
        " font-family: 'Segoe UI'; background: transparent; }"
    )
    header.addWidget(title_lbl, 1)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setFixedHeight(32)
    close_btn.setStyleSheet(
        "QPushButton {"
        "  background: #1B2B4B; color: #FFFFFF; border: none; border-radius: 8px;"
        "  padding: 0 16px; font-family: 'Segoe UI'; font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: #243656; }"
    )
    close_btn.clicked.connect(dlg.accept)
    header.addWidget(close_btn)
    root.addLayout(header)

    card = QFrame()
    card.setObjectName("fullViewCard")
    card.setStyleSheet(
        "QFrame#fullViewCard {"
        "  background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;"
        "}"
    )
    card_vl = QVBoxLayout(card)
    card_vl.setContentsMargins(16, 16, 16, 16)
    try:
        body = factory()
    except Exception as exc:
        err = QLabel(f"Could not open chart.\n{exc}")
        err.setWordWrap(True)
        err.setStyleSheet("color: #6B7280; font-size: 13px;")
        card_vl.addWidget(err)
        root.addWidget(card, 1)
        dlg.exec()
        return
    body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    body.setMinimumHeight(480)
    card_vl.addWidget(body, 1)
    root.addWidget(card, 1)
    dlg.exec()


class ChartInteractionMixin:
    """Mixin for painted Analytics charts: hover tip + right-click full view."""

    _chart_title: str = "Chart"

    def _enable_chart_interactions(self, *, title: str = "Chart") -> None:
        self._chart_title = title or "Chart"
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        """Override: return tip text for pixel ``pos``, or None."""
        return None

    def chart_full_view_factory(self) -> QWidget:
        """Override: return a new widget cloned with the same data."""
        raise NotImplementedError

    def chart_full_view_title(self) -> str:
        return getattr(self, "_chart_title", None) or "Chart"

    def set_chart_title(self, title: str) -> None:
        self._chart_title = title or "Chart"

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        tip = self.chart_tooltip_at(event.position().toPoint())
        if tip:
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
        try:
            super().mouseMoveEvent(event)  # type: ignore[misc]
        except Exception:
            pass

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        QToolTip.hideText()
        try:
            super().leaveEvent(event)  # type: ignore[misc]
        except Exception:
            pass

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        act = menu.addAction("Open full view")
        chosen = menu.exec(event.globalPos())
        if chosen is act:
            open_chart_full_view(
                self.window(),
                self.chart_full_view_title(),
                self.chart_full_view_factory,
            )


def rect_contains(rect: QRectF, pos: QPoint) -> bool:
    return rect.contains(float(pos.x()), float(pos.y()))
