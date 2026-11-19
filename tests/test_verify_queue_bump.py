"""Verified delete / pending-edit must emit verify_queue_bumped for instant Verify badge."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_delete_verified_emits_verify_queue_bump(monkeypatch) -> None:
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    _app()
    user = SimpleNamespace(_id="cashier-1", role="cashier")
    reg = DailyRegister.__new__(DailyRegister)
    reg._user = user
    bumped: list[int] = []
    trash: list[int] = []
    drafts: list[int] = []
    reg.verify_queue_bumped = SimpleNamespace(emit=lambda n: bumped.append(int(n)))
    reg.trash_changed = SimpleNamespace(emit=lambda: trash.append(1))
    reg.drafts_changed = SimpleNamespace(emit=lambda: drafts.append(1))
    reg._reload_after_server_row_change = AsyncMock()

    async def fake_request(tx_id, cashier_id=None):
        return "deletion_requested"

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.request_or_delete_transaction",
        fake_request,
    )
    monkeypatch.setattr(
        "tahmeed.ui.async_utils.pause_background_polls",
        lambda _w: __import__("contextlib").nullcontext(),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    await DailyRegister._do_delete_saved_many(reg, ["tx-1", "tx-2"])

    assert bumped == [2]
    assert trash == [1]
    assert drafts == []  # verified delete is not soft-trash
    reg._reload_after_server_row_change.assert_awaited()


@pytest.mark.asyncio
async def test_delete_draft_trashed_emits_drafts_not_verify(monkeypatch) -> None:
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    _app()
    user = SimpleNamespace(_id="cashier-1", role="cashier")
    reg = DailyRegister.__new__(DailyRegister)
    reg._user = user
    bumped: list[int] = []
    trash: list[int] = []
    drafts: list[int] = []
    reg.verify_queue_bumped = SimpleNamespace(emit=lambda n: bumped.append(int(n)))
    reg.trash_changed = SimpleNamespace(emit=lambda: trash.append(1))
    reg.drafts_changed = SimpleNamespace(emit=lambda: drafts.append(1))
    reg._reload_after_server_row_change = AsyncMock()

    async def fake_request(tx_id, cashier_id=None):
        return "trashed"

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.request_or_delete_transaction",
        fake_request,
    )
    monkeypatch.setattr(
        "tahmeed.ui.async_utils.pause_background_polls",
        lambda _w: __import__("contextlib").nullcontext(),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    await DailyRegister._do_delete_saved_many(reg, ["tx-draft"])

    assert bumped == []
    assert drafts == [1]
    assert trash == [1]
