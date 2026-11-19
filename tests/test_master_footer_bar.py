"""Master Expenses footer — one row, black totals, refund included."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_footer_bar_single_row_black_totals_and_refund() -> None:
    from tahmeed.ui.accountant.master_expenses import _FooterBar

    _app()
    footer = _FooterBar()
    footer.update_totals(tzs=-58_810_798.0, usd=0.0, refund=-700_000.0, record_count=561)
    footer.set_status("Showing 150 of 561  •  Scroll down for more")

    assert footer._tzs_lbl.text() == "TZS  -58,810,798"
    assert "#DC2626" not in footer._tzs_lbl.styleSheet()
    assert footer._refund_lbl.text() == "Refund  TZS -700,000"
    assert footer._usd_lbl.text() == "USD  —"
    assert footer._status_lbl.text() == "Showing 150 of 561  •  Scroll down for more"
    assert footer._status_lbl.parent() is footer
