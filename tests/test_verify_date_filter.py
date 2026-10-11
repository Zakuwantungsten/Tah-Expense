"""Verify Inbox day-picker filter resolution."""

from datetime import date, datetime

from tahmeed.ui.accountant.verify_inbox import (
    _DATE_ALL,
    _DATE_TODAY,
    _DATE_YESTERDAY,
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
