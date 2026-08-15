"""UploadBusy cancel overlay used during daily Excel import."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from tahmeed.ui.widgets.upload_busy import UploadBusy, UploadCancelled


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_upload_busy_default_has_no_cancel() -> None:
    _app()
    with UploadBusy(None, "Reading Excel file…") as busy:
        buttons = [b for b in busy._dlg.findChildren(QPushButton) if b.isVisible()]
        assert not busy.should_cancel()
        assert all("Cancel" not in (b.text() or "").replace("&", "") for b in buttons)


def test_upload_busy_cancellable_sets_flag() -> None:
    _app()
    with UploadBusy(None, "Reading Excel file…", cancellable=True) as busy:
        labels = [
            (b.text() or "").replace("&", "")
            for b in busy._dlg.findChildren(QPushButton)
        ]
        assert any(t == "Cancel" for t in labels)
        assert not busy.should_cancel()
        busy._on_canceled()
        assert busy.should_cancel()
        assert "Cancelling" in busy._dlg.labelText()
        with pytest.raises(UploadCancelled):
            busy.raise_if_cancelled()


@pytest.mark.asyncio
async def test_await_or_cancel_aborts_when_cancelled() -> None:
    _app()

    async def slow() -> str:
        await asyncio.sleep(2)
        return "done"

    with UploadBusy(None, "Loading items…", cancellable=True) as busy:
        busy._on_canceled()
        with pytest.raises(UploadCancelled):
            await busy.await_or_cancel(slow())


@pytest.mark.asyncio
async def test_await_or_cancel_aborts_mid_wait() -> None:
    _app()

    async def slow() -> str:
        await asyncio.sleep(2)
        return "done"

    with UploadBusy(None, "Loading items…", cancellable=True) as busy:

        async def cancel_soon() -> None:
            await asyncio.sleep(0.05)
            busy._on_canceled()

        asyncio.create_task(cancel_soon())
        with pytest.raises(UploadCancelled):
            await busy.await_or_cancel(slow())
