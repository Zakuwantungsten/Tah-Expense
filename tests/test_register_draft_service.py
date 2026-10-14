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


def test_expense_content_fingerprint_normalizes_case_and_money():
    a = drafts.expense_content_fingerprint(
        description="  diesel ",
        truck_number="t100",
        item="fuel",
        tzs=1000,
        usd=0,
        row_date_iso="2026-08-08",
    )
    b = drafts.expense_content_fingerprint(
        description="DIESEL",
        truck_number="T100",
        item="FUEL",
        tzs=1000.0,
        usd=0.0,
        row_date_iso="2026-08-08",
    )
    assert a == b
    assert drafts.fingerprint_is_meaningful(a)
    assert not drafts.fingerprint_is_meaningful(
        drafts.expense_content_fingerprint(description="")
    )


def test_claim_fingerprint_is_multiset():
    fp = drafts.expense_content_fingerprint(
        description="PARKING",
        truck_number="T1",
        item="PARKING",
        tzs=5000,
        row_date_iso="2026-08-08",
    )
    available = {fp: 2}
    assert drafts.claim_fingerprint(available, fp) is True
    assert available[fp] == 1
    assert drafts.claim_fingerprint(available, fp) is True
    assert fp not in available
    assert drafts.claim_fingerprint(available, fp) is False


def test_ensure_draft_client_row_ids_migrates_v1():
    payload = {
        "schema_version": 1,
        "new_rows": [
            {"cells": {"3": "DIESEL"}, "pending_meta": {"currency": "TZS"}},
            {
                "cells": {"3": "PARKING"},
                "pending_meta": {"client_row_id": "already-here"},
            },
            {
                "cells": {"3": "MID"},
                "pending_meta": {
                    "client_row_id": "in-flight-id",
                    "sync_status": "IN_FLIGHT",
                },
            },
        ],
        "dirty_saved": [],
    }
    drafts.ensure_draft_client_row_ids(payload)
    assert payload["schema_version"] == drafts.SCHEMA_VERSION
    assert drafts.entry_client_row_id(payload["new_rows"][0])
    assert payload["new_rows"][0]["client_row_id"] == payload["new_rows"][0]["pending_meta"]["client_row_id"]
    assert drafts.entry_client_row_id(payload["new_rows"][1]) == "already-here"
    assert payload["new_rows"][1]["client_row_id"] == "already-here"
    # Mid-flight crash → PENDING so reconnect can retry.
    assert drafts.entry_sync_status(payload["new_rows"][2]) == drafts.SYNC_PENDING
    # Rows without sync_status stay ordinary (manual Save).
    assert "sync_status" not in (payload["new_rows"][0].get("pending_meta") or {})


def test_load_register_draft_stamps_missing_client_row_ids(draft_dir):
    user_id = ObjectId()
    d = date(2026, 8, 8)
    path = drafts.draft_path(user_id, d, merged=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version":1,"register_date":"2026-08-08","merged":false,'
        '"user_id":"%s","username":"aisha","edit_mode":false,"dirty_saved":[],'
        '"new_rows":[{"cells":{"3":"DIESEL"},"pending_meta":null}],'
        '"updated_at":"2026-08-08T12:00:00"}' % user_id,
        encoding="utf-8",
    )
    loaded = drafts.load_register_draft(user_id, d, merged=False)
    assert loaded is not None
    assert loaded["schema_version"] == drafts.SCHEMA_VERSION
    assert drafts.entry_client_row_id(loaded["new_rows"][0])
