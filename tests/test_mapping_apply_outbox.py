"""Mapping apply outbox — enqueue, drain, idempotent bulk apply."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from bson import ObjectId

from tahmeed.services import mapping_apply_outbox as outbox


@pytest.fixture(autouse=True)
def _reset_outbox():
    outbox.reset_outbox_for_tests()
    yield
    outbox.reset_outbox_for_tests()


@pytest.mark.asyncio
async def test_drain_calls_bulk_apply_once(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_bulk(keys, **kwargs):
        calls.append({"keys": list(keys), **kwargs})
        return len(keys)

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.apply_description_mapping_bulk",
        fake_bulk,
    )
    # Avoid Qt scheduling; drain directly.
    monkeypatch.setattr(outbox, "_schedule_drain", lambda *a, **k: None)

    cat_id = ObjectId()
    job = outbox.enqueue_mapping_apply(
        description_keys=["MILEAGE", "mileage"],
        payee="MILEAGE",
        item="DIRECT COSTS:MILEAGE EXPENSE",
        category_id=cat_id,
        dates=[date(2026, 10, 13)],
        cashier_id=None,
    )
    assert job.status == "pending"
    assert job.description_keys == ["MILEAGE"]

    await outbox.drain_mapping_apply_outbox()
    assert job.status == "done"
    assert len(calls) == 1
    assert calls[0]["keys"] == ["MILEAGE"]
    assert calls[0]["payee"] == "MILEAGE"
    assert calls[0]["category_name"] == "DIRECT COSTS:MILEAGE EXPENSE"
    assert calls[0]["category_id"] == cat_id
    assert calls[0]["dates"] == [date(2026, 10, 13)]
    assert calls[0]["client_job_id"] == job.job_id


@pytest.mark.asyncio
async def test_enqueue_dedupes_identical_pending_job(monkeypatch) -> None:
    monkeypatch.setattr(outbox, "_schedule_drain", lambda *a, **k: None)
    cat_id = ObjectId()
    first = outbox.enqueue_mapping_apply(
        description_keys=["LATRA FEE"],
        payee="CITY",
        item="Parking",
        category_id=cat_id,
        dates=[date(2026, 10, 13)],
    )
    second = outbox.enqueue_mapping_apply(
        description_keys=["latra fee"],
        payee="city",
        item="Parking",
        category_id=cat_id,
        dates=[date(2026, 10, 13)],
    )
    assert first is second
    assert len(outbox.pending_jobs()) == 1


@pytest.mark.asyncio
async def test_drain_retries_then_fails(monkeypatch) -> None:
    monkeypatch.setattr(outbox, "_schedule_drain", lambda *a, **k: None)
    monkeypatch.setattr(outbox, "_MAX_ATTEMPTS", 2)

    async def boom(*_a, **_k):
        raise ConnectionError("offline")

    failures: list = []
    outbox.set_failure_handler(lambda job, exc: failures.append((job.job_id, str(exc))))
    monkeypatch.setattr(
        "tahmeed.services.cashier_service.apply_description_mapping_bulk",
        boom,
    )

    job = outbox.enqueue_mapping_apply(
        description_keys=["X"],
        payee="P",
        item="I",
        category_id=ObjectId(),
    )
    await outbox.drain_mapping_apply_outbox()
    assert job.status == "pending"
    assert job.attempts == 1

    await outbox.drain_mapping_apply_outbox()
    assert job.status == "failed"
    assert job.attempts == 2
    assert failures and failures[0][0] == job.job_id


@pytest.mark.asyncio
async def test_save_mappings_bulk_one_request(monkeypatch) -> None:
    from tahmeed.services import description_mapping_service as dms

    dms.invalidate_description_mapping_cache()
    # Warm empty cache so _cache_put works.
    dms._maps_by_key = {}
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        assert method == "POST"
        assert path == "v1/description-mappings/bulk"
        items = kwargs["json"]["mappings"]
        return {
            "items": [
                {
                    "_id": str(ObjectId()),
                    "description": m["description"],
                    "description_key": " ".join(m["description"].upper().split()),
                    "category_id": m["category_id"],
                    "category_name": m["category_name"],
                    "payee": m["payee"],
                }
                for m in items
            ],
            "upserted_count": len(items),
        }

    monkeypatch.setattr(dms.api_client, "request", fake_request)
    cat = ObjectId()
    saved = await dms.save_mappings_bulk(
        [
            ("mileage", cat, "Mileage", "mileage"),
            ("LATRA FEE", cat, "Parking", "CITY"),
            ("mileage", cat, "Mileage", "mileage"),
        ]
    )
    assert len(calls) == 1
    assert len(saved) == 2
    assert dms.lookup_mapping_sync("MILEAGE") is not None
    dms.invalidate_description_mapping_cache()


@pytest.mark.asyncio
async def test_apply_description_mapping_bulk_client(monkeypatch) -> None:
    from tahmeed.services import cashier_service as cs

    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {"updated": 48, "matched": 48}

    monkeypatch.setattr(cs.api_client, "request", fake_request)
    n = await cs.apply_description_mapping_bulk(
        ["MILEAGE"],
        payee="MILEAGE",
        category_name="DIRECT COSTS:MILEAGE EXPENSE",
        category_id=ObjectId(),
        dates=[date(2026, 10, 13)],
        cashier_id=None,
        client_job_id="abc",
    )
    assert n == 48
    assert calls[0]["path"] == "v1/transactions/apply-description-mapping"
    assert calls[0]["json"]["description_keys"] == ["MILEAGE"]
    assert calls[0]["json"]["client_job_id"] == "abc"
    assert calls[0]["json"].get("clear") is False


@pytest.mark.asyncio
async def test_apply_description_mapping_bulk_clear(monkeypatch) -> None:
    from tahmeed.services import cashier_service as cs

    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {"updated": 3, "matched": 3}

    monkeypatch.setattr(cs.api_client, "request", fake_request)
    n = await cs.apply_description_mapping_bulk(
        ["MILEAGE"],
        dates=[date(2026, 10, 13)],
        clear=True,
    )
    assert n == 3
    body = calls[0]["json"]
    assert body["clear"] is True
    assert body["category_name"] == ""
    assert "category_id" not in body


def test_cancel_pending_jobs_for_keys(monkeypatch) -> None:
    monkeypatch.setattr(outbox, "_schedule_drain", lambda *a, **k: None)
    cat = ObjectId()
    keep = outbox.enqueue_mapping_apply(
        description_keys=["KEEP"],
        payee="A",
        item="I",
        category_id=cat,
    )
    drop = outbox.enqueue_mapping_apply(
        description_keys=["UNDO ME"],
        payee="B",
        item="I",
        category_id=cat,
    )
    n = outbox.cancel_pending_jobs_for_keys(["undo me"])
    assert n == 1
    assert drop.status == "cancelled"
    assert keep.status == "pending"
    assert keep in outbox.pending_jobs()
    assert drop not in outbox.pending_jobs()
