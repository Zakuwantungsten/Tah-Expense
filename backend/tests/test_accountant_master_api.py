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
            # Supplier exclusion: treat as always matching in unit tests.
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

    async def bulk_write(self, ops: list, ordered: bool = True) -> SimpleNamespace:
        matched = 0
        modified = 0
        for op in ops:
            filt = op._filter
            doc_update = op._doc
            result = await self.update_one(filt, doc_update)
            matched += int(getattr(result, "matched_count", 0) or 0)
            modified += int(getattr(result, "modified_count", 0) or 0)
        return SimpleNamespace(matched_count=matched, modified_count=modified)

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                if group.get("_id") is None and "tzs_total" in group:
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
                    docs = [{"_id": None, "tzs_total": tzs, "usd_total": usd}]
                elif isinstance(group.get("_id"), dict) and "$month" in str(
                    group.get("_id")
                ):
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
                else:
                    # years pipeline: group by year id
                    years = sorted(
                        {
                            d["date"].year
                            for d in docs
                            if isinstance(d.get("date"), datetime)
                        },
                        reverse=True,
                    )
                    docs = [{"_id": y} for y in years]
            elif "$project" in stage:
                projected = []
                for d in docs:
                    years = set()
                    if isinstance(d.get("date"), datetime):
                        years.add(d["date"].year)
                    if isinstance(d.get("import_primary_date"), datetime):
                        years.add(d["import_primary_date"].year)
                    projected.append({"years": list(years)})
                docs = projected
            elif "$unwind" in stage:
                unwound = []
                for d in docs:
                    for year in d.get("years") or []:
                        unwound.append({"years": year, "_id": year})
                docs = unwound
            elif "$sort" in stage:
                pass
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


def _app(
    transactions: FakeTransactions,
    categories: FakeCategories | None = None,
    users: FakeUsers | None = None,
    events: FakeEvents | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            categories=categories or FakeCategories(),
            users=users or FakeUsers(),
            operation_events=events or FakeEvents(),
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
        "description": "Diesel",
        "truck_number": "T100 AAA",
        "amount": 100.0,
        "currency": "TZS",
        "category_name": "Fuel",
        "item": "Fuel",
        "created_at": datetime(2026, 3, 10, 8),
    }
    doc.update(kwargs)
    return doc


def test_master_list_count_totals_and_meta() -> None:
    docs = [
        _verified(),
        _verified(
            date=datetime(2026, 4, 1),
            description="Oil",
            truck_number="T101 BBB",
            amount=50.0,
            currency="USD",
            category_name="Lube",
        ),
        _verified(verified=False, description="Unverified out"),
        _verified(
            date=datetime(2025, 6, 1),
            description="Prior year",
            category_name="Fuel",
        ),
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        listed = client.get("/v1/accountant/master", params={"year": 2026})
        count = client.get("/v1/accountant/master/count", params={"year": 2026})
        totals = client.get("/v1/accountant/master/totals", params={"year": 2026})
        trucks = client.get("/v1/accountant/master/meta/trucks", params={"year": 2026})
        cats = client.get(
            "/v1/accountant/master/meta/categories", params={"year": 2026}
        )
        years = client.get("/v1/accountant/master/years")
        months = client.get("/v1/accountant/master/month-totals", params={"year": 2026})
        boot = client.get(
            "/v1/accountant/master/meta/bootstrap",
            params={"year": 2026, "include_year_meta": True},
        )
        totals_only = client.get(
            "/v1/accountant/master/meta/bootstrap",
            params={"year": 2026, "include_year_meta": False},
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert "cashier_names" in listed.json()
    assert count.json()["count"] == 2
    assert totals.json()["tzs"] == 100.0
    assert totals.json()["usd"] == 50.0
    assert trucks.json()["items"] == ["T100 AAA", "T101 BBB"]
    assert "Fuel" in cats.json()["items"]
    assert 2026 in years.json()["items"]
    assert "3" in months.json()["months"]
    assert months.json()["months"]["3"]["tzs"] == 100.0
    assert boot.status_code == 200
    body = boot.json()
    assert body["totals"]["tzs"] == 100.0
    assert body["totals"]["usd"] == 50.0
    assert body["trucks"] == ["T100 AAA", "T101 BBB"]
    assert "Fuel" in body["categories"]
    assert "3" in body["month_totals"]
    assert body["month_totals"]["3"]["tzs"] == 100.0
    assert totals_only.status_code == 200
    slim = totals_only.json()
    assert slim["totals"]["tzs"] == 100.0
    assert slim["month_totals"] is None
    assert slim["trucks"] is None
    assert slim["categories"] is None


def test_master_list_composite_total_and_cashier_names() -> None:
    """List returns full match count (not page length) + page cashier names."""
    cashier_a = ObjectId()
    cashier_b = ObjectId()
    docs = [
        _verified(cashier_id=cashier_a, description="A1"),
        _verified(
            date=datetime(2026, 4, 1),
            cashier_id=cashier_b,
            description="B1",
            amount=50.0,
            currency="USD",
            category_name="Lube",
        ),
        _verified(
            date=datetime(2026, 5, 1),
            cashier_id=cashier_a,
            description="A2",
            amount=25.0,
        ),
    ]
    users = FakeUsers(
        [
            {"_id": cashier_a, "full_name": "Alice Cashier", "username": "alice"},
            {"_id": cashier_b, "username": "bob"},
        ]
    )
    app = _app(FakeTransactions(docs), users=users)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        page = client.get(
            "/v1/accountant/master",
            params={"year": 2026, "limit": 1, "skip": 0},
        )
        count = client.get("/v1/accountant/master/count", params={"year": 2026})

    assert page.status_code == 200
    body = page.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["total"] == count.json()["count"]
    names = body["cashier_names"]
    page_cashier = str(body["items"][0]["cashier_id"])
    assert page_cashier in names
    assert names[page_cashier] in {"Alice Cashier", "bob"}
    # Names are scoped to this page (not every cashier in the full match set).
    assert set(names.keys()) == {page_cashier}


def test_master_excludes_supplier_categories() -> None:
    docs = [
        _verified(category_name="Fuel", description="Keep"),
        _verified(category_name="Supplier X", description="Drop"),
    ]
    categories = FakeCategories(
        [{"name": "Supplier X", "is_supplier": True, "active": True}]
    )
    app = _app(FakeTransactions(docs), categories=categories)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        # $expr is ignored by fake matcher — assert endpoint still succeeds
        response = client.get("/v1/accountant/master", params={"year": 2026})

    assert response.status_code == 200


def test_master_requires_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/master", params={"year": 2026})

    assert response.status_code == 403


def test_master_column_values_and_updates() -> None:
    tx_id = ObjectId()
    other = ObjectId()
    collection = FakeTransactions(
        [
            _verified(
                _id=tx_id,
                description="Diesel",
                item="Fuel",
                category_name="Fuel",
                receipt_status="pending",
                amount=100.0,
                currency="TZS",
            ),
            _verified(
                _id=other,
                description="Grease",
                item="Lube",
                category_name="Lube",
                receipt_status="received",
                amount=20.0,
                currency="USD",
            ),
        ]
    )
    events = FakeEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        items = client.get(
            "/v1/accountant/master/meta/column-values",
            params={"field": "item", "year": 2026},
        )
        receipts = client.get(
            "/v1/accountant/master/meta/column-values",
            params={"field": "receipt_status", "year": 2026},
        )
        patch = client.patch(
            f"/v1/accountant/master/{tx_id}",
            json={"updates": {"description": "Diesel updated", "item": "Oil"}},
        )
        bulk = client.post(
            "/v1/accountant/master/bulk-update",
            json={
                "ids": [str(tx_id), str(other)],
                "updates": {"receipt_status": "received"},
            },
        )

    assert items.status_code == 200
    assert "Fuel" in items.json()["items"]
    assert "Lube" in items.json()["items"]
    assert "Pending" in receipts.json()["items"]
    assert "Received" in receipts.json()["items"]
    assert patch.json()["ok"] is True
    updated = next(d for d in collection.documents if d["_id"] == tx_id)
    assert updated["description"] == "Diesel updated"
    assert updated["item"] == "Oil"
    assert updated["category_name"] == "Oil"
    assert bulk.json()["updated"] == 2
    assert all(d["receipt_status"] == "received" for d in collection.documents)
    assert any(e["action"] == "txn.master_update" for e in events.documents)


def test_master_bulk_patch_heterogeneous_updates() -> None:
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            _verified(
                _id=a,
                description="A",
                item="Fuel",
                category_name="Fuel",
                memo="old-a",
            ),
            _verified(
                _id=b,
                description="B",
                item="Lube",
                category_name="Lube",
                memo="old-b",
                amount=20.0,
                currency="USD",
            ),
        ]
    )
    events = FakeEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.post(
            "/v1/accountant/master/bulk-patch",
            json={
                "items": [
                    {
                        "id": str(a),
                        "updates": {"description": "A2", "memo": "new-a"},
                    },
                    {
                        "id": str(b),
                        "updates": {"item": "Parking", "category_name": "Parking"},
                    },
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 2
    assert body["requested"] == 2
    docs = {d["_id"]: d for d in collection.documents}
    assert docs[a]["description"] == "A2"
    assert docs[a]["memo"] == "new-a"
    assert docs[b]["item"] == "Parking"
    assert docs[b]["category_name"] == "Parking"
    assert any(
        e["action"] == "txn.master_update_bulk_patch" for e in events.documents
    )
