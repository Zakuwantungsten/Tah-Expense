"""Excel-style mouse cursors for the cashier register grid.

Excel uses two distinct pointers at the same compact size (~16 px):
  - Hover over cells: *fat* plus (thick arms)
  - Dragging (select / fill): *thin* plus (1 px lines, same span)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QPainter, QPen, QColor, QPixmap

_HOVER_CURSOR: QCursor | None = None
_DRAG_CURSOR: QCursor | None = None

_CURSOR_SIZE = 16
_CURSOR_HOT = 8
_ARM_REACH = 5          # center → tip (matches hover fat plus span)


def _make_cursor(size: int, hot: int, draw) -> QCursor:
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing, False)
    draw(painter, size)
    painter.end()
    return QCursor(px, hot, hot)


def excel_hover_cursor() -> QCursor:
    """Small fat plus — Excel default pointer over grid cells."""

    global _HOVER_CURSOR
    if _HOVER_CURSOR is not None:
        return _HOVER_CURSOR

    def draw(p: QPainter, size: int) -> None:
        center = size // 2
        arm_half = 2          # half-width of each bar (≈4 px thick)
        reach = _ARM_REACH
        p.setPen(QPen(QColor("#000000"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(center - arm_half, center - reach, 2 * arm_half + 1, 2 * reach + 1)
        p.drawRect(center - reach, center - arm_half, 2 * reach + 1, 2 * arm_half + 1)

    _HOVER_CURSOR = _make_cursor(_CURSOR_SIZE, _CURSOR_HOT, draw)
    return _HOVER_CURSOR


def excel_drag_cursor() -> QCursor:
    """Thin plus — same footprint as hover, while dragging a selection or fill."""

    global _DRAG_CURSOR
    if _DRAG_CURSOR is not None:
        return _DRAG_CURSOR

    def draw(p: QPainter, size: int) -> None:
        center = size // 2
        pen = QPen(QColor("#000000"), 1)
        p.setPen(pen)
        p.drawLine(center, center - _ARM_REACH, center, center + _ARM_REACH)
        p.drawLine(center - _ARM_REACH, center, center + _ARM_REACH, center)

    _DRAG_CURSOR = _make_cursor(_CURSOR_SIZE, _CURSOR_HOT, draw)
    return _DRAG_CURSOR


# Back-compat aliases used elsewhere
def cell_selection_cursor() -> QCursor:
    return excel_hover_cursor()


def fill_handle_cursor() -> QCursor:
    return excel_drag_cursor()
