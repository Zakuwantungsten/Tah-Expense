"""Phase 5: Verify approve requires Item + Payee."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.models.transaction import Transaction
from tahmeed.services.description_mapping_service import (
    transaction_needs_mapping,
    transaction_needs_payee,
)


def _tx(**kwargs) -> Transaction:
    base = dict(
        date=datetime(2026, 1, 1),
        description="TRIANGLE",
        truck_number="T123",
        amount=100.0,
        item="",
        category_name="",
        payee="",
        _id=ObjectId(),
    )
    base.update(kwargs)
    return Transaction(**base)


def test_needs_payee_and_mapping_helpers() -> None:
    assert transaction_needs_payee("") is True
    assert transaction_needs_payee("  ") is True
    assert transaction_needs_payee("PARKING DAR") is False
    assert transaction_needs_mapping("", None, "") is True
    assert transaction_needs_mapping("Parking", None, "") is True
    assert transaction_needs_mapping("", None, "PARKING DAR") is True
    assert transaction_needs_mapping("Parking", "Parking", "PARKING DAR") is False


@pytest.mark.asyncio
async def test_ensure_blocks_when_item_set_but_payee_empty(monkeypatch) -> None:
    pytest.importorskip("PySide6")
    from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget

    tx = _tx(item="Parking", category_name="Parking", payee="")
    warnings: list[str] = []

    async def fake_mappings(_descriptions):
        return {}

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.get_mappings_for_descriptions",
        fake_mappings,
    )

    widget = VerifyInboxWidget.__new__(VerifyInboxWidget)
    widget._set_busy = lambda *a, **k: None
    widget._clear_busy = lambda: None

    opened: list = []

    async def fake_await_ui(fn):
        warnings.append("warned")
        # Simulate choosing "Open Day Issues"
        return True

    widget._await_ui = fake_await_ui
    widget.open_day_issues = type("Sig", (), {"emit": lambda self, d: opened.append(d)})()

    ok = await VerifyInboxWidget._ensure_item_and_payee(widget, [tx])
    assert ok is False
    assert warnings == ["warned"]
    assert opened
    nav = opened[0]
    assert nav.date == datetime(2026, 1, 1).date()
    assert "TRIANGLE" in nav.description_keys
    assert nav.all_days is False


@pytest.mark.asyncio
async def test_ensure_applies_complete_mapping_then_ok(monkeypatch) -> None:
    pytest.importorskip("PySide6")
    from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget

    cat_id = ObjectId()
    tx = _tx(item="", category_name="", payee="")
    mapping = DescriptionMapping(
        description_key="TRIANGLE",
        description="TRIANGLE",
        category_id=cat_id,
        category_name="Parking",
        payee="PARKING DAR",
    )
    bulk_calls: list = []

    async def fake_mappings(_descriptions):
        return {"TRIANGLE": mapping}

    async def fake_bulk(ids, name, cid, *, payee=None):
        bulk_calls.append((list(ids), name, cid, payee))
        return len(ids)

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.get_mappings_for_descriptions",
        fake_mappings,
    )
    monkeypatch.setattr(
        "tahmeed.services.accountant_service.bulk_update_transaction_category",
        fake_bulk,
    )

    widget = VerifyInboxWidget.__new__(VerifyInboxWidget)
    widget._set_busy = lambda *a, **k: None
    widget._clear_busy = lambda: None

    ok = await VerifyInboxWidget._ensure_item_and_payee(widget, [tx])
    assert ok is True
    assert bulk_calls == [([tx._id], "Parking", cat_id, "PARKING DAR")]
    assert tx.item == "Parking"
    assert tx.payee == "PARKING DAR"


@pytest.mark.asyncio
async def test_ensure_mixed_batch_fails_without_partial_approve(monkeypatch) -> None:
    """One complete + one incomplete → whole ensure returns False (batch gate)."""
    pytest.importorskip("PySide6")
    from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget

    cat_id = ObjectId()
    good = _tx(description="TRIANGLE", item="", payee="")
    bad = _tx(description="LATRA", item="Fees", category_name="Fees", payee="")
    mapping = DescriptionMapping(
        description_key="TRIANGLE",
        description="TRIANGLE",
        category_id=cat_id,
        category_name="Parking",
        payee="PARKING DAR",
    )
    bulk_calls: list = []

    async def fake_mappings(_descriptions):
        return {"TRIANGLE": mapping}

    async def fake_bulk(ids, name, cid, *, payee=None):
        bulk_calls.append((list(ids), name, payee))
        return len(ids)

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.get_mappings_for_descriptions",
        fake_mappings,
    )
    monkeypatch.setattr(
        "tahmeed.services.accountant_service.bulk_update_transaction_category",
        fake_bulk,
    )

    widget = VerifyInboxWidget.__new__(VerifyInboxWidget)
    widget._set_busy = lambda *a, **k: None
    widget._clear_busy = lambda: None
    widget._await_ui = AsyncMock(return_value=False)  # Close without opening
    widget.open_day_issues = type("Sig", (), {"emit": lambda self, d: None})()

    ok = await VerifyInboxWidget._ensure_item_and_payee(widget, [good, bad])
    assert ok is False
    # Good row may be patched, but ensure still fails the batch.
    assert any(c[1] == "Parking" for c in bulk_calls)
    widget._await_ui.assert_awaited()


def test_preferred_day_issues_date_prefers_import_primary() -> None:
    from tahmeed.ui.accountant.verify_inbox import _preferred_day_issues_date

    tx = _tx(
        date=datetime(2026, 1, 5),
        import_primary_date=datetime(2026, 1, 10),
    )
    assert _preferred_day_issues_date([tx]) == date(2026, 1, 10)


def test_day_issues_nav_from_unmapped_multi_day_and_keys() -> None:
    from tahmeed.services.description_mapping_service import normalize_description
    from tahmeed.ui.accountant.verify_inbox import _day_issues_nav_from_unmapped

    a = _tx(
        description="AGENCY FEE",
        date=datetime(2026, 1, 5),
        import_primary_date=datetime(2026, 1, 5),
    )
    b = _tx(
        description="ALLOWANCE",
        date=datetime(2026, 1, 10),
        import_primary_date=datetime(2026, 1, 10),
    )
    c = _tx(
        description="agency fee",
        date=datetime(2026, 1, 5),
        import_primary_date=datetime(2026, 1, 5),
    )
    nav = _day_issues_nav_from_unmapped([a, b, c])
    assert nav.all_days is True
    assert nav.date == date(2026, 1, 5)  # most common
    assert set(nav.description_keys) == {
        normalize_description("AGENCY FEE"),
        normalize_description("ALLOWANCE"),
    }


def test_action_panel_has_no_map_on_approve_placeholder() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from tahmeed.ui.accountant.verify_inbox import _ActionPanel

    _app = QApplication.instance() or QApplication([])
    panel = _ActionPanel()
    tx = _tx(item="", payee="")
    panel.load(tx, "Cashier", ["Parking", "Fuel"])
    texts = [panel._cat_combo.itemText(i) for i in range(panel._cat_combo.count())]
    assert "— map on approve —" not in texts
    # Panel itself starts hidden; chip is shown() so isHidden() is False.
    assert not panel._mapping_warn.isHidden()
    assert "Missing" in panel._mapping_warn.text()
