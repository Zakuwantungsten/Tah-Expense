from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from bson import ObjectId

from tahmeed.services.import_session_pack import (
    PACK_FILE_FORMAT,
    apply_mapping_pack,
    builtin_mappings,
    builtin_pack_path,
    find_pack_folder,
    find_pack_source,
    load_builtin_pack,
    read_mapping_pack,
    write_mapping_pack,
)
from tahmeed.services.import_session_parser import StagedRow
from tahmeed.services.import_session_service import (
    MAPPING_SOURCE_MANUAL,
    MAPPING_SOURCE_PACK,
    ImportSession,
)
from tahmeed.services.import_session_validation import (
    DAY_READY,
    SOURCE_PACK,
    SOURCE_TEMP,
    references_from_snapshot,
    validate_session,
)

D1 = date(2026, 10, 1)


def _refs():
    return references_from_snapshot(
        {
            "categories": [
                {"_id": ObjectId(), "name": "DIRECT COSTS:SPARES"},
                {"_id": ObjectId(), "name": "DIRECT COSTS:FUEL"},
            ],
            "payees": [{"name": "SPARE PARTS"}, {"name": "PUMA"}],
            "trucks": [{"number": "T141 EFP"}],
            "trailers": [],
        }
    )


def _pack_dir(tmp_path: Path) -> Path:
    data = tmp_path / "expense automation" / "data"
    data.mkdir(parents=True)
    (data / "description_memory.json").write_text(
        json.dumps(
            {
                "1 GET VALVE": {"payee": "Spare Parts", "account": "Direct Costs:Spares", "source": "manual"},
                "DIESEL": {"payee": "Puma", "account": "Direct Costs:Fuel"},
                "TYRE": {"payee": "Tyre World", "account": "Direct Costs:Tyres"},
                "BROKEN": "not a dict",
            }
        ),
        encoding="utf-8",
    )
    (data / "truck_corrections.json").write_text(
        json.dumps({"corrections": {"141 EFP": "T141 EFP", "168 EGJ": "", "KDL 077": "KDL 077S"}}),
        encoding="utf-8",
    )
    (data / "ignored_descriptions.json").write_text(
        json.dumps({"ignored": ["ADVANCE", "  ", "Bank charges"]}), encoding="utf-8"
    )
    return tmp_path / "expense automation"


def _row(n: int, desc: str, truck: str = "") -> StagedRow:
    return StagedRow(
        row_id=f"f|S|{n}",
        file_key="f",
        source_file="MATUMIZI YA 01-10-26.xlsx",
        excel_row=n,
        batch_date=D1,
        description=desc,
        amount=100.0,
        truck_raw=truck,
    )


def test_read_pack_from_automation_root(tmp_path: Path) -> None:
    root = _pack_dir(tmp_path)
    folder = find_pack_folder(root)
    assert folder == root / "data"
    assert find_pack_folder(tmp_path) is None

    pack = read_mapping_pack(folder)
    assert {m.description for m in pack.mappings} == {"1 GET VALVE", "DIESEL", "TYRE"}
    # The automation's truck corrections and ignored list are never used.
    assert not hasattr(pack, "truck_corrections")
    assert not hasattr(pack, "ignored")
    assert pack.missing_files == [] and pack.errors == []


def test_missing_and_broken_files_are_reported(tmp_path: Path) -> None:
    (tmp_path / "truck_corrections.json").write_text('{"corrections": {}}', encoding="utf-8")
    assert find_pack_folder(tmp_path) is None
    pack = read_mapping_pack(tmp_path)
    assert pack.is_empty and pack.missing_files == ["description_memory.json"]

    (tmp_path / "description_memory.json").write_text("{oops", encoding="utf-8")
    pack = read_mapping_pack(tmp_path)
    assert pack.is_empty
    assert pack.errors and pack.errors[0].startswith("description_memory.json")


def test_apply_replaces_built_in_and_keeps_typed(tmp_path: Path) -> None:
    session = ImportSession.new("t")
    session.rows = [
        _row(2, "1 GET VALVE", "141 EFP"),
        _row(3, "DIESEL", "168 EGJ"),
        _row(4, "BANK CHARGES"),
    ]
    session.set_mapping("DIESEL", "DIRECT COSTS:SPARES", "SPARE PARTS")  # typed by the user
    refs = _refs()
    pack = read_mapping_pack(find_pack_folder(_pack_dir(tmp_path)))

    result = apply_mapping_pack(session, pack, refs)

    get_valve = session.pack_mappings["1 GET VALVE"]
    assert (get_valve.item, get_valve.payee, get_valve.source) == (
        "DIRECT COSTS:SPARES",
        "SPARE PARTS",
        MAPPING_SOURCE_PACK,
    )
    assert session.pack_name == "expense automation"
    assert session.get_mapping("1 GET VALVE") is None
    assert session.get_mapping("DIESEL").source == MAPPING_SOURCE_MANUAL
    assert result.mappings_added == 3
    assert result.mappings_in_session == 2
    assert result.unresolved_items == {"DIRECT COSTS:TYRES": 1}
    assert result.unresolved_payees == {"TYRE WORLD": 1}
    rows = validate_session(session, refs).rows
    assert rows["f|S|2"].mapping_source == SOURCE_PACK
    assert rows["f|S|3"].mapping_source == SOURCE_TEMP
    # Truck values are never mapped from the pack: each row is fixed on its own.
    assert not session.truck_fixes

    # BANK CHARGES is on the automation's ignored list but must still be tracked.
    assert not session.ignored
    validation = validate_session(session, refs)
    assert validation.day(D1).reasons == [
        "1 unmapped description(s)",
        "1 row(s) with a wrong truck",
    ]
    session.set_mapping("BANK CHARGES", "SPARES", "SPARE PARTS")
    session.set_truck_fix("f|S|3", "")
    assert validate_session(session, refs).day(D1).status == DAY_READY

    # Going back to the built-in pack drops the loaded one; typed mappings stay.
    session.use_builtin_mappings()
    assert session.pack_mappings == {}
    assert session.get_mapping("DIESEL") is not None


def test_builtin_mappings_feed_validation() -> None:
    mappings = builtin_mappings()
    assert len(mappings) > 1000
    assert all(m.source == MAPPING_SOURCE_PACK for m in mappings.values())
    assert builtin_mappings() is mappings


def test_pack_file_round_trip_keeps_automation_formats(tmp_path: Path) -> None:
    folder_pack = read_mapping_pack(find_pack_folder(_pack_dir(tmp_path)))
    target = write_mapping_pack(folder_pack, tmp_path / "out" / "x.tmpack.json", name="Office pack")

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["format"] == PACK_FILE_FORMAT
    assert raw["description_memory"]["1 GET VALVE"] == {
        "payee": "Spare Parts",
        "account": "Direct Costs:Spares",
        "source": "manual",
    }
    assert "truck_corrections" not in raw
    assert "ignored_descriptions" not in raw

    assert find_pack_source(target) == target
    file_pack = read_mapping_pack(target)
    assert file_pack.name == "Office pack" and file_pack.created_at is not None
    assert file_pack.mappings == folder_pack.mappings


def test_pack_source_accepts_one_automation_file_and_rejects_other_json(tmp_path: Path) -> None:
    data = find_pack_folder(_pack_dir(tmp_path))
    assert find_pack_source(data / "description_memory.json") == data
    other = tmp_path / "other.json"
    other.write_text('{"hello": 1}', encoding="utf-8")
    pack = read_mapping_pack(find_pack_source(other))
    assert pack.is_empty and "not a mapping pack" in pack.errors[0]


def test_builtin_pack_ships_with_the_app() -> None:
    assert builtin_pack_path().is_file()
    pack = load_builtin_pack()
    assert pack is not None
    assert len(pack.mappings) > 1000
    raw = json.loads(builtin_pack_path().read_text(encoding="utf-8"))
    assert "ignored_descriptions" not in raw
    assert "truck_corrections" not in raw
