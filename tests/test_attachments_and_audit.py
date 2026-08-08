"""Tests for two-phase attachments and operation_events audit."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId


@pytest.fixture()
def attach_dirs(tmp_path, monkeypatch):
    from tahmeed.services import attachment_service as att

    root = tmp_path / "attachments"
    monkeypatch.setattr(att, "attachments_root", lambda: root)
    return root


@pytest.mark.asyncio
async def test_add_attachment_two_phase(attach_dirs, tmp_path):
    from tahmeed.services import attachment_service as att

    src = tmp_path / "receipt.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    tx_id = ObjectId()

    coll = MagicMock()
    coll.update_one = AsyncMock(
        return_value=SimpleNamespace(matched_count=1, modified_count=1)
    )
    db = SimpleNamespace(transactions=coll)

    with (
        patch.object(att, "get_db", return_value=db),
        patch(
            "tahmeed.services.audit_service.record_event",
            new=AsyncMock(return_value=ObjectId()),
        ),
    ):
        meta = await att.add_attachment(tx_id, src, actor_id=ObjectId())

    final = att.tx_dir(tx_id) / meta["stored_name"]
    assert final.is_file()
    assert final.read_bytes().startswith(b"%PDF")
    staging = list(att.staging_root().glob("*.part"))
    assert staging == []
    coll.update_one.assert_awaited()


@pytest.mark.asyncio
async def test_add_attachment_rolls_back_staging_when_tx_missing(attach_dirs, tmp_path):
    from tahmeed.services import attachment_service as att

    src = tmp_path / "scan.png"
    src.write_bytes(b"pngdata")
    tx_id = ObjectId()

    coll = MagicMock()
    coll.update_one = AsyncMock(
        return_value=SimpleNamespace(matched_count=0, modified_count=0)
    )
    db = SimpleNamespace(transactions=coll)

    with patch.object(att, "get_db", return_value=db):
        with pytest.raises(ValueError, match="Transaction not found"):
            await att.add_attachment(tx_id, src)

    assert list(att.staging_root().glob("*.part")) == []
    assert not (att.tx_dir(tx_id)).exists() or not any(att.tx_dir(tx_id).iterdir())


def test_purge_stale_staging(attach_dirs):
    from tahmeed.services import attachment_service as att
    import os
    import time

    stage = att.staging_root()
    stage.mkdir(parents=True, exist_ok=True)
    stale = stage / "old.part"
    stale.write_text("x", encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))
    fresh = stage / "new.part"
    fresh.write_text("y", encoding="utf-8")

    removed = att.purge_stale_staging(max_age_hours=24.0)
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


@pytest.mark.asyncio
async def test_record_event_inserts_and_swallows_errors():
    from tahmeed.services import audit_service as audit

    coll = MagicMock()
    coll.insert_one = AsyncMock(
        return_value=SimpleNamespace(inserted_id=ObjectId())
    )
    db = SimpleNamespace(operation_events=coll)

    with patch.object(audit, "get_db", return_value=db):
        eid = await audit.record_event(
            "txn.approve",
            actor_id=ObjectId(),
            entity_type="transaction",
            entity_ids=[ObjectId()],
        )
    assert eid is not None
    coll.insert_one.assert_awaited()

    coll.insert_one = AsyncMock(side_effect=RuntimeError("db down"))
    with patch.object(audit, "get_db", return_value=db):
        assert await audit.record_event("txn.save") is None
