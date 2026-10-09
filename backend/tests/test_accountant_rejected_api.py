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


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeTransactions):
    database = SimpleNamespace(db=SimpleNamespace(transactions=collection))
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def test_rejected_list_excludes_discarded_and_supports_filters() -> None:
    c1 = ObjectId()
    c2 = ObjectId()
    docs = [
        {
            "_id": ObjectId(),
            "rejected": True,
            "discarded": False,
            "date": datetime(2026, 6, 1),
            "created_at": datetime(2026, 6, 1, 9),
            "truck_number": "T100 AAA",
            "description": "Diesel reject",
            "item": "Diesel",
            "category_name": "Fuel",
            "cashier_id": c1,
        },
        {
            "_id": ObjectId(),
            "rejected": True,
            "discarded": True,
            "date": datetime(2026, 6, 2),
            "description": "Discarded out",
            "truck_number": "T200",
            "cashier_id": c2,
        },
        {
            "_id": ObjectId(),
            "rejected": False,
            "date": datetime(2026, 6, 3),
            "description": "Not rejected",
            "truck_number": "T300",
            "cashier_id": c1,
        },
        {
            "_id": ObjectId(),
            "rejected": True,
            "discarded": False,
            "date": datetime(2026, 6, 4),
            "created_at": datetime(2026, 6, 4, 9),
            "truck_number": "T101 BBB",
            "description": "Oil reject",
            "item": "Oil",
            "category_name": "Lube",
            "cashier_id": c2,
        },
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        listed = client.get("/v1/accountant/rejected")
        count = client.get("/v1/accountant/rejected/count")
        badge = client.get("/v1/accountant/counts/rejected")
        filtered = client.get(
            "/v1/accountant/rejected",
            params={"search": "Diesel", "cashier_id": str(c1)},
        )
        trucks = client.get("/v1/accountant/rejected/meta/trucks")
        items = client.get("/v1/accountant/rejected/meta/items")
        descs = client.get("/v1/accountant/rejected/meta/descriptions")
        cashiers = client.get("/v1/accountant/rejected/meta/cashiers")

    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert count.json()["count"] == 2
    assert badge.json()["count"] == 2
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["description"] == "Diesel reject"
    assert trucks.json()["items"] == ["T100 AAA", "T101 BBB"]
    assert "Diesel" in items.json()["items"]
    assert "Lube" in items.json()["items"]
    assert "Oil reject" in descs.json()["items"]
    assert set(cashiers.json()["items"]) == {str(c1), str(c2)}


def test_rejected_requires_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/rejected")

    assert response.status_code == 403
