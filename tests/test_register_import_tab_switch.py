"""Import → Park → tab switch must stay on the keep-alive path and keep Allow."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import ObjectId


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def register(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.services import register_draft_service as drafts
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")

    user = MagicMock()
    user._id = ObjectId()
    user.username = "aisha"
    reg = DailyRegister(user, [])
    monkeypatch.setattr(reg, "_schedule_outbox_drain", lambda *a, **k: None)
    reg._outbox_drain_timer.stop()
    try:
        from tahmeed.signals import app_signals

        app_signals.connectivity_changed.disconnect(reg._on_connectivity_changed)
    except Exception:
        pass
    reg._current_date = date(2026, 10, 28)
    reg._merged_mode = False
    reg._parked_mode = False
    reg._view_mode = "my"
    reg._saved_count = 0
    reg._table.setRowCount(6)
    reg._init_editable_rows(0, 6)
    return reg


class _FakeBusy:
    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    async def tick(self):
        return None

    def update(self, *_a, **_k):
        return None


def _stub_import(register, monkeypatch, loads: list):
    from PySide6.QtWidgets import QMessageBox

    async def no_rows(*_a, **_k):
        return None

    async def fake_load(d):
        loads.append(d)

    monkeypatch.setattr("tahmeed.ui.widgets.upload_busy.UploadBusy", _FakeBusy)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(register, "has_unsaved_work", lambda *a, **k: False)
    monkeypatch.setattr(register, "_wipe_unsaved_editable_rows", lambda: None)
    monkeypatch.setattr(register, "_load_staged_import_rows_async", no_rows)
    monkeypatch.setattr(register, "_load_date", fake_load)


@pytest.mark.asyncio
async def test_same_day_import_keeps_fast_tab_switch(register, monkeypatch):
    day = register._current_date
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        day, merged=False, upload_id=""
    )
    register._day_tx_stale = False
    loads: list = []
    _stub_import(register, monkeypatch, loads)

    await register.apply_daily_import_preview(
        SimpleNamespace(primary_date=day, source_filename="day.xlsx", rows=[])
    )

    assert loads == []
    assert register._day_tx_stale is False
    assert register._day_tx_cache is not None
    # My → Parked keeps My alive instead of a server reload + draft rebuild.
    assert register._flip_to_parked_keep_my() is True
    assert register._my_session is not None


def test_allowed_truck_survives_draft_rebuild(register):
    from PySide6.QtWidgets import QTableWidgetItem

    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._fleet_numbers = {"T100 AAA"}
    register._table.setItem(0, COL_DESC, QTableWidgetItem("PETROL"))
    register._table.setItem(0, COL_TZS, QTableWidgetItem("210,000.00"))
    register._table.setItem(0, COL_TRUCK, QTableWidgetItem("T789 EMU"))
    register._truck_allow_anyway[0] = "T789 EMU"

    register._flush_local_draft()

    # Slow Parked → My path: grid repainted from the server, maps reset.
    for col in (COL_DESC, COL_TZS, COL_TRUCK):
        register._table.item(0, col).setText("")
    register._truck_allow_anyway.clear()
    register._pending_truck_issues.clear()
    register._suppress_truck_dialog = True

    assert register._restore_local_draft() == (0, 1)

    row = next(
        r for r in range(register._table.rowCount())
        if register._cell_text(r, COL_TRUCK) == "T789 EMU"
    )
    assert register._truck_allow_anyway.get(row) == "T789 EMU"
    # No "Correct truck numbers" prompt for a truck the user already allowed.
    assert register._pending_truck_issues == {}


def test_park_then_tab_round_trip_saves_nothing_and_keeps_allow(
    register, monkeypatch
):
    from PySide6.QtWidgets import QTableWidgetItem

    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    def boom(*_a, **_k):
        raise AssertionError("parking / tab switches must not save or reload")

    register._fleet_numbers = {"T100 AAA"}
    register._table.blockSignals(True)
    try:
        for row, (desc, truck) in enumerate(
            [("PETROL", "T789 EMU"), ("OIL", "BAD TRUCK")]
        ):
            register._table.setItem(row, COL_DESC, QTableWidgetItem(desc))
            register._table.setItem(row, COL_TZS, QTableWidgetItem("1,000.00"))
            register._table.setItem(row, COL_TRUCK, QTableWidgetItem(truck))
    finally:
        register._table.blockSignals(False)
    register._truck_allow_anyway[0] = "T789 EMU"
    register._pending_row_meta[1] = {"parked": True}
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register._day_tx_stale = False
    monkeypatch.setattr(register, "_do_save", boom)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", boom)
    monkeypatch.setattr(register, "_load_date", boom)
    monkeypatch.setattr(register, "_restore_local_draft", boom)

    register._after_truck_dialog_parked()
    register.set_view_mode("parked")
    register.set_view_mode("my")

    assert register._table is register._my_table
    assert register._cell_text(0, COL_DESC) == "PETROL"
    assert register._truck_allow_anyway.get(0) == "T789 EMU"
    assert register._pending_truck_issues == {}
    assert len(register._local_parked) == 1
    assert register.has_unsaved_work()


@pytest.mark.asyncio
async def test_other_day_import_still_drops_day_cache(register, monkeypatch):
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register._day_tx_stale = False
    loads: list = []
    _stub_import(register, monkeypatch, loads)
    other = date(2026, 10, 27)

    await register.apply_daily_import_preview(
        SimpleNamespace(primary_date=other, source_filename="day.xlsx", rows=[])
    )

    assert loads == [other]
    assert register._day_tx_cache is None
