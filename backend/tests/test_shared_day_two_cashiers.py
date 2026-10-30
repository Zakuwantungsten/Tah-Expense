"""Two cashiers share one register day: park, view, fix, map, delete, submit."""

from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient

from app.dependencies import current_user
from tests.test_badge_counters import FakeCounters, _app
from tests.test_transactions_api import FakeTransactions, _cashier

DAY = "2026-10-22"


def _row(description: str, client_row_id: str, status: str | None = None) -> dict:
    row = {
        "date": f"{DAY}T00:00:00",
        "description": description,
        "amount": 1000,
        "currency": "TZS",
        "truck_number": "BAD TRUCK",
        "client_row_id": client_row_id,
    }
    if status:
        row["register_status"] = status
    return row


def _bucket(counters: FakeCounters, cashier: dict) -> dict:
    row = (counters.doc.get("cashiers") or {}).get(str(cashier["_id"])) or {}
    return {"drafts": int(row.get("drafts") or 0), "parked": int(row.get("parked") or 0)}


def test_second_cashier_fixes_deletes_and_submits_first_cashiers_parked_rows() -> None:
    a, b = _cashier(), _cashier()
    collection = FakeTransactions()
    counters = FakeCounters()
    app = _app(collection, counters)
    acting = {"user": a}
    app.dependency_overrides[current_user] = lambda: acting["user"]

    def browse_day(client: TestClient) -> dict:
        resp = client.get(
            f"/v1/transactions/daily-summaries?date_from={DAY}&date_to={DAY}"
        )
        return resp.json()["items"][0]

    with TestClient(app) as client:
        # Cashier A enters the day, parks two wrong-truck rows, and saves.
        created = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    _row("A FUEL", "a-1"),
                    _row("A OIL", "a-2", "parked"),
                    _row("A TYRE", "a-3", "parked"),
                ]
            },
        )
        assert created.json()["created"] == 3
        assert _bucket(counters, a) == {"drafts": 1, "parked": 2}

        # Browse: parked rows are reported as parked, not submitted.
        day = browse_day(client)
        assert (day["draft_count"], day["parked_count"], day["submitted_count"]) == (
            1,
            2,
            0,
        )

        # Cashier B opens the day: Parked shows A's rows, My stays B's own.
        acting["user"] = b
        shared = client.get(f"/v1/transactions/by-date?date={DAY}&include_parked=true")
        own = client.get(f"/v1/transactions/by-date?date={DAY}")
        by_desc = {d["description"]: d for d in shared.json()["items"]}
        assert set(by_desc) == {"A OIL", "A TYRE"}
        assert own.json()["items"] == []

        # B fixes the truck on A OIL and unparks it back to A's drafts.
        fixed = client.post(
            "/v1/transactions/bulk-patch",
            json={
                "items": [
                    {
                        "id": by_desc["A OIL"]["_id"],
                        "values": {"truck_number": "T 123 ABC", "register_status": "draft"},
                    }
                ]
            },
        )
        assert fixed.status_code == 200
        assert _bucket(counters, a) == {"drafts": 2, "parked": 1}

        # B deletes A TYRE; it shows in both B's and A's Trash.
        trashed = client.post(
            f"/v1/transactions/{by_desc['A TYRE']['_id']}/request-delete", json={}
        )
        assert trashed.json() == {"result": "trashed"}
        b_trash = [d["description"] for d in client.get("/v1/transactions/trash").json()["items"]]
        acting["user"] = a
        a_trash = [d["description"] for d in client.get("/v1/transactions/trash").json()["items"]]
        assert b_trash == a_trash == ["A TYRE"]
        assert _bucket(counters, a) == {"drafts": 2, "parked": 0}

        # B submits the day: A's drafts leave A's badge, B's stays untouched.
        acting["user"] = b
        submitted = client.post("/v1/transactions/submit-day", json={"date": DAY})
        assert submitted.json()["updated"] == 2
        day = browse_day(client)

    assert _bucket(counters, a) == {"drafts": 0, "parked": 0}
    assert _bucket(counters, b) == {"drafts": 0, "parked": 0}
    assert counters.doc["drafts"] == 0
    assert counters.doc["parked"] == 0
    assert counters.doc["verify"] == 2
    assert (day["entries_count"], day["draft_count"], day["parked_count"]) == (2, 0, 0)
    assert day["submitted_count"] == 2
    oil = next(d for d in collection.documents if d["description"] == "A OIL")
    assert oil["cashier_id"] == a["_id"]
    assert oil["truck_number"] == "T 123 ABC"
    assert oil["register_status"] == "submitted"


def test_second_cashiers_day_issues_list_and_map_first_cashiers_parked_rows() -> None:
    a, b = _cashier(), _cashier()
    collection = FakeTransactions()
    app = _app(collection, FakeCounters())
    acting = {"user": a}
    app.dependency_overrides[current_user] = lambda: acting["user"]

    with TestClient(app) as client:
        # Cashier A saves one unmapped draft row and one unmapped parked row.
        created = client.post(
            "/v1/transactions/bulk-create",
            json={"items": [_row("A FUEL", "a-1"), _row("A TOLL", "a-2", "parked")]},
        )
        assert created.json()["created"] == 2

        # Cashier B's Day Issues: the day is listed and only A's parked row loads.
        acting["user"] = b
        days = client.get("/v1/transactions/open-day-summaries?include_parked=true")
        assert [d["date"] for d in days.json()["items"]] == [DAY]
        rows = client.get(f"/v1/transactions/by-date?date={DAY}&include_parked=true")
        assert [d["description"] for d in rows.json()["items"]] == ["A TOLL"]

        # B maps both descriptions: only A's parked row is in B's scope.
        mapped = client.post(
            "/v1/transactions/apply-description-mapping",
            json={
                "description_keys": ["A TOLL", "A FUEL"],
                "payee": "TANROADS",
                "category_name": "TOLLS",
                "category_id": str(ObjectId()),
                "dates": [DAY],
            },
        )
        assert mapped.json()["updated"] == 1

    docs = {d["description"]: d for d in collection.documents}
    assert docs["A TOLL"]["payee"] == "TANROADS"
    assert docs["A TOLL"]["item"] == "TOLLS"
    assert docs["A TOLL"]["cashier_id"] == a["_id"]
    assert docs["A TOLL"]["register_status"] == "parked"
    assert docs["A FUEL"].get("payee", "") == ""
