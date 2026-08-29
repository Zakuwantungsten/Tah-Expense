"""Supplier catalog flag and Master Expenses exclusion."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from tahmeed.models.category import Category
from tahmeed.services import accountant_service
from tahmeed.services.accountant_service import (
    _exclude_supplier_payments,
    get_supplier_payments_totals,
)


class _FakeAggregateCursor:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def to_list(self, length=None):
        return self._rows


class _FakeTransactions:
    def __init__(self, aggregate_result: list | None = None) -> None:
        self.aggregate_result = aggregate_result if aggregate_result is not None else []
        self.last_pipeline: list | None = None

    def aggregate(self, pipeline: list):
        self.last_pipeline = pipeline
        return _FakeAggregateCursor(self.aggregate_result)


def test_category_is_supplier_roundtrip():
    cat = Category(name="ABC SPARES", is_supplier=True, requires_truck=False)
    doc = cat.to_doc()
    assert doc["is_supplier"] is True
    restored = Category.from_doc(doc)
    assert restored.is_supplier is True
    assert restored.name == "ABC SPARES"


def test_category_legacy_doc_defaults_not_supplier():
    restored = Category.from_doc({"name": "Mileage"})
    assert restored.is_supplier is False


def test_exclude_supplier_payments_noop_without_names():
    base = {"verified": True}
    assert _exclude_supplier_payments(base, []) == base


def test_sort_payment_targets_puts_items_before_suppliers():
    from tahmeed.services.category_service import sort_payment_targets

    cats = [
        Category(name="Z SUPPLIER", is_supplier=True),
        Category(name="MILEAGE", is_supplier=False),
        Category(name="A SUPPLIER", is_supplier=True),
        Category(name="FUEL", is_supplier=False),
    ]
    ordered = sort_payment_targets(cats)
    assert [c.name for c in ordered] == ["FUEL", "MILEAGE", "A SUPPLIER", "Z SUPPLIER"]
    from tahmeed.services.category_service import _apply_supplier_filter

    cats = [
        Category(name="MILEAGE", is_supplier=False),
        Category(name="ABC SPARES", is_supplier=True),
    ]
    assert [c.name for c in _apply_supplier_filter(cats, True)] == ["ABC SPARES"]
    assert [c.name for c in _apply_supplier_filter(cats, False)] == ["MILEAGE"]
    assert len(_apply_supplier_filter(cats, None)) == 2


def test_exclude_supplier_payments_adds_case_insensitive_clause():
    base = {"verified": True, "trashed": {"$ne": True}}
    out = _exclude_supplier_payments(base, ["abc spares", "lake oil"])
    assert out["verified"] is True
    blob = str(out).lower()
    assert "abc spares" in blob
    assert "lake oil" in blob
    assert "$expr" in out or "$and" in out
    assert "category_name" in blob or "$tolower" in blob


def test_get_supplier_payments_totals_empty_without_suppliers(monkeypatch) -> None:
    async def _no_suppliers():
        return []

    monkeypatch.setattr(
        accountant_service, "_supplier_category_names_lower", _no_suppliers,
    )
    result = asyncio.run(get_supplier_payments_totals(2026))
    assert result == {"count": 0, "tzs": 0.0, "usd": 0.0}


def test_get_supplier_payments_totals_returns_aggregate(monkeypatch) -> None:
    txs = _FakeTransactions([{
        "count": 3,
        "tzs_total": 1_250_000.0,
        "usd_total": 4200.0,
    }])

    async def _suppliers():
        return ["abc spares", "lake oil"]

    monkeypatch.setattr(
        accountant_service, "_supplier_category_names_lower", _suppliers,
    )
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )

    result = asyncio.run(get_supplier_payments_totals(2026))
    assert result == {"count": 3, "tzs": 1_250_000.0, "usd": 4200.0}
    assert txs.last_pipeline is not None
    match_stage = txs.last_pipeline[0]["$match"]
    assert match_stage["verified"] is True
    assert match_stage["date"]["$gte"] == datetime(2026, 1, 1)
    assert match_stage["date"]["$lte"] == datetime(2026, 12, 31, 23, 59, 59)
    supplier_stage = txs.last_pipeline[2]["$match"]
    assert supplier_stage["_cat_l"]["$in"] == ["abc spares", "lake oil"]


def test_get_supplier_payments_totals_empty_aggregate(monkeypatch) -> None:
    txs = _FakeTransactions([])

    async def _suppliers():
        return ["abc spares"]

    monkeypatch.setattr(
        accountant_service, "_supplier_category_names_lower", _suppliers,
    )
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )

    result = asyncio.run(get_supplier_payments_totals(2025))
    assert result == {"count": 0, "tzs": 0.0, "usd": 0.0}
