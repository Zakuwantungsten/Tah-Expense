"""Payee IIF import must pause badge polls and defer modals (Py 3.14-safe)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from tahmeed.ui.accountant.manage_payees import ManagePayeesWidget


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_do_import_iif_pauses_polls_and_defers_success_dialog(monkeypatch) -> None:
    _app()
    widget = ManagePayeesWidget()
    paused = {"n": 0}
    resumed = {"n": 0}
    scheduled: list = []
    boxed: list[str] = []

    class _PauseCM:
        def __enter__(self):
            paused["n"] += 1
            return self

        def __exit__(self, *args):
            resumed["n"] += 1
            return False

    async def _fake_import(path, *, replace_existing):
        assert paused["n"] == 1
        assert resumed["n"] == 0
        return {"imported": 3, "updated": 1, "removed": 0}

    async def _fake_load():
        widget._rows = []
        widget._total = 0

    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_payees.pause_background_polls",
        lambda _w: _PauseCM(),
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_payees.schedule_call",
        lambda fn: scheduled.append(fn),
    )
    monkeypatch.setattr(
        "tahmeed.services.payee_service.import_payee_list",
        _fake_import,
    )
    monkeypatch.setattr(widget, "_load", _fake_load)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: boxed.append("info"),
    )

    await widget._do_import_iif(r"C:\fake\payees.IIF", replace_existing=False)

    assert paused["n"] == 1
    assert resumed["n"] == 1
    assert len(scheduled) == 1
    assert boxed == []  # modal not shown while import task was current
    scheduled[0]()
    assert boxed == ["info"]
    widget.deleteLater()


@pytest.mark.asyncio
async def test_do_import_iif_defers_error_dialog(monkeypatch) -> None:
    _app()
    widget = ManagePayeesWidget()
    scheduled: list = []
    boxed: list[str] = []

    class _PauseCM:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    async def _boom(path, *, replace_existing):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_payees.pause_background_polls",
        lambda _w: _PauseCM(),
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_payees.schedule_call",
        lambda fn: scheduled.append(fn),
    )
    monkeypatch.setattr(
        "tahmeed.services.payee_service.import_payee_list",
        _boom,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *a, **k: boxed.append("critical"),
    )

    await widget._do_import_iif(r"C:\fake\payees.IIF", replace_existing=True)

    assert len(scheduled) == 1
    assert boxed == []
    scheduled[0]()
    assert boxed == ["critical"]
    widget.deleteLater()
