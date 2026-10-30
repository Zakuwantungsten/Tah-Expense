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


def test_apply_mapping_to_draft_payload_stamps_parked_rows() -> None:
    from datetime import date

    def entry(desc: str, meta: dict | None = None) -> dict:
        return {
            "cells": cells_for_json({COL_DESC: desc, COL_ITEM: "", COL_PAYEE: ""}),
            "pending_meta": meta,
        }

    draft = build_draft_payload(
        user_id=ObjectId(),
        username="c1",
        register_date=date(2026, 3, 15),
        merged=False,
        edit_mode=False,
        dirty_saved=[],
        new_rows=[entry("LATRA FEE")],
        parked_rows=[entry("LATRA FEE", {"parked": True})],
    )
    keys = {normalize_description("LATRA FEE")}
    cat_id = ObjectId()
    common = dict(
        payee="CITY COUNCIL",
        item_name="Parking",
        category_id=cat_id,
        desc_col=COL_DESC,
        item_col=COL_ITEM,
        payee_col=COL_PAYEE,
    )

    assert apply_mapping_to_draft_payload(draft, keys, sections=("parked_rows",), **common) == 1
    assert cells_from_json(draft["new_rows"][0]["cells"])[COL_PAYEE] == ""
    parked = draft["parked_rows"][0]
    assert cells_from_json(parked["cells"])[COL_PAYEE] == "CITY COUNCIL"
    assert parked["pending_meta"] == {"parked": True, "category_id": str(cat_id)}

    assert apply_mapping_to_draft_payload(draft, keys, **common) == 2


def test_save_mapping_signature_includes_payee() -> None:
    import inspect

    from tahmeed.services.description_mapping_service import save_mapping

    params = inspect.signature(save_mapping).parameters
    assert "payee" in params


def test_apply_mapping_to_draft_payload_clear_category() -> None:
    from datetime import date

    cat_id = ObjectId()
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
                    COL_ITEM: "Parking",
                    COL_PAYEE: "CITY COUNCIL",
                }),
                "pending_meta": {"category_id": str(cat_id)},
            },
        ],
    )
    keys = {normalize_description("LATRA FEE")}
    n = apply_mapping_to_draft_payload(
        draft,
        keys,
        payee="",
        item_name="",
        clear_category=True,
        desc_col=COL_DESC,
        item_col=COL_ITEM,
        payee_col=COL_PAYEE,
    )
    assert n == 1
    cells = cells_from_json(draft["new_rows"][0]["cells"])
    assert cells[COL_ITEM] == ""
    assert cells[COL_PAYEE] == ""
    meta = draft["new_rows"][0]["pending_meta"] or {}
    assert "category_id" not in meta


def test_split_undo_map_actions_deletes_new_and_restores_prior() -> None:
    from datetime import date

    from tahmeed.ui.cashier.day_issues import (
        MappingUndoSnapshot,
        split_undo_map_actions,
    )

    new_id = ObjectId()
    prior_cat = ObjectId()
    snap = MappingUndoSnapshot(
        descriptions=["NEW FEE", "OLD FEE"],
        dates=[date(2026, 10, 13)],
        prior_by_key={
            "NEW FEE": None,
            "OLD FEE": ("OLD FEE", prior_cat, "Old Item", "OLD PAYEE"),
        },
        new_ids_by_key={"NEW FEE": new_id, "OLD FEE": ObjectId()},
    )
    delete_ids, restore = split_undo_map_actions(snap)
    assert delete_ids == [new_id]
    assert restore == [("OLD FEE", prior_cat, "Old Item", "OLD PAYEE")]
