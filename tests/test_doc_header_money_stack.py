"""Doc header money tiles stack My / Parked / Combined amounts."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from tahmeed.ui.cashier.dashboard import _QBDocHeader


def _app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


def test_update_stats_stacks_my_parked_combined():
    _app()
    header = _QBDocHeader()
    header.update_stats(
        5,
        -18_100_000.0,
        1240.0,
        -18_100_000.0,
        None,
        parked_tzs=-4_216_341.0,
        parked_usd=180.5,
        parked_refund=-4_216_341.0,
    )

    assert header._val_entries.text() == "5 entries"
    assert header._stack_tzs["totals"].text() == "TZS -18,100,000"
    assert header._stack_tzs["parked"].text() == "TZS -4,216,341"
    assert header._stack_tzs["combined"].text() == "TZS -22,316,341"

    assert header._stack_usd["totals"].text() == "USD 1,240.00"
    assert header._stack_usd["parked"].text() == "USD 180.50"
    assert header._stack_usd["combined"].text() == "USD 1,420.50"

    assert header._stack_refund["totals"].text() == "TZS -18,100,000"
    assert header._stack_refund["parked"].text() == "TZS -4,216,341"
    assert header._stack_refund["combined"].text() == "TZS -22,316,341"


def test_update_stats_blank_parked_shows_dashes_for_zero():
    _app()
    header = _QBDocHeader()
    header.update_stats(1, 500.0, 0.0, 0.0, None)

    assert header._stack_tzs["totals"].text() == "TZS 500"
    assert header._stack_tzs["parked"].text() == "—"
    assert header._stack_tzs["combined"].text() == "TZS 500"
    assert header._stack_usd["totals"].text() == "—"
    assert header._stack_usd["combined"].text() == "—"
    assert header._stack_refund["combined"].text() == "—"


def test_doc_header_stays_compact():
    _app()
    header = _QBDocHeader()
    header.adjustSize()
    # Was ~108px forced min; content-driven height should stay compact.
    assert header.sizeHint().height() <= 72
