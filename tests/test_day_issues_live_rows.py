"""Phase 1: Day Issues include unsaved (no _id) incomplete rows."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from tahmeed.services.day_issues_service import (
    collect_day_issues,
    merge_transactions_for_day_issues,
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
        _id=ObjectId(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_unsaved_incomplete_row_appears_as_unmapped(monkeypatch) -> None:
    async def no_db(_target, merged=True):
        return []

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        no_db,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    unsaved = _tx(_id=None, description="LATRA FEE", item="", payee="")
    snap = await collect_day_issues(
        date(2026, 3, 15),
        transactions=[unsaved],
    )
    assert snap.total == 1
    assert snap.issues[0].kind == "Unmapped"
    assert snap.issues[0].description == "LATRA FEE"


@pytest.mark.asyncio
async def test_unsaved_complete_row_not_an_issue(monkeypatch) -> None:
    async def no_db(_target, merged=True):
        return []

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        no_db,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    complete = _tx(
        _id=None,
        description="LATRA FEE",
        item="Parking",
        category_name="Parking",
        payee="CITY COUNCIL",
    )
    snap = await collect_day_issues(
        date(2026, 3, 15),
        transactions=[complete],
    )
    assert snap.total == 0


@pytest.mark.asyncio
async def test_extra_unsaved_merges_onto_saved(monkeypatch) -> None:
    saved = _tx(description="DIESEL", item="", payee="")

    async def db_rows(_target, merged=True):
        return [saved]

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        db_rows,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    staged = _tx(_id=None, description="TOLLS", item="", payee="")
    snap = await collect_day_issues(
        date(2026, 3, 15),
        extra_transactions=[staged],
    )
    kinds = {i.description: i.kind for i in snap.issues}
    assert kinds["DIESEL"] == "Unmapped"
    assert kinds["TOLLS"] == "Unmapped"
    assert snap.total == 2


def test_merge_prefers_live_saved_overlay() -> None:
    tid = ObjectId()
    saved = _tx(_id=tid, description="A", item="", payee="")
    live = _tx(_id=tid, description="A", item="Fuel", payee="SHELL", category_name="Fuel")
    staged = _tx(_id=None, description="B", item="", payee="")
    merged = merge_transactions_for_day_issues([saved], [live, staged])
    assert len(merged) == 2
    assert merged[0].item == "Fuel"
    assert merged[0].payee == "SHELL"
    assert merged[1].description == "B"


async def _async(value):
    return value
