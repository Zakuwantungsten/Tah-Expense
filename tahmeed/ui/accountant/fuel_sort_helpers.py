"""Build fuel station column sort specs from schema columns."""

from __future__ import annotations

from typing import List, Tuple

from tahmeed.ui.widgets.sortable_ledger_header import ColumnSpec

_KIND_MAP = {
    "text": "text",
    "date": "date",
    "num": "number",
    "money": "number",
}

_TRUCK_KEYS = frozenset({"truck_no"})


def diesel_columns_sort(columns: List[Tuple[str, str, str]]) -> List[ColumnSpec]:
    """Map fuel schema columns to sort specs; append File Name column."""
    specs: List[ColumnSpec] = []
    for label, key, kind in columns:
        if key == "sn":
            specs.append((label, None, "text"))
            continue
        sort_kind = "truck" if key in _TRUCK_KEYS else _KIND_MAP.get(kind, "text")
        specs.append((label, key, sort_kind))
    specs.append(("UPLOAD DESCRIPTION", "upload_label", "text"))
    return specs
