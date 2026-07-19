"""Persist QTableWidget column widths across app sessions (local QSettings)."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QHeaderView, QTableWidget


_SETTINGS_ORG = "Tahmeed"
_SETTINGS_APP = "tahmeed-expense"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def restore_column_widths(
    table: QTableWidget,
    key: str,
    defaults: Sequence[int],
    *,
    stretch_columns: Optional[Sequence[int]] = None,
) -> None:
    """Apply saved widths, falling back to ``defaults``."""
    stretch = set(stretch_columns or ())
    saved = _settings().value(f"col_widths/{key}")
    widths: List[int] = []
    if saved:
        try:
            widths = [int(w) for w in saved]
        except (TypeError, ValueError):
            widths = []

    hdr = table.horizontalHeader()
    for col in range(table.columnCount()):
        if col in stretch:
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            continue
        hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        if col < len(widths) and widths[col] > 24:
            table.setColumnWidth(col, widths[col])
        elif col < len(defaults) and defaults[col] > 0:
            table.setColumnWidth(col, defaults[col])


def bind_column_width_persistence(
    table: QTableWidget,
    key: str,
    defaults: Sequence[int],
    *,
    stretch_columns: Optional[Sequence[int]] = None,
) -> None:
    """Restore widths on bind and save whenever the user resizes a column."""
    restore_column_widths(table, key, defaults, stretch_columns=stretch_columns)

    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.setInterval(250)

    stretch = set(stretch_columns or ())

    def _save() -> None:
        widths: List[int] = []
        for col in range(table.columnCount()):
            if col in stretch:
                widths.append(0)
            else:
                widths.append(table.columnWidth(col))
        _settings().setValue(f"col_widths/{key}", widths)

    def _on_resized(_index: int, _old: int, _new: int) -> None:
        timer.start()

    timer.timeout.connect(_save)
    table.horizontalHeader().sectionResized.connect(_on_resized)
    table._col_width_timer = timer  # type: ignore[attr-defined]
