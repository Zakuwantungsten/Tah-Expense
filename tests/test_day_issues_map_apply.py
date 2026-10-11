"""Phase 3: mapping stamps draft/unsaved rows and saves Description Maps path."""

from __future__ import annotations

from bson import ObjectId

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.register_draft_service import (
    apply_mapping_to_draft_payload,
    build_draft_payload,
    cells_for_json,
    cells_from_json,
)
from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_PAYEE


def test_apply_mapping_to_draft_payload_updates_matching_new_rows() -> None:
    from datetime import date

    draft = build_draft_payload(
        user_id=ObjectId(),
        username="c1",
        register_date=date(2026, 3, 15),
        merged=False,
        edit_mode=False,
        dirty_saved=[],
        new_rows=[
            {
                "cells": cells_for_json({
                    COL_DESC: "LATRA FEE",
                    COL_ITEM: "",
                    COL_PAYEE: "",
                }),
                "pending_meta": None,
            },
            {
                "cells": cells_for_json({
                    COL_DESC: "OTHER",
                    COL_ITEM: "X",
                    COL_PAYEE: "Y",
                }),
                "pending_meta": None,
            },
        ],
    )
    keys = {normalize_description("LATRA FEE")}
    cat_id = ObjectId()
    n = apply_mapping_to_draft_payload(
        draft,
        keys,
        payee="CITY COUNCIL",
        item_name="Parking",
        category_id=cat_id,
        desc_col=COL_DESC,
        item_col=COL_ITEM,
        payee_col=COL_PAYEE,
    )
    assert n == 1
    cells = cells_from_json(draft["new_rows"][0]["cells"])
    assert cells[COL_ITEM] == "Parking"
    assert cells[COL_PAYEE] == "CITY COUNCIL"
    assert draft["new_rows"][0]["pending_meta"]["category_id"] == str(cat_id)
    other = cells_from_json(draft["new_rows"][1]["cells"])
    assert other[COL_ITEM] == "X"
    assert other[COL_PAYEE] == "Y"


def test_save_mapping_signature_includes_payee() -> None:
    import inspect

    from tahmeed.services.description_mapping_service import save_mapping

    params = inspect.signature(save_mapping).parameters
    assert "payee" in params
