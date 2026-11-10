"""Local Skipped bucket: materialize rollback + Move to Skipped extract."""

from __future__ import annotations

import sys
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication, QTableWidgetItem


@pytest.fixture
def qapp():
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
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, EDIT_BG

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
    reg._current_date = date(2026, 8, 8)
    reg._merged_mode = False
    reg._edit_mode = False
    reg._table.setRowCount(8)
    reg._saved_count = 0
    reg._saved_ids = {}
    reg._saved_txs = {}
    reg._init_editable_rows(0, 8)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    reg._put = _put
    return reg


def _stage_import_like_rows(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
        register._put(1, COL_DESC, "OIL")
        register._put(1, COL_TZS, "2,000.00")
        register._put(1, COL_TRUCK, "BAD TRUCK")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[1] = {
        "parked": True,
        "daily_import_source": "matumizi.xlsx",
    }


def _descs(register) -> list[str]:
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    out = []
    for r in range(register._table.rowCount()):
        it = register._table.item(r, COL_DESC)
        text = (it.text() if it else "").strip()
        if text:
            out.append(text)
    return out


def test_materialize_rollback_leaves_my_clean_and_parked_intact(register, monkeypatch):
    """Dup cancel / abort must drop Save copies without clearing Parked."""
    from tahmeed.services.register_draft_service import entry_client_row_id

    _stage_import_like_rows(register)
    monkeypatch.setattr(
        register, "_persist_parked_after_dialog", lambda *a, **k: None
    )
    register._after_truck_dialog_parked()
    assert len(register._local_parked) == 1
    parked_id = entry_client_row_id(register._local_parked[0])
    assert parked_id
    assert "OIL" not in _descs(register)
    assert "DIESEL" in _descs(register)

    materialized = register._materialize_local_parked_for_save()
    assert materialized
    assert "OIL" in _descs(register)
    assert len(register._local_parked) == 1
    assert entry_client_row_id(register._local_parked[0]) == parked_id

    register._rollback_materialized_parked_rows(materialized)
    assert "OIL" not in _descs(register)
    assert "DIESEL" in _descs(register)
    assert len(register._local_parked) == 1
    assert entry_client_row_id(register._local_parked[0]) == parked_id


def test_move_rows_to_skipped_extracts_from_my(register):
    """Deferred duplicates leave My and land in the Skipped draft bucket."""
    from tahmeed.services.register_draft_service import (
        draft_skipped_rows,
        entry_client_row_id,
        hydrate_pending_meta,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
        register._put(1, COL_DESC, "TOLLS")
        register._put(1, COL_TZS, "500.00")
        register._put(1, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)

    moved = register._move_rows_to_skipped([1], skip_reason="duplicate_deferred")
    assert moved == 1
    assert "TOLLS" not in _descs(register)
    assert "DIESEL" in _descs(register)
    assert len(register._local_skipped) == 1
    entry = register._local_skipped[0]
    assert entry_client_row_id(entry)
    meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
    assert meta.get("skipped") is True
    assert meta.get("skip_reason") == "duplicate_deferred"

    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    skipped = draft_skipped_rows(draft)
    assert len(skipped) == 1
    assert "TOLLS" in (skipped[0].get("cells") or {}).values()


def test_skipped_tab_keep_alive_avoids_full_draft_reload(register, monkeypatch):
    """My↔Skipped and Skipped↔Parked must keep My mounted and not re-parse draft JSON."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
    finally:
        register._table.blockSignals(False)
    register._local_skipped = [{
        "client_row_id": "skip-1",
        "cells": {"1": "TOLLS", "5": "500"},
        "pending_meta": {"skipped": True, "client_row_id": "skip-1"},
    }]

    loads = {"n": 0}

    def _boom(*_a, **_k):
        loads["n"] += 1
        raise AssertionError("tab switch must not reload the full draft file")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.load_register_draft",
        _boom,
    )
    flushes = {"n": 0}
    monkeypatch.setattr(
        register,
        "_flush_local_draft",
        lambda *a, **k: flushes.__setitem__("n", flushes["n"] + 1),
    )
    scheduled = {"n": 0}
    monkeypatch.setattr(
        register,
        "_schedule_draft_autosave",
        lambda *a, **k: scheduled.__setitem__("n", scheduled["n"] + 1),
    )

    register.set_view_mode("skipped")
    assert register._view_mode == "skipped"
    assert register._my_session is not None
    assert loads["n"] == 0
    assert flushes["n"] == 0

    register.set_view_mode("parked")
    assert register._view_mode == "parked"
    assert register._my_session is not None
    assert loads["n"] == 0

    register.set_view_mode("skipped")
    assert register._view_mode == "skipped"
    assert register._my_session is not None

    register.set_view_mode("my")
    assert register._view_mode == "my"
    assert register._my_session is None
    assert loads["n"] == 0
    assert flushes["n"] == 0
    assert scheduled["n"] >= 1


@pytest.mark.asyncio
async def test_absorb_empty_description_skip_builds_skipped_rows(monkeypatch):
    from tahmeed.services.daily_import_service import (
        REASON_EMPTY_DESCRIPTION,
        DailyImportPreview,
        DailyImportProblemRow,
        absorb_import_problems,
    )

    preview = DailyImportPreview(
        source_filename="x.xlsx",
        source_path="x.xlsx",
        rows=[],
        primary_date=date(2026, 8, 20),
        problem_rows=[
            DailyImportProblemRow(
                excel_row=2,
                reason=REASON_EMPTY_DESCRIPTION,
                description="",
                amount=2500.0,
                date=datetime(2026, 8, 20),
                skipped=True,
            ),
        ],
    )

    async def _no_maps(_descs):
        return {}

    monkeypatch.setattr(
        "tahmeed.services.daily_import_service.get_mappings_for_descriptions",
        _no_maps,
    )
    await absorb_import_problems(preview)

    assert preview.rows == []
    assert preview.problem_rows == []
    assert preview.skipped_blank == 1
    assert len(preview.skipped_rows) == 1
    meta = preview.skipped_rows[0].get("pending_meta") or {}
    assert meta.get("skipped") is True
    assert meta.get("skip_reason") == "empty_description_user_skip"
