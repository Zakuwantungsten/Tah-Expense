"""Trash restore alerts + Parked landing for saved/submitted/parked rows."""

from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _tx(*, status: str = "draft", desc: str = "OIL"):
    from tahmeed.models.transaction import Transaction

    return Transaction(
        _id=ObjectId(),
        date=datetime(2026, 8, 8),
        description=desc,
        truck_number="BAD TRUCK",
        amount=1000.0,
        register_status=status,
        cashier_id=ObjectId(),
        verified=False,
        trashed=True,
    )


@pytest.mark.asyncio
async def test_restore_own_alerts_cycle_and_opens_parked(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.trash.schedule_coro", lambda *_a, **_k: None
    )
    from tahmeed.ui.cashier.trash import CashierTrashWidget

    user = MagicMock()
    user._id = ObjectId()
    widget = CashierTrashWidget(user=user)
    parked = _tx(status="parked", desc="PARKED OIL")
    draft = _tx(status="draft", desc="DRAFT DIESEL")
    widget._transactions = [parked, draft]

    infos: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: infos.append(a),
    )

    async def fake_restore(ids):
        assert set(ids) == {parked._id, draft._id}
        return 2

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.bulk_restore_own_trash",
        fake_restore,
    )

    opened: list[tuple] = []
    widget.open_register_after_restore.connect(
        lambda d, mode: opened.append((d, mode))
    )

    async def _noop_reload():
        return None

    monkeypatch.setattr(widget, "_reload", _noop_reload)

    await widget._do_restore_own([parked._id, draft._id], [parked, draft])

    assert infos
    assert "finish the cycle" in infos[0][1].lower()
    assert opened == [(parked.date.date(), "parked")]


@pytest.mark.asyncio
async def test_restore_own_draft_only_opens_my(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.trash.schedule_coro", lambda *_a, **_k: None
    )
    from tahmeed.ui.cashier.trash import CashierTrashWidget

    user = MagicMock()
    user._id = ObjectId()
    widget = CashierTrashWidget(user=user)
    draft = _tx(status="draft", desc="DRAFT DIESEL")
    widget._transactions = [draft]

    infos: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: infos.append(a),
    )

    async def fake_restore(ids):
        return 1

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.bulk_restore_own_trash",
        fake_restore,
    )
    opened: list[tuple] = []
    widget.open_register_after_restore.connect(
        lambda d, mode: opened.append((d, mode))
    )

    async def _noop_reload():
        return None

    monkeypatch.setattr(widget, "_reload", _noop_reload)

    await widget._do_restore_own([draft._id], [draft])

    assert infos and "finish the cycle" in infos[0][1].lower()
    assert opened == [(draft.date.date(), "my")]
