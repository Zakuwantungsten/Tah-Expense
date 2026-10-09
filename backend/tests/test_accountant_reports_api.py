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
        if key == "$expr":
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
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

    async def count_documents(self, query: dict, limit: int | None = None) -> int:
        total = sum(1 for doc in self.documents if _matches(doc, query))
        if limit is not None:
            return min(total, limit)
        return total

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$addFields" in stage:
                fields = stage["$addFields"]
                if "_cat_l" in fields:
                    for d in docs:
                        d["_cat_l"] = str(d.get("category_name") or "").lower()
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
            elif "$sort" in stage:
                pass
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None and ("tzs_total" in group or "count" in group):
                    tzs = sum(
                        float(d.get("amount") or 0)
                        for d in docs
                        if str(d.get("currency") or "").upper() in {"TZS", "TSH", "TZ"}
                    )
                    usd = sum(
                        float(d.get("amount") or 0)
                        for d in docs
                        if str(d.get("currency") or "").upper() == "USD"
                    )
                    docs = [
                        {
                            "_id": None,
                            "count": len(docs),
                            "tzs_total": tzs,
                            "usd_total": usd,
                        }
                    ]
                elif gid == "$_cat_l":
                    buckets: dict[str, dict] = {}
                    for d in docs:
                        key = str(d.get("_cat_l") or d.get("category_name") or "").lower()
                        if not key:
                            continue
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": key,
                                "count": 0,
                                "tzs_total": 0.0,
                                "usd_total": 0.0,
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = float(d.get("amount") or 0)
                        bucket["count"] += 1
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs_total"] += amt
                        elif cur == "USD":
                            bucket["usd_total"] += amt
                    docs = list(buckets.values())
                elif gid == "$amount":
                    amounts = sorted(
                        {
                            float(d["amount"])
                            for d in docs
                            if d.get("amount") is not None
                        }
                    )
                    docs = [{"_id": a} for a in amounts]
                else:
                    docs = []
        return FakeCursor(docs)


class FakeCategories:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(d) for d in self.documents if _matches(d, query)]
        )


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


def _app(
    transactions: FakeTransactions,
    categories: FakeCategories | None = None,
    users: FakeUsers | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            categories=categories or FakeCategories(),
            users=users or FakeUsers(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def _verified(**kwargs) -> dict:
    doc = {
        "_id": ObjectId(),
        "verified": True,
        "deletion_requested": False,
        "trashed": False,
        "date": datetime(2026, 3, 10),
        "description": "Diesel fill",
        "truck_number": "T100 AAA",
        "amount": 100.0,
        "currency": "TZS",
        "category_name": "Fuel",
        "item": "Fuel",
        "created_at": datetime(2026, 3, 10, 8),
    }
    doc.update(kwargs)
    return doc


def test_category_report_lifetime_totals_transactions_and_meta() -> None:
    docs = [
        _verified(),
        _verified(
            date=datetime(2025, 1, 5),
            amount=25.0,
            currency="USD",
            description="Oil top-up",
        ),
        _verified(category_name="Lube", description="Other item"),
        _verified(verified=False, description="Unverified"),
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        lifetime = client.get("/v1/accountant/reports/category/Fuel/lifetime")
        totals = client.get("/v1/accountant/reports/category/Fuel/totals")
        txs = client.get("/v1/accountant/reports/category/Fuel/transactions")
        descs = client.get("/v1/accountant/reports/category/Fuel/descriptions")
        cols = client.get(
            "/v1/accountant/reports/category/Fuel/column-values",
            params={"field": "description"},
        )
        opening = client.get(
            "/v1/accountant/reports/category/Fuel/opening-balance",
            params={"skip": 1},
        )

    assert lifetime.status_code == 200
    assert lifetime.json()["count"] == 2
    assert lifetime.json()["tzs"] == 100.0
    assert lifetime.json()["usd"] == 25.0
    assert totals.json()["count"] == 2
    assert len(txs.json()["items"]) == 2
    assert "Diesel fill" in descs.json()["items"]
    assert "Oil top-up" in descs.json()["items"]
    assert "Diesel fill" in cols.json()["items"]
    assert opening.json()["tzs"] == 100.0 or opening.json()["usd"] == 25.0


def test_categories_usage_totals_and_supplier_payments() -> None:
    docs = [
        _verified(category_name="Fuel", amount=100.0, currency="TZS"),
        _verified(category_name="Lube", amount=40.0, currency="USD"),
        _verified(
            category_name="Supplier X",
            amount=200.0,
            currency="TZS",
            date=datetime(2026, 5, 1),
        ),
        _verified(
            category_name="Supplier X",
            amount=10.0,
            currency="USD",
            date=datetime(2025, 5, 1),
        ),
    ]
    categories = FakeCategories(
        [{"name": "Supplier X", "is_supplier": True, "active": True}]
    )
    app = _app(FakeTransactions(docs), categories=categories)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        usage = client.post(
            "/v1/accountant/reports/categories/usage-totals",
            json={"names": ["Fuel", "Lube", "Missing"]},
        )
        supplier = client.get(
            "/v1/accountant/reports/supplier-payments/totals",
            params={"year": 2026},
        )

    assert usage.status_code == 200
    totals = usage.json()["totals"]
    assert totals["fuel"]["tzs"] == 100.0
    assert totals["lube"]["usd"] == 40.0
    assert "missing" not in totals
    assert supplier.status_code == 200
    assert supplier.json()["count"] == 1
    assert supplier.json()["tzs"] == 200.0
    assert supplier.json()["usd"] == 0.0


def test_reports_require_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/reports/category/Fuel/lifetime")

    assert response.status_code == 403
