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
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self.documents)
        return list(self.documents)[:length]


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(doc) for doc in self.documents if _matches(doc, query)]
        )

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        count = 0
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    async def delete_one(self, query: dict) -> SimpleNamespace:
        for index, doc in enumerate(self.documents):
            if _matches(doc, query):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeTransactions, events: FakeEvents | None = None):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            operation_events=events or FakeEvents(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager(user_id: ObjectId | None = None) -> dict:
    return {
        "_id": user_id or ObjectId(),
        "username": "acct1",
        "role": "accountant",
    }


def test_approve_and_reject() -> None:
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "verified": False,
                "rejected": False,
                "description": "Diesel",
                "item": "Fuel",
                "payee": "SHELL",
            }
        ]
    )
    events = FakeEvents()
    app = _app(collection, events)
    manager = _manager()
    app.dependency_overrides[current_user] = lambda: manager

    with TestClient(app) as client:
        approve = client.post(f"/v1/accountant/transactions/{tx_id}/approve")
        assert approve.status_code == 200
        assert approve.json()["ok"] is True
        assert collection.documents[0]["verified"] is True
        assert collection.documents[0]["verified_by"] == manager["_id"]

        # already verified — second approve fails
        again = client.post(f"/v1/accountant/transactions/{tx_id}/approve")
        assert again.json()["ok"] is False

        reject = client.post(
            f"/v1/accountant/transactions/{tx_id}/reject",
            json={"reason": "Wrong truck"},
        )
        assert reject.json()["ok"] is True
        assert collection.documents[0]["rejected"] is True
        assert collection.documents[0]["rejection_reason"] == "Wrong truck"

        restored = client.post(
            f"/v1/accountant/transactions/{tx_id}/return-to-inbox"
        )
        assert restored.json()["ok"] is True
        assert collection.documents[0]["rejected"] is False

    assert any(e["action"] == "txn.approve" for e in events.documents)
    assert any(e["action"] == "txn.reject" for e in events.documents)


def test_bulk_approve_reject_and_category() -> None:
    a = ObjectId()
    b = ObjectId()
    cat = ObjectId()
    collection = FakeTransactions(
        [
            {"_id": a, "verified": False, "item": "X", "category_name": "X", "payee": "P"},
            {"_id": b, "verified": False, "item": "Y", "category_name": "Y", "payee": "P"},
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        cat_resp = client.post(
            "/v1/accountant/transactions/bulk-category",
            json={
                "ids": [str(a), str(b)],
                "category_name": "Fuel",
                "category_id": str(cat),
            },
        )
        assert cat_resp.json()["updated"] == 2
        assert collection.documents[0]["item"] == "Fuel"
        assert collection.documents[0]["category_id"] == cat

        single = client.patch(
            f"/v1/accountant/transactions/{a}/category",
            json={"category_name": "Oil"},
        )
        assert single.json()["ok"] is True
        assert collection.documents[0]["item"] == "Oil"

        approve = client.post(
            "/v1/accountant/transactions/bulk-approve",
            json={"ids": [str(a), str(b)]},
        )
        assert approve.json()["updated"] == 2

        reject = client.post(
            "/v1/accountant/transactions/bulk-reject",
            json={"ids": [str(a)], "reason": "dup"},
        )
        assert reject.json()["updated"] == 1
        assert collection.documents[0]["rejected"] is True


def test_re_approve_cascade_and_simple() -> None:
    original_id = ObjectId()
    pending_id = ObjectId()
    simple_id = ObjectId()
    manager = _manager()
    collection = FakeTransactions(
        [
            {
                "_id": original_id,
                "verified": True,
                "description": "Old",
                "amount": 10.0,
                "item": "Old",
            },
            {
                "_id": pending_id,
                "verified": False,
                "original_transaction_id": original_id,
                "description": "New",
                "amount": 25.0,
                "item": "New",
                "payee": "SHELL",
                "edited_after_verification": True,
                "last_edited_at": datetime(2026, 6, 1),
                "last_edited_by": ObjectId(),
            },
            {
                "_id": simple_id,
                "verified": False,
                "edited_after_verification": True,
                "description": "Legacy edit",
                "item": "Fuel",
                "payee": "SHELL",
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: manager

    with TestClient(app) as client:
        cascade = client.post(
            f"/v1/accountant/transactions/{pending_id}/re-approve"
        )
        assert cascade.json()["ok"] is True
        original = next(d for d in collection.documents if d["_id"] == original_id)
        assert original["description"] == "New"
        assert original["amount"] == 25.0
        assert original["verified"] is True
        assert all(d["_id"] != pending_id for d in collection.documents)

        bulk = client.post(
            "/v1/accountant/transactions/bulk-re-approve",
            json={"ids": [str(simple_id)]},
        )
        assert bulk.json()["updated"] == 1
        simple = next(d for d in collection.documents if d["_id"] == simple_id)
        assert simple["verified"] is True
        assert simple["edited_after_verification"] is False


def test_actions_forbid_cashier() -> None:
    tx_id = ObjectId()
    app = _app(FakeTransactions([{"_id": tx_id, "verified": False}]))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.post(f"/v1/accountant/transactions/{tx_id}/approve")

    assert response.status_code == 403
