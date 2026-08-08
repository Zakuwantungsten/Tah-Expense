"""Tests for local Daily Register draft outbox."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from bson import ObjectId

from tahmeed.services import register_draft_service as drafts


@pytest.fixture()
def draft_dir(tmp_path, monkeypatch):
    root = tmp_path / "register_drafts"
    monkeypatch.setattr(drafts, "drafts_root", lambda: root)
    return root


def test_save_load_clear_roundtrip(draft_dir):
    user_id = ObjectId()
    d = date(2026, 8, 8)
    payload = drafts.build_draft_payload(
        user_id=user_id,
        username="aisha",
        register_date=d,
        merged=False,
        edit_mode=True,
        dirty_saved=[{
            "tx_id": str(ObjectId()),
            "cashier_id": str(user_id),
            "cells": {"4": "PARKING", "8": "5,000.00"},
        }],
        new_rows=[{
            "cells": {"4": "DIESEL", "8": "10,000.00"},
            "pending_meta": {"currency": "TZS", "category_id": str(ObjectId())},
        }],
    )
    path = drafts.save_register_draft(payload)
    assert path.is_file()
    assert path.parent == draft_dir

    loaded = drafts.load_register_draft(user_id, d, merged=False)
    assert loaded is not None
    assert loaded["username"] == "aisha"
    assert len(loaded["dirty_saved"]) == 1
    assert len(loaded["new_rows"]) == 1
    assert drafts.load_register_draft(user_id, d, merged=True) is None

    drafts.clear_register_draft(user_id, d, merged=False)
    assert drafts.load_register_draft(user_id, d, merged=False) is None
    assert not path.exists()


def test_empty_draft_deletes_file(draft_dir):
    user_id = ObjectId()
    d = date(2026, 8, 8)
    payload = drafts.build_draft_payload(
        user_id=user_id,
        username="aisha",
        register_date=d,
        merged=False,
        edit_mode=False,
        dirty_saved=[{"tx_id": "x", "cells": {"4": "A"}}],
        new_rows=[],
    )
    path = drafts.save_register_draft(payload)
    assert path.is_file()

    empty = drafts.build_draft_payload(
        user_id=user_id,
        username="aisha",
        register_date=d,
        merged=False,
        edit_mode=False,
        dirty_saved=[],
        new_rows=[],
    )
    drafts.save_register_draft(empty)
    assert not path.exists()


def test_cells_json_roundtrip():
    raw = drafts.cells_for_json({1: "08 Aug", 4: "FUEL", 8: "1,250.00"})
    assert raw == {"1": "08 Aug", "4": "FUEL", "8": "1,250.00"}
    assert drafts.cells_from_json(raw)[4] == "FUEL"


def test_pending_meta_objectid_roundtrip():
    cid = ObjectId()
    meta = {
        "category_id": cid,
        "currency": "TZS",
        "import_primary_date": datetime(2026, 8, 1, 12, 0, 0),
        "lpo_do": "LPO1",
    }
    serialized = drafts.serialize_pending_meta(meta)
    assert serialized["category_id"] == str(cid)
    assert isinstance(serialized["import_primary_date"], str)

    hydrated = drafts.hydrate_pending_meta(serialized)
    assert hydrated["category_id"] == cid
    assert hydrated["import_primary_date"].year == 2026
