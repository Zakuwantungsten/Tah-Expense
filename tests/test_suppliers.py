"""Supplier catalog flag and Master Expenses exclusion."""

from __future__ import annotations

from tahmeed.models.category import Category
from tahmeed.services.accountant_service import _exclude_supplier_payments


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
