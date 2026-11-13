"""Upload detail: ribbon owns Delete/Export/KPI; meta sits with Search/Clear."""

from __future__ import annotations

import sys
from datetime import datetime

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from tahmeed.ui.accountant.separate_expenses import (
    CongoExpensesWidget,
    ZambiaParkingWidget,
    _CongoExpUploadDetail,
    _ZambiaParkingUploadDetail,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _sample_congo_doc() -> dict:
    return {
        "_id": "u1",
        "source_filename": "congo.xlsx",
        "sheet_label": "C236",
        "record_count": 55,
        "money_in": 100.0,
        "money_out": -40.0,
        "balance_usd": 60.0,
        "import_date": datetime(2026, 11, 6),
    }


def test_congo_upload_detail_has_no_inline_delete_or_export(qapp) -> None:
    d = _CongoExpUploadDetail()
    labels = [
        b.text().replace("\n", " ")
        for b in d.findChildren(QPushButton)
    ]
    assert not any("Delete Upload" in t for t in labels)
    assert not any(t.strip().startswith("Export") for t in labels)
    # Meta sits on the search row, right side.
    assert d._info_lbl.parentWidget() is d._search_edit.parentWidget()
    # Money KPI is registered for the ribbon, not laid out in the page body.
    assert d.summary_widget() is d._summary_frame
    assert d._summary_frame.parentWidget() is d._chrome_stash_host
    d.close()


def test_congo_upload_detail_meta_and_kpi_values(qapp, monkeypatch) -> None:
    monkeypatch.setattr(_CongoExpUploadDetail, "_reset_and_load", lambda self: None)
    d = _CongoExpUploadDetail()
    d.load_upload(_sample_congo_doc())
    assert "C236" in d._info_lbl.text()
    assert "55" in d._info_lbl.text()
    assert "06 Nov 2026" in d._info_lbl.text()
    assert "In USD" in d._in_lbl.text()
    assert not d._summary_frame.isWindow()
    d.close()


def test_zambia_shell_enables_ribbon_delete_on_detail(qapp, monkeypatch) -> None:
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingAllEntries.refresh",
        lambda self: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingMoneyInEntries.refresh",
        lambda self: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingUploadBrowse.refresh",
        lambda self: None,
    )
    monkeypatch.setattr(
        _ZambiaParkingUploadDetail,
        "_reset_and_load",
        lambda self: None,
    )

    shell = QWidget()
    shell.resize(1100, 700)
    shell.show()
    w = ZambiaParkingWidget(shell)
    w.show()

    doc = {
        "_id": "z1",
        "source_filename": "zambia.xlsx",
        "sheet_label": "Week 45",
        "record_count": 12,
        "total_debit": 100.0,
        "total_credit": 250.0,
        "import_date": datetime(2026, 11, 6),
    }
    w._show_detail(doc)

    assert w._uploads_detail_active()
    assert w._detail.summary_widget() is not None
    assert not w._detail.summary_widget().isWindow()
    assert "Week 45" in w._detail._info_lbl.text()
    assert w._detail._info_lbl.parentWidget() is w._detail._search_edit.parentWidget()
    # Ribbon Delete is enabled for the open upload.
    assert w._ribbon._btn_delete.isEnabled()

    w.close()
    shell.close()


def test_congo_shell_ribbon_delete_targets_open_upload(qapp, monkeypatch) -> None:
    for name in (
        "_CongoAllEntries",
        "_CongoMoneyInEntries",
        "_CongoCalledOutEntries",
        "_CongoExpUploadBrowse",
    ):
        monkeypatch.setattr(
            f"tahmeed.ui.accountant.separate_expenses.{name}.refresh",
            lambda self: None,
        )
    monkeypatch.setattr(
        _CongoExpUploadDetail,
        "_reset_and_load",
        lambda self: None,
    )

    seen: list[dict] = []

    shell = QWidget()
    shell.show()
    w = CongoExpensesWidget(shell)
    w.show()
    w._detail.delete_requested.connect(lambda doc: seen.append(doc))
    w._show_detail(_sample_congo_doc())
    w._on_ribbon_delete()
    assert len(seen) == 1
    assert seen[0]["_id"] == "u1"
    w.close()
    shell.close()
