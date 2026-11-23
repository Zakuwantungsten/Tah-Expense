"""Analytics → Trucks: fleet table → detail stack smoke."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_trucks_page_fleet_to_detail_and_back() -> None:
    from tahmeed.ui.accountant.analytics_trucks import AnalyticsTrucksPage

    _app()
    page = AnalyticsTrucksPage()
    page._fleet = {
        "ready": True,
        "period_label": "2026",
        "truck_count": 1,
        "share_basis": "tzs",
        "mom_label": "vs 2025",
        "trucks": [
            {
                "sn": 1,
                "truck": "T 832 DND",
                "tzs": 1_000_000.0,
                "usd": 400.0,
                "zmw": 0.0,
                "count": 12,
                "share": 0.8,
                "mom_pct": 0.25,
                "mom_delta": 200_000.0,
                "mom_direction": "up",
                "vehicle_kind": "truck",
            },
            {
                "sn": 2,
                "truck": "T 900 TRL",
                "tzs": 250_000.0,
                "usd": 0.0,
                "zmw": 0.0,
                "count": 3,
                "share": 0.2,
                "mom_pct": 0.0,
                "mom_delta": 0.0,
                "mom_direction": "flat",
                "vehicle_kind": "trailer",
            },
        ],
        "totals": {"tzs": 1_250_000.0, "usd": 400.0, "zmw": 0.0, "count": 15},
        "truck_count": 2,
    }
    page._apply_fleet()
    assert page._view == "fleet"
    assert page._body_stack is not None
    assert page._body_stack.currentIndex() == 1
    assert page._table is not None
    assert page._table.rowCount() == 3  # 2 data + totals
    assert page._table.columnCount() == 8  # SN…Entries
    assert page._table.item(0, 0).text() == "1"
    truck_item = page._table.item(0, 1)
    assert truck_item is not None
    assert truck_item.data(Qt.UserRole) == "T 832 DND"
    mom_item = page._table.item(0, 5)
    assert mom_item is not None
    assert mom_item.text() == "+25%"
    assert "MoM vs 2025" in (page._subtitle.text() if page._subtitle else "")

    assert page._search_edit is not None
    assert page._search_edit.width() == 260
    assert page._kind_combo is not None
    assert page._kind_combo.count() == 4

    page._on_kind_filter("trailer")
    assert page._kind_combo.currentData() == "trailer"
    assert page._table.rowCount() == 2  # 1 trailer + totals
    assert page._table.item(0, 1).text() == "T 900 TRL"
    assert page._table.item(0, 0).text() == "1"

    page._on_kind_filter("all")
    assert page._kind_combo.currentData() == "all"
    page._on_search_changed("832")
    assert page._table.rowCount() == 2
    assert page._table.item(0, 1).text() == "T 832 DND"
    page._on_search_changed("")


    page._selected_truck = "T 832 DND"
    page._view = "detail"
    page._detail = {
        "ready": True,
        "sparse": False,
        "label": "T 832 DND",
        "period_label": "2026",
        "days": 30,
        "mix_label": "Spend by Category",
        "trucks_label": "Top descriptions",
        "kpis": {
            "totals": {"tzs": 1_000_000.0, "usd": 400.0, "zmw": 0.0},
            "prior_totals": {"tzs": 800_000.0, "usd": 300.0, "zmw": 0.0},
            "avg_per_day": {"tzs": 33_333.0, "usd": 13.0, "zmw": 0.0},
            "count": 12,
            "prior_count": 8,
        },
        "categories": [{"name": "Fuel", "tzs": 600_000.0, "usd": 0.0, "zmw": 0.0}],
        "trucks": [{"name": "Diesel top-up", "tzs": 400_000.0, "usd": 0.0, "zmw": 0.0}],
        "month_trend": [{"month": 1, "tzs": 100_000.0, "usd": 0.0, "zmw": 0.0}],
        "prior_month_trend": [{"month": 1, "tzs": 80_000.0, "usd": 0.0, "zmw": 0.0}],
        "deltas": {
            "yoy": {
                "tzs": [
                    {"month": 1, "delta": 20_000.0, "direction": "up"},
                ]
            },
            "mom": {
                "tzs": [
                    {"month": 2, "delta": 10_000.0, "direction": "up"},
                ]
            },
        },
        "receipts": {"received": 8, "pending": 3, "missing": 1},
    }
    page._apply_detail()
    assert page._view == "detail"
    assert page._body_stack.currentIndex() == 2
    assert page._back_btn is not None and not page._back_btn.isHidden()
    assert page._kpi_total is not None
    assert "1.0M" in page._kpi_total._val_lbl.text()
    assert page._deltas_title is not None
    assert "YoY" in page._deltas_title.text()
    page._on_delta_mode("mom")
    assert page._delta_mode == "mom"
    assert "MoM" in page._deltas_title.text()

    page._on_back()
    assert page._view == "fleet"
    assert page._selected_truck == ""
    assert page._detail == {}
    assert page._body_stack.currentIndex() == 1
    assert page._back_btn is not None and page._back_btn.isHidden()
    # Deep-dive painters cleared so the stack flip stays cheap.
    assert page._deltas is not None
    assert page._deltas.minimumHeight() == 220
    assert page._kpi_total is not None
    assert page._kpi_total._val_lbl.text() == "—"

    page._fleet_loaded = True
    page.ensure_loaded()  # revisit must not kick a reload when fleet is warm
    assert page._view == "fleet"

    # Blank / placeholder plates must not open deep-dive (API 422).
    page._fleet = {
        **page._fleet,
        "trucks": [
            {
                "sn": 1,
                "truck": "—",
                "tzs": 9_999_000.0,
                "usd": 100.0,
                "zmw": 0.0,
                "count": 50,
                "share": 0.9,
                "mom_pct": 1.0,
                "mom_delta": 1.0,
                "mom_direction": "up",
                "vehicle_kind": "unknown",
            },
            {
                "sn": 2,
                "truck": "T 832 DND",
                "tzs": 1_000_000.0,
                "usd": 400.0,
                "zmw": 0.0,
                "count": 12,
                "share": 0.1,
                "mom_pct": 0.25,
                "mom_delta": 200_000.0,
                "mom_direction": "up",
                "vehicle_kind": "truck",
            },
        ],
    }
    page._apply_fleet()
    assert page._table is not None
    assert page._table.item(0, 1).data(Qt.UserRole) == "T 832 DND"
    page._on_row_clicked(0, 1)
    assert page._selected_truck == "T 832 DND"
