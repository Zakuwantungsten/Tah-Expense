"""Excel-style column header filter: ▾ menu with sort, search, checklist, Apply."""

from __future__ import annotations

from typing import Callable, Optional, Set

from PySide6.QtCore import Qt, Signal, QRect, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QHeaderView, QCheckBox,
)

# Sort modes emitted by the popup
SORT_ASC = "asc"
SORT_DESC = "desc"

# Pixels reserved at the section's right edge for QHeaderView resize drag.
_FILTER_RESIZE_MARGIN = 6
# Clickable ▾ zone width (must match paintSection chevron placement).
_CHEVRON_ZONE_WIDTH = 16


def chevron_hit_contains(section_x: int, section_width: int, click_x: int) -> bool:
    """True when *click_x* is on the ▾ affordance, not the resize handle."""
    if section_width < 28:
        return False
    left = section_x + section_width - _CHEVRON_ZONE_WIDTH - _FILTER_RESIZE_MARGIN
    right = section_x + section_width - _FILTER_RESIZE_MARGIN
    return left <= click_x < right


def cascade_column_values(
    rows: list,
    *,
    target_col: int,
    active_filters: dict,
) -> set:
    """Distinct values for *target_col* from rows that pass every *other* filter.

    ``rows`` is a list of ``{col_index: cell_text}`` maps (empty strings omitted).
    ``active_filters`` maps col_index -> accepted value set (empty set = no filter).
    """
    values: set = set()
    for row in rows:
        ok = True
        for col, accepted in (active_filters or {}).items():
            if col == target_col or not accepted:
                continue
            if (row.get(col) or "") not in accepted:
                ok = False
                break
        if not ok:
            continue
        v = (row.get(target_col) or "").strip()
        if v:
            values.add(v)
    return values


class ExcelColumnFilterPopup(QFrame):
    """Excel-like filter popup: sort + search + multi-select values + Apply/Clear."""

    applied = Signal(object)          # set[str] — empty set = clear / show all
    sort_requested = Signal(str)      # SORT_ASC | SORT_DESC

    def __init__(
        self,
        values: set,
        current: set,
        parent=None,
        *,
        column_label: str = "Column",
        sort_kind: str = "text",  # "text" | "number" | "date" | "truck"
        sort_only: bool = False,
    ):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("excelColFilterPopup")
        self.setStyleSheet(
            "QFrame#excelColFilterPopup{"
            " background:#ffffff;border:1px solid #D1D5DB;border-radius:6px;}"
            "QLabel{background:transparent;border:none;}"
            "QPushButton{background:transparent;border:none;text-align:left;"
            " padding:5px 8px;font-size:12px;color:#111827;border-radius:4px;}"
            "QPushButton:hover{background:#F3F4F6;}"
        )
        self._all_values = sorted(values, key=lambda v: (v.lower(), v))
        self._current = set(current or [])
        self._column_label = column_label
        self._sort_kind = sort_kind
        self._sort_only = bool(sort_only)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        title = QLabel(self._column_label)
        title.setStyleSheet(
            "font-size:11px;font-weight:700;color:#6B7280;padding:0 4px 2px;"
        )
        root.addWidget(title)

        if self._sort_kind == "number":
            asc_lbl, desc_lbl = "Sort Smallest → Largest", "Sort Largest → Smallest"
        elif self._sort_kind == "date":
            asc_lbl, desc_lbl = "Sort Oldest → Newest", "Sort Newest → Oldest"
        elif self._sort_kind == "truck":
            asc_lbl, desc_lbl = "Sort Smallest No. → Largest", "Sort Largest No. → Smallest"
        else:
            asc_lbl, desc_lbl = "Sort A → Z", "Sort Z → A"

        sort_asc = QPushButton(f"  {asc_lbl}")
        sort_asc.setCursor(Qt.PointingHandCursor)
        sort_asc.clicked.connect(lambda: self._emit_sort(SORT_ASC))
        root.addWidget(sort_asc)

        sort_desc = QPushButton(f"  {desc_lbl}")
        sort_desc.setCursor(Qt.PointingHandCursor)
        sort_desc.clicked.connect(lambda: self._emit_sort(SORT_DESC))
        root.addWidget(sort_desc)

        if not self._sort_only:
            clear_filter = QPushButton("  Clear Filter from Column")
            clear_filter.setCursor(Qt.PointingHandCursor)
            clear_filter.setEnabled(bool(self._current))
            clear_filter.setStyleSheet(
                "QPushButton{background:transparent;border:none;text-align:left;"
                " padding:5px 8px;font-size:12px;color:#DC2626;border-radius:4px;}"
                "QPushButton:hover{background:#FEF2F2;}"
                "QPushButton:disabled{color:#D1D5DB;}"
            )
            clear_filter.clicked.connect(self._on_clear_filter)
            root.addWidget(clear_filter)

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background:#E5E7EB;border:none;")
            root.addWidget(sep)

            hint = QLabel(f"{len(self._all_values)} value(s) in this table")
            hint.setStyleSheet("font-size:10px;color:#6B7280;padding:2px 4px;")
            root.addWidget(hint)

            self._search = QLineEdit()
            self._search.setPlaceholderText("Search…")
            self._search.setClearButtonEnabled(True)
            self._search.setStyleSheet(
                "QLineEdit{border:1px solid #D1D5DB;border-radius:4px;"
                "padding:4px 8px;font-size:12px;color:#111827;background:#fff;}"
            )
            self._search.textChanged.connect(self._refilter)
            root.addWidget(self._search)

            self._select_all = QCheckBox("Select All")
            self._select_all.setTristate(True)
            self._select_all.setStyleSheet(
                "QCheckBox{font-size:12px;color:#111827;padding:2px 4px;}"
            )
            self._select_all.stateChanged.connect(self._on_select_all)
            root.addWidget(self._select_all)

            self._list = QListWidget()
            self._list.setMinimumWidth(240)
            self._list.setMaximumHeight(240)
            self._list.setStyleSheet(
                "QListWidget{border:1px solid #E5E7EB;border-radius:4px;font-size:12px;"
                " color:#111827;background:#fff;}"
                "QListWidget::item{padding:3px 6px;}"
            )
            self._list.itemChanged.connect(self._sync_select_all_state)
            root.addWidget(self._list, 1)

            btns = QHBoxLayout()
            btns.setSpacing(6)
            clear_btn = QPushButton("Clear")
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setStyleSheet(
                "QPushButton{background:#fff;color:#6B7280;border:1px solid #D1D5DB;"
                " border-radius:4px;padding:5px 12px;font-size:12px;}"
                "QPushButton:hover{background:#F3F4F6;}"
            )
            clear_btn.clicked.connect(self._on_uncheck_visible)
            apply_btn = QPushButton("Apply")
            apply_btn.setCursor(Qt.PointingHandCursor)
            apply_btn.setStyleSheet(
                "QPushButton{background:#0077C5;color:#fff;border:none;"
                "border-radius:4px;padding:5px 14px;font-weight:600;font-size:12px;}"
                "QPushButton:hover{background:#005EA3;}"
            )
            apply_btn.clicked.connect(self._on_apply)
            btns.addWidget(clear_btn)
            btns.addStretch()
            btns.addWidget(apply_btn)
            root.addLayout(btns)

            self._refilter("")
            self._search.setFocus()
        else:
            self._search = None
            self._select_all = None
            self._list = None

    def _emit_sort(self, mode: str) -> None:
        self.sort_requested.emit(mode)
        self.close()

    def _on_clear_filter(self) -> None:
        self.applied.emit(set())
        self.close()

    def _on_uncheck_visible(self) -> None:
        if self._list is None:
            return
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.Unchecked)
        self._list.blockSignals(False)
        self._select_all.blockSignals(True)
        self._select_all.setCheckState(Qt.Unchecked)
        self._select_all.blockSignals(False)

    def _on_select_all(self, state: int) -> None:
        checked = state == Qt.Checked or state == int(Qt.Checked)
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(
                Qt.Checked if checked else Qt.Unchecked
            )
        self._list.blockSignals(False)

    def _sync_select_all_state(self, *_args) -> None:
        if self._list.count() == 0:
            return
        n_checked = sum(
            1 for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        )
        self._select_all.blockSignals(True)
        if n_checked == 0:
            self._select_all.setCheckState(Qt.Unchecked)
        elif n_checked == self._list.count():
            self._select_all.setCheckState(Qt.Checked)
        else:
            self._select_all.setCheckState(Qt.PartiallyChecked)
        self._select_all.blockSignals(False)

    def _refilter(self, text: str = "") -> None:
        if self._sort_only or self._list is None:
            return
        needle = (text or self._search.text() or "").strip().lower()
        self._list.blockSignals(True)
        self._list.clear()
        for val in self._all_values:
            if needle and needle not in val.lower():
                continue
            it = QListWidgetItem(val)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            # Empty current = show-all mode → pre-check everything.
            checked = (not self._current) or (val in self._current)
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self._list.addItem(it)
        self._list.blockSignals(False)
        self._sync_select_all_state()

    def _checked(self) -> set:
        if self._list is None:
            return set()
        # Preserve hidden (search-filtered-out) prior selections, sync visible.
        result = set(self._current) if self._current else set(self._all_values)
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.Checked:
                result.add(it.text())
            else:
                result.discard(it.text())
        return result

    def _on_apply(self) -> None:
        checked = self._checked()
        # All values selected → treat as no filter (show all).
        if checked >= set(self._all_values):
            self.applied.emit(set())
        else:
            self.applied.emit(checked)
        self.close()


class ExcelFilterHeaderView(QHeaderView):
    """Horizontal header with Excel ▾ filter chevrons."""

    filter_changed = Signal(int, object)   # (col, accepted set | empty = cleared)
    sort_requested = Signal(int, str)      # (col, SORT_ASC | SORT_DESC)

    def __init__(
        self,
        parent=None,
        *,
        filterable_columns: Optional[Set[int]] = None,
        sort_kinds: Optional[dict] = None,  # col -> "text"|"number"|"date"|"truck"
        sort_only: bool = False,
    ):
        super().__init__(Qt.Horizontal, parent)
        self._active: dict = {}
        self._value_provider: Optional[Callable[[int], set]] = None
        self._label_provider: Optional[Callable[[int], str]] = None
        self._filterable = set(filterable_columns or [])
        self._sort_kinds = dict(sort_kinds or {})
        self._sort_only = bool(sort_only)
        self._popup = None
        self.setSectionsClickable(True)
        self.setHighlightSections(False)

    def set_filterable_columns(self, cols: Set[int]) -> None:
        self._filterable = set(cols)
        self.viewport().update()

    def set_value_provider(self, provider: Callable[[int], set]) -> None:
        self._value_provider = provider

    def set_label_provider(self, provider: Callable[[int], str]) -> None:
        self._label_provider = provider

    def clear_filters(self) -> None:
        self._active.clear()
        self.viewport().update()

    def sync_active(self, filters: dict) -> None:
        self._active = {c: set(v) for c, v in (filters or {}).items() if v}
        self.viewport().update()

    def active_filters(self) -> dict:
        return {c: set(v) for c, v in self._active.items() if v}

    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        return QSize(sh.width(), max(sh.height(), 28))

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if logical_index not in self._filterable or rect.width() < 28:
            return
        painter.save()
        is_active = bool(self._active.get(logical_index))
        painter.setPen(QColor("#EA580C") if is_active else QColor("#64748B"))
        f = painter.font()
        f.setPointSize(8)
        f.setBold(is_active)
        painter.setFont(f)
        painter.drawText(
            QRect(rect.right() - 16, rect.top(), 14, rect.height()),
            Qt.AlignVCenter | Qt.AlignHCenter,
            "▾",
        )
        painter.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            col = self.logicalIndexAt(event.pos())
            if col in self._filterable:
                col_x = self.sectionViewportPosition(col)
                col_w = self.sectionSize(col)
                if chevron_hit_contains(col_x, col_w, event.pos().x()):
                    self._open_menu(col, event.globalPosition().toPoint())
                    return
        super().mousePressEvent(event)

    def _open_menu(self, col: int, global_pos) -> None:
        if not self._sort_only and not callable(self._value_provider):
            return
        values = set()
        if callable(self._value_provider):
            values = set(self._value_provider(col) or [])
        current = set(self._active.get(col, set()) or [])
        values |= current
        if not values and not current:
            # Still allow sort-only menu when the column has no values yet.
            values = set()

        if self._popup is not None:
            self._popup.close()
            self._popup = None

        label = (
            self._label_provider(col)
            if callable(self._label_provider)
            else f"Column {col}"
        )
        kind = self._sort_kinds.get(col, "text")
        popup = ExcelColumnFilterPopup(
            values,
            current,
            parent=self,
            column_label=label,
            sort_kind=kind,
            sort_only=self._sort_only,
        )
        self._popup = popup

        def _on_applied(new_filter):
            new_filter = set(new_filter or [])
            if new_filter:
                self._active[col] = new_filter
            else:
                self._active.pop(col, None)
            self.filter_changed.emit(col, new_filter)
            self.viewport().update()

        def _on_sort(mode: str):
            self.sort_requested.emit(col, mode)

        popup.applied.connect(_on_applied)
        popup.sort_requested.connect(_on_sort)
        popup.adjustSize()
        # Anchor under the chevron, keep on-screen.
        popup.move(global_pos.x() - popup.width() + 16, global_pos.y() + 4)
        popup.show()
