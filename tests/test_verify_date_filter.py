"""Verify Inbox day-picker filter resolution."""

from datetime import date, datetime, timedelta

import pytest
from bson import ObjectId
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.ui.accountant.verify_inbox import (
    _COL_CHK,
    _DATE_ALL,
    _DATE_TODAY,
    _DATE_YESTERDAY,
    VerifyInboxWidget,
    _resolve_date_filter,
)


def test_resolve_all_dates_clears_bounds() -> None:
    assert _resolve_date_filter(_DATE_ALL) == (None, None)
    assert _resolve_date_filter(None) == (None, None)
    assert _resolve_date_filter("") == (None, None)


def test_resolve_today_and_yesterday() -> None:
    now = datetime(2026, 10, 11, 15, 30, 0)
    today_from, today_to = _resolve_date_filter(_DATE_TODAY, now=now)
    assert today_from == datetime(2026, 10, 11, 0, 0, 0)
    assert today_to == datetime(2026, 10, 11, 23, 59, 59)

    y_from, y_to = _resolve_date_filter(_DATE_YESTERDAY, now=now)
    assert y_from == datetime(2026, 10, 10, 0, 0, 0)
    assert y_to == datetime(2026, 10, 10, 23, 59, 59)


def test_resolve_specific_day_token() -> None:
    start, end = _resolve_date_filter("2026-06-01")
    assert start == datetime(2026, 6, 1, 0, 0, 0)
    assert end == datetime(2026, 6, 1, 23, 59, 59)
    assert start.date() == date(2026, 6, 1)


def test_resolve_invalid_token_falls_back_to_all() -> None:
    assert _resolve_date_filter("not-a-date") == (None, None)


def _inbox() -> VerifyInboxWidget:
    QApplication.instance() or QApplication([])
    user = User(
        username="acct",
        password_hash="x",
        role="accountant",
        full_name="Accountant",
        _id=ObjectId(),
    )
    return VerifyInboxWidget(user)


def _combo_data(inbox: VerifyInboxWidget) -> list:
    cb = inbox._filter_bar._date_cb
    return [cb.itemData(i) for i in range(cb.count())]


def test_day_picker_lists_every_queue_register_day_newest_first() -> None:
    inbox = _inbox()
    today = date.today()
    older = today - timedelta(days=6)
    inbox._filter_bar.populate_available_dates(
        [older, today, today - timedelta(days=1), older]
    )

    cb = inbox._filter_bar._date_cb
    assert _combo_data(inbox) == [
        _DATE_ALL,
        today.isoformat(),
        (today - timedelta(days=1)).isoformat(),
        older.isoformat(),
    ]
    assert cb.itemText(1).endswith("(Today)")
    assert cb.itemText(2).endswith("(Yesterday)")
    assert cb.itemText(3) == older.strftime("%d %b %Y")


def test_day_picker_keeps_selected_day_across_reload() -> None:
    inbox = _inbox()
    picked = date.today() - timedelta(days=3)
    inbox._filter_bar.populate_available_dates([picked, date.today()])
    cb = inbox._filter_bar._date_cb
    cb.setCurrentIndex(cb.findData(picked.isoformat()))

    inbox._filter_bar.populate_available_dates([date.today()])

    assert cb.currentData() == picked.isoformat()


@pytest.mark.asyncio
async def test_day_list_loads_on_its_own_and_survives_failure(monkeypatch) -> None:
    inbox = _inbox()
    day = date.today() - timedelta(days=5)

    async def days(_tab):
        return [day]

    monkeypatch.setattr(inbox, "_fetch_available_dates", days)
    await inbox._refresh_available_dates(0, inbox._reload_generation)
    assert day.isoformat() in _combo_data(inbox)

    async def boom(_tab):
        raise TimeoutError("slow server")

    monkeypatch.setattr(inbox, "_fetch_available_dates", boom)
    await inbox._refresh_available_dates(0, inbox._reload_generation)
    assert day.isoformat() in _combo_data(inbox)


@pytest.mark.asyncio
async def test_select_all_on_new_counts_the_whole_queue(monkeypatch) -> None:
    from tahmeed.services import accountant_service

    inbox = _inbox()
    pool = [
        Transaction(
            _id=ObjectId(), date=datetime(2026, 10, 21), description="PARKING",
            truck_number="T1", amount=1000.0, item="PARKING",
        )
        for _ in range(1100)
    ]
    limits: list = []

    async def fetch_page(*, skip, limit, kw):
        limits.append(limit)
        return pool[skip:skip + limit]

    async def count(**_kw):
        return len(pool)

    async def names(_ids):
        return {}

    monkeypatch.setattr(inbox, "_fetch_page", fetch_page)
    monkeypatch.setattr(accountant_service, "count_unverified_filtered", count)
    monkeypatch.setattr(accountant_service, "get_cashier_names", names)

    inbox._current_tab = 0
    inbox._loading = False
    inbox._fill_table(pool[:100], {}, 0, append=False)
    inbox._loaded = 100
    inbox._total = 101  # New's uncounted "loaded + 1" estimate
    inbox._page_has_count = False

    await inbox._select_all_matching()

    assert inbox._loaded == 1100
    assert inbox._total == 1100
    assert all(limit <= 500 for limit in limits)
    t = inbox._table
    assert t.rowCount() == 1100
    assert all(
        t.item(r, _COL_CHK).checkState() == Qt.Checked for r in range(t.rowCount())
    )
