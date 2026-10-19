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
    from PySide6.QtCore import Qt

    _app()
    with UploadBusy(None, "Reading Excel file…") as busy:
        buttons = [b for b in busy._dlg.findChildren(QPushButton) if b.isVisible()]
        assert not busy.should_cancel()
        assert all("Cancel" not in (b.text() or "").replace("&", "") for b in buttons)
        # Determinate bar starts visible at 0% so the dialog is never blank.
        assert not busy._dlg._panel._bar.isHidden()
        assert busy._dlg._panel.percent_text() == "0%"
        # Opaque panel so Windows QDialog does not paint a blank body.
        assert busy._dlg._panel._opaque is True
        assert not busy._dlg._panel.testAttribute(Qt.WA_TranslucentBackground)
        assert not busy._dlg._panel._horse.pixmap().isNull()


def test_upload_busy_shows_percent_progress() -> None:
    _app()
    with UploadBusy(
        None, "Importing…", title="Importing Daily Register"
    ) as busy:
        assert busy._dlg.windowTitle() == "Importing Daily Register"
        busy.update("Loading rows…", value=25, maximum=100)
        assert busy._dlg._panel.percent_text() == "25%"
        assert not busy._dlg._panel._bar.isHidden()
        busy.update("Almost done…", value=100, maximum=100)
        assert busy._dlg._panel.percent_text() == "100%"


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
async def test_update_inside_task_repaints_instead_of_processing_events() -> None:
    """Inside an asyncio Task processEvents is skipped; update() must still
    push the label / percentage to the dialog and request a synchronous paint
    so the import dialog is never a blank window."""
    _app()
    calls = {"process": 0, "repaint": 0}

    import tahmeed.ui.async_utils as au

    orig_spe = au.safe_process_events

    def counting_spe():
        calls["process"] += 1
        orig_spe()

    au.safe_process_events = counting_spe
    try:
        with UploadBusy(None, "Loading rows…", title="Importing Daily Register") as busy:
            orig_repaint = busy._dlg.repaint

            def counting_repaint(*a, **k):
                calls["repaint"] += 1
                return orig_repaint(*a, **k)

            busy._dlg.repaint = counting_repaint
            busy.update("Loading 25 / 100 row(s)…", value=25, maximum=100)
            assert busy._dlg._panel.percent_text() == "25%"
            assert "Loading 25 / 100 row(s)" in busy._dlg.labelText()
            assert calls["repaint"] >= 1
            # tick() yields to the loop so the real paint / animation runs.
            await busy.tick()
    finally:
        au.safe_process_events = orig_spe
    assert calls["process"] == 0


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
