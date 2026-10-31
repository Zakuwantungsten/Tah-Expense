"""Stored badge counters: one seed, then deltas. No request-path scans."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.accountant_ops import pending_inbox_count_query
from app.badge_counters import repair_badge_counters
from app.dependencies import current_user
from app.main import create_app
from app.transaction_ops import DRAFT_BASE_MATCH, PARKED_BASE_MATCH
from tests.test_transactions_api import (
    FakeMappings,
    FakeOperationEvents,
    FakeTransactions,
    FakeUsers,
    _cashier,
    settings,
)


class FakeCounters:
    def __init__(self) -> None:
        self.doc: dict | None = None

    async def find_one(self, query: dict) -> dict | None:
        if self.doc is None or self.doc.get("_id") != query.get("_id"):
            return None
        return _copy(self.doc)

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        if self.doc is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            self.doc = {"_id": query.get("_id", "badges")}
        for path, amount in (update.get("$inc") or {}).items():
            _inc_path(self.doc, path, int(amount))
        for key, value in (update.get("$set") or {}).items():
            if "." in key:
                _set_path(self.doc, key, value)
            else:
                self.doc[key] = value
        return SimpleNamespace(modified_count=1)


def _copy(doc: dict) -> dict:
    out = dict(doc)
    cashiers = out.get("cashiers")
    if isinstance(cashiers, dict):
        out["cashiers"] = {key: dict(value) for key, value in cashiers.items()}
    return out


def _inc_path(doc: dict, path: str, amount: int) -> None:
    node = doc
    parts = path.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    leaf = parts[-1]
    node[leaf] = int(node.get(leaf) or 0) + amount


def _set_path(doc: dict, path: str, value) -> None:
    node = doc
    parts = path.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _open(status: str, **extra) -> dict:
    return {
        "_id": ObjectId(),
        "cashier_id": extra.pop("cashier_id", ObjectId()),
        "register_status": status,
        "verified": False,
        "rejected": False,
        "discarded": False,
        "deletion_requested": False,
        "trashed": False,
        "date": datetime(2026, 10, 22, 9, 0, 0),
        **extra,
    }


def _app(collection: FakeTransactions, counters: FakeCounters):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            description_mappings=FakeMappings(),
            operation_events=FakeOperationEvents(),
            users=FakeUsers(),
            system_counters=counters,
        )
    )
    return create_app(settings=settings(), database=database)


@pytest.mark.asyncio
async def test_seed_matches_today_count_functions(caplog) -> None:
    cashier = ObjectId()
    other = ObjectId()
    rows = [
        _open("draft", cashier_id=cashier),
        _open("draft", cashier_id=cashier),
        _open("parked", cashier_id=other),
        _open("submitted", cashier_id=cashier),
        _open("draft", cashier_id=cashier, trashed=True),
        {
            "_id": ObjectId(),
            "cashier_id": cashier,
            "register_status": "submitted",
            "verified": False,
            "rejected": False,
            "trashed": False,
            "deletion_requested": True,
        },
        {
            "_id": ObjectId(),
            "cashier_id": cashier,
            "register_status": "submitted",
            "verified": True,
            "trashed": True,
            "restore_requested": True,
        },
    ]
    collection = FakeTransactions(rows)
    counters = FakeCounters()
    db = SimpleNamespace(transactions=collection, system_counters=counters)

    await repair_badge_counters(db)

    assert counters.doc["drafts"] == await collection.count_documents(dict(DRAFT_BASE_MATCH))
    assert counters.doc["parked"] == await collection.count_documents(dict(PARKED_BASE_MATCH))
    assert counters.doc["verify"] == await collection.count_documents(pending_inbox_count_query())
    assert counters.doc["deletion_requests"] == 1
    assert counters.doc["restore_requests"] == 1
    assert counters.doc["drafts"] == 2
    assert counters.doc["parked"] == 1
    assert counters.doc["verify"] == 1
    mine = counters.doc["cashiers"][str(cashier)]
    assert mine["drafts"] == 2
    assert counters.doc["cashiers"][str(other)]["parked"] == 1

    counters.doc["verify"] = 99
    with caplog.at_level("WARNING"):
        await repair_badge_counters(db)
    assert counters.doc["verify"] == 1
    assert "drifted" in caplog.text


def test_create_and_submit_adjust_stored_badges_without_a_second_insert() -> None:
    user = _cashier()
    collection = FakeTransactions()
    _watch_counts(collection)
    counters = FakeCounters()
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user
    payload = {
        "date": "2026-10-22T00:00:00",
        "description": "Diesel",
        "amount": 10,
        "currency": "TZS",
        "client_row_id": "row-1",
    }

    with TestClient(app) as client:
        scans_before = _count_calls(collection)
        first = client.post("/v1/transactions", json=payload)
        second = client.post("/v1/transactions", json=payload)
        parked = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    {
                        **payload,
                        "client_row_id": "row-parked",
                        "register_status": "parked",
                    }
                ]
            },
        )
        parked_again = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    {
                        **payload,
                        "client_row_id": "row-parked",
                        "register_status": "parked",
                    }
                ]
            },
        )
        submitted = client.post(
            "/v1/transactions/submit-day",
            json={"date": "2026-10-22"},
        )
        counts = client.get("/v1/transactions/drafts/count?all_cashiers=true")
        scans_after = _count_calls(collection)

    assert first.status_code == 201
    assert second.status_code == 200
    assert parked.json()["created"] == 1
    assert parked_again.json()["already_exists"] == 1
    assert submitted.json()["updated"] == 1
    assert counters.doc["drafts"] == 0
    assert counters.doc["parked"] == 1
    assert counters.doc["verify"] == 1
    assert counts.json()["count"] == 1
    assert scans_after == scans_before


def test_resubmit_and_patch_unreject_move_verify_badge() -> None:
    user = _cashier()
    first = _open("submitted", cashier_id=user["_id"], rejected=True)
    second = _open("submitted", cashier_id=user["_id"], rejected=True)
    accountant = {**_cashier(), "role": "accountant"}
    collection = FakeTransactions([first, second])
    counters = FakeCounters()
    app = _app(collection, counters)
    acting = {"user": user}
    app.dependency_overrides[current_user] = lambda: acting["user"]

    with TestClient(app) as client:
        resubmit = client.post(
            "/v1/transactions/cashier/resubmit",
            json={
                "cashier_id": str(user["_id"]),
                "updates_by_id": {str(first["_id"]): {"description": "FIXED"}},
            },
        )
        assert resubmit.json() == {"updated": 1}
        assert counters.doc["verify"] == 1
        acting["user"] = accountant
        etag = client.get("/v1/accountant/notification-counts").headers["etag"]

        acting["user"] = user
        patched = client.patch(
            f"/v1/transactions/{second['_id']}",
            json={"values": {"rejected": False, "rejection_reason": None}},
        )
        assert patched.status_code == 200
        acting["user"] = accountant
        again = client.get(
            "/v1/accountant/notification-counts", headers={"If-None-Match": etag}
        )

    assert counters.doc["verify"] == 2
    assert again.status_code == 200
    assert again.json() == {"verify": 2}


def test_bulk_patch_park_moves_draft_to_parked() -> None:
    user = _cashier()
    row_id = ObjectId()
    collection = FakeTransactions(
        [
            _open("draft", cashier_id=user["_id"], _id=row_id),
        ]
    )
    counters = FakeCounters()
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/bulk-patch",
            json={"items": [{"id": str(row_id), "values": {"register_status": "parked"}}]},
        )

    assert response.status_code == 200
    assert counters.doc["drafts"] == 0
    assert counters.doc["parked"] == 1
    assert counters.doc["cashiers"][str(user["_id"])]["parked"] == 1


def test_bulk_patch_ten_parked_rows_to_draft_is_one_delta() -> None:
    user = _cashier()
    rows = [_open("parked", cashier_id=user["_id"]) for _ in range(10)]
    collection = FakeTransactions(rows)
    _watch_counts(collection)
    counters = FakeCounters()
    counters.doc = {
        "_id": "badges",
        "drafts": 0,
        "parked": 10,
        "verify": 0,
        "cashiers": {str(user["_id"]): {"drafts": 0, "parked": 10}},
    }
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        scans_before = _count_calls(collection)
        response = client.post(
            "/v1/transactions/bulk-patch",
            json={
                "items": [
                    {"id": str(row["_id"]), "values": {"register_status": "draft"}}
                    for row in rows
                ]
            },
        )
        scans_after = _count_calls(collection)

    assert response.status_code == 200
    assert response.json()["requested"] == 10
    assert counters.doc["parked"] == 0
    assert counters.doc["drafts"] == 10
    mine = counters.doc["cashiers"][str(user["_id"])]
    assert mine["parked"] == 0
    assert mine["drafts"] == 10
    assert scans_after == scans_before


@pytest.mark.asyncio
async def test_overlapping_bulk_create_and_submit_keep_both_deltas() -> None:
    import httpx

    user = _cashier()
    day = datetime(2026, 10, 22, 9, 0, 0)
    rows = [_open("draft", cashier_id=user["_id"], date=day) for _ in range(3)]
    collection = FakeTransactions(rows)
    counters = FakeCounters()
    counters.doc = {
        "_id": "badges",
        "drafts": 3,
        "parked": 0,
        "verify": 0,
        "cashiers": {str(user["_id"]): {"drafts": 3, "parked": 0}},
    }
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created, submitted = await asyncio.gather(
                client.post(
                    "/v1/transactions/bulk-create",
                    json={
                        "items": [
                            {
                                "date": "2026-10-23T00:00:00",
                                "description": "Diesel",
                                "amount": 10,
                                "currency": "TZS",
                                "client_row_id": f"other-{index}",
                            }
                            for index in range(2)
                        ]
                    },
                ),
                client.post(
                    "/v1/transactions/submit-day",
                    json={"date": "2026-10-22"},
                ),
            )

    assert created.status_code == 200
    assert created.json()["created"] == 2
    assert submitted.status_code == 200
    assert submitted.json()["updated"] == 3
    assert counters.doc["drafts"] == 2
    assert counters.doc["verify"] == 3
    assert counters.doc["cashiers"][str(user["_id"])]["drafts"] == 2


def test_submit_day_moves_each_owners_draft_badge() -> None:
    submitter = _cashier()
    other = ObjectId()
    day = datetime(2026, 10, 22, 9, 0, 0)
    rows = [
        _open("draft", cashier_id=submitter["_id"], date=day),
        _open("draft", cashier_id=other, date=day),
        _open("draft", cashier_id=other, date=day),
        _open("parked", cashier_id=other, date=day),
    ]
    collection = FakeTransactions(rows)
    counters = FakeCounters()
    counters.doc = {
        "_id": "badges",
        "drafts": 3,
        "parked": 1,
        "verify": 0,
        "cashiers": {
            str(submitter["_id"]): {"drafts": 1, "parked": 0},
            str(other): {"drafts": 2, "parked": 1},
        },
    }
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: submitter

    with TestClient(app) as client:
        resp = client.post("/v1/transactions/submit-day", json={"date": "2026-10-22"})

    assert resp.json()["updated"] == 3
    assert counters.doc["drafts"] == 0
    assert counters.doc["parked"] == 1
    assert counters.doc["verify"] == 3
    assert counters.doc["cashiers"][str(submitter["_id"])] == {"drafts": 0, "parked": 0}
    assert counters.doc["cashiers"][str(other)] == {"drafts": 0, "parked": 1}


def test_submit_day_delta_matches_modified_count() -> None:
    user = _cashier()
    day = datetime(2026, 10, 22, 9, 0, 0)
    rows = [
        _open("draft", cashier_id=user["_id"], date=day) for _ in range(4)
    ]
    rows.append(_open("submitted", cashier_id=user["_id"], date=day, verified=False))
    collection = FakeTransactions(rows)
    _watch_counts(collection)
    counters = FakeCounters()
    counters.doc = {
        "_id": "badges",
        "drafts": 4,
        "parked": 0,
        "verify": 1,
        "cashiers": {str(user["_id"]): {"drafts": 4, "parked": 0}},
    }
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        scans_before = _count_calls(collection)
        first = client.post("/v1/transactions/submit-day", json={"date": "2026-10-22"})
        second = client.post("/v1/transactions/submit-day", json={"date": "2026-10-22"})
        scans_after = _count_calls(collection)

    assert first.status_code == 200
    assert first.json()["updated"] == 4
    assert counters.doc["drafts"] == 0
    assert counters.doc["verify"] == 5
    assert counters.doc["cashiers"][str(user["_id"])]["drafts"] == 0
    assert second.status_code == 200
    assert second.json()["updated"] == 0
    assert counters.doc["drafts"] == 0
    assert counters.doc["verify"] == 5
    assert scans_after == scans_before


def test_bulk_approve_page_drops_verify_by_the_page_count() -> None:
    user = {"_id": ObjectId(), "username": "acct", "role": "accountant"}
    rows = [
        _open(
            "submitted",
            item="Fuel",
            payee="SHELL",
            verified=False,
        )
        for _ in range(10)
    ]
    collection = FakeTransactions(rows)
    _watch_counts(collection)
    counters = FakeCounters()
    counters.doc = {
        "_id": "badges",
        "drafts": 0,
        "parked": 0,
        "verify": 10,
        "cashiers": {},
    }
    app = _app(collection, counters)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        scans_before = _count_calls(collection)
        response = client.post(
            "/v1/accountant/transactions/bulk-approve",
            json={"ids": [str(row["_id"]) for row in rows]},
        )
        scans_after = _count_calls(collection)

    assert response.status_code == 200
    assert response.json()["updated"] == 10
    assert counters.doc["verify"] == 0
    assert scans_after == scans_before


def _watch_counts(collection: FakeTransactions) -> None:
    original = collection.count_documents

    async def _counting(query: dict) -> int:
        collection.count_calls = getattr(collection, "count_calls", 0) + 1
        return await original(query)

    collection.count_documents = _counting  # type: ignore[method-assign]


def _count_calls(collection: FakeTransactions) -> int:
    return getattr(collection, "count_calls", 0)
