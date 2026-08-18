"""Browse Simple / Uploads should open the Daily Register table, not Advanced."""

from datetime import date

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.cashier.transactions_table import (
    TransactionBrowser,
    _MODE_SIMPLE,
    _MODE_UPLOADS,
    _as_py_date,
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
