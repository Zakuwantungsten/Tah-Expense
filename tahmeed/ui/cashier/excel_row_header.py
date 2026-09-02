"""Excel-style vertical row header: 1..N labels, row select, drag-to-resize."""
from __future__ import annotations

from PySide6.QtCore import Qt, QItemSelection, QItemSelectionModel, QRect
from PySide6.QtGui import QMouseEvent, QPainter, QColor
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from tahmeed.ui.cashier.register_delegates import COL_DESC

ROW_HEADER_WIDTH = 40
DEFAULT_ROW_HEIGHT = 20   # 15 pt @ 96 DPI
MIN_ROW_HEIGHT = 15
_ROW_HEADER_RESIZE_HIT = 5

# QSS snippet — pair with view-specific horizontal header rules.
ROW_HEADER_QSS = """
QHeaderView:vertical {
    background: #ffffff;
    border: none;
}
QHeaderView::section:vertical {
    background: #F2F2F2;
    color: #333333;
    font-family: Calibri;
    font-size: 11pt;
    padding: 0 2px;
    border: none;
    border-right: 1px solid #D4D4D4;
    border-bottom: 1px solid #D4D4D4;
}
"""

_ROW_HEADER_FILL = QColor("#ffffff")


def sync_row_header_labels(table: QTableWidget) -> None:
    """Keep vertical gutter labels as 1..N for every row."""
    for row in range(table.rowCount()):
        label = str(row + 1)
        item = table.verticalHeaderItem(row)
        if item is None:
            item = QTableWidgetItem(label)
            table.setVerticalHeaderItem(row, item)
        else:
            item.setText(label)
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(Qt.ItemIsEnabled)


class ExcelRowHeaderView(QHeaderView):
    """Excel-style row index gutter: 1..N labels, click/drag row select, resize."""

    def __init__(
        self,
        table: QTableWidget,
        *,
        focus_column: int = COL_DESC,
        owner: QWidget | None = None,
    ) -> None:
        super().__init__(Qt.Vertical, table)
        self._table = table
        self._owner = owner
        self._focus_column = focus_column
        self._dragging = False
        self._anchor_row = -1
        self.setHighlightSections(True)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setSectionResizeMode(QHeaderView.Interactive)
        self.setMinimumSectionSize(MIN_ROW_HEIGHT)
        self.setDefaultSectionSize(DEFAULT_ROW_HEIGHT)
        self.setFixedWidth(ROW_HEADER_WIDTH)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(ROW_HEADER_QSS)
        self.viewport().setAutoFillBackground(True)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # Below the last row there are no sections — fill with table white, not
        # the default dark header chrome.
        vp = self.viewport()
        if vp is None:
            return
        bottom = 0
        if self.count() > 0:
            last = self.count() - 1
            bottom = self.sectionPosition(last) + self.sectionSize(last)
        rect = vp.rect()
        if bottom >= rect.height():
            return
        painter = QPainter(vp)
        try:
            painter.fillRect(QRect(0, bottom, rect.width(), rect.height() - bottom), _ROW_HEADER_FILL)
        finally:
            painter.end()

    def _on_context_menu(self, pos) -> None:
        handler = getattr(self._owner, "_show_row_header_context_menu", None)
        if callable(handler):
            handler(self.mapToGlobal(pos))

    def _on_resize_edge(self, pos) -> bool:
        y = pos.y()
        for logical in range(self.count()):
            edge = self.sectionPosition(logical) + self.sectionSize(logical)
            if abs(y - edge) <= _ROW_HEADER_RESIZE_HIT:
                return True
        return False

    def _select_rows(self, row_a: int, row_b: int) -> None:
        r0, r1 = min(row_a, row_b), max(row_a, row_b)
        model = self._table.model()
        last_col = self._table.columnCount() - 1
        focus_col = self._focus_column
        if focus_col < 0 or focus_col > last_col:
            focus_col = 0
        selection = QItemSelection(
            model.index(r0, 0),
            model.index(r1, last_col),
        )
        self._table.selectionModel().select(
            selection, QItemSelectionModel.ClearAndSelect,
        )
        self._table.selectionModel().setCurrentIndex(
            model.index(row_b, focus_col),
            QItemSelectionModel.NoUpdate,
        )
        self._table.setFocus(Qt.OtherFocusReason)

    def _event_pos(self, event: QMouseEvent):
        if hasattr(event, "position"):
            return event.position().toPoint()
        return event.pos()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = self._event_pos(event)
        if event.button() == Qt.LeftButton and not self._on_resize_edge(pos):
            logical = self.logicalIndexAt(pos)
            if logical >= 0:
                self._dragging = True
                if (event.modifiers() & Qt.ShiftModifier) and self._anchor_row >= 0:
                    self._select_rows(self._anchor_row, logical)
                else:
                    self._anchor_row = logical
                    self._select_rows(logical, logical)
                event.accept()
                return
        self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.LeftButton):
            logical = self.logicalIndexAt(self._event_pos(event))
            if logical >= 0 and self._anchor_row >= 0:
                self._select_rows(self._anchor_row, logical)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)
