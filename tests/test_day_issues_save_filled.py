"""Phase 6: inline drafts — only fully filled Payee+Item commit."""

from __future__ import annotations

import pytest

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.day_issues_service import DayIssue


def _issue(desc: str, **kwargs) -> DayIssue:
    key = normalize_description(desc)
    base = dict(
        kind="Unmapped",
        description=desc,
        description_key=key,
        count=1,
        payee="",
        item="",
        detail="No payee + item mapping",
    )
    base.update(kwargs)
    return DayIssue(**base)


def test_filled_drafts_require_both_fields() -> None:
    """Mirrors DayIssuesWidget._filled_drafts gating logic."""
    issues = [
        _issue("LATRA FEE"),
        _issue("DIESEL"),
        _issue("TOLLS"),
    ]
    drafts = {
        normalize_description("LATRA FEE"): {
            "description": "LATRA FEE",
            "payee": "CITY",
            "item": "Parking",
        },
        normalize_description("DIESEL"): {
            "description": "DIESEL",
            "payee": "SHELL",
            "item": "",  # incomplete
        },
        normalize_description("TOLLS"): {
            "description": "TOLLS",
            "payee": "",
            "item": "Fees",  # incomplete
        },
    }
    filled = []
    for issue in issues:
        draft = drafts.get(issue.description_key) or {}
        payee = (draft.get("payee") or "").strip()
        item = (draft.get("item") or "").strip()
        if payee and item:
            filled.append((issue.description, payee, item))
    assert filled == [("LATRA FEE", "CITY", "Parking")]


def test_editable_cell_blank_not_em_dash() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from tahmeed.ui.cashier.day_issues import _editable_cell

    _app = QApplication.instance() or QApplication([])
    cell = _editable_cell("")
    assert cell.text() == ""
    assert bool(cell.flags() & Qt.ItemIsEditable)
