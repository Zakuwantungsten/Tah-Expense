from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from tahmeed.services import import_session_push as push
from tahmeed.services import import_session_service as svc
from tahmeed.services.import_session_parser import StagedRow
from tahmeed.services.import_session_validation import (
    DAY_PUSHED,
    references_from_snapshot,
    validate_session,
)

D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)
SPARES_ID = ObjectId()


@pytest.fixture(autouse=True)
def _local_appdata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


def _refs():
    return references_from_snapshot(
        {
            "categories": [{"_id": SPARES_ID, "name": "DIRECT COSTS:SPARES"}],
            "payees": [{"name": "SPARE PARTS"}],
            "trucks": [{"number": "T100 ABC"}],
            "trailers": [],
        }
    )


def _row(n: int, desc: str, *, day: date = D1, source: str = "MATUMIZI YA 01-10-26.xlsx",
         truck: str = "", serial=None, excel_date=None, amount: float = 100.0) -> StagedRow:
    return StagedRow(
        row_id=f"{source}|S|{n}",
        file_key=source,
        source_file=source,
        excel_row=n,
        batch_date=day,
        description=desc,
        amount=amount,
        truck_raw=truck,
        serial=serial,
        excel_date=excel_date,
        notes="refund to float",
    )


def _session(*rows: StagedRow) -> svc.ImportSession:
    session = svc.ImportSession.new("push")
    session.rows = list(rows)
    session.set_mapping("GET VALVE", "SPARES", "SPARE PARTS")
    return session


def test_upload_name_always_carries_the_reconciled_date() -> None:
    assert push.upload_name_for("MATUMIZI YA 01-10-26.xlsx", D1) == "MATUMIZI YA 01-10-26.xlsx"
    assert push.upload_name_for("MATUMIZI YA 01/10/26", D1) == "MATUMIZI YA 01/10/26"
    assert push.upload_name_for("MASTER 2026.xlsx", D1) == "MATUMIZI YA 01-10-26 (MASTER 2026.xlsx)"
    assert push.upload_id_for("MATUMIZI YA 01-10-26.xlsx", D1) == push.upload_id_for(
        "matumizi ya 01-10-26", D1
    )
    assert push.upload_id_for("MATUMIZI YA 01-10-26", D1) != push.upload_id_for(
        "MATUMIZI YA 01-10-26", D2
    )


def test_groups_per_day_and_source_file_with_payloads() -> None:
    session = _session(
        _row(2, "GET VALVE", truck="t100abc", serial=7, excel_date=date(2026, 9, 30)),
        _row(3, "GET VALVE"),
        _row(4, "ADVANCE"),
        _row(5, "GET VALVE", source="MATUMIZI YA 01-10-26 (2).xlsx"),
        _row(2, "GET VALVE", day=D2, source="MATUMIZI YA 02-10-26.xlsx"),
    )
    session.ignore(["ADVANCE"])
    validation = validate_session(session, _refs())

    groups = push.build_push_groups(session, validation, [D2, D1])
    assert [(g.batch_date, g.upload_name, len(g.payloads)) for g in groups] == [
        (D1, "MATUMIZI YA 01-10-26.xlsx", 2),
        (D1, "MATUMIZI YA 01-10-26 (2).xlsx", 1),
        (D2, "MATUMIZI YA 02-10-26.xlsx", 1),
    ]
    first = groups[0].payloads[0]
    assert first["date"] == datetime(2026, 9, 30)
    assert first["import_primary_date"] == datetime(2026, 10, 1)
    assert first["item"] == first["category_name"] == "DIRECT COSTS:SPARES"
    assert first["category_id"] == SPARES_ID
    assert first["payee"] == "SPARE PARTS"
    assert first["truck_number"] == "T100 ABC"
    assert first["serial"] == 7
    assert first["ref_float"] == "REFUND TO FLOAT"
    assert first["daily_import_id"] == groups[0].upload_id
    assert first["daily_import_source"] == "MATUMIZI YA 01-10-26.xlsx"
    second = groups[0].payloads[1]
    assert second["date"] == datetime(2026, 10, 1)
    assert second["serial"] == "XL3"


def test_rows_master_files_elsewhere_are_reported() -> None:
    session = _session(
        _row(2, "GET VALVE", excel_date=date(2026, 9, 30)),
        _row(3, "GET VALVE", excel_date=date(2025, 10, 1)),
        _row(4, "GET VALVE", excel_date=date(2025, 10, 1)),
        _row(5, "GET VALVE", excel_date=date(2026, 10, 20)),
        _row(6, "GET VALVE"),
    )
    refs = references_from_snapshot(
        {
            "categories": [
                {"_id": SPARES_ID, "name": "DIRECT COSTS:SPARES", "is_supplier": True},
                {"_id": ObjectId(), "name": "OLD SUPPLIER", "is_supplier": True, "active": False},
            ],
            "payees": [{"name": "SPARE PARTS"}],
        }
    )
    assert refs.suppliers == {"DIRECT COSTS:SPARES"}
    groups = push.build_push_groups(session, validate_session(session, refs), [D1])

    assert [(o.batch_date, o.row_date, o.rows) for o in push.date_outliers(groups)] == [
        (D1, date(2025, 10, 1), 2),
        (D1, date(2026, 9, 30), 1),
    ]
    assert push.row_date_range(groups) == (date(2025, 10, 1), date(2026, 10, 20))
    assert push.supplier_rows(groups, refs.is_supplier) == {"DIRECT COSTS:SPARES": 5}
    assert push.supplier_rows(groups, _refs().is_supplier) == {}
    assert push.row_date_range([]) is None


def test_blocked_day_cannot_be_pushed() -> None:
    session = _session(_row(2, "GET VALVE"), _row(2, "UNKNOWN", day=D2, source="MATUMIZI YA 02-10-26.xlsx"))
    validation = validate_session(session, _refs())
    with pytest.raises(push.DayNotReady) as exc:
        push.build_push_groups(session, validation, [D1, D2])
    assert exc.value.days == [D2]


def test_push_marks_days_and_stops_at_first_failure(monkeypatch) -> None:
    session = _session(
        _row(2, "GET VALVE"),
        _row(2, "GET VALVE", day=D2, source="MATUMIZI YA 02-10-26.xlsx"),
    )
    svc.save_session(session)
    validation = validate_session(session, _refs())
    groups = push.build_push_groups(session, validation, [D1, D2])
    calls = []

    async def fake_commit(payloads, **_kw):
        calls.append(payloads)
        if len(calls) == 2:
            raise RuntimeError("server down")
        return {"inserted": len(payloads), "duplicates_skipped": 0, "upload_id": payloads[0]["daily_import_id"]}

    monkeypatch.setattr(push, "commit_daily_to_master", fake_commit)
    progress = []
    summary = asyncio.run(
        push.push_groups(session, groups, on_progress=lambda i, t, g: progress.append((i, t)))
    )
    assert progress == [(1, 2), (2, 2)]
    assert [d.batch_date for d in summary.days] == [D1]
    assert summary.inserted == 1
    assert summary.failed_day == D2 and summary.error == "server down"
    assert session.is_day_pushed(D1) and not session.is_day_pushed(D2)
    assert session.pushed_days[D1].upload_ids == [groups[0].upload_id]
    assert svc.load_session(session.session_id).is_day_pushed(D1)
    assert validate_session(session, _refs()).day(D1).status == DAY_PUSHED


def test_push_cancel_between_days(monkeypatch) -> None:
    session = _session(_row(2, "GET VALVE"))
    groups = push.build_push_groups(session, validate_session(session, _refs()), [D1])

    async def fake_commit(payloads, **_kw):  # pragma: no cover - must not run
        raise AssertionError("should not commit")

    monkeypatch.setattr(push, "commit_daily_to_master", fake_commit)
    summary = asyncio.run(push.push_groups(session, groups, should_cancel=lambda: True))
    assert summary.cancelled and not summary.days


def test_find_prior_uploads_flags_same_batch_name_and_day(monkeypatch) -> None:
    session = _session(
        _row(2, "GET VALVE"),
        _row(2, "GET VALVE", day=D2, source="MATUMIZI YA 02-10-26.xlsx"),
    )
    groups = push.build_push_groups(session, validate_session(session, _refs()), [D1, D2])
    ours = groups[0].upload_id
    items = [
        {"_id": ours, "source_filename": "MATUMIZI YA 01-10-26.xlsx", "count": 1,
         "primary_date": datetime(2026, 10, 1)},
        {"_id": "classic-1", "source_filename": "matumizi ya 02-10-26.XLSX", "count": 40,
         "primary_date": datetime(2026, 10, 2)},
        {"_id": "classic-2", "source_filename": "other.xlsx", "count": 3,
         "primary_date": "2026-10-01T00:00:00"},
        {"_id": "unrelated", "source_filename": "x.xlsx", "count": 3,
         "primary_date": datetime(2026, 8, 1)},
    ]
    pages = []

    async def fake_page(limit=100, skip=0):
        pages.append(skip)
        chunk = items[skip: skip + 2]
        return SimpleNamespace(items=chunk, total=len(items))

    monkeypatch.setattr(push, "list_daily_uploads_page", fake_page)
    found = asyncio.run(push.find_prior_uploads(groups, page_size=2))
    assert pages == [0, 2]
    assert [(p.upload_id, p.same_batch) for p in found[D1]] == [(ours, True), ("classic-2", False)]
    assert [(p.upload_id, p.same_batch) for p in found[D2]] == [("classic-1", False)]
