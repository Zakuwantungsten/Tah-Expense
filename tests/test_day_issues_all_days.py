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


@pytest.mark.asyncio
async def test_collect_all_days_loads_in_parallel_and_maps_once(monkeypatch) -> None:
    import asyncio

    days = [date(2026, 3, d) for d in range(1, 9)]
    in_flight = 0
    peak = 0
    map_calls: list = []

    async def fake_by_date(target, merged=True, cashier_id=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [_tx(description=f"FEE {target.day}", item="", payee="")]

    async def fake_maps(descs):
        map_calls.append(sorted(descs))
        return {}

    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date", fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions", fake_maps,
    )

    ticks: list = []
    snap = await collect_day_issues_all_days(
        anchor_date=days[0], only_days=days, payee_names=[],
        progress=lambda done, total: ticks.append((done, total)),
    )
    assert ticks[0] == (0, 8) and ticks[-1] == (8, 8)
    assert [d for d, _ in ticks] == list(range(9))
    assert 1 < peak <= 4
    assert len(map_calls) == 1
    assert len(map_calls[0]) == 8
    assert snap.total == 8


@pytest.mark.asyncio
async def test_collect_all_days_only_days_skips_open_day_lookup(monkeypatch) -> None:
    d1 = date(2026, 3, 1)
    d3 = date(2026, 3, 3)
    fetched: list = []

    async def no_summaries(*_a, **_k):
        raise AssertionError("open-day lookup must be skipped")

    async def fake_by_date(target, merged=True, cashier_id=None):
        fetched.append(target)
        return [_tx(description=f"FEE {target.day}", item="", payee="")]

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.get_open_day_summaries", no_summaries,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date", fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        lambda _descs: _async({}),
    )

    snap = await collect_day_issues_all_days(
        anchor_date=d1, only_days=[d3, d1], payee_names=[],
    )
    assert sorted(fetched) == [d1, d3]
    assert set(snap.days_scanned) == {d1, d3}
    assert {i.description for i in snap.issues} == {"FEE 1", "FEE 3"}
