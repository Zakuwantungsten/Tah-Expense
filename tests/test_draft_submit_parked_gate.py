"""Drafts Submit must block while parked truck rows remain for the day."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.ui.cashier.draft_submit import submit_draft_day_with_gates


@pytest.mark.asyncio
async def test_submit_blocked_when_server_parked_remain(monkeypatch):
    day = date(2026, 8, 8)
    parked = Transaction(
        _id=ObjectId(),
        date=datetime(2026, 8, 8),
        description="OIL",
        truck_number="BAD",
        amount=100.0,
        register_status="parked",
        cashier_id=ObjectId(),
    )

    async def fake_by_date(*_a, **_k):
        return [parked]

    warned: list = []

    monkeypatch.setattr(
        "tahmeed.ui.cashier.draft_submit.get_transactions_by_date",
        fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.draft_submit.submit_day_for_verify",
        AsyncMock(side_effect=AssertionError("must not submit")),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda *a, **k: warned.append(a),
    )

    parent = MagicMock()
    result = await submit_draft_day_with_gates(parent, day, user_id=ObjectId())
    assert result is None
    assert warned
    assert "Parked trucks block submit" in warned[0][1]


@pytest.mark.asyncio
async def test_submit_blocked_when_local_parked_remain(monkeypatch, tmp_path):
    from tahmeed.services import register_draft_service as drafts

    day = date(2026, 8, 8)
    uid = ObjectId()
    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")
    drafts.save_register_draft(
        drafts.build_draft_payload(
            user_id=uid,
            username="aisha",
            register_date=day,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[],
            parked_rows=[
                {
                    "client_row_id": "p1",
                    "cells": {},
                    "pending_meta": {"parked": True},
                }
            ],
        )
    )

    async def fake_by_date(*_a, **_k):
        return []

    warned: list = []
    monkeypatch.setattr(
        "tahmeed.ui.cashier.draft_submit.get_transactions_by_date",
        fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.draft_submit.submit_day_for_verify",
        AsyncMock(side_effect=AssertionError("must not submit")),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda *a, **k: warned.append(a),
    )

    result = await submit_draft_day_with_gates(
        MagicMock(), day, cashier_id=uid, user_id=uid
    )
    assert result is None
    assert warned
