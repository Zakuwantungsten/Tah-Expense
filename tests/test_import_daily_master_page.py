"""Bulk Import Daily page: day batches, issues, trucks, previews (offscreen)."""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from types import SimpleNamespace
from pathlib import Path

import openpyxl
import pytest
from bson import ObjectId

from tahmeed.services import import_session_service as svc
from tahmeed.services.import_session_parser import parse_workbook
from tahmeed.services.import_session_validation import references_from_snapshot

D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture(autouse=True)
def _local_appdata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


def _refs():
    return references_from_snapshot(
        {
            "categories": [
                {"_id": ObjectId(), "name": "DIRECT COSTS:SPARES"},
                {"_id": ObjectId(), "name": "DIRECT COSTS:FUEL"},
            ],
            "payees": [{"name": "SPARE PARTS"}, {"name": "PUMA"}],
            "trucks": [{"number": "T100 ABC"}],
            "trailers": [{"number": "T689 ELK"}],
        }
    )


def _daily(tmp_path: Path, name: str, rows) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Truck No", "TZS"])
    for row in rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    wb.close()
    return path


def _page(qapp, tmp_path: Path):
    from tahmeed.ui.accountant.import_daily_master import ImportDailyMasterWidget

    session = svc.ImportSession.new("Test")
    session.add_parsed(
        parse_workbook(
            _daily(
                tmp_path,
                "MATUMIZI YA 01-10-26.xlsx",
                [
                    (datetime(2026, 10, 1), "Get valve", "T100ABC", 300),
                    (datetime(2026, 10, 1), "Bank charges", None, 5),
                ],
            )
        )
    )
    session.add_parsed(
        parse_workbook(
            _daily(
                tmp_path,
                "MATUMIZI YA 02-10-26.xlsx",
                [(datetime(2026, 10, 2), "Diesel", "141 EFP", 1000)],
            )
        )
    )
    widget = ImportDailyMasterWidget(user=None)
    widget._loaded = True
    widget._refs = _refs()
    widget._start = lambda coro: asyncio.run(coro)
    # Session-only mappings unless a test saves to Description Maps on purpose.
    widget._save_global_box.setChecked(False)
    widget._set_session(session)
    return widget, session


def _day_status(widget, day: date) -> str:
    model = widget._days_model
    for r in range(model.rowCount()):
        if model.row_at(r).key == day:
            return model.row_at(r).cells[-1]
    raise AssertionError(day)


def test_days_issues_and_temporary_mapping_flow(qapp, tmp_path: Path) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session = _page(qapp, tmp_path)
    assert _day_status(widget, D1).startswith("Blocked")
    assert _day_status(widget, D2).startswith("Blocked")
    assert "2 file(s)" in widget._banner.text()

    widget._tabs.setCurrentIndex(page_mod.TAB_ISSUES)
    keys = widget._issues_model.keys()
    assert set(keys) == {"GET VALVE", "BANK CHARGES", "DIESEL"}

    # Fill both descriptions inline, then save (nothing can be ignored).
    for key in ("GET VALVE", "BANK CHARGES"):
        widget._on_issue_cell_edited(key, page_mod._ISSUE_PAYEE_COL, "spare parts")
        widget._on_issue_cell_edited(key, page_mod._ISSUE_ITEM_COL, "spares")
    qapp.processEvents()
    widget._on_save_filled()
    assert session.get_mapping("GET VALVE").item == "DIRECT COSTS:SPARES"
    assert _day_status(widget, D1) == "Ready"
    assert widget._issue_drafts == {}

    # Saved to disk after every change.
    reloaded = svc.load_session(session.session_id)
    assert reloaded.get_mapping("GET VALVE") is not None
    assert reloaded.get_mapping("bank charges") is not None

    # Mappings typed here count whichever mapping mode is chosen.
    combo = widget._mapping_mode
    combo.setCurrentIndex(combo.findData(svc.MODE_MAPS_ONLY))
    assert session.options.mapping_mode == svc.MODE_MAPS_ONLY
    assert "Description Maps only" in widget._banner.text()
    assert _day_status(widget, D1) == "Ready"
    assert svc.load_session(session.session_id).options.mapping_mode == svc.MODE_MAPS_ONLY

    widget._on_tick_ready()
    assert widget.ticked_days() == [D1]
    assert "1 day(s) ticked" in widget._ticked_label.text()


def _row_id(session, description: str) -> str:
    return next(r.row_id for r in session.rows if r.description.upper() == description)


def test_truck_fixed_row_by_row_and_preview(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.accountant import import_daily_master as page_mod

    answers = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(page_mod, "show_question", lambda *a, **k: answers[0])
    widget, session = _page(qapp, tmp_path)
    session.set_mapping("DIESEL", "FUEL", "PUMA")
    widget._changed()
    diesel = _row_id(session, "DIESEL")

    widget._tabs.setCurrentIndex(page_mod.TAB_TRUCKS)
    assert widget._trucks_model.keys() == [diesel]
    cells = widget._trucks_model.row_at(0).cells
    assert cells[0] == "Wrong truck" and cells[6] == "141 EFP" and cells[7] == ""

    # Not one of our trucks: No leaves the cell empty, Yes allows it on this row.
    widget._on_truck_cell_edited(diesel, page_mod._TRUCK_FIX_COL, "T999 ZZZ")
    qapp.processEvents()
    assert diesel not in widget._truck_drafts
    answers[0] = QMessageBox.StandardButton.Yes
    widget._on_truck_cell_edited(diesel, page_mod._TRUCK_FIX_COL, "T999 ZZZ")
    qapp.processEvents()
    assert widget._truck_drafts == {diesel: "T999 ZZZ"}
    assert widget._truck_draft_allowed == {diesel}

    widget._on_truck_cell_edited(diesel, page_mod._TRUCK_FIX_COL, "t100abc")
    assert widget._truck_drafts == {diesel: "T100 ABC"}
    assert not widget._truck_draft_allowed
    assert widget._trucks_model.row_at(0).cells[7] == "T100 ABC"
    assert not session.truck_fixes  # typed, not saved yet
    widget._on_save_truck_fixes()
    assert session.truck_fixes == {diesel: "T100 ABC"}
    assert _day_status(widget, D2) == "Ready"
    assert widget._trucks_model.row_at(0).cells[0] == "Fixed"

    # Undo, then mark the row as having no truck.
    widget._trucks_model.set_checked([diesel])
    widget._on_remove_truck_fix()
    assert not session.truck_fixes
    assert _day_status(widget, D2).startswith("Blocked")
    widget._trucks_model.set_checked([diesel])
    widget._on_set_no_truck()
    assert session.truck_fixes == {diesel: ""}
    assert widget._trucks_model.row_at(0).cells[0] == "No truck"

    session.set_truck_fix(diesel, "T100 ABC")
    widget._changed()
    widget._days_view.select_key(D2)
    widget._tabs.setCurrentIndex(page_mod.TAB_PREVIEW)
    model = widget._preview_model
    assert model.rowCount() == 1
    cells = model.row_at(0).cells
    assert cells[2:6] == ["PUMA", "DIRECT COSTS:FUEL", "DIESEL", "T100 ABC"]
    assert cells[-1] == "OK"


def test_fix_trucks_dialog_fixes_every_row_with_that_value(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.accountant import import_daily_master as page_mod
    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    widget, session = _page(qapp, tmp_path)
    session.add_parsed(
        parse_workbook(
            _daily(
                tmp_path,
                "MATUMIZI YA 03-10-26.xlsx",
                [
                    (datetime(2026, 10, 3), "Oil", "141 EFP", 50),
                    (datetime(2026, 10, 3), "Tyre", "KWACHA 550", 70),
                ],
            )
        )
    )
    widget._changed()
    diesel, oil, tyre = (_row_id(session, d) for d in ("DIESEL", "OIL", "TYRE"))
    widget._tabs.setCurrentIndex(page_mod.TAB_TRUCKS)

    answers = []
    monkeypatch.setattr(dlg_mod, "show_question", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(dlg_mod, "show_info", lambda *a, **k: None)
    seen = {}

    def script(dlg) -> int:
        seen["queue"] = [dlg._queue.item(i).text() for i in range(dlg._queue.count())]
        seen["typed"] = dlg._correct.text()
        dlg._correct.setText("t100abc")
        dlg._apply()  # 141 EFP -> T100 ABC on both rows
        answers.append(QMessageBox.StandardButton.Yes)
        dlg._allow()  # KWACHA 550 kept as it is
        return 1

    monkeypatch.setattr(dlg_mod.TruckFixDialog, "exec", script)
    widget._on_fix_trucks()

    assert seen["queue"] == ["141 EFP  (2 row(s))", "KWACHA 550  (1 row(s))"]
    assert seen["typed"] == ""  # nothing is picked for you
    assert session.truck_fixes == {diesel: "T100 ABC", oil: "T100 ABC", tyre: "KWACHA 550"}
    assert session.truck_allowed == {tyre}
    statuses = {
        widget._trucks_model.row_at(i).key: widget._trucks_model.row_at(i).cells[0]
        for i in range(widget._trucks_model.rowCount())
    }
    assert statuses == {diesel: "Fixed", oil: "Fixed", tyre: "Allowed"}
    assert widget._tabs.badge(page_mod.TAB_TRUCKS) == ""


def test_truck_fix_dialog_skip_accept_empty_and_preview(qapp, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    monkeypatch.setattr(
        dlg_mod, "show_question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(dlg_mod, "show_info", lambda *a, **k: None)
    applied = []
    fleet = {"T641 EQC", "T641 EAF", "T100 ABC"}
    dlg = dlg_mod.TruckFixDialog(
        [
            dlg_mod.WrongTruck("641 EQC", ["a", "b"], ["row a"]),
            dlg_mod.WrongTruck("T137 DRF", ["c"], ["row c"]),
        ],
        choices=sorted(fleet),
        resolve=lambda text: text if text in fleet else None,
        on_apply=lambda wrong, plate, allowed: applied.append((wrong.value, plate, allowed)),
        start_value="T137 DRF",
    )
    assert dlg._queue.currentRow() == 1
    dlg._skip()
    assert dlg._queue.currentRow() == 0
    assert dlg._correct.text() == ""
    assert dlg._preview.item(0).text() == "T641 EQC"  # closest first, not filled in

    dlg._preview.itemClicked.emit(dlg._preview.item(0))
    assert dlg._correct.text() == "T641 EQC"
    dlg._apply()
    dlg._accept_empty()
    assert applied == [("641 EQC", "T641 EQC", False), ("T137 DRF", "", False)]
    assert dlg.applied_count == 2


def test_excel_skipped_and_files_tabs(qapp, tmp_path: Path) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session = _page(qapp, tmp_path)
    widget._tabs.setCurrentIndex(page_mod.TAB_EXCEL)
    assert widget._excel_model.rowCount() == 3
    widget._excel_filter.setCurrentIndex(1)
    assert widget._excel_model.rowCount() == 3

    widget._tabs.setCurrentIndex(page_mod.TAB_FILES)
    assert widget._files_model.rowCount() == 2

    key = next(k for k, f in session.files.items() if f.name.endswith("02-10-26.xlsx"))
    session.remove_file(key)
    widget._changed()
    assert [r.key for r in (widget._days_model.row_at(i) for i in range(widget._days_model.rowCount()))] == [D1]

    widget._tabs.setCurrentIndex(page_mod.TAB_SKIPPED)
    assert widget._skipped_model.rowCount() == 0


def _ready_page(qapp, tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session = _page(qapp, tmp_path)
    session.set_mapping("GET VALVE", "SPARES", "SPARE PARTS")
    session.set_mapping("BANK CHARGES", "SPARES", "SPARE PARTS")
    session.set_mapping("DIESEL", "FUEL", "PUMA")
    session.set_truck_fix(_row_id(session, "DIESEL"), "T100 ABC")
    widget._changed()
    messages = []
    monkeypatch.setattr(page_mod, "show_question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(page_mod, "show_info", lambda _p, _t, text: messages.append(text))
    monkeypatch.setattr(page_mod, "show_warning", lambda _p, _t, text: messages.append(text))
    return widget, session, messages


def test_push_ticked_days_to_master(qapp, tmp_path: Path, monkeypatch) -> None:
    import asyncio

    from tahmeed.services import import_session_push as push_mod
    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session, messages = _ready_page(qapp, tmp_path, monkeypatch)
    assert _day_status(widget, D1) == "Ready" and _day_status(widget, D2) == "Ready"

    async def no_prior(_groups):
        return {}

    committed = []

    async def fake_commit(payloads, **_kw):
        committed.append(payloads)
        return {"inserted": len(payloads), "upload_id": payloads[0]["daily_import_id"]}

    monkeypatch.setattr(page_mod, "find_prior_uploads", no_prior)
    monkeypatch.setattr(push_mod, "commit_daily_to_master", fake_commit)

    widget._on_tick_ready()
    assert widget.ticked_days() == [D1, D2]
    asyncio.run(widget._push([D1, D2]))

    assert [len(p) for p in committed] == [2, 1]
    assert committed[0][0]["description"] == "GET VALVE"
    assert committed[0][0]["daily_import_source"] == "MATUMIZI YA 01-10-26.xlsx"
    assert committed[1][0]["truck_number"] == "T100 ABC"
    assert _day_status(widget, D1) == "Pushed" and _day_status(widget, D2) == "Pushed"
    assert widget.ticked_days() == []
    assert "Pushed 2 of 2 day(s)" in messages[-1]
    assert svc.load_session(session.session_id).is_day_pushed(D2)

    widget._days_model.set_checked([D1])
    widget._on_unmark_pushed()
    assert _day_status(widget, D1) == "Ready"
    assert session.is_day_pushed(D2)


def test_push_skips_days_already_uploaded_elsewhere(qapp, tmp_path: Path, monkeypatch) -> None:
    from tahmeed.services.import_session_push import PriorUpload, build_push_groups

    widget, session, _messages = _ready_page(qapp, tmp_path, monkeypatch)
    groups = build_push_groups(session, widget._validation, [D1, D2])
    prior = {D1: [PriorUpload("classic", "MATUMIZI YA 01-10-26.xlsx", 2, D1, False)]}

    from PySide6.QtWidgets import QMessageBox

    def click_skip(box):
        box.button = next(b for b in box.buttons() if b.text() == "Skip Those Days")
        box.clickedButton = lambda: box.button
        return 0

    monkeypatch.setattr(QMessageBox, "exec", click_skip)
    kept = widget._confirm_push(groups, prior, "")
    assert [g.batch_date for g in kept] == [D2]


def test_load_pack_dialog_builtin_folder_and_export(qapp, tmp_path: Path, monkeypatch) -> None:
    import json

    from tahmeed.services.import_session_pack import load_builtin_pack, read_mapping_pack
    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    monkeypatch.setattr(dlg_mod, "_PACK_FOLDER_KEY", "import_session/test_unused_pack_folder")
    monkeypatch.setattr(dlg_mod, "show_info", lambda *a, **k: None)
    dlg = dlg_mod.LoadPackDialog()
    builtin = load_builtin_pack()
    assert dlg._use_builtin.isChecked()
    assert f"{len(builtin.mappings):,}" in dlg._count.text()
    assert dlg._load_btn.isEnabled() and dlg._export_btn.isEnabled()

    data = tmp_path / "automation" / "data"
    data.mkdir(parents=True)
    (data / "description_memory.json").write_text(
        json.dumps({"TYRE": {"payee": "Puma", "account": "Direct Costs:Fuel"}}), encoding="utf-8"
    )
    dlg._folder.setText(str(tmp_path / "automation"))
    dlg._use_other.setChecked(True)
    assert dlg._pack is not None and len(dlg._pack.mappings) == 1
    assert "<b>1</b>" in dlg._count.text()

    out = tmp_path / "shared"
    monkeypatch.setattr(
        dlg_mod.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out / "pack.json"), "")
    )
    dlg._on_export()
    exported = out / "pack.tmpack.json"
    assert read_mapping_pack(exported).mappings[0].description == "TYRE"
    dlg.close()


def test_banner_reports_description_map_fallback(qapp, tmp_path: Path) -> None:
    from tahmeed.models.description_mapping import DescriptionMapping

    widget, session = _page(qapp, tmp_path)
    cat = widget._refs.items.resolve("SPARES")
    widget._refs.global_mappings["GET VALVE"] = DescriptionMapping(
        description_key="GET VALVE",
        description="GET VALVE",
        category_id=cat.id,
        category_name=cat.name,
        payee="SPARE PARTS",
    )
    widget._revalidate()
    text = widget._banner.text()
    assert "Both — Description Maps first" in text
    assert "built-in 0, Description Maps 1, typed here 0" in text
    assert "Rows mapped: 0 Typed, 0 Built-in, 1 Description Maps" in text

    session.set_pack_mappings(
        {"DIESEL": svc.TempMapping("DIESEL", "DIRECT COSTS:FUEL", "PUMA", "pack")}, "Newer"
    )
    widget._revalidate()
    text = widget._banner.text()
    assert "pack 'Newer' (1) instead of built-in" in text
    assert "Rows mapped: 0 Typed, 1 Built-in, 1 Description Maps" in text

    combo = widget._mapping_mode
    combo.setCurrentIndex(combo.findData(svc.MODE_BUILTIN_ONLY))
    text = widget._banner.text()
    assert "Description Maps 1" not in text
    assert "Rows mapped: 0 Typed, 1 Built-in, 0 Description Maps" in text


def test_finish_session_promotes_ticked_and_discards(qapp, tmp_path: Path, monkeypatch) -> None:
    import asyncio

    from tahmeed.models.description_mapping import DescriptionMapping
    from tahmeed.services import description_mapping_service as dms

    widget, session, messages = _ready_page(qapp, tmp_path, monkeypatch)
    session.set_mapping("TYRE", "NOT AN ITEM", "PUMA")
    candidates = {c.key: c for c in widget._promote_candidates()}
    assert set(candidates) == {"GET VALVE", "BANK CHARGES", "DIESEL"}
    assert candidates["GET VALVE"].ticked and candidates["GET VALVE"].rows == 1

    saved = []

    async def fake_bulk(entries):
        saved.extend(entries)
        return [
            DescriptionMapping(
                description_key=d, description=d, category_id=cid, category_name=name, payee=p
            )
            for d, cid, name, p in entries
        ]

    monkeypatch.setattr(dms, "save_mappings_bulk", fake_bulk)
    old_id = session.session_id
    asyncio.run(widget._finish(["GET VALVE"]))

    assert [(d, name, p) for d, _cid, name, p in saved] == [
        ("GET VALVE", "DIRECT COSTS:SPARES", "SPARE PARTS")
    ]
    assert widget._refs.global_mappings["GET VALVE"].payee == "SPARE PARTS"
    assert widget._session.session_id != old_id
    assert not widget._session.rows
    assert old_id not in {s.session_id for s in svc.list_sessions()}
    assert "1 mapping(s) saved" in messages[-1]


def _open_editor(view):
    from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit

    editors = [w for w in view.findChildren(CompleterLineEdit) if w.isVisible()]
    assert editors, "no cell editor open"
    return editors[-1]


def test_truck_cell_suggests_fleet_plates_and_tab_picks_one(qapp, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session = _page(qapp, tmp_path)
    widget.show()
    widget._tabs.setCurrentIndex(page_mod.TAB_TRUCKS)
    diesel = _row_id(session, "DIESEL")
    entry = widget._trucks_entry
    entry.open(0, entry.columns[0])
    editor = _open_editor(widget._trucks_view)
    QTest.keyClicks(editor, "100abc")  # no T, no space
    qapp.processEvents()
    assert editor._completer.popup().isVisible()
    assert editor._completer.completionModel().index(0, 0).data() == "T100 ABC"
    QTest.keyClick(editor, Qt.Key_Tab)
    qapp.processEvents()
    assert widget._truck_drafts == {diesel: "T100 ABC"}


def test_truck_dialog_field_suggests_and_tab_picks(qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    fleet = ["T641 EQC", "T641 EAF", "T100 ABC", "MC 123 ABC"]
    dlg = dlg_mod.TruckFixDialog(
        [dlg_mod.WrongTruck("641 EQC", ["a"], ["row a"])],
        choices=fleet,
        resolve=lambda text: text if text in fleet else None,
        on_apply=lambda *a: None,
    )
    dlg.show()
    QTest.keyClicks(dlg._correct, "mc123")
    qapp.processEvents()
    assert dlg._correct._completer.completionModel().index(0, 0).data() == "MC 123 ABC"
    QTest.keyClick(dlg._correct, Qt.Key_Tab)
    qapp.processEvents()
    assert dlg._correct.text() == "MC 123 ABC"
    dlg.close()


def test_issue_cells_type_and_tab_like_cashier_day_issues(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView

    from tahmeed.ui.accountant import import_daily_master as page_mod
    from tahmeed.ui.accountant.import_session_grid import next_entry_cell

    assert next_entry_cell(0, 5, "next", [5, 6]) == (0, 6)
    assert next_entry_cell(0, 6, "next", [5, 6]) == (1, 5)
    assert next_entry_cell(1, 5, "prev", [5, 6]) == (0, 6)
    assert next_entry_cell(1, 6, "down", [5, 6]) == (2, 6)

    widget, session = _page(qapp, tmp_path)
    widget.show()
    widget._tabs.setCurrentIndex(page_mod.TAB_ISSUES)
    view, entry = widget._issues_view, widget._issues_entry
    payee_col, item_col = entry.columns
    keys = [view.source_key(r) for r in range(view.model().rowCount())]

    def at() -> tuple:
        cur = view.currentIndex()
        return cur.row(), cur.column(), view.state() == QAbstractItemView.EditingState

    entry.open(0, payee_col)
    _open_editor(view).setText("spare parts")
    QTest.keyClick(_open_editor(view), Qt.Key_Tab)
    qapp.processEvents()
    assert widget._issue_drafts[keys[0]]["payee"] == "SPARE PARTS"
    assert at() == (0, item_col, True)

    _open_editor(view).setText("spares")
    QTest.keyClick(_open_editor(view), Qt.Key_Tab)
    qapp.processEvents()
    assert widget._issue_drafts[keys[0]]["item"] == "DIRECT COSTS:SPARES"
    assert at() == (1, payee_col, True)

    # Enter goes down; passing through an empty cell marks nothing.
    QTest.keyClick(_open_editor(view), Qt.Key_Return)
    qapp.processEvents()
    assert keys[1] not in widget._issue_drafts
    assert at() == (2, payee_col, True)

    # A name not in the list asks first; the next box waits for the answer.
    asked = []
    monkeypatch.setattr(page_mod, "ask_new_payee", lambda _p, name: asked.append(name))
    _open_editor(view).setText("nobody")
    QTest.keyClick(_open_editor(view), Qt.Key_Tab)
    qapp.processEvents()
    qapp.processEvents()
    assert asked == ["NOBODY"]
    assert widget._issue_drafts[keys[2]]["payee"] == ""
    assert at() == (2, item_col, True)
    widget.close()


def test_tab_badges_count_what_is_left(qapp, tmp_path: Path) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod

    widget, session = _page(qapp, tmp_path)
    bar = widget._tabs.tabBar()

    def shown() -> dict:
        tabs = widget._tabs
        return {i: tabs.badge(i) for i in range(tabs.count()) if tabs.badge(i)}

    # 2 blocked days, 3 descriptions to map, 1 wrong truck (141 EFP), 3 problem rows.
    assert shown() == {
        page_mod.TAB_DAYS: "2",
        page_mod.TAB_ISSUES: "3",
        page_mod.TAB_TRUCKS: "1",
        page_mod.TAB_EXCEL: "3",
    }
    assert bar.tabToolTip(page_mod.TAB_ISSUES) == "3 description(s) to map"

    for desc in ("GET VALVE", "BANK CHARGES", "DIESEL"):
        session.set_mapping(desc, "SPARES", "SPARE PARTS")
    session.set_truck_fix(_row_id(session, "DIESEL"), "T100 ABC")
    width_with_bubble = bar.tabSizeHint(page_mod.TAB_ISSUES).width()
    widget._changed()
    assert shown() == {}
    assert bar.tabSizeHint(page_mod.TAB_ISSUES).width() < width_with_bubble
    assert bar.tabToolTip(page_mod.TAB_ISSUES) == ""


def test_old_ignored_descriptions_are_dropped(qapp, tmp_path: Path) -> None:
    from tahmeed.ui.accountant.import_daily_master import ImportDailyMasterWidget

    widget, session = _page(qapp, tmp_path)
    session.ignore(["BANK CHARGES"])
    svc.save_session(session)
    widget2 = ImportDailyMasterWidget(user=None)
    widget2._loaded = True
    widget2._refs = _refs()
    widget2._set_session(svc.load_session(session.session_id))
    assert not widget2._session.ignored
    assert not svc.load_session(session.session_id).ignored
    assert _day_status(widget2, D1) != "Ready"


def _fake_server(monkeypatch):
    from tahmeed.models.description_mapping import DescriptionMapping
    from tahmeed.services import description_mapping_service as dms
    from tahmeed.services import mapping_assignment_service as mas
    from tahmeed.services import payee_service as ps

    calls = {"payees": [], "items": [], "maps": []}

    async def add_payee(name, currency="TZS"):
        calls["payees"].append((name, currency))

    async def create_item(name, fields):
        calls["items"].append(name)
        return SimpleNamespace(_id=ObjectId(), name=name)

    async def save_bulk(entries):
        calls["maps"].extend(entries)
        return [
            DescriptionMapping(
                description_key=d, description=d, category_id=cid, category_name=n, payee=p
            )
            for d, cid, n, p in entries
        ]

    monkeypatch.setattr(ps, "add_payee", add_payee)
    monkeypatch.setattr(mas, "create_item_for_mapping", create_item)
    monkeypatch.setattr(dms, "save_mappings_bulk", save_bulk)
    return calls


def test_typed_new_payee_and_item_are_added_on_the_spot(qapp, tmp_path: Path, monkeypatch) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod

    calls = _fake_server(monkeypatch)
    asked = []

    def new_payee(_parent, name):
        asked.append(("payee", name))
        return ("CRDB BANK", "TZS")

    def new_item(_parent, name):
        asked.append(("item", name))
        return {"name": "FINANCE COSTS:BANK CHARGES", "type": "Expenses"}

    monkeypatch.setattr(page_mod, "ask_new_payee", new_payee)
    monkeypatch.setattr(page_mod, "ask_new_item", new_item)

    widget, session = _page(qapp, tmp_path)
    widget._tabs.setCurrentIndex(page_mod.TAB_ISSUES)
    # Leaving the cell asks right away and adds the name on the server.
    widget._on_issue_cell_edited("BANK CHARGES", page_mod._ISSUE_PAYEE_COL, "crdb")
    qapp.processEvents()
    widget._on_issue_cell_edited("BANK CHARGES", page_mod._ISSUE_ITEM_COL, "bank fees")
    qapp.processEvents()
    assert asked == [("payee", "CRDB"), ("item", "BANK FEES")]
    assert calls["payees"] == [("CRDB BANK", "TZS")]
    assert calls["items"] == ["FINANCE COSTS:BANK CHARGES"]
    assert widget._issue_drafts["BANK CHARGES"] == {
        "payee": "CRDB BANK",
        "item": "FINANCE COSTS:BANK CHARGES",
    }

    # The new names are now in the lists for the next row.
    widget._on_issue_cell_edited("GET VALVE", page_mod._ISSUE_PAYEE_COL, "crdb bank")
    widget._on_issue_cell_edited("GET VALVE", page_mod._ISSUE_ITEM_COL, "finance costs:bank charges")
    qapp.processEvents()
    assert len(asked) == 2
    widget._on_save_filled()

    assert calls["maps"] == []
    mapping = session.get_mapping("BANK CHARGES")
    assert (mapping.item, mapping.payee) == ("FINANCE COSTS:BANK CHARGES", "CRDB BANK")
    assert _day_status(widget, D1) == "Ready"


def test_save_filled_can_save_to_description_maps(qapp, tmp_path: Path, monkeypatch) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod

    calls = _fake_server(monkeypatch)
    messages = []
    monkeypatch.setattr(page_mod, "show_info", lambda _p, _t, text: messages.append(text))

    assert page_mod.ImportDailyMasterWidget(user=None)._save_global_box.isChecked()
    widget, session = _page(qapp, tmp_path)
    session.set_mapping("GET VALVE", "FUEL", "PUMA")
    widget._changed()
    widget._tabs.setCurrentIndex(page_mod.TAB_ISSUES)
    widget._save_global_box.setChecked(True)
    widget._on_issue_cell_edited("BANK CHARGES", page_mod._ISSUE_PAYEE_COL, "spare parts")
    widget._on_issue_cell_edited("BANK CHARGES", page_mod._ISSUE_ITEM_COL, "spares")
    qapp.processEvents()
    widget._on_save_filled()

    assert [(d, n, p) for d, _c, n, p in calls["maps"]] == [
        ("BANK CHARGES", "DIRECT COSTS:SPARES", "SPARE PARTS")
    ]
    assert session.get_mapping("BANK CHARGES") is None
    assert session.typed_to_maps == {"BANK CHARGES"}
    assert widget._refs.global_mappings["BANK CHARGES"].payee == "SPARE PARTS"
    assert _day_status(widget, D1) == "Ready"
    assert "1 mapping(s) saved to Description Maps" in messages[-1]


def _bulk_dialog(dlg_mod, descriptions):
    return dlg_mod.MapDescriptionsDialog(
        descriptions,
        item_names=["DIRECT COSTS:SPARES"],
        payee_names=["PUMA"],
        resolve_item=lambda t: "DIRECT COSTS:SPARES" if "SPARES" in t.upper() else None,
        resolve_payee=lambda t: "PUMA" if t.strip().upper() == "PUMA" else None,
    )


def test_map_dialog_payee_per_description(qapp, monkeypatch) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    seen = {}

    def fake_exec(preview) -> int:
        table = preview._table
        seen["status"] = [table.item(r, dlg_mod._PREVIEW_PAYEE_IS).text() for r in range(3)]
        seen["item_status"] = table.item(0, dlg_mod._PREVIEW_ITEM_IS).text()
        table.item(2, dlg_mod._PREVIEW_PAYEE).setText("mary ann  ltd")
        table.item(3, dlg_mod._PREVIEW_DESC).setCheckState(Qt.Unchecked)
        seen["summary"] = preview._summary.text()
        preview._currency.setCurrentText("USD")
        preview._on_accept()
        return preview.result()

    questions = []
    monkeypatch.setattr(
        dlg_mod, "show_question",
        lambda _p, _t, text, **_k: questions.append(text) or QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(dlg_mod.BulkMapPreviewDialog, "exec", fake_exec)
    dlg = _bulk_dialog(dlg_mod, ["JOHN DOE", "puma", "MARY  ANN", "SKIP ME"])
    dlg._payee_each.setChecked(True)
    assert not dlg._payee.isEnabled()
    dlg._item.setText("spares")
    dlg._on_save()

    assert seen["status"] == ["New", "Existing", "New"]
    assert seen["item_status"] == "Existing"
    assert questions == ["Create 2 new Payee(s) in USD.\nMap 3 description(s).\n\nGo ahead?"]
    assert seen["summary"] == "3 description(s) · 2 new Payee(s) · 0 new Item(s)"
    choice = dlg.choice
    assert choice.rows == [
        ("JOHN DOE", "DIRECT COSTS:SPARES", "JOHN DOE"),
        ("puma", "DIRECT COSTS:SPARES", "PUMA"),
        ("MARY  ANN", "DIRECT COSTS:SPARES", "MARY ANN LTD"),
    ]
    assert choice.descriptions == ["JOHN DOE", "puma", "MARY  ANN"]
    assert choice.new_payees == {"JOHN DOE": "USD", "MARY ANN LTD": "USD"}
    assert choice.new_items == {}


def test_map_dialog_item_per_description_needs_no_forms(qapp, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    def no_form(*_a, **_k):
        raise AssertionError("no Add Item / Add Payee form in bulk mode")

    def fake_exec(preview) -> int:
        preview._on_accept()
        return preview.result()

    answer = {"value": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(dlg_mod, "show_question", lambda *_a, **_k: answer["value"])
    for name in ("run_add_item_dialog", "run_add_payee_dialog", "ask_new_item", "ask_new_payee"):
        monkeypatch.setattr(dlg_mod, name, no_form)
    monkeypatch.setattr(dlg_mod.BulkMapPreviewDialog, "exec", fake_exec)
    dlg = _bulk_dialog(dlg_mod, ["GET VALVE", "spares", "TYRE  REPAIR"])
    dlg._item_each.setChecked(True)
    dlg._payee.setText("new garage")
    dlg._on_save()

    choice = dlg.choice
    assert [r[1] for r in choice.rows] == ["GET VALVE", "DIRECT COSTS:SPARES", "TYRE REPAIR"]
    assert {r[2] for r in choice.rows} == {"NEW GARAGE"}
    assert choice.new_items == {
        "GET VALVE": {**dlg_mod.BULK_ITEM_FIELDS, "name": "GET VALVE"},
        "TYRE REPAIR": {**dlg_mod.BULK_ITEM_FIELDS, "name": "TYRE REPAIR"},
    }
    assert not any(dlg_mod.BULK_ITEM_FIELDS.values())
    assert choice.new_payees == {"NEW GARAGE": "TZS"}

    # Answering No to the confirmation creates nothing.
    answer["value"] = QMessageBox.StandardButton.No
    dlg = _bulk_dialog(dlg_mod, ["GET VALVE"])
    dlg._item_each.setChecked(True)
    dlg._payee.setText("puma")
    dlg._on_save()
    assert dlg.choice is None


def test_bulk_map_creates_names_and_maps_each(qapp, tmp_path: Path, monkeypatch) -> None:
    from tahmeed.ui.accountant import import_daily_master as page_mod
    from tahmeed.ui.dialogs.import_session_dialogs import MappingChoice

    calls = _fake_server(monkeypatch)
    messages = []
    monkeypatch.setattr(page_mod, "show_info", lambda _p, _t, text: messages.append(text))

    class FakeDialog:
        Accepted = 1

        def __init__(self, descriptions, **_kwargs) -> None:
            self.choice = MappingChoice(
                list(descriptions), item="", payee="PUMA",
                rows=[(d, d.upper(), "PUMA") for d in descriptions],
                new_items={d.upper(): {"name": d.upper()} for d in descriptions},
            )

        def exec(self) -> int:
            return 1

    monkeypatch.setattr(page_mod, "MapDescriptionsDialog", FakeDialog)
    widget, session = _page(qapp, tmp_path)
    widget._tabs.setCurrentIndex(page_mod.TAB_ISSUES)
    issues = [widget._issues_by_key[k] for k in ("GET VALVE", "BANK CHARGES")]
    widget._open_map_dialog(issues)

    assert sorted(calls["items"]) == ["BANK CHARGES", "GET VALVE"]
    assert session.get_mapping("GET VALVE").item == "GET VALVE"
    assert session.get_mapping("BANK CHARGES").payee == "PUMA"
    assert _day_status(widget, D1) == "Ready"
    assert "2 new Item(s)" in messages[-1]


def test_map_dialog_new_item_and_payee(qapp, monkeypatch) -> None:
    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    monkeypatch.setattr(
        dlg_mod, "run_add_item_dialog", lambda _p, prefill_name="": {"name": "NEW:ITEM"}
    )
    monkeypatch.setattr(dlg_mod, "ask_new_payee", lambda _p, name: (name.upper(), "USD"))

    def make():
        return dlg_mod.MapDescriptionsDialog(
            ["GET VALVE", "BANK CHARGES"],
            item_names=["DIRECT COSTS:SPARES"],
            payee_names=["PUMA"],
            resolve_item=lambda t: "DIRECT COSTS:SPARES" if "SPARES" in t.upper() else None,
            resolve_payee=lambda t: "PUMA" if t.upper() == "PUMA" else None,
            save_global=True,
        )

    dlg = make()
    dlg._on_new_item()
    dlg._payee.setText("New Garage")
    dlg._on_save()
    choice = dlg.choice
    assert choice.item == "NEW:ITEM" and choice.new_item == {"name": "NEW:ITEM"}
    assert choice.payee == "NEW GARAGE" and choice.new_payee == ("NEW GARAGE", "USD")
    assert choice.save_global and choice.descriptions == ["GET VALVE", "BANK CHARGES"]

    # Declining "add it?" keeps the dialog open with no choice.
    monkeypatch.setattr(dlg_mod, "ask_new_item", lambda _p, name: None)
    dlg = make()
    dlg._payee.setText("puma")
    dlg._item.setText("unknown")
    dlg._on_save()
    assert dlg.choice is None

    # Existing names need no new entries.
    dlg = make()
    dlg._payee.setText("puma")
    dlg._item.setText("spares")
    dlg._on_save()
    assert dlg.choice.new_item is None and dlg.choice.new_payee is None
    assert (dlg.choice.item, dlg.choice.payee) == ("DIRECT COSTS:SPARES", "PUMA")
