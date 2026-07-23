"""Shared From/To QDateEdit helpers for accountant list filters."""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QLabel, QLineEdit

_MIN_FILTER_DATE = QDate(2000, 1, 1)

# Explicit light calendar colors — Fusion + system light mode otherwise yields
# white day text on a white popup background.
_CALENDAR_SS = """
QCalendarWidget QWidget {
    alternate-background-color: #F4F6F8;
    background-color: #FFFFFF;
    color: #111827;
}
QCalendarWidget QAbstractItemView:enabled {
    background-color: #FFFFFF;
    color: #111827;
    selection-background-color: #0077C5;
    selection-color: #FFFFFF;
    outline: 0;
}
QCalendarWidget QAbstractItemView:disabled {
    color: #9CA3AF;
}
QCalendarWidget QToolButton {
    color: #111827;
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 4px;
    margin: 2px;
    font-weight: 600;
}
QCalendarWidget QToolButton:hover {
    background-color: #EFF6FF;
}
QCalendarWidget QMenu {
    background-color: #FFFFFF;
    color: #111827;
}
QCalendarWidget QSpinBox {
    background-color: #FFFFFF;
    color: #111827;
    selection-background-color: #0077C5;
    selection-color: #FFFFFF;
}
"""


def style_calendar_popup(edit: QDateEdit) -> None:
    """Force readable light-mode colors on a QDateEdit calendar popup."""
    cal = edit.calendarWidget()
    if cal is None:
        return
    cal.setStyleSheet(_CALENDAR_SS)


def _qdate_to_dt_start(qd: QDate) -> datetime:
    return datetime(qd.year(), qd.month(), qd.day())


def _qdate_to_dt_end(qd: QDate) -> datetime:
    return datetime(qd.year(), qd.month(), qd.day(), 23, 59, 59)


def add_from_to_editors(
    layout: QHBoxLayout,
    on_changed: Callable[[], None],
    *,
    input_ss: str,
    lbl_factory: Callable[..., QLabel],
    optional: bool = True,
    width: int = 120,
) -> Tuple[QDateEdit, QDateEdit]:
    """Append From/To calendar pickers to *layout*. Returns (from_edit, to_edit)."""
    layout.addWidget(lbl_factory("From", size=12, color="#6B7280"))
    from_edit = QDateEdit()
    from_edit.setCalendarPopup(True)
    from_edit.setDisplayFormat("dd MMM yyyy")
    from_edit.setFixedWidth(width)
    from_edit.setStyleSheet(input_ss)
    style_calendar_popup(from_edit)
    if optional:
        from_edit.setMinimumDate(_MIN_FILTER_DATE)
        from_edit.setSpecialValueText("From")
        from_edit.setDate(_MIN_FILTER_DATE)
    else:
        from_edit.setDate(QDate.currentDate().addMonths(-1))
    from_edit.dateChanged.connect(lambda *_: on_changed())
    layout.addWidget(from_edit)

    layout.addWidget(lbl_factory("To", size=12, color="#6B7280"))
    to_edit = QDateEdit()
    to_edit.setCalendarPopup(True)
    to_edit.setDisplayFormat("dd MMM yyyy")
    to_edit.setFixedWidth(width)
    to_edit.setStyleSheet(input_ss)
    style_calendar_popup(to_edit)
    if optional:
        to_edit.setMinimumDate(_MIN_FILTER_DATE)
        to_edit.setSpecialValueText("To")
        to_edit.setDate(_MIN_FILTER_DATE)
    else:
        to_edit.setDate(QDate.currentDate())
    to_edit.dateChanged.connect(lambda *_: on_changed())
    layout.addWidget(to_edit)
    return from_edit, to_edit


def read_from_to(
    from_edit: QDateEdit,
    to_edit: QDateEdit,
    *,
    optional: bool = True,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Return (date_from, date_to). Optional pickers treat min-date as unset."""
    date_from = date_to = None
    if optional:
        if from_edit.date() > _MIN_FILTER_DATE:
            date_from = _qdate_to_dt_start(from_edit.date())
        if to_edit.date() > _MIN_FILTER_DATE:
            date_to = _qdate_to_dt_end(to_edit.date())
    else:
        date_from = _qdate_to_dt_start(from_edit.date())
        date_to = _qdate_to_dt_end(to_edit.date())
    return date_from, date_to


def sync_from_to(
    from_edit: QDateEdit,
    to_edit: QDateEdit,
    year: int,
    month: int = 0,
    *,
    optional: bool = True,
) -> None:
    """Sync pickers to year/month window without firing dateChanged."""
    from_edit.blockSignals(True)
    to_edit.blockSignals(True)
    try:
        if year <= 0:
            if optional:
                from_edit.setDate(_MIN_FILTER_DATE)
                to_edit.setDate(_MIN_FILTER_DATE)
            return
        if month and 1 <= month <= 12:
            last = calendar.monthrange(year, month)[1]
            start = QDate(year, month, 1)
            end = QDate(year, month, last)
        else:
            start = QDate(year, 1, 1)
            end = QDate(year, 12, 31)
        from_edit.setDate(start)
        to_edit.setDate(end)
    finally:
        from_edit.blockSignals(False)
        to_edit.blockSignals(False)


def clear_list_filters(
    *,
    search_edit: QLineEdit,
    year_cb: QComboBox,
    month_cb: QComboBox,
    from_edit: QDateEdit,
    to_edit: QDateEdit,
) -> Tuple[int, int]:
    """Reset search, year/month, and optional From/To without emitting change signals.

    Returns ``(0, 0)`` for the cleared year/month state.
    """
    search_edit.blockSignals(True)
    year_cb.blockSignals(True)
    month_cb.blockSignals(True)
    try:
        search_edit.clear()
        year_cb.setCurrentIndex(0)
        month_cb.setCurrentIndex(0)
        month_cb.setEnabled(False)
    finally:
        search_edit.blockSignals(False)
        year_cb.blockSignals(False)
        month_cb.blockSignals(False)
    sync_from_to(from_edit, to_edit, 0, 0, optional=True)
    return 0, 0
