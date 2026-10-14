"""DailyRegister local draft capture / restore."""

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
    from tahmeed.ui.cashier.register_delegates import (
        COL_DESC,
        COL_ITEM,
        COL_TZS,
        EDIT_BG,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush
    from PySide6.QtWidgets import QTableWidgetItem

    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")

    user = MagicMock()
    user._id = ObjectId()
    user.username = "aisha"
    reg = DailyRegister(user, [])
    # Phase 4 outbox timers / connectivity must not fire during unit tests.
    monkeypatch.setattr(reg, "_schedule_outbox_drain", lambda *a, **k: None)
    reg._outbox_drain_timer.stop()
    try:
        from tahmeed.signals import app_signals

        app_signals.connectivity_changed.disconnect(reg._on_connectivity_changed)
    except Exception:
        pass
    reg._current_date = date(2026, 8, 8)
    reg._merged_mode = False
    reg._edit_mode = True

    from datetime import datetime

    tx0 = ObjectId()
    reg._table.setRowCount(6)
    reg._saved_count = 1
    reg._saved_ids = {0: tx0}
    reg._saved_txs = {
        0: SimpleNamespace(
            _id=tx0,
            cashier_id=user._id,
            register_status="draft",
            verified=False,
            description="DIESEL",
            truck_number="",
            item="FUEL",
            date=datetime(2026, 8, 8),
            money_parts=lambda: (1000.0, 0.0),
        ),
    }

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    _put(0, COL_ITEM, "FUEL")
    _put(0, COL_DESC, "DIESEL")
    _put(0, COL_TZS, "1,000.00")
    reg._init_editable_rows(1, 6)
    reg._tx0 = tx0
    return reg


def test_flush_and_restore_new_and_dirty_rows(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._dirty_rows.add(0)
    register._table.item(0, COL_DESC).setText("DIESEL EDITED")
    register._table.setItem(1, COL_DESC, QTableWidgetItem("NEW PARKING"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("5,000.00"))

    register._flush_local_draft()

    # Simulate reload wipe
    register._dirty_rows.clear()
    register._table.item(0, COL_DESC).setText("DIESEL")
    for row in range(register._saved_count, register._table.rowCount()):
        for col in (COL_DESC, COL_ITEM, COL_TZS):
            it = register._table.item(row, col)
            if it is not None:
                it.setText("")

    restored = register._restore_local_draft()
    assert restored == (1, 1)
    assert 0 in register._dirty_rows
    assert register._table.item(0, COL_DESC).text() == "DIESEL EDITED"
    assert register._table.item(1, COL_DESC).text() == "NEW PARKING"
    assert register._table.item(1, COL_TZS).text() == "5,000.00"


def test_clear_local_draft_removes_file(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import load_register_draft

    register._table.setItem(1, COL_DESC, QTableWidgetItem("TEMP"))
    register._flush_local_draft()
    assert load_register_draft(
        register._user._id, register._current_date, merged=False
    ) is not None

    register._clear_local_draft()
    assert load_register_draft(
        register._user._id, register._current_date, merged=False
    ) is None


def test_restore_skips_new_rows_that_match_saved_fingerprint(register):
    """Partial-save leftovers: already-inserted rows must not reappear on restore."""
    from tahmeed.ui.cashier.register_delegates import (
        COL_DESC,
        COL_ITEM,
        COL_TRUCK,
        COL_TZS,
    )
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import load_register_draft

    # Fixture saved row 0 is already DIESEL / FUEL / 1,000. Draft still has
    # that content plus one true leftover (simulates post-partial-save crash).
    register._dirty_rows.clear()
    register._table.blockSignals(True)
    try:
        register._table.setItem(1, COL_DESC, QTableWidgetItem("DIESEL"))
        register._table.setItem(1, COL_ITEM, QTableWidgetItem("FUEL"))
        register._table.setItem(1, COL_TZS, QTableWidgetItem("1,000.00"))
        register._table.setItem(2, COL_DESC, QTableWidgetItem("NEW PARKING"))
        register._table.setItem(2, COL_ITEM, QTableWidgetItem("PARKING"))
        register._table.setItem(2, COL_TRUCK, QTableWidgetItem("T100"))
        register._table.setItem(2, COL_TZS, QTableWidgetItem("5,000.00"))
    finally:
        register._table.blockSignals(False)
    register._flush_local_draft()

    # Wipe editable zone (as after _populate from server).
    register._table.blockSignals(True)
    try:
        for row in range(register._saved_count, register._table.rowCount()):
            for col in (COL_DESC, COL_ITEM, COL_TRUCK, COL_TZS):
                it = register._table.item(row, col)
                if it is not None:
                    it.setText("")
    finally:
        register._table.blockSignals(False)
    register._dirty_rows.clear()

    restored = register._restore_local_draft()
    assert restored == (0, 1)
    assert register._table.item(1, COL_DESC).text() == "NEW PARKING"
    assert register._table.item(1, COL_TZS).text() == "5,000.00"
    # No second DIESEL copy in the editable zone.
    for row in range(register._saved_count + 1, register._table.rowCount()):
        it = register._table.item(row, COL_DESC)
        if it and it.text().strip():
            assert it.text() != "DIESEL"

    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1


def test_mark_new_row_saved_clears_row_and_prunes_draft(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import load_register_draft

    register._table.setItem(1, COL_DESC, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("5,000.00"))
    register._table.setItem(2, COL_DESC, QTableWidgetItem("STILL UNSAVED"))
    register._table.setItem(2, COL_TZS, QTableWidgetItem("100.00"))
    register._flush_local_draft()
    assert len(
        load_register_draft(
            register._user._id, register._current_date, merged=False
        )["new_rows"]
    ) == 2

    register._mark_new_row_saved(1)

    assert not register._row_has_data(1)
    assert register._table.item(2, COL_DESC).text() == "STILL UNSAVED"
    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1
    from tahmeed.services.register_draft_service import cells_from_json
    from tahmeed.ui.cashier.register_delegates import COL_DESC as DESC

    cells = cells_from_json(draft["new_rows"][0]["cells"])
    assert cells[DESC] == "STILL UNSAVED"


def test_activate_stamps_client_row_id(register):
    rid = register._ensure_row_client_id(1)
    assert rid
    assert register._pending_row_meta[1]["client_row_id"] == rid
    # Stable across calls.
    assert register._ensure_row_client_id(1) == rid


def test_capture_draft_includes_client_row_id(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import entry_client_row_id

    register._table.blockSignals(True)
    try:
        register._table.setItem(1, COL_DESC, QTableWidgetItem("PARKING"))
    finally:
        register._table.blockSignals(False)
    payload = register._capture_local_draft()
    assert len(payload["new_rows"]) == 1
    rid = entry_client_row_id(payload["new_rows"][0])
    assert rid
    assert payload["schema_version"] >= 2
    assert payload["new_rows"][0]["pending_meta"]["client_row_id"] == rid


def test_restore_skips_by_client_row_id(register):
    """Even if cells differ slightly, matching client_row_id must not re-append."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        save_register_draft,
    )

    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    register._saved_txs[0].client_row_id = rid
    register._dirty_rows.clear()

    save_register_draft(
        build_draft_payload(
            user_id=register._user._id,
            username="aisha",
            register_date=register._current_date,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[{
                "client_row_id": rid,
                "cells": {"3": "CHANGED DESC", "7": "9,999.00"},
                "pending_meta": {"client_row_id": rid},
            }],
        )
    )

    register._table.blockSignals(True)
    try:
        for row in range(register._saved_count, register._table.rowCount()):
            for col in (COL_DESC, COL_TZS):
                it = register._table.item(row, col)
                if it is not None:
                    it.setText("")
    finally:
        register._table.blockSignals(False)

    restored = register._restore_local_draft()
    assert restored is None
    for row in range(register._saved_count, register._table.rowCount()):
        it = register._table.item(row, COL_DESC)
        assert not it or not it.text().strip()


def test_footer_excludes_editable_copy_of_saved_row(register):
    from datetime import datetime

    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._saved_txs[0] = SimpleNamespace(
        _id=register._tx0,
        cashier_id=register._user._id,
        register_status="draft",
        verified=False,
        description="DIESEL",
        truck_number="",
        item="FUEL",
        date=datetime(2026, 8, 8),
        money_parts=lambda: (1000.0, 0.0),
    )
    register._table.setItem(0, COL_DESC, QTableWidgetItem("DIESEL"))
    register._table.setItem(0, COL_ITEM, QTableWidgetItem("FUEL"))
    register._table.setItem(0, COL_TZS, QTableWidgetItem("1,000.00"))
    # Editable duplicate of the saved row — must not double the footer.
    register._table.setItem(1, COL_DESC, QTableWidgetItem("DIESEL"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("FUEL"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("1,000.00"))
    # Distinct leftover.
    register._table.setItem(2, COL_DESC, QTableWidgetItem("PARKING"))
    register._table.setItem(2, COL_TZS, QTableWidgetItem("5,000.00"))

    register._update_footer()
    assert "2 entries" in register._totals_label.text()
    assert "6,000" in register._totals_label.text()
