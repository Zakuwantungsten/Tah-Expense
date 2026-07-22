"""Shared From/To QDateEdit helpers for accountant list filters."""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDateEdit, QHBoxLayout, QLabel

_MIN_FILTER_DATE = QDate(2000, 1, 1)


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
