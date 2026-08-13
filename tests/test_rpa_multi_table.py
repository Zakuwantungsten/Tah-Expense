"""RPA workbooks may stack more than one station table on the same sheet."""

from __future__ import annotations

from pathlib import Path

from tahmeed.ui.accountant.reconciliation import (
    _parse_recon_sheet_rows,
    parse_recon_workbook,
)

_HEADER = (
    "SR. NO", "SM REF NO", "PRN", "REG NO", "ASYCUDA AMOUNT", "T1 NUMBER",
    "T1 DATE", "IMPORTER", "EXPORTER", "TRUCK NO & TRAILER NO",
    "DESCRIPTION OF SHIPMENT", "CHARGE",
)


def _row(*values) -> tuple:
    return tuple(values)


def test_parse_skips_second_header_and_tags_stations() -> None:
    rows = [
        _row("NAKONDE - TAHMEED RPA"),
        _row("SCHEDULE FROM 01.06.2026 - 15.06.2026"),
        _HEADER,
        _row(1, "SM0771TM", "9726486610756", "S93919", 1966.8, 101081,
             None, "IMPORTER A", "EXPORTER A", "T717EET/T599EEN", "SULPHUR", 70),
        _row(8610),
        _row("KASUMBALESA - TAHMEED RPA"),
        _row("SCHEDULE FROM 01.06.2026 - 15.06.2026"),
        _HEADER,
        _row(1, "SM760TH", "9726780394456", "S29083", 1966.8, 27470,
             None, "IMPORTER B", "EXPORTER B", "T539EKT/T706ELK", "COPPER BLISTER", 70),
    ]
    entries = _parse_recon_sheet_rows(rows, "rpa_schedule")
    assert len(entries) == 2
    assert {e.station for e in entries} == {"nakonde", "kasumbalesa"}
    nakonde = next(e for e in entries if e.station == "nakonde")
    kasumba = next(e for e in entries if e.station == "kasumbalesa")
    assert nakonde.prn_number == "9726486610756"
    assert nakonde.truck_and_trailer == "T717EET/T599EEN"
    assert kasumba.prn_number == "9726780394456"
    assert kasumba.truck_and_trailer == "T539EKT/T706ELK"
    assert all("truck" not in e.truck_and_trailer.lower() for e in entries)


_BONDS_HEADER = (
    "SR. NO", "SM REF NO", "PRN NUMBER", "ENTRY REG NO", "T1 NO", "T1 DATE",
    "IMPORTER", "CONSIGNMENT", "TRUCK AND TRAILER DETAILS", "CHARGE",
)


def test_parse_bonds_skips_rits_banner_and_reads_period() -> None:
    rows = [
        _row("NAKONDE - TAHMEED RITS"),
        _row(),
        _BONDS_HEADER,
        _row("SCHEDULE FROM 01.05.2026 - 15.05.2026"),
        _row(1, "SM0289TR", "9726685441645", "S78711", "80276", None,
             "IMPORTER A", "SULPHUR", "T124DYY/T966DYY", 70),
        _row(4, "SM0662TM", "9726580270735", "S78723", "79348", None,
             "IMPORTER B", "SULPHUR", "T724CPQ/T631DZX/T632DZX", 70),
    ]
    entries = _parse_recon_sheet_rows(rows, "bonds", "Nakonde")
    assert len(entries) == 2
    assert all(e.station == "nakonde" for e in entries)
    assert entries[0].schedule_period == "SCHEDULE FROM 01.05.2026 - 15.05.2026"
    assert entries[0].truck_and_trailer == "T124DYY/T966DYY"
    assert entries[1].truck_and_trailer == "T724CPQ/T631DZX/T632DZX"
    assert all("truck" not in e.truck_and_trailer.lower() for e in entries)
    assert all("tahmeed" not in (e.prn_number or "").lower() for e in entries)


def test_parse_bonds_stacked_tables_tag_stations() -> None:
    rows = [
        _row("NAKONDE - TAHMEED RITS"),
        _BONDS_HEADER,
        _row("SCHEDULE FROM 01.05.2026 - 15.05.2026"),
        _row(1, "SM1", "111", "S1", "1", None, "A", "SULPHUR", "T124DYY/T966DYY", 70),
        _row("KASUMBALESA - TAHMEED RITS"),
        _BONDS_HEADER,
        _row("SCHEDULE FROM 01.05.2026 - 15.05.2026"),
        _row(1, "SM2", "222", "S2", "2", None, "B", "COPPER", "T770DWK/T295DWL", 70),
    ]
    entries = _parse_recon_sheet_rows(rows, "bonds", "Sheet1")
    assert len(entries) == 2
    assert {e.station for e in entries} == {"nakonde", "kasumbalesa"}


def test_parse_real_bonds_workbook() -> None:
    path = Path(__file__).resolve().parents[1] / "SM BURHANI - BONDS 15.05.2026.xlsx"
    if not path.exists():
        return
    entries = parse_recon_workbook(str(path), "bonds")
    stations = {e.station for e in entries}
    assert "nakonde" in stations
    assert "kasumbalesa" in stations
    assert "sakania" in stations
    trucks = {e.truck_and_trailer.lower() for e in entries}
    assert not any("truck" in t and "trailer" in t for t in trucks)
    assert any("/" in e.truck_and_trailer and e.truck_and_trailer.count("/") == 2
               for e in entries)
    assert all(e.schedule_period for e in entries)


def test_parse_real_rpa_stacked_file() -> None:
    path = Path(__file__).resolve().parents[1] / "rpa format maker and ukali.xlsx"
    if not path.exists():
        return
    entries = parse_recon_workbook(str(path), "rpa_schedule")
    trucks = {e.truck_and_trailer.lower() for e in entries}
    assert not any("truck no" in t for t in trucks)
    stations = {e.station for e in entries}
    assert "nakonde" in stations
    assert "kasumbalesa" in stations
    assert len(entries) > 2
