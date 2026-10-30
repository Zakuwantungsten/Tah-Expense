"""Local import sessions: staged rows, temporary mappings, persistence."""

from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from tahmeed.services import import_session_service as svc
from tahmeed.services.import_session_parser import parse_workbook


@pytest.fixture(autouse=True)
def _local_appdata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


def _book(path: Path, rows) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    wb.save(path)
    wb.close()
    return path


def _daily(tmp_path: Path, name: str, *descs: str) -> Path:
    rows = [("Date", "Description", "Truck No", "TZS")]
    rows += [(datetime(2026, 10, 1), d, "T100 ABC", 1000) for d in descs]
    return _book(tmp_path / name, rows)


def test_add_parsed_replaces_same_file_and_groups_days(tmp_path: Path) -> None:
    session = svc.ImportSession.new("October backlog")
    a = _daily(tmp_path, "MATUMIZI YA 01-10-26.xlsx", "Diesel", "Tolls")
    b = _daily(tmp_path, "MATUMIZI YA 02-10-26.xlsx", "Parking")
    session.add_parsed(parse_workbook(a))
    session.add_parsed(parse_workbook(b))
    session.add_parsed(parse_workbook(a))
    days = session.days()
    assert list(days) == [date(2026, 10, 1), date(2026, 10, 2)]
    assert [r.description for r in days[date(2026, 10, 1)]] == ["DIESEL", "TOLLS"]
    assert len(session.files) == 2

    assert session.remove_file(parse_workbook(a).file.file_key)
    assert list(session.days()) == [date(2026, 10, 2)]


def test_temp_mappings_ignored_and_row_truck_fixes() -> None:
    session = svc.ImportSession.new()
    session.set_mappings(["get valve -", "Tyre  repair"], "direct costs:spares", "spare parts")
    mapping = session.get_mapping("GET VALVE")
    assert mapping is not None
    assert (mapping.item, mapping.payee) == ("DIRECT COSTS:SPARES", "SPARE PARTS")
    assert session.remove_mapping("get valve")
    assert session.get_mapping("GET VALVE") is None

    assert session.ignore(["Bank charges", "BANK CHARGES"]) == 1
    assert session.is_ignored("bank  charges")
    assert session.unignore(["bank charges"]) == 1

    session.set_truck_fix("f|S|3", "t141  efp")
    session.set_truck_fix("f|S|4", "")
    assert session.truck_fix_for("f|S|3") == "T141 EFP"
    assert session.truck_fix_for("f|S|4") == ""
    assert session.truck_fix_for("f|S|5") is None
    assert session.remove_truck_fix("f|S|3")
    assert not session.remove_truck_fix("f|S|3")

    session.set_truck_fix("f|S|6", "kwacha 550", allowed=True)
    session.set_truck_fix("f|S|7", "", allowed=True)  # no truck is never "allowed"
    assert session.truck_fix_allowed("f|S|6") and not session.truck_fix_allowed("f|S|7")
    restored = svc.ImportSession.from_dict(session.to_dict())
    assert restored.truck_allowed == {"f|S|6"}
    restored.set_truck_fix("f|S|6", "T141 EFP")
    assert not restored.truck_fix_allowed("f|S|6")
    session.remove_truck_fix("f|S|6")
    assert not session.truck_allowed


def test_round_trip_save_load_list_delete(tmp_path: Path) -> None:
    session = svc.ImportSession.new("Backlog")
    session.add_parsed(parse_workbook(_daily(tmp_path, "MATUMIZI YA 01-10-26.xlsx", "Diesel")))
    session.set_mapping("Diesel", "FUEL", "PUMA")
    session.ignore(["Bank charges"])
    session.set_truck_fix(session.rows[0].row_id, "T100 ABC")
    session.options.prefer_temp_mappings = False
    session.mark_day_pushed(date(2026, 10, 1), row_count=1, upload_ids=["u1"])
    svc.save_session(session)

    loaded = svc.load_session(session.session_id)
    assert loaded.name == "Backlog"
    assert loaded.rows == session.rows
    assert loaded.files == session.files
    assert loaded.temp_mappings == session.temp_mappings
    assert loaded.ignored == session.ignored
    assert loaded.truck_fixes == session.truck_fixes == {session.rows[0].row_id: "T100 ABC"}
    assert loaded.options.prefer_temp_mappings is False
    assert loaded.pushed_days[date(2026, 10, 1)].upload_ids == ["u1"]

    summaries = svc.list_sessions()
    assert [(s.session_id, s.row_count, s.day_count, s.pushed_day_count) for s in summaries] == [
        (session.session_id, 1, 1, 1)
    ]
    assert svc.delete_session(session.session_id)
    assert svc.list_sessions() == []


def test_list_sessions_skips_corrupt_files() -> None:
    root = svc.sessions_root()
    root.mkdir(parents=True)
    (root / "broken.json").write_text("{not json", encoding="utf-8")
    assert svc.list_sessions() == []


@pytest.mark.asyncio
async def test_add_files_reports_bad_files_and_keeps_good(tmp_path: Path) -> None:
    good = _daily(tmp_path, "MATUMIZI YA 01-10-26.xlsx", "Diesel")
    bad = _book(tmp_path / "bad.xlsx", [("nothing", "here")])
    seen = []
    session = svc.ImportSession.new()
    errors = await svc.add_files_to_session(
        session, [good, bad], on_file=lambda i, n, name: seen.append((i, n, name))
    )
    assert [Path(e.path).name for e in errors] == ["bad.xlsx"]
    assert len(session.rows) == 1
    assert seen == [(1, 2, good.name), (2, 2, "bad.xlsx")]
    assert svc.session_path(session.session_id).exists()
