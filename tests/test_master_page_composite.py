"""Master Expenses Phase 1 — composite list client parsing."""

from types import SimpleNamespace

from bson import ObjectId

from tahmeed.services.accountant_service import (
    MasterPage,
    _master_cashier_names_from_payload,
)


def test_master_cashier_names_from_payload_keys_by_tx_cashier_id() -> None:
    cid = ObjectId()
    txs = [SimpleNamespace(cashier_id=cid)]
    names = _master_cashier_names_from_payload(
        {str(cid): "Alice Cashier"},
        txs,
    )
    assert names[cid] == "Alice Cashier"


def test_master_cashier_names_unknown_when_missing() -> None:
    cid = ObjectId()
    txs = [SimpleNamespace(cashier_id=cid)]
    names = _master_cashier_names_from_payload({}, txs)
    assert names[cid] == "Unknown cashier"


def test_master_page_namedtuple_fields() -> None:
    page = MasterPage(items=[], total=0, cashier_names={})
    assert page.total == 0
    assert page.items == []
    assert page.cashier_names == {}
