"""Tests for two-phase attachments and operation_events audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from bson import ObjectId


@pytest.fixture()
def attach_dirs(tmp_path, monkeypatch):
    from tahmeed.services import attachment_service as att

    root = tmp_path / "attachments"
    monkeypatch.setattr(att, "attachments_root", lambda: root)
    return root


@pytest.mark.asyncio
async def test_add_attachment_uploads_via_api(attach_dirs, tmp_path, monkeypatch):
    from tahmeed.services import attachment_service as att
    import tahmeed.services.api_client as api_mod

    src = tmp_path / "receipt.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    tx_id = ObjectId()
    calls: list[dict] = []

    async def request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "files": kwargs.get("files")})
        assert method == "POST"
        assert path == f"v1/transactions/{tx_id}/attachments"
        assert "file" in (kwargs.get("files") or {})
        return {
            "id": "up1",
            "filename": "receipt.pdf",
            "stored_name": "up1_receipt.pdf",
            "size": len(b"%PDF-1.4 test"),
        }

    monkeypatch.setattr(api_mod.api_client, "request", request)
    meta = await att.add_attachment(tx_id, src, actor_id=ObjectId())
    assert meta["id"] == "up1"
    cached = att.tx_dir(tx_id) / "up1_receipt.pdf"
    assert cached.is_file()
    assert cached.read_bytes().startswith(b"%PDF")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_add_attachment_maps_404(attach_dirs, tmp_path, monkeypatch):
    from tahmeed.services import attachment_service as att
    from tahmeed.services.api_client import ApiError
    import tahmeed.services.api_client as api_mod

    src = tmp_path / "scan.png"
    src.write_bytes(b"pngdata")
    tx_id = ObjectId()

    async def request(*_args, **_kwargs):
        raise ApiError("missing", status_code=404, code="not_found")

    monkeypatch.setattr(api_mod.api_client, "request", request)
    with pytest.raises(ValueError, match="Transaction not found"):
        await att.add_attachment(tx_id, src)


@pytest.mark.asyncio
async def test_remove_attachment_via_api(attach_dirs, monkeypatch):
    from tahmeed.services import attachment_service as att
    import tahmeed.services.api_client as api_mod

    tx_id = ObjectId()
    att_id = "rm1"
    cached = att.tx_dir(tx_id)
    cached.mkdir(parents=True)
    (cached / f"{att_id}_scan.png").write_bytes(b"PNG")
    deleted: list[str] = []

    async def request(method, path, **kwargs):
        deleted.append(f"{method} {path}")
        return None

    monkeypatch.setattr(api_mod.api_client, "request", request)
    ok = await att.remove_attachment(tx_id, att_id, actor_id=ObjectId())
    assert ok is True
    assert deleted == [f"DELETE v1/transactions/{tx_id}/attachments/{att_id}"]
    assert not (cached / f"{att_id}_scan.png").exists()


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
async def test_cleanup_attachment_orphans_calls_api(attach_dirs, monkeypatch):
    from tahmeed.services import attachment_service as att
    import tahmeed.services.api_client as api_mod

    calls: list[dict] = []

    async def request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "params": kwargs.get("params")})
        return {
            "staging_removed": 2,
            "orphan_files_removed": 3,
            "empty_dirs_removed": 1,
        }

    monkeypatch.setattr(api_mod.api_client, "request", request)
    result = await att.cleanup_attachment_orphans(max_age_hours=12.0, dry_run=True)
    assert calls == [
        {
            "method": "POST",
            "path": "v1/transactions/attachments/cleanup-orphans",
            "params": {"max_age_hours": 12.0, "dry_run": True},
        }
    ]
    assert result["orphan_files_removed"] == 3
    assert result["empty_dirs_removed"] == 1
    assert result["server_skipped"] is False


@pytest.mark.asyncio
async def test_cleanup_skips_server_on_403(attach_dirs, monkeypatch):
    from tahmeed.services import attachment_service as att
    from tahmeed.services.api_client import ApiError
    import tahmeed.services.api_client as api_mod

    async def request(*_args, **_kwargs):
        raise ApiError("forbidden", status_code=403, code="forbidden")

    monkeypatch.setattr(api_mod.api_client, "request", request)
    result = await att.cleanup_attachment_orphans()
    assert result["server_skipped"] is True
    assert result["orphan_files_removed"] == 0


@pytest.mark.asyncio
async def test_get_attachments_uses_api(monkeypatch):
    from tahmeed.services import attachment_service as att
    import tahmeed.services.api_client as api_mod

    tx_id = ObjectId()
    calls: list[tuple] = []

    async def request(method, path, **kwargs):
        calls.append((method, path))
        return {
            "items": [
                {
                    "id": "a1",
                    "filename": "r.pdf",
                    "stored_name": "a1_r.pdf",
                    "size": 3,
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(api_mod.api_client, "request", request)
    items = await att.get_attachments(tx_id)
    assert len(items) == 1
    assert items[0]["id"] == "a1"
    assert calls == [("GET", f"v1/transactions/{tx_id}/attachments")]


@pytest.mark.asyncio
async def test_ensure_attachment_local_downloads(attach_dirs, monkeypatch):
    from tahmeed.services import attachment_service as att
    import tahmeed.services.api_client as api_mod

    tx_id = ObjectId()
    meta = {
        "id": "dl1",
        "filename": "scan.png",
        "stored_name": "dl1_scan.png",
        "size": 4,
    }

    async def request_bytes(method, path, **kwargs):
        assert method == "GET"
        assert path.endswith("/attachments/dl1/file")
        return b"PNG!"

    monkeypatch.setattr(api_mod.api_client, "request_bytes", request_bytes)
    path = await att.ensure_attachment_local(tx_id, meta)
    assert path is not None
    assert path.is_file()
    assert path.read_bytes() == b"PNG!"
    # Second call should hit cache (no second download needed)
    path2 = await att.ensure_attachment_local(tx_id, meta)
    assert path2 == path


@pytest.mark.asyncio
async def test_record_event_inserts_and_swallows_errors(monkeypatch):
    from tahmeed.services import audit_service as audit

    event_id = ObjectId()

    async def ok_request(method, path, **kwargs):
        assert method == "POST"
        assert path == "v1/audit/events"
        return {"id": str(event_id)}

    monkeypatch.setattr(audit.api_client, "request", ok_request)
    eid = await audit.record_event(
        "txn.approve",
        actor_id=ObjectId(),
        entity_type="transaction",
        entity_ids=[ObjectId()],
    )
    assert eid == event_id

    async def boom_request(*_args, **_kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(audit.api_client, "request", boom_request)
    assert await audit.record_event("txn.save") is None
