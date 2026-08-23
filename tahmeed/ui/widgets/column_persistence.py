"""Persist QTableWidget column widths across app sessions (local QSettings)."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QHeaderView, QTableWidget


_SETTINGS_ORG = "Tahmeed"
_SETTINGS_APP = "tahmeed-expense"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def has_saved_column_widths(key: str) -> bool:
    """True when this table has stored user (or first-fit) widths."""
    saved = _settings().value(f"col_widths/{key}")
    if not saved:
        return False
    try:
        widths = [int(w) for w in saved]
    except (TypeError, ValueError):
        return False
    return any(w > 24 for w in widths)


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


def apply_pending_column_autofit(table: QTableWidget) -> None:
    """Auto-size columns once when no saved widths exist, then persist them."""
    if not getattr(table, "_col_auto_fit_pending", False):
        return
    if table.rowCount() <= 0 or table.columnCount() <= 0:
        return

    table._col_width_suspend = True  # type: ignore[attr-defined]
    try:
        hdr = table.horizontalHeader()
        stretch_last = hdr.stretchLastSection()
        hdr.setStretchLastSection(False)
        table.resizeColumnsToContents()
        for col in range(table.columnCount()):
            width = table.columnWidth(col) + 16
            table.setColumnWidth(col, min(max(width, 48), 520))
        hdr.setStretchLastSection(stretch_last)
        table._col_auto_fit_pending = False  # type: ignore[attr-defined]
        saver = getattr(table, "_col_width_save", None)
        if callable(saver):
            saver()
    finally:
        table._col_width_suspend = False  # type: ignore[attr-defined]


def bind_column_width_persistence(
    table: QTableWidget,
    key: str,
    defaults: Sequence[int],
    *,
    stretch_columns: Optional[Sequence[int]] = None,
    auto_fit_if_unset: bool = False,
) -> None:
    """Restore widths on bind and save whenever the user resizes a column.

    When ``auto_fit_if_unset`` is True and the user has never saved widths for
    ``key``, columns fit to contents on the first data load and that fit is
    stored. Later sessions restore the saved widths instead of re-fitting, so a
    manual resize sticks across days.
    """
    already_saved = has_saved_column_widths(key)
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
        if getattr(table, "_col_width_suspend", False):
            return
        if getattr(table, "_col_auto_fit_pending", False):
            return
        timer.start()

    timer.timeout.connect(_save)
    table.horizontalHeader().sectionResized.connect(_on_resized)
    table._col_width_timer = timer  # type: ignore[attr-defined]
    table._col_width_save = _save  # type: ignore[attr-defined]
    table._col_auto_fit_pending = bool(auto_fit_if_unset and not already_saved)  # type: ignore[attr-defined]

    if table._col_auto_fit_pending:  # type: ignore[attr-defined]
        model = table.model()

        def _on_rows_inserted(*_args) -> None:
            apply_pending_column_autofit(table)
            if not getattr(table, "_col_auto_fit_pending", False):
                try:
                    model.rowsInserted.disconnect(_on_rows_inserted)
                except TypeError:
                    pass

        model.rowsInserted.connect(_on_rows_inserted)
        if table.rowCount() > 0:
            QTimer.singleShot(0, lambda: apply_pending_column_autofit(table))


# Original public names used across accountant tables.
restore_column_widths = restore_column_widths
bind_column_width_persistence = bind_column_width_persistence
