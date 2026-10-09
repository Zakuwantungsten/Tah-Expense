"""Supplier catalog flag and Master Expenses exclusion."""

from __future__ import annotations

import asyncio

from tahmeed.models.category import Category
from tahmeed.services import accountant_service
from tahmeed.services.accountant_service import (
    _exclude_supplier_payments,
    get_supplier_payments_totals,
)


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
    calls: list[tuple] = []

    async def request(method: str, path: str, *, params: dict | None = None, **_kw):
        calls.append((method, path, params))
        return {"count": 0, "tzs": 0.0, "usd": 0.0}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    result = asyncio.run(get_supplier_payments_totals(2026))
    assert result == {"count": 0, "tzs": 0.0, "usd": 0.0}
    assert calls[0][0] == "GET"
    assert calls[0][1] == "v1/accountant/reports/supplier-payments/totals"
    assert calls[0][2] == {"year": 2026}


def test_get_supplier_payments_totals_returns_aggregate(monkeypatch) -> None:
    async def request(method: str, path: str, *, params: dict | None = None, **_kw):
        assert method == "GET"
        assert path == "v1/accountant/reports/supplier-payments/totals"
        assert params == {"year": 2026}
        return {"count": 3, "tzs": 1_250_000.0, "usd": 4200.0}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    result = asyncio.run(get_supplier_payments_totals(2026))
    assert result == {"count": 3, "tzs": 1_250_000.0, "usd": 4200.0}


def test_get_supplier_payments_totals_empty_aggregate(monkeypatch) -> None:
    async def request(*_a, **_k):
        return {"count": 0, "tzs": 0.0, "usd": 0.0}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    result = asyncio.run(get_supplier_payments_totals(2025))
    assert result == {"count": 0, "tzs": 0.0, "usd": 0.0}
