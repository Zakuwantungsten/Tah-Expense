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


def test_compact_empty_editable_rows_packs_leftover_after_clear_in_place(register):
    """Partial-save / outbox ACK holes must not hide the leftover bad-truck row."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TRUCK, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._table.setItem(1, COL_DESC, QTableWidgetItem("GOOD ROW"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("1,000.00"))
    register._table.setItem(2, COL_DESC, QTableWidgetItem("BAD TRUCK ROW"))
    register._table.setItem(2, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(2, COL_TRUCK, QTableWidgetItem("T999 ZZZ"))
    register._table.setItem(2, COL_TZS, QTableWidgetItem("2,000.00"))
    register._ensure_row_client_id(2)

    # Simulate successful clear-in-place for the good row (hole at index 1).
    register._mark_new_row_saved(1, flush=False)
    assert not register._row_has_data(1)
    assert register._table.item(2, COL_DESC).text() == "BAD TRUCK ROW"

    packed = register._compact_empty_editable_rows()
    assert packed is True
    assert register._table.item(1, COL_DESC).text() == "BAD TRUCK ROW"
    assert register._table.item(1, COL_TRUCK).text() == "T999 ZZZ"
    assert not register._row_has_data(2)


def test_outbox_ack_style_insert_remaps_open_truck_issue_row(register):
    """Allow after ACK insert must still hit the leftover, not a cleared hole."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TRUCK, COL_TZS
    from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue
    from PySide6.QtWidgets import QTableWidgetItem

    register._table.setItem(1, COL_DESC, QTableWidgetItem("GOOD"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("1,000.00"))
    register._table.setItem(2, COL_DESC, QTableWidgetItem("LEFTOVER"))
    register._table.setItem(2, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(2, COL_TRUCK, QTableWidgetItem("T999 ZZZ"))
    register._table.setItem(2, COL_TZS, QTableWidgetItem("2,000.00"))

    issue = TruckIssue(row=2, original="T999 ZZZ", kind="not_in_registry")
    register._pending_truck_issues[2] = issue

    class _OpenDlg:
        def __init__(self, issues):
            self.issues = []
            self._held_issues = []
            self._rows = [SimpleNamespace(issue=issues[0])]

        def isVisible(self):
            return True

    register._open_truck_dialog = _OpenDlg([issue])

    # Clear good row, then insert a saved row at the top (outbox ACK pattern).
    register._mark_new_row_saved(1, flush=False)
    tx_id = ObjectId()
    acked = SimpleNamespace(
        _id=tx_id,
        cashier_id=register._user._id,
        register_status="draft",
        verified=False,
        description="GOOD",
        truck_number="",
        item="PARKING",
        date=None,
        money_parts=lambda: (1000.0, 0.0),
        receipt_status="pending",
        ownership="",
        approver="",
        payee="",
        cheque="",
        memo="",
        amount=1000.0,
        amount_usd=0.0,
    )
    # Minimal fill path used by append — stub to avoid full row paint deps.
    register._fill_saved_row = lambda at, tx: None  # type: ignore[method-assign]
    register._append_saved_transactions([acked])
    register._compact_empty_editable_rows()

    # Leftover packed under saved block; dialog issue.row remapped with it.
    assert issue.row == register._saved_count
    assert register._table.item(issue.row, COL_DESC).text() == "LEFTOVER"

    issue.corrected = "T999 ZZZ"
    issue.allow_anyway = True
    register._on_truck_issue_resolved_live(issue)

    assert register._allowed_truck_for_row(issue.row) == "T999 ZZZ"
    status, value = register._resolve_truck_text("T999 ZZZ", row=issue.row)
    assert status == "ok"
    assert value == "T999 ZZZ"


def test_allow_anyway_survives_in_pending_meta_without_index_map(register):
    """Build/resolve must honor Allow stored on pending meta alone."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TRUCK, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._table.setItem(1, COL_DESC, QTableWidgetItem("PARTNER"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TRUCK, QTableWidgetItem("T128 EFP"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("1,000.00"))
    register._pending_row_meta[1] = {
        "client_row_id": register._ensure_row_client_id(1),
        "allow_anyway": "T128 EFP",
    }
    register._truck_allow_anyway.clear()

    assert register._allowed_truck_for_row(1) == "T128 EFP"
    status, value = register._resolve_truck_text("T128 EFP", row=1)
    assert status == "ok"
    assert value == "T128 EFP"
    tx = register._build_transaction_from_row(1)
    assert tx is not None
    assert tx.truck_number == "T128 EFP"
