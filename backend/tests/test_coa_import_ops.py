"""Phase 10.1 — Chart of Accounts import helpers."""

from app.coa_import_ops import (
    COA_PALETTE,
    build_coa_category_doc,
    normalize_coa_row,
    prepare_coa_category_docs,
)


def test_normalize_coa_row_uppercases_and_skips_blank() -> None:
    assert normalize_coa_row({"name": "  fuel  ", "ref_num": "a1"}) == {
        "name": "FUEL",
        "ref_num": "A1",
        "account_type": "",
        "coa_description": "",
        "account_number": "",
        "currency": "",
    }
    assert normalize_coa_row({"name": "  "}) is None
    assert normalize_coa_row({}) is None


def test_build_and_prepare_docs() -> None:
    doc = build_coa_category_doc(
        {
            "name": "FUEL",
            "ref_num": "1",
            "account_type": "EXPENSE",
            "coa_description": "DIESEL",
            "account_number": "5000",
            "currency": "TZS",
        },
        sort_order=0,
    )
    assert doc["name"] == "FUEL"
    assert doc["description"] == "DIESEL"
    assert doc["coa_description"] == "DIESEL"
    assert doc["color"] == COA_PALETTE[0]
    assert doc["show_in_sidebar"] is False
    assert doc["requires_truck"] is False

    docs = prepare_coa_category_docs(
        [
            {"name": "a"},
            {"name": ""},
            {"name": "b", "currency": "usd"},
        ]
    )
    assert len(docs) == 2
    assert docs[0]["sort_order"] == 0
    assert docs[1]["sort_order"] == 1
    assert docs[1]["currency"] == "USD"
    assert docs[1]["color"] == COA_PALETTE[1]
