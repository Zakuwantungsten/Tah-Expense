"""Cashier Day Issues: own rows + every parked row; accountant sees all."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from tahmeed.services.day_issues_service import (
    collect_day_issues,
    filter_rows_for_cashier_scope,
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


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_cashier_scope_hides_other_cashiers_rows(monkeypatch) -> None:
    me = ObjectId()
    other = ObjectId()
    mine = _tx(cashier_id=me, description="MINE FEE", item="", payee="")
    theirs = _tx(cashier_id=other, description="THEIR FEE", item="", payee="")

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        lambda *a, **k: _async([mine, theirs]),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    snap = await collect_day_issues(
        date(2026, 3, 15),
        transactions=[mine, theirs],
        cashier_id=me,
    )
    assert snap.total == 1
    assert snap.issues[0].description == "MINE FEE"


@pytest.mark.asyncio
async def test_accountant_scope_includes_all_cashiers(monkeypatch) -> None:
    a = ObjectId()
    b = ObjectId()
    rows = [
        _tx(cashier_id=a, description="A FEE", item="", payee=""),
        _tx(cashier_id=b, description="B FEE", item="", payee=""),
    ]
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    snap = await collect_day_issues(
        date(2026, 3, 15),
        transactions=rows,
        cashier_id=None,
    )
    descs = {i.description for i in snap.issues}
    assert descs == {"A FEE", "B FEE"}


def test_filter_rows_for_cashier_scope() -> None:
    me = ObjectId()
    other = ObjectId()
    rows = [
        _tx(cashier_id=me, description="MINE"),
        _tx(cashier_id=other, description="THEIRS"),
        _tx(cashier_id=None, description="ORPHAN"),
        _tx(cashier_id=other, description="THEIR PARKED", register_status="parked"),
    ]
    scoped = filter_rows_for_cashier_scope(rows, me)
    assert [r.description for r in scoped] == ["MINE", "THEIR PARKED"]
    assert len(filter_rows_for_cashier_scope(rows, None)) == 4


@pytest.mark.asyncio
async def test_cashier_day_issues_include_other_cashiers_parked_rows(monkeypatch) -> None:
    me = ObjectId()
    other = ObjectId()
    rows = [
        _tx(cashier_id=me, description="MINE FEE", register_status="draft"),
        _tx(cashier_id=other, description="THEIR FEE", register_status="draft"),
        _tx(cashier_id=other, description="THEIR PARKED FEE", register_status="parked"),
    ]
    fetches: list[dict] = []

    async def fake_by_date(*_a, **kwargs):
        fetches.append(kwargs)
        return rows

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date", fake_by_date
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    snap = await collect_day_issues(date(2026, 3, 15), cashier_id=me)

    assert {i.description for i in snap.issues} == {"MINE FEE", "THEIR PARKED FEE"}
    assert fetches == [{"merged": False, "cashier_id": me, "include_parked": True}]


@pytest.mark.asyncio
async def test_all_days_lists_days_with_shared_parked_rows(monkeypatch) -> None:
    from tahmeed.services.day_issues_service import collect_day_issues_all_days

    me = ObjectId()
    summary_calls: list[dict] = []

    async def fake_summaries(*_a, **kwargs):
        summary_calls.append(kwargs)
        return []

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.get_open_day_summaries", fake_summaries
    )

    await collect_day_issues_all_days(cashier_id=me, anchor_date=date(2026, 3, 15))

    assert summary_calls and summary_calls[0]["include_parked"] is True
