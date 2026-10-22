from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected:
                exists = key in doc
                if bool(expected["$exists"]) != exists:
                    return False
            if "$regex" in expected:
                import re

                pattern = expected["$regex"]
                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(pattern, str(actual), flags) is None:
                    return False
                continue
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)
        self._skip = 0
        self._limit: int | None = None

    def sort(self, *_args: object, **_kwargs: object) -> "FakeCursor":
        return self

    def skip(self, value: int) -> "FakeCursor":
        self._skip = value
        return self

    def limit(self, value: int) -> "FakeCursor":
        self._limit = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = list(self.documents)[self._skip :]
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is None:
            return docs
        return docs[:length]


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(doc) for doc in self.documents if _matches(doc, query)]
        )

    async def distinct(self, field: str, query: dict | None = None) -> list:
        query = query or {}
        values: list = []
        for doc in self.documents:
            if _matches(doc, query):
                value = doc.get(field)
                if value and value not in values:
                    values.append(value)
        return values

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))


class FakeUsers:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        ids = set()
        if "_id" in query and isinstance(query["_id"], dict) and "$in" in query["_id"]:
            ids = set(query["_id"]["$in"])
        docs = [dict(d) for d in self.documents if not ids or d.get("_id") in ids]
        return FakeCursor(docs)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(transactions: FakeTransactions, users: FakeUsers | None = None):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            users=users or FakeUsers(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def _submitted(**kwargs) -> dict:
    doc = {
        "_id": ObjectId(),
        "verified": False,
        "rejected": False,
        "register_status": "submitted",
        "date": datetime(2026, 6, 1),
        "day_order": 1,
        "created_at": datetime(2026, 6, 1, 8, 0),
        "truck_number": "T100 ABC",
        "description": "Diesel fill",
        "item": "Diesel",
        "category_name": "Fuel",
        "amount": 100.0,
        "cashier_id": ObjectId(),
    }
    doc.update(kwargs)
    return doc


def test_inbox_first_page_does_not_count_documents() -> None:
    collection = FakeTransactions([_submitted(description="On the page")])
    calls = {"n": 0}
    original = collection.count_documents

    async def _counting(query: dict) -> int:
        calls["n"] += 1
        return await original(query)

    collection.count_documents = _counting  # type: ignore[method-assign]
    app = _app(collection)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        response = client.get("/v1/accountant/inbox", params={"limit": 25, "skip": 0})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert calls["n"] == 0


def test_inbox_lists_submitted_and_excludes_drafts() -> None:
    cashier = ObjectId()
    docs = [
        _submitted(cashier_id=cashier, description="Keep me"),
        _submitted(register_status="draft", description="Draft out"),
        {
            "_id": ObjectId(),
            "verified": False,
            "rejected": True,
            "register_status": "submitted",
            "date": datetime(2026, 6, 2),
            "description": "Rejected out",
            "truck_number": "T200",
            "cashier_id": cashier,
        },
    ]
    collection = FakeTransactions(docs)
    app = _app(collection)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        response = client.get("/v1/accountant/inbox")
        count = client.get("/v1/accountant/inbox/count")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["description"] == "Keep me"
    assert count.json()["count"] == 1


def test_inbox_edited_and_badge_counts() -> None:
    cashier = ObjectId()
    new_row = _submitted(cashier_id=cashier, edited_after_verification=False)
    edited_row = _submitted(
        cashier_id=cashier,
        edited_after_verification=True,
        last_edited_at=datetime(2026, 6, 3, 12, 0),
        description="Edited row",
    )
    deletion = {
        "_id": ObjectId(),
        "deletion_requested": True,
        "verified": True,
        "date": datetime(2026, 5, 1),
    }
    collection = FakeTransactions([new_row, edited_row, deletion])
    app = _app(collection)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        edited = client.get("/v1/accountant/inbox/edited")
        edited_count = client.get("/v1/accountant/inbox/edited/count")
        badge = client.get("/v1/accountant/counts/edited")
        pending = client.get("/v1/accountant/counts/pending")
        new_only = client.get("/v1/accountant/inbox", params={"edited": "false"})

    assert edited.status_code == 200
    assert edited.json()["total"] == 1
    assert edited.json()["items"][0]["description"] == "Edited row"
    assert edited_count.json()["count"] == 1
    assert badge.json()["count"] == 1
    # pending = submitted inbox (2) + deletions (1)
    assert pending.json()["count"] == 3
    assert new_only.json()["total"] == 1


def test_inbox_meta_dates_buckets_by_day() -> None:
    docs = [
        _submitted(date=datetime(2026, 6, 1, 0, 0), description="Day1a"),
        _submitted(date=datetime(2026, 6, 1, 12, 0), description="Day1b"),
        _submitted(
            date=datetime(2026, 6, 3, 0, 0),
            edited_after_verification=True,
            description="Edited day",
        ),
        _submitted(date=datetime(2026, 5, 20, 0, 0), description="Older"),
        _submitted(
            register_status="draft",
            date=datetime(2026, 6, 4, 0, 0),
            description="Draft excluded",
        ),
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        all_days = client.get("/v1/accountant/inbox/meta/dates")
        new_days = client.get(
            "/v1/accountant/inbox/meta/dates", params={"edited": "false"}
        )
        edited_days = client.get(
            "/v1/accountant/inbox/meta/dates", params={"edited": "true"}
        )
        filtered = client.get(
            "/v1/accountant/inbox",
            params={
                "edited": "false",
                "date_from": "2026-06-01T00:00:00",
                "date_to": "2026-06-01T23:59:59",
            },
        )

    assert all_days.status_code == 200
    assert [row["date"] for row in all_days.json()["items"]] == [
        "2026-06-03",
        "2026-06-01",
        "2026-05-20",
    ]
    assert [row["date"] for row in new_days.json()["items"]] == [
        "2026-06-01",
        "2026-05-20",
    ]
    assert [row["date"] for row in edited_days.json()["items"]] == ["2026-06-03"]
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert {item["description"] for item in filtered.json()["items"]} == {
        "Day1a",
        "Day1b",
    }


def test_inbox_meta_and_cashier_names() -> None:
    c1 = ObjectId()
    c2 = ObjectId()
    docs = [
        _submitted(cashier_id=c1, truck_number="T101 AAA", item="Oil", description="Engine oil"),
        _submitted(
            cashier_id=c2,
            truck_number="T102 BBB",
            item="Grease",
            category_name="Lube",
            description="Wheel grease",
        ),
    ]
    users = FakeUsers(
        [
            {"_id": c1, "full_name": "Alice Cashier", "username": "alice"},
            {"_id": c2, "username": "bob"},
        ]
    )
    app = _app(FakeTransactions(docs), users)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        trucks = client.get("/v1/accountant/inbox/meta/trucks")
        items = client.get("/v1/accountant/inbox/meta/items")
        descs = client.get("/v1/accountant/inbox/meta/descriptions")
        cashiers = client.get("/v1/accountant/inbox/meta/cashiers")
        names = client.get(
            "/v1/accountant/cashier-names",
            params={"ids": f"{c1},{c2}"},
        )
        forbidden = client.get("/v1/accountant/inbox")

    assert trucks.json()["items"] == ["T101 AAA", "T102 BBB"]
    assert "Oil" in items.json()["items"]
    assert "Lube" in items.json()["items"]
    assert "Engine oil" in descs.json()["items"]
    assert set(cashiers.json()["items"]) == {str(c1), str(c2)}
    assert names.json()["names"][str(c1)] == "Alice Cashier"
    assert names.json()["names"][str(c2)] == "bob"
    assert forbidden.status_code == 200


def test_inbox_requires_manager_role() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/inbox")

    assert response.status_code == 403


def test_cashier_can_resolve_cashier_names() -> None:
    c1 = ObjectId()
    users = FakeUsers(
        [{"_id": c1, "full_name": "Alice Cashier", "username": "alice"}]
    )
    app = _app(FakeTransactions(), users)
    app.dependency_overrides[current_user] = lambda: {
        "_id": c1,
        "username": "alice",
        "role": "cashier",
    }

    with TestClient(app) as client:
        names = client.get(
            "/v1/accountant/cashier-names",
            params={"ids": str(c1)},
        )
        inbox = client.get("/v1/accountant/inbox")

    assert names.status_code == 200
    assert names.json()["names"][str(c1)] == "Alice Cashier"
    assert inbox.status_code == 403
