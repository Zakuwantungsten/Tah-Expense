"""Checkable multi-select combo — same UX as Truck Overview All Sources."""

from __future__ import annotations

from time import monotonic
from typing import List, Sequence

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QColor, QFontMetrics, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox


# Force light readable colors — parent/app themes often paint editable
# combos and their popup lists black-on-black.
_COMBO_SS = """
QComboBox {
    border: 1px solid #E5E7EB;
    border-radius: 5px;
    background: #FFFFFF;
    color: #111827;
    font-size: 12px;
    font-family: 'Segoe UI';
    padding: 0 8px;
}
QComboBox:focus { border-color: #0077C5; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    border: 1px solid #E5E7EB;
    background: #FFFFFF;
    color: #111827;
    selection-background-color: #E8F4FD;
    selection-color: #111827;
    outline: none;
    padding: 2px;
}
QComboBox QLineEdit {
    background: #FFFFFF;
    color: #111827;
    border: none;
    selection-background-color: #DBEAFE;
    selection-color: #111827;
}
"""

_FG = QColor("#111827")
_BG = QColor("#FFFFFF")

# Closed combo stays compact on the toolbar; the open list grows to the
# longest label so filenames are readable. Cap + h-scroll for outliers.
_POPUP_CHECK_PAD = 48
_POPUP_MAX_WIDTH = 560


def popup_content_width(
    labels: Sequence[str],
    font_metrics: QFontMetrics,
    scrollbar_extra: int = 0,
) -> int:
    """Pixel width needed to paint every label plus checkbox padding."""
    longest = 0
    for label in labels:
        longest = max(longest, font_metrics.horizontalAdvance(str(label)))
    return longest + _POPUP_CHECK_PAD + scrollbar_extra


def desired_popup_width(
    labels: Sequence[str],
    *,
    combo_width: int,
    font_metrics: QFontMetrics,
    scrollbar_extra: int = 0,
) -> int:
    """Width for the open list: fit the longest label, never narrower than the combo."""
    needed = popup_content_width(labels, font_metrics, scrollbar_extra)
    return max(combo_width, min(needed, _POPUP_MAX_WIDTH))


class CheckableMultiCombo(QComboBox):
    """Pick one or many values, or the All row (empty selection = all)."""

    selectionChanged = Signal()

    def __init__(
        self,
        all_label: str = "All",
        *,
        noun_plural: str = "selected",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._all_label = all_label
        self._noun_plural = noun_plural
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText(all_label)
        self.lineEdit().setCursor(Qt.PointingHandCursor)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(14)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(_COMBO_SS)

        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self._updating = False

        all_item = self._make_item(all_label, "all")
        all_item.setCheckState(Qt.Checked)
        self._model.appendRow(all_item)

        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self._model.itemChanged.connect(self._on_item_changed)
        self._refresh_label()

    @staticmethod
    def _make_item(label: str, key) -> QStandardItem:
        item = QStandardItem(label)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setData(key, Qt.UserRole)
        item.setCheckState(Qt.Unchecked)
        item.setForeground(_FG)
        item.setBackground(_BG)
        item.setToolTip(label)
        return item

    def set_options(
        self,
        options: Sequence[str],
        *,
        keep_selected: bool = True,
        emit: bool = True,
    ) -> None:
        """Replace option rows; optionally preserve still-valid selections."""
        prev = set(self.selected_values()) if keep_selected else set()
        self._updating = True
        try:
            while self._model.rowCount() > 1:
                self._model.removeRow(1)
            for label in options:
                item = self._make_item(label, label)
                if label in prev:
                    item.setCheckState(Qt.Checked)
                self._model.appendRow(item)
            kept = [v for v in prev if v in set(options)]
            if kept:
                self._model.item(0).setCheckState(Qt.Unchecked)
            else:
                self._model.item(0).setCheckState(Qt.Checked)
                for row in range(1, self._model.rowCount()):
                    self._model.item(row).setCheckState(Qt.Unchecked)
        finally:
            self._updating = False
        self._refresh_label()
        if emit:
            self.selectionChanged.emit()

    def setStyleSheet(self, ss: str) -> None:  # noqa: N802 — Qt API
        """Merge caller styles with the built-in light readable base."""
        # Keep our contrast rules last so parent dark themes cannot override.
        super().setStyleSheet((ss or "") + "\n" + _COMBO_SS)

    def showPopup(self) -> None:
        width = self._size_popup_to_contents()
        self._popup_shown_at = monotonic()
        super().showPopup()
        container = self.view().parentWidget()
        if container is not None and container.width() < width:
            container.resize(width, container.height())

    def _size_popup_to_contents(self) -> int:
        """Grow the open list to the longest option so long filenames stay readable."""
        view = self.view()
        labels = [
            self._model.item(row).text()
            for row in range(self._model.rowCount())
            if self._model.item(row) is not None
        ]
        extra = 0
        if self._model.rowCount() > self.maxVisibleItems():
            vsb = view.verticalScrollBar()
            extra = vsb.sizeHint().width() if vsb is not None else 16
        fm = view.fontMetrics()
        needed = popup_content_width(labels, fm, extra)
        width = desired_popup_width(
            labels,
            combo_width=self.width(),
            font_metrics=fm,
            scrollbar_extra=extra,
        )
        view.setMinimumWidth(width)
        view.setTextElideMode(Qt.ElideNone)
        view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if needed > _POPUP_MAX_WIDTH
            else Qt.ScrollBarAlwaysOff
        )
        return width

    def hidePopup(self) -> None:
        if monotonic() - getattr(self, "_popup_shown_at", 0) < 0.2:
            return
        super().hidePopup()
        self._refresh_label()

    def mousePressEvent(self, event) -> None:
        self.showPopup()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            self.showPopup()
            return True
        if obj is self.view().viewport():
            if event.type() == QEvent.MouseButtonPress:
                index = self.view().indexAt(event.position().toPoint())
                if index.isValid():
                    return True
            if event.type() == QEvent.MouseButtonRelease:
                index = self.view().indexAt(event.position().toPoint())
                if index.isValid():
                    item = self._model.itemFromIndex(index)
                    if item is not None and item.flags() & Qt.ItemIsUserCheckable:
                        item.setCheckState(
                            Qt.Unchecked
                            if item.checkState() == Qt.Checked
                            else Qt.Checked
                        )
                        return True
        return super().eventFilter(obj, event)

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            key = item.data(Qt.UserRole)
            if key == "all" and item.checkState() == Qt.Checked:
                for row in range(1, self._model.rowCount()):
                    self._model.item(row).setCheckState(Qt.Unchecked)
            elif key != "all" and item.checkState() == Qt.Checked:
                self._model.item(0).setCheckState(Qt.Unchecked)
            if not any(
                self._model.item(row).checkState() == Qt.Checked
                for row in range(self._model.rowCount())
            ):
                self._model.item(0).setCheckState(Qt.Checked)
        finally:
            self._updating = False
        self._refresh_label()
        self.selectionChanged.emit()

    def selected_values(self) -> List[str]:
        """Selected labels, or ``[]`` meaning All."""
        if self._model.item(0).checkState() == Qt.Checked:
            return []
        values: List[str] = []
        for row in range(1, self._model.rowCount()):
            item = self._model.item(row)
            if item.checkState() == Qt.Checked:
                values.append(str(item.data(Qt.UserRole)))
        return values

    def summary_text(self) -> str:
        if self._model.item(0).checkState() == Qt.Checked:
            return self._all_label
        labels = [
            self._model.item(row).text()
            for row in range(1, self._model.rowCount())
            if self._model.item(row).checkState() == Qt.Checked
        ]
        if not labels:
            return self._all_label
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]}, {labels[1]}"
        return f"{len(labels)} {self._noun_plural}"

    def _refresh_label(self) -> None:
        text = self.summary_text()
        le = self.lineEdit()
        le.setText(text)
        le.setToolTip(text)
        # Re-assert contrast after Qt may reset palette on editable combos.
        le.setStyleSheet(
            "background:#FFFFFF;color:#111827;border:none;"
            "selection-background-color:#DBEAFE;selection-color:#111827;"
        )

    def reset_to_all(self, *, emit: bool = True) -> None:
        self._updating = True
        try:
            self._model.item(0).setCheckState(Qt.Checked)
            for row in range(1, self._model.rowCount()):
                self._model.item(row).setCheckState(Qt.Unchecked)
        finally:
            self._updating = False
        self._refresh_label()
        if emit:
            self.selectionChanged.emit()
