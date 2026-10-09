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
            elif "$sort" in stage:
                pass
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None and "tzs_total" in group:
                    tzs = sum(
                        float(d.get("amount") or 0)
                        for d in docs
                        if str(d.get("currency") or "") == "TZS"
                    )
                    usd = sum(
                        float(d.get("amount") or 0)
                        for d in docs
                        if str(d.get("currency") or "") == "USD"
                    )
                    docs = [{"_id": None, "tzs_total": tzs, "usd_total": usd}]
                elif isinstance(gid, dict) and "$month" in gid:
                    buckets: dict[int, dict] = {}
                    for d in docs:
                        day = d.get("date")
                        if not isinstance(day, datetime):
                            continue
                        month = day.month
                        bucket = buckets.setdefault(
                            month,
                            {
                                "_id": month,
                                "record_count": 0,
                                "tzs_total": 0.0,
                                "usd_total": 0.0,
                                "min_date": day,
                                "max_date": day,
                            },
                        )
                        cur = str(d.get("currency") or "")
                        amt = float(d.get("amount") or 0)
                        bucket["record_count"] += 1
                        if cur == "TZS":
                            bucket["tzs_total"] += amt
                        elif cur == "USD":
                            bucket["usd_total"] += amt
                        if day < bucket["min_date"]:
                            bucket["min_date"] = day
                        if day > bucket["max_date"]:
                            bucket["max_date"] = day
                    docs = [buckets[k] for k in sorted(buckets)]
                else:
                    docs = []
        return FakeCursor(docs)


class FakeSystemSettings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {doc["key"]: dict(doc) for doc in (documents or [])}

    async def find_one(self, query: dict) -> dict | None:
        key = query.get("key")
        doc = self.documents.get(key)
        return dict(doc) if doc else None

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        key = query["key"]
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = values
        return SimpleNamespace(matched_count=1, modified_count=1)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    transactions: FakeTransactions,
    system_settings: FakeSystemSettings | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            system_settings=system_settings or FakeSystemSettings(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def _diesel(**kwargs) -> dict:
    doc = {
        "_id": ObjectId(),
        "verified": True,
        "deletion_requested": False,
        "trashed": False,
        "date": datetime(2026, 3, 10),
        "description": "Cash diesel 200L",
        "truck_number": "T100 AAA",
        "amount": 100.0,
        "currency": "TZS",
        "category_name": "Diesel Cash",
        "item": "Diesel Cash",
        "created_at": datetime(2026, 3, 10, 8),
    }
    doc.update(kwargs)
    return doc


def test_diesel_cash_items_default_and_put() -> None:
    settings_col = FakeSystemSettings()
    app = _app(FakeTransactions(), settings_col)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        defaulted = client.get("/v1/accountant/diesel-cash/items")
        saved = client.put(
            "/v1/accountant/diesel-cash/items",
            json={"names": ["Diesel", "Diesel Cash", "diesel"]},
        )
        loaded = client.get("/v1/accountant/diesel-cash/items")

    assert defaulted.status_code == 200
    assert defaulted.json()["items"] == ["Diesel Cash"]
    assert saved.json()["items"] == ["Diesel", "Diesel Cash"]
    assert loaded.json()["items"] == ["Diesel", "Diesel Cash"]
    assert settings_col.documents["diesel_cash_items"]["value"] == [
        "Diesel",
        "Diesel Cash",
    ]


def test_diesel_cash_list_count_totals_and_month_summaries() -> None:
    docs = [
        _diesel(),
        _diesel(
            date=datetime(2026, 4, 5),
            amount=50.0,
            currency="USD",
            description="April fill",
        ),
        _diesel(category_name="Fuel", item="Fuel", description="Not diesel"),
        _diesel(verified=False, description="Unverified"),
        _diesel(
            date=datetime(2025, 6, 1),
            description="Prior year",
        ),
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        listed = client.get(
            "/v1/accountant/diesel-cash/transactions",
            params={"year": 2026},
        )
        count = client.get(
            "/v1/accountant/diesel-cash/transactions/count",
            params={"year": 2026},
        )
        totals = client.get(
            "/v1/accountant/diesel-cash/totals",
            params={"year": 2026},
        )
        months = client.get(
            "/v1/accountant/diesel-cash/month-summaries",
            params={"year": 2026},
        )
        entries = client.get("/v1/accountant/diesel-cash/entries")

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2
    assert count.json()["count"] == 2
    assert totals.json()["tzs"] == 100.0
    assert totals.json()["usd"] == 50.0
    assert months.status_code == 200
    month_nums = {row["month"] for row in months.json()["items"]}
    assert month_nums == {3, 4}
    assert entries.json()["items"] == []


def test_diesel_cash_requires_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/diesel-cash/items")

    assert response.status_code == 403
