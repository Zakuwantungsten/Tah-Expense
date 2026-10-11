"""Readable QComboBox popup stylesheet helpers."""

from __future__ import annotations

from tahmeed.ui.widgets.combo_styles import COMBO_POPUP_SS, with_combo_popup


def test_combo_popup_ss_forces_light_list():
    assert "QComboBox QAbstractItemView" in COMBO_POPUP_SS
    assert "background: #FFFFFF" in COMBO_POPUP_SS
    assert "color: #111827" in COMBO_POPUP_SS
    assert "selection-background-color: #E8F4FD" in COMBO_POPUP_SS


def test_with_combo_popup_appends_once():
    base = "QComboBox { color: #111827; }"
    once = with_combo_popup(base)
    twice = with_combo_popup(once)
    assert once.count("QComboBox QAbstractItemView") == 1
    assert twice == once
    assert "background: #FFFFFF" in once


def test_browse_field_ss_includes_popup():
    from tahmeed.ui.cashier.transactions_table import _FIELD_SS

    assert "QComboBox QAbstractItemView" in _FIELD_SS
    assert "background: #FFFFFF" in _FIELD_SS
