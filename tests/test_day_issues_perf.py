"""Day Issues performance: dirty flag, skip redundant scans, all-days cache."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from tahmeed.models.user import User
from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    collect_day_issues_all_days,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.cashier.day_issues import DayIssuesWidget


def _app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _issue(desc: str, *, kind: str = "Unmapped") -> DayIssue:
    return DayIssue(
        kind=kind,
        description=desc,
        description_key=normalize_description(desc),
        count=1,
        detail="No payee + item mapping",
    )


def _tx(**kwargs):
    base = dict(
        date=datetime(2026, 3, 15),
        description="TRIANGLE",
        truck_number="T1",
        amount=10.0,
        item="",
        category_name="",
        payee="",
        verified=False,
        trashed=False,
        discarded=False,
        cashier_id=ObjectId(),
        _id=ObjectId(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_set_date_skips_refresh_when_clean() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []

    def capture() -> None:
        calls.append("refresh")

    widget._schedule_refresh = capture  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    day = date(2026, 3, 15)
    widget._target = day

    widget.set_date(day)
    assert calls == []

    widget.set_date(day, force=True)
    assert calls == ["refresh"]


def test_set_date_refreshes_when_dirty_or_date_changed() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = True
    day = date(2026, 3, 15)
    widget._target = day

    widget.set_date(day)
    assert calls == ["refresh"]

    calls.clear()
    widget._dirty = False
    widget._loaded = True
    widget.set_date(date(2026, 3, 16))
    assert calls == ["refresh"]
    assert widget._target == date(2026, 3, 16)


def test_open_for_date_forces_this_day_and_refreshes() -> None:
    """Table / sidebar deep-link: SHOW=This day + table date, always reload."""
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)
    # Leave the page on All days — open_for_date must snap back to This day.
    widget._scope_combo.blockSignals(True)
    all_idx = widget._scope_combo.findData("all")
    assert all_idx >= 0
    widget._scope_combo.setCurrentIndex(all_idx)
    widget._scope_combo.blockSignals(False)
    widget._scope = "all"
    widget._date.setEnabled(False)

    day = date(2026, 3, 15)
    widget.open_for_date(day)

    assert widget._scope == "day"
    assert widget._scope_combo.currentData() == "day"
    assert widget._target == day
    assert widget._date.isEnabled()
    assert widget._date.date().toPython() == day
    assert calls == ["refresh"]


def test_open_from_verify_filters_and_uses_all_days() -> None:
    """Verify handoff: VALUE-filter blocked keys + All days when multi-day."""
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)
    widget._scope = "day"

    keys = (
        normalize_description("AGENCY FEE"),
        normalize_description("ALLOWANCE"),
    )
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=keys,
        all_days=True,
    )

    assert widget._scope == "all"
    assert widget._scope_combo.currentData() == "all"
    assert widget._target == date(2026, 3, 15)
    assert widget._value_filter == set(keys)
    assert widget._checked_keys == set(keys)
    assert calls == ["refresh"]


def test_open_from_verify_single_day_keeps_this_day() -> None:
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)

    key = normalize_description("CHANNELS")
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=[key],
        all_days=False,
    )

    assert widget._scope == "day"
    assert widget._value_filter == {key}
    assert widget._checked_keys == {key}
    assert widget._target == date(2026, 3, 15)
    assert calls == ["refresh"]


def test_on_register_source_changed_marks_dirty_without_scan_when_hidden() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._day_snap_cache[date(2026, 3, 15)] = DayIssuesSnapshot(
        target_date=date(2026, 3, 15), issues=[]
    )
    widget._target = date(2026, 3, 15)

    widget.on_register_source_changed(visible=False)
    assert widget._dirty is True
    assert date(2026, 3, 15) not in widget._day_snap_cache
    assert calls == []
    assert not widget._source_refresh_timer.isActive()


def test_drop_issue_keys_updates_list_and_badge_signal() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    a = _issue("LATRA FEE")
    b = _issue("DIESEL")
    widget._issues_all = [a, b]
    widget._issue_dates = {
        a.description_key: [date(2026, 3, 15)],
        b.description_key: [date(2026, 3, 15)],
    }
    counts: list[int] = []
    widget.issues_changed.connect(counts.append)

    widget._drop_issue_keys({a.description_key})
    assert [i.description for i in widget._issues_all] == ["DIESEL"]
    assert counts == [1]


@pytest.mark.asyncio
async def test_all_days_reuses_day_cache(monkeypatch) -> None:
    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 2)
    me = ObjectId()
    collect_calls: list[date] = []

    async def fake_summaries(*_a, **_k):
        return [{"date": d1}, {"date": d2}]

    async def fake_by_date(target, merged=True, cashier_id=None):
        collect_calls.append(target)
        return [_tx(cashier_id=me, description=f"FEE {target}", item="", payee="")]

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.get_open_day_summaries",
        fake_summaries,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        AsyncMock(return_value=[]),
    )

    cache: dict = {}
    snap1 = await collect_day_issues_all_days(
        cashier_id=me, anchor_date=d1, day_cache=cache
    )
    assert snap1.total == 2
    assert set(collect_calls) == {d1, d2}

    collect_calls.clear()
    snap2 = await collect_day_issues_all_days(
        cashier_id=me, anchor_date=d1, day_cache=cache
    )
    assert snap2.total == 2
    assert collect_calls == []  # both days served from cache

    # Live target always re-scans that day (in-memory; no by-date call).
    live_snap = await collect_day_issues_all_days(
        cashier_id=me,
        anchor_date=d1,
        live_target=d1,
        live_transactions=[_tx(cashier_id=me, description="LIVE ONLY", item="", payee="")],
        day_cache=cache,
    )
    assert collect_calls == []  # live path replaces DB load
    assert any(i.description == "LIVE ONLY" for i in live_snap.issues)
    assert d1 in cache
    assert any(i.description == "LIVE ONLY" for i in cache[d1].issues)
