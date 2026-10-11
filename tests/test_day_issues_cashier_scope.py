"""Phase 2: cashier Day Issues scoped to own rows; accountant sees all."""

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
    ]
    scoped = filter_rows_for_cashier_scope(rows, me)
    assert [r.description for r in scoped] == ["MINE"]
    assert len(filter_rows_for_cashier_scope(rows, None)) == 3
