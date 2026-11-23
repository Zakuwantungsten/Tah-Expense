"""Ribbon Submit badge mirrors unsubmitted draft counts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.widgets.register_ribbon_toolbar import RegisterRibbonToolbar


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_submit_badge_shows_and_clears():
    _app()
    ribbon = RegisterRibbonToolbar()
    assert ribbon._submit_badge.isHidden()

    ribbon.set_submit_badge(3)
    assert not ribbon._submit_badge.isHidden()
    assert ribbon._submit_badge.text() == "3"
    assert "3 unsubmitted" in ribbon._btn_submit.toolTip()

    ribbon.set_submit_badge(0)
    assert ribbon._submit_badge.isHidden()
    assert ribbon._btn_submit.toolTip() == "Submit day to Verify"


def test_scan_duplicates_ribbon_button_exists():
    _app()
    ribbon = RegisterRibbonToolbar()
    assert ribbon._btn_scan_dups is not None
    assert ribbon._btn_scan_dups.text() == "Dupes"
    assert any(key == "dupes" for key, *_rest in ribbon._overflow_catalog)


def test_unpark_saved_rows_defaults_to_scan_duplicates():
    """Already-saved parked → My must soft-dup; local-only promote never calls this."""
    import inspect

    from tahmeed.ui.cashier.excel_grid import DailyRegister

    sig = inspect.signature(DailyRegister._unpark_saved_rows)
    assert sig.parameters["scan_duplicates"].default is True
    assert hasattr(DailyRegister, "_scan_duplicates_for_tx_ids")
    assert hasattr(DailyRegister, "_collect_soft_dup_candidates_for_tx_ids")
