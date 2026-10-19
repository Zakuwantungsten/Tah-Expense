"""Browse Simple / Uploads should open the Daily Register table, not Advanced."""

from datetime import date

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.cashier.transactions_table import (
    TransactionBrowser,
    _MODE_SIMPLE,
    _MODE_UPLOADS,
    _U_COL_CASHIER,
    _U_COL_SN,
    _U_COL_TOTAL,
    _as_py_date,
    _format_upload_total,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _browser() -> TransactionBrowser:
    _app()
    browser = TransactionBrowser()
    browser._do_find = lambda: None
    return browser


def test_as_py_date_datetime_and_date():
    from datetime import datetime

    assert _as_py_date(date(2026, 7, 21)) == date(2026, 7, 21)
    assert _as_py_date(datetime(2026, 7, 21, 15, 30)) == date(2026, 7, 21)
    assert _as_py_date(None) is None


def test_open_upload_jumps_to_register_not_advanced():
    browser = _browser()
    jumped = []
    uploads = []
    browser.go_to_date.connect(lambda d, t="": jumped.append((d, t)))
    browser.go_to_upload.connect(lambda uid, d=None: uploads.append((uid, d)))
    browser._current_mode = _MODE_UPLOADS
    browser._results_uploads = [
        {
            "_id": "batch-1",
            "primary_date": date(2026, 7, 21),
            "min_date": date(2026, 7, 1),
            "max_date": date(2026, 7, 21),
        }
    ]
    browser._open_upload_at(0)
    assert jumped == []
    assert uploads == [("batch-1", date(2026, 7, 21))]
    assert browser._current_mode == _MODE_UPLOADS


def test_open_simple_day_jumps_to_register_not_advanced():
    from unittest.mock import patch

    browser = _browser()
    jumped = []
    browser.go_to_date.connect(lambda d, t="": jumped.append((d, t)))
    browser._current_mode = _MODE_SIMPLE
    browser._results_simple = [{"date": date(2026, 5, 4), "entries_count": 12}]
    with patch.object(browser._simple_table, "currentRow", return_value=0):
        browser._on_go_to()
    assert jumped == [(date(2026, 5, 4), "")]
    assert browser._current_mode == _MODE_SIMPLE


def test_cashier_delete_only_own_uploads():
    from types import SimpleNamespace

    me = SimpleNamespace(_id="cashier-me")
    browser = TransactionBrowser(user=me, can_manage_uploads=False)
    browser._do_find = lambda: None
    assert browser._can_delete_upload({"cashier_id": "cashier-me"}) is True
    assert browser._can_delete_upload({"cashier_id": "cashier-other"}) is False
    manager = TransactionBrowser(user=me, can_manage_uploads=True)
    manager._do_find = lambda: None
    assert manager._can_delete_upload({"cashier_id": "cashier-other"}) is True


def test_format_upload_total_tzs_usd_or_both():
    assert _format_upload_total({"total_tzs": -1900, "total_usd": 0}) == "TZS -1,900"
    assert _format_upload_total({"total_tzs": 0, "total_usd": -25.5}) == "USD -25.50"
    assert _format_upload_total(
        {"total_tzs": -1000, "total_usd": -10}
    ) == "TZS -1,000 · USD -10.00"
    assert _format_upload_total({}) == "—"


def test_uploads_table_has_sn_and_total_after_cashier():
    browser = _browser()
    headers = [
        browser._uploads_table.horizontalHeaderItem(i).text()
        for i in range(browser._uploads_table.columnCount())
    ]
    assert headers[_U_COL_SN] == "SN"
    assert headers[_U_COL_CASHIER] == "Cashier"
    assert headers[_U_COL_TOTAL] == "Total"
    assert _U_COL_TOTAL == _U_COL_CASHIER + 1
    assert headers.index("Total") == headers.index("Cashier") + 1
