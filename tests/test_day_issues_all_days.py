"""Phase 5: This day / All days Day Issues merge."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    collect_day_issues_all_days,
    merge_day_issue_snapshots,
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


def test_merge_day_issue_snapshots_sums_counts() -> None:
    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 2)
    snap1 = DayIssuesSnapshot(
        target_date=d1,
        issues=[
            DayIssue(
                kind="Unmapped",
                description="LATRA",
                description_key="LATRA",
                count=2,
                detail="No payee + item mapping",
            )
        ],
    )
    snap2 = DayIssuesSnapshot(
        target_date=d2,
        issues=[
            DayIssue(
                kind="Unmapped",
                description="LATRA",
                description_key="LATRA",
                count=3,
                detail="No payee + item mapping",
            ),
            DayIssue(
                kind="Bad map",
                description="DIESEL",
                description_key="DIESEL",
                count=1,
                payee="X",
                detail="missing item",
            ),
        ],
    )
    merged = merge_day_issue_snapshots([snap1, snap2], anchor_date=d1)
    assert merged.scope == "all"
    assert merged.total == 2
    by_key = {i.description_key: i for i in merged.issues}
    assert by_key["LATRA"].count == 5
    assert by_key["DIESEL"].kind == "Bad map"
    assert merged.issue_dates["LATRA"] == [d1, d2]


@pytest.mark.asyncio
async def test_collect_all_days_uses_open_summaries(monkeypatch) -> None:
    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 2)
    me = ObjectId()

    async def fake_summaries(*_a, **_k):
        return [{"date": d1}, {"date": d2}]

    async def fake_by_date(target, merged=True, cashier_id=None):
        if target == d1:
            return [_tx(cashier_id=me, description="A FEE", item="", payee="")]
        return [_tx(cashier_id=me, description="B FEE", item="", payee="")]

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
        lambda _descs: _async({}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        lambda active_only=True: _async([]),
    )

    snap = await collect_day_issues_all_days(
        cashier_id=me,
        anchor_date=d1,
    )
    assert snap.scope == "all"
    descs = {i.description for i in snap.issues}
    assert descs == {"A FEE", "B FEE"}
    assert set(snap.days_scanned) == {d1, d2}
