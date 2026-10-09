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
            if "$gte" in expected or "$lte" in expected or "$lt" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
                if "$lt" in expected and actual >= expected["$lt"]:
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


def _currency_sum(docs: list[dict], codes: set[str], absolute: bool = False) -> float:
    total = 0.0
    for d in docs:
        cur = str(d.get("currency") or "").upper()
        if cur in codes:
            amt = float(d.get("amount") or 0)
            total += abs(amt) if absolute else amt
    return total


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, projection: dict | None = None) -> FakeCursor:
        docs = [dict(doc) for doc in self.documents if _matches(doc, query)]
        if projection:
            projected = []
            for doc in docs:
                row = {k: doc.get(k) for k in projection if k != "_id"}
                if projection.get("_id", 1):
                    row["_id"] = doc.get("_id")
                projected.append(row)
            docs = projected
        return FakeCursor(docs)

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
                if "_cat_l" in stage["$addFields"]:
                    for d in docs:
                        d["_cat_l"] = str(d.get("category_name") or "").lower()
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    row: dict = {"_id": None}
                    if "count" in group:
                        row["count"] = len(docs)
                    if "tzs_total" in group:
                        row["tzs_total"] = _currency_sum(
                            docs, {"TZS", "TSH", "TZ"}
                        )
                    if "usd_total" in group:
                        row["usd_total"] = _currency_sum(docs, {"USD"})
                    if "zmw_total" in group:
                        row["zmw_total"] = _currency_sum(
                            docs, {"ZMW", "ZMB", "ZK"}
                        )
                    if "verified" in group:
                        row["verified"] = sum(
                            1 for d in docs if d.get("verified")
                        )
                    if "total" in group:
                        row["total"] = len(docs)
                    docs = [row]
                elif isinstance(gid, dict) and "$month" in str(gid):
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
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "count": 0,
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = float(d.get("amount") or 0)
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                        bucket["count"] += 1
                    docs = list(buckets.values())
                elif "receipt_status" in str(gid) or gid == "$receipt_status":
                    buckets2: dict[str | None, int] = {}
                    for d in docs:
                        key = d.get("receipt_status")
                        buckets2[key] = buckets2.get(key, 0) + 1
                    docs = [{"_id": k, "count": v} for k, v in buckets2.items()]
                else:
                    # category breakdown
                    buckets3: dict[str, dict] = {}
                    for d in docs:
                        name = d.get("category_name") or "Uncategorised"
                        bucket = buckets3.setdefault(
                            name,
                            {"_id": name, "tzs": 0.0, "usd": 0.0, "zmw": 0.0},
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = abs(float(d.get("amount") or 0))
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                    docs = list(buckets3.values())
            elif "$sort" in stage:
                pass
        return FakeCursor(docs)


class FakeImportedFeeds:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if isinstance(gid, dict) and "feed" in str(gid):
                    buckets: dict[tuple, dict] = {}
                    for d in docs:
                        day = d.get("transaction_date")
                        if not isinstance(day, datetime):
                            continue
                        key = (
                            d.get("feed_type"),
                            day.year,
                            day.month,
                            day.day,
                        )
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": {
                                    "feed": key[0],
                                    "y": key[1],
                                    "m": key[2],
                                    "d": key[3],
                                },
                                "liters": 0.0,
                            },
                        )
                        bucket["liters"] += float(d.get("ltrs") or 0)
                    docs = list(buckets.values())
                elif isinstance(gid, dict) and "$month" in str(gid):
                    field = (
                        "import_date"
                        if "import_date" in str(pipeline)
                        else "transaction_date"
                    )
                    buckets2: dict[int, float] = {}
                    for d in docs:
                        day = d.get(field) or d.get("transaction_date") or d.get(
                            "import_date"
                        )
                        if not isinstance(day, datetime):
                            continue
                        amt = float(
                            d.get("tender_amount")
                            or d.get("debit")
                            or d.get("amount")
                            or d.get("total_invoice")
                            or 0
                        )
                        buckets2[day.month] = buckets2.get(day.month, 0.0) + amt
                    docs = [{"_id": m, "total": v} for m, v in buckets2.items()]
                else:
                    docs = []
        return FakeCursor(docs)


class FakeSeparateExpenses:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                buckets: dict[int, float] = {}
                for d in docs:
                    day = d.get("expense_date")
                    if not isinstance(day, datetime):
                        continue
                    amt = float(d.get("amount_usd") or 0)
                    if amt > 0:
                        buckets[day.month] = buckets.get(day.month, 0.0) + amt
                docs = [{"_id": m, "total": v} for m, v in buckets.items()]
        return FakeCursor(docs)


class FakeCategories:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(d) for d in self.documents if _matches(d, query)]
        )


class FakeSystemSettings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {doc["key"]: dict(doc) for doc in (documents or [])}

    async def find_one(self, query: dict) -> dict | None:
        key = query.get("key")
        doc = self.documents.get(key)
        return dict(doc) if doc else None


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    transactions: FakeTransactions,
    imported_feeds: FakeImportedFeeds | None = None,
    separate_expenses: FakeSeparateExpenses | None = None,
    categories: FakeCategories | None = None,
    system_settings: FakeSystemSettings | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            imported_feeds=imported_feeds or FakeImportedFeeds(),
            separate_expenses=separate_expenses or FakeSeparateExpenses(),
            categories=categories or FakeCategories(),
            system_settings=system_settings or FakeSystemSettings(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def _tx(**kwargs) -> dict:
    doc = {
        "_id": ObjectId(),
        "verified": True,
        "rejected": False,
        "deletion_requested": False,
        "trashed": False,
        "date": datetime(2026, 3, 10),
        "description": "Diesel 200 LTRS",
        "amount": 100.0,
        "currency": "TZS",
        "category_name": "Fuel",
        "item": "Fuel",
        "receipt_status": "received",
        "created_at": datetime(2026, 3, 10, 8),
    }
    doc.update(kwargs)
    return doc


def test_overview_kpis_categories_receipts_and_dashboard() -> None:
    docs = [
        _tx(),
        _tx(
            amount=50.0,
            currency="USD",
            category_name="Lube",
            receipt_status="pending",
        ),
        _tx(
            category_name="Supplier X",
            amount=200.0,
            currency="TZS",
            description="Supplier pay",
        ),
        _tx(verified=False, rejected=False, description="Pending inbox"),
        _tx(
            category_name="Diesel Cash",
            item="Diesel Cash",
            description="Cash fill 400 LTRS",
            amount=80.0,
        ),
    ]
    categories = FakeCategories(
        [{"name": "Supplier X", "is_supplier": True, "active": True}]
    )
    feeds = FakeImportedFeeds(
        [
            {
                "feed_type": "diesel_infinity",
                "transaction_date": datetime(2026, 3, 10),
                "ltrs": 100,
            },
            {
                "feed_type": "toll_plaza",
                "transaction_date": datetime(2026, 2, 5),
                "tender_amount": 30.0,
            },
        ]
    )
    separate = FakeSeparateExpenses(
        [
            {
                "expense_type": "congo_expenses",
                "expense_date": datetime(2026, 1, 15),
                "amount_usd": 25.0,
            }
        ]
    )
    app = _app(
        FakeTransactions(docs),
        imported_feeds=feeds,
        separate_expenses=separate,
        categories=categories,
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        kpis = client.get("/v1/accountant/overview/kpis", params={"year": 2026})
        cats = client.get(
            "/v1/accountant/overview/categories", params={"year": 2026}
        )
        receipts = client.get(
            "/v1/accountant/overview/receipts", params={"year": 2026}
        )
        fuel = client.get(
            "/v1/accountant/overview/fuel-liters", params={"year": 2026}
        )
        months = client.get(
            "/v1/accountant/overview/month-totals", params={"year": 2026}
        )
        dash = client.get("/v1/accountant/overview", params={"year": 2026})

    assert kpis.status_code == 200
    assert kpis.json()["pending_count"] == 1
    assert kpis.json()["master_count"] == 4
    assert cats.status_code == 200
    names = {row["name"] for row in cats.json()["items"]}
    assert "Fuel" in names or "Supplier X" in names
    assert receipts.json()["received"] >= 1
    assert receipts.json()["pending"] >= 1
    assert fuel.status_code == 200
    assert "diesel_cash" in fuel.json()["series"]
    assert "infinity" in fuel.json()["series"]
    assert months.status_code == 200
    assert months.json()["feed_usd_ytd"] == 25.0
    assert months.json()["feed_zmw_ytd"] == 30.0
    assert dash.status_code == 200
    assert "kpis" in dash.json()
    assert dash.json()["kpis"]["supplier_tzs_ytd"] == 200.0
    assert len(dash.json()["recent"]) <= 8


def test_overview_requires_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get(
            "/v1/accountant/overview/kpis", params={"year": 2026}
        )

    assert response.status_code == 403
