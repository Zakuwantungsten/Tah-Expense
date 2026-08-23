"""Sort-only ledger header — same UX as Master Expenses without column filters."""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget

from tahmeed.ui.widgets.excel_column_filter import (
    ExcelFilterHeaderView,
    SORT_ASC,
)

# (header label, mongo sort field or None, kind: text|number|date|truck)
ColumnSpec = Tuple[str, Optional[str], str]


class LedgerSortState:
    """Tracks active sort and wires header clicks to a reload callback."""

    def __init__(
        self,
        table: QTableWidget,
        columns: Sequence[ColumnSpec],
        *,
        default_field: str = "date",
        default_asc: bool = False,
        on_sort_changed: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self._table = table
        self._columns = list(columns)
        self._default_field = default_field
        self._sort_field = default_field
        self._sort_asc = default_asc
        self._on_sort_changed = on_sort_changed
        self._attach_header()

    @property
    def sort_field(self) -> str:
        return self._sort_field

    @property
    def sort_asc(self) -> bool:
        return self._sort_asc

    def _attach_header(self) -> None:
        sortable: set[int] = set()
        sort_kinds: dict[int, str] = {}
        for i, (_label, field, kind) in enumerate(self._columns):
            if field:
                sortable.add(i)
                sort_kinds[i] = kind

        filter_hdr = ExcelFilterHeaderView(
            self._table,
            filterable_columns=sortable,
            sort_kinds=sort_kinds,
            sort_only=True,
        )
        filter_hdr.set_label_provider(
            lambda c: self._columns[c][0] if 0 <= c < len(self._columns) else "",
        )
        filter_hdr.sort_requested.connect(self._on_excel_sort)
        self._table.setHorizontalHeader(filter_hdr)

        hdr = self._table.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header_click)
        self._update_sort_indicator()

    def _field_for_col(self, col: int) -> Optional[str]:
        if 0 <= col < len(self._columns):
            return self._columns[col][1]
        return None

    def _on_header_click(self, col: int) -> None:
        field = self._field_for_col(col)
        if not field:
            return
        if self._sort_field == field:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_field = field
            self._sort_asc = False
        self._apply_sort()

    def _on_excel_sort(self, col: int, mode: str) -> None:
        field = self._field_for_col(col)
        if not field:
            return
        self._sort_field = field
        self._sort_asc = mode == SORT_ASC
        self._apply_sort()

    def _apply_sort(self) -> None:
        self._update_sort_indicator()
        if self._on_sort_changed:
            self._on_sort_changed(self._sort_field, self._sort_asc)

    def _update_sort_indicator(self) -> None:
        order = Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder
        hdr = self._table.horizontalHeader()
        for i, (_label, field, _kind) in enumerate(self._columns):
            if field == self._sort_field:
                hdr.setSortIndicator(i, order)
                return

    def reset(self) -> None:
        """Restore default sort (e.g. after Clear filters)."""
        self._sort_field = self._default_field
        self._sort_asc = self._default_asc
        self._update_sort_indicator()


def attach_sortable_header(
    table: QTableWidget,
    columns: Sequence[ColumnSpec],
    *,
    default_field: str = "date",
    default_asc: bool = False,
    on_sort_changed: Optional[Callable[[str, bool], None]] = None,
) -> LedgerSortState:
    """Replace table header with sort-only Excel-style header."""
    return LedgerSortState(
        table,
        columns,
        default_field=default_field,
        default_asc=default_asc,
        on_sort_changed=on_sort_changed,
    )


def column_specs_from_labels(
    headers: Sequence[str],
    field_map: dict[str, str],
    *,
    kind_map: Optional[dict[str, str]] = None,
    skip_labels: Optional[set[str]] = None,
) -> List[ColumnSpec]:
    """Build column specs from header labels and a label→mongo-field map."""
    skip = skip_labels or set()
    kinds = kind_map or {}
    specs: List[ColumnSpec] = []
    for label in headers:
        if label in skip:
            specs.append((label, None, "text"))
            continue
        field = field_map.get(label)
        kind = kinds.get(label, kinds.get(field or "", "text"))
        specs.append((label, field, kind))
    return specs
