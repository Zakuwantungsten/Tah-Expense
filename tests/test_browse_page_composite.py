"""Browse Phase 1 — composite list client parsing."""

from __future__ import annotations

from bson import ObjectId

from tahmeed.services.cashier_service import _cashier_names_from_payload


def test_cashier_names_from_payload_keys_by_caller_ids() -> None:
    cid = ObjectId()
    names = _cashier_names_from_payload({str(cid): "Alice Cashier"}, [cid])
    assert names[cid] == "Alice Cashier"


def test_cashier_names_unknown_when_missing() -> None:
    cid = ObjectId()
    names = _cashier_names_from_payload({}, [cid])
    assert names[cid] == "Unknown cashier"
