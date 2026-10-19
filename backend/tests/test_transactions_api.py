from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import BulkWriteError

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
            if "$type" in expected:
                want = expected["$type"]
                if want == "string" and not isinstance(actual, str):
                    return False
                if want == "null" and actual is not None:
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                continue
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected:
                exists = key in doc
                if bool(expected["$exists"]) != exists:
                    return False
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
        self._limit: int | None = None

    def sort(self, *_args: object, **_kwargs: object) -> "FakeCursor":
        return self

    def limit(self, value: int) -> "FakeCursor":
        self._limit = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = list(self.documents)
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is None:
            return docs
        return docs[:length]


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def _client_row_conflict(self, document: dict) -> bool:
        crid = document.get("client_row_id")
        cid = document.get("cashier_id")
        if not (isinstance(crid, str) and crid and cid is not None):
            return False
        return any(
            d.get("cashier_id") == cid and d.get("client_row_id") == crid
            for d in self.documents
        )

    async def insert_one(self, document: dict) -> SimpleNamespace:
        if self._client_row_conflict(document):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("E11000 duplicate key")
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def insert_many(
        self, documents: list[dict], ordered: bool = True
    ) -> SimpleNamespace:
        inserted_ids: dict[int, ObjectId] = {}
        write_errors: list[dict] = []
        for index, document in enumerate(documents):
            if self._client_row_conflict(document):
                write_errors.append(
                    {"index": index, "code": 11000, "errmsg": "E11000 duplicate key"}
                )
                if ordered:
                    raise BulkWriteError(
                        {
                            "nInserted": len(inserted_ids),
                            "writeErrors": write_errors,
                            "insertedIds": inserted_ids,
                        }
                    )
                continue
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            self.documents.append(stored)
            inserted_ids[index] = stored["_id"]
        if write_errors:
            raise BulkWriteError(
                {
                    "nInserted": len(inserted_ids),
                    "writeErrors": write_errors,
                    "insertedIds": inserted_ids,
                }
            )
        return SimpleNamespace(inserted_ids=inserted_ids)

    async def create_index(self, *_args: object, **_kwargs: object) -> str:
        return "uniq_cashier_client_row"

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor([dict(doc) for doc in self.documents if _matches(doc, query)])

    async def distinct(self, field: str, query: dict | None = None) -> list:
        query = query or {}
        values = []
        for doc in self.documents:
            if _matches(doc, query):
                value = doc.get(field)
                if value and value not in values:
                    values.append(value)
        return values

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = list(self.documents)
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                mx_expr = group.get("mx")
                if isinstance(mx_expr, dict) and "$max" in mx_expr:
                    field = str(mx_expr["$max"]).lstrip("$")
                    values = [
                        doc.get(field) for doc in docs if doc.get(field) is not None
                    ]
                    docs = [{"_id": None, "mx": max(values)}] if values else []
                elif group.get("_id") is None and "count" in group:
                    docs = [
                        {
                            "_id": None,
                            "count": len(docs),
                            "tzs_total": sum(float(d.get("amount") or 0) for d in docs),
                            "receipt_received": sum(
                                1
                                for d in docs
                                if d.get("receipt_status") == "received"
                            ),
                            "receipt_pending": sum(
                                1
                                for d in docs
                                if d.get("receipt_status") == "pending"
                            ),
                            "receipt_missing": sum(
                                1
                                for d in docs
                                if d.get("receipt_status") == "missing"
                            ),
                            "verified": sum(
                                1 for d in docs if d.get("verified") is True
                            ),
                            "unverified": sum(
                                1 for d in docs if d.get("verified") is False
                            ),
                        }
                    ]
                elif isinstance(group.get("_id"), dict) and "year" in group["_id"]:
                    # daily-summaries / months style
                    buckets: dict[tuple, dict] = {}
                    for doc in docs:
                        day = doc.get("date") or doc.get("import_primary_date")
                        if day is None:
                            continue
                        key = (day.year, day.month, getattr(day, "day", 1))
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": {
                                    "year": key[0],
                                    "month": key[1],
                                    "day": key[2],
                                },
                                "entries_count": 0,
                                "total_tzs": 0.0,
                                "total_refund": 0.0,
                                "cashier_ids": [],
                                "draft_count": 0,
                                "submitted_count": 0,
                            },
                        )
                        bucket["entries_count"] += 1
                        bucket["total_tzs"] += float(doc.get("amount") or 0)
                    docs = list(buckets.values())
                elif group.get("_id") == {"$year": "$date"} or (
                    isinstance(group.get("_id"), dict) and "$year" in str(group.get("_id"))
                ):
                    years = sorted(
                        {
                            d["date"].year
                            for d in docs
                            if isinstance(d.get("date"), datetime)
                        },
                        reverse=True,
                    )
                    docs = [{"_id": year} for year in years]
                elif group.get("_id") == "$daily_import_id":
                    from app.import_ops import doc_money_parts

                    buckets: dict[str, dict] = {}
                    for d in docs:
                        key = str(d.get("daily_import_id") or "")
                        if not key:
                            continue
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": key,
                                "source_filename": d.get("daily_import_source"),
                                "count": 0,
                                "total_tzs": 0.0,
                                "total_usd": 0.0,
                                "primary_date": d.get("import_primary_date"),
                                "min_date": d.get("date"),
                                "max_date": d.get("date"),
                                "created_at": d.get("created_at"),
                                "cashier_id": d.get("cashier_id"),
                                "outlier_count": 0,
                                "duplicate_count": 0,
                            },
                        )
                        bucket["count"] += 1
                        tzs, usd = doc_money_parts(d)
                        bucket["total_tzs"] += tzs
                        bucket["total_usd"] += usd
                        if d.get("possible_duplicate"):
                            bucket["duplicate_count"] += 1
                    docs = list(buckets.values())
            elif "$sort" in stage:
                continue
            elif "$facet" in stage:
                facet = stage["$facet"]
                items = list(docs)
                for sub in facet.get("items") or []:
                    if "$skip" in sub:
                        items = items[int(sub["$skip"]) :]
                    elif "$limit" in sub:
                        items = items[: int(sub["$limit"])]
                meta = [{"total": len(docs)}] if docs else []
                docs = [{"items": items, "meta": meta}]
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
        return FakeCursor(docs)

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
                if "$unset" in update:
                    for key in update["$unset"]:
                        doc.pop(key, None)
                if "$set" in update:
                    doc.update(update["$set"])
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    async def bulk_write(self, ops: list, ordered: bool = True) -> SimpleNamespace:
        matched = 0
        modified = 0
        for op in ops:
            filt = op._filter  # pymongo UpdateOne
            doc_update = op._doc
            result = await self.update_one(filt, doc_update)
            matched += int(getattr(result, "matched_count", 0) or 0)
            modified += int(getattr(result, "modified_count", 0) or 0)
        return SimpleNamespace(matched_count=matched, modified_count=modified)

    async def delete_one(self, query: dict) -> SimpleNamespace:
        for index, doc in enumerate(self.documents):
            if _matches(doc, query):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query: dict) -> SimpleNamespace:
        keep: list[dict] = []
        deleted = 0
        for doc in self.documents:
            if _matches(doc, query):
                deleted += 1
            else:
                keep.append(doc)
        self.documents = keep
        return SimpleNamespace(deleted_count=deleted)

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


class FakeMappings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


class FakeUsers:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        ids = set()
        if "_id" in query and isinstance(query["_id"], dict) and "$in" in query["_id"]:
            ids = set(query["_id"]["$in"])
        docs = [dict(d) for d in self.documents if not ids or d.get("_id") in ids]
        return FakeCursor(docs)


def _app(
    collection: FakeTransactions,
    mappings: list[dict] | None = None,
    events: FakeOperationEvents | None = None,
    users: FakeUsers | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            description_mappings=FakeMappings(mappings),
            operation_events=events or FakeOperationEvents(),
            users=users or FakeUsers(),
        )
    )
    return create_app(settings=settings(), database=database)


def _cashier(user_id: ObjectId | None = None) -> dict:
    return {
        "_id": user_id or ObjectId(),
        "username": "cash1",
        "role": "cashier",
    }


def _accountant(user_id: ObjectId | None = None) -> dict:
    return {
        "_id": user_id or ObjectId(),
        "username": "acct1",
        "role": "accountant",
    }


def test_create_transaction() -> None:
    collection = FakeTransactions()
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    user = _cashier()
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions",
            json={
                "date": "2026-04-01T00:00:00",
                "description": "Diesel",
                "truck_number": "T100",
                "amount": 50.0,
                "currency": "TZS",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "Diesel"
    assert body["cashier_id"] == str(user["_id"])
    assert body["register_status"] == "draft"
    assert len(collection.documents) == 1
    assert any(e["action"] == "txn.create" for e in events.documents)


def test_create_transaction_idempotent_on_client_row_id() -> None:
    """Replay of the same client_row_id must return the original doc, not a duplicate."""
    collection = FakeTransactions()
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    user = _cashier()
    app.dependency_overrides[current_user] = lambda: user
    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = {
        "date": "2026-04-01T00:00:00",
        "description": "Diesel",
        "truck_number": "T100",
        "amount": 50.0,
        "currency": "TZS",
        "client_row_id": rid,
    }

    with TestClient(app) as client:
        first = client.post("/v1/transactions", json=payload)
        second = client.post(
            "/v1/transactions",
            json={**payload, "description": "Changed on retry", "amount": 999.0},
        )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["_id"] == first.json()["_id"]
    assert second.json()["description"] == "Diesel"
    assert second.json()["amount"] == 50.0
    assert second.json()["client_row_id"] == rid
    assert len(collection.documents) == 1
    assert sum(1 for e in events.documents if e["action"] == "txn.create") == 1


def test_create_without_client_row_id_still_inserts_each_time() -> None:
    collection = FakeTransactions()
    app = _app(collection)
    user = _cashier()
    app.dependency_overrides[current_user] = lambda: user
    payload = {
        "date": "2026-04-01T00:00:00",
        "description": "Parking",
        "amount": 5.0,
        "currency": "TZS",
    }

    with TestClient(app) as client:
        a = client.post("/v1/transactions", json=payload)
        b = client.post("/v1/transactions", json=payload)

    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["_id"] != b.json()["_id"]
    assert len(collection.documents) == 2


def test_get_and_patch_and_delete_transaction() -> None:
    tx_id = ObjectId()
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "date": datetime(2026, 4, 1),
                "description": "Old",
                "amount": 1.0,
                "currency": "TZS",
                "cashier_id": user["_id"],
                "register_status": "draft",
            }
        ]
    )
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        got = client.get(f"/v1/transactions/{tx_id}")
        assert got.status_code == 200
        assert got.json()["description"] == "Old"

        patched = client.patch(
            f"/v1/transactions/{tx_id}",
            json={"values": {"description": "New", "amount": 9.5}},
        )
        assert patched.status_code == 200
        assert patched.json()["description"] == "New"
        assert patched.json()["amount"] == 9.5

        deleted = client.delete(f"/v1/transactions/{tx_id}")
        assert deleted.status_code == 204
        assert collection.documents == []

    actions = [e["action"] for e in events.documents]
    assert "txn.update" in actions
    assert "txn.delete" in actions


def test_list_by_ids() -> None:
    a, b = ObjectId(), ObjectId()
    collection = FakeTransactions(
        [
            {"_id": a, "description": "A", "amount": 1},
            {"_id": b, "description": "B", "amount": 2},
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _cashier()

    with TestClient(app) as client:
        response = client.get(f"/v1/transactions?ids={a},{b}")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["description"] for item in body["items"]} == {"A", "B"}


def test_by_date_scopes_to_cashier() -> None:
    user = _cashier()
    other = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "date": datetime(2026, 4, 1, 10, 0, 0),
                "import_primary_date": None,
                "description": "mine",
                "cashier_id": user["_id"],
                "rejected": False,
                "trashed": False,
                "deletion_requested": False,
            },
            {
                "_id": ObjectId(),
                "date": datetime(2026, 4, 1, 11, 0, 0),
                "import_primary_date": None,
                "description": "theirs",
                "cashier_id": other,
                "rejected": False,
                "trashed": False,
                "deletion_requested": False,
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.get("/v1/transactions/by-date?date=2026-04-01")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["description"] == "mine"


def test_create_requires_auth() -> None:
    app = _app(FakeTransactions())
    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions",
            json={"date": "2026-04-01T00:00:00", "description": "x", "amount": 1},
        )
    assert response.status_code == 401


def test_submit_day_marks_drafts() -> None:
    user = _cashier()
    draft_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": draft_id,
                "date": datetime(2026, 4, 2, 8, 0, 0),
                "import_primary_date": None,
                "register_status": "draft",
                "verified": False,
                "rejected": False,
                "discarded": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post("/v1/transactions/submit-day", json={"date": "2026-04-02"})

    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    assert collection.documents[0]["register_status"] == "submitted"


def test_day_order_and_next() -> None:
    user = _cashier()
    a, b = ObjectId(), ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": a,
                "date": datetime(2026, 4, 3),
                "import_primary_date": None,
                "day_order": 0,
            },
            {
                "_id": b,
                "date": datetime(2026, 4, 3),
                "import_primary_date": None,
                "day_order": 1,
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        ordered = client.post(
            "/v1/transactions/day-order",
            json={"date": "2026-04-03", "ordered_ids": [str(b), str(a)]},
        )
        assert ordered.status_code == 204
        nxt = client.get("/v1/transactions/next-day-order?date=2026-04-03")

    assert nxt.status_code == 200
    assert nxt.json()["next"] == 2
    by_id = {doc["_id"]: doc["day_order"] for doc in collection.documents}
    assert by_id[b] == 0
    assert by_id[a] == 1


def test_request_delete_soft_trash_unverified_and_request_verified() -> None:
    user = _cashier()
    draft = ObjectId()
    submitted = ObjectId()
    verified = ObjectId()
    pending_clone = ObjectId()
    original = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": draft,
                "verified": False,
                "register_status": "draft",
                "cashier_id": user["_id"],
            },
            {
                "_id": submitted,
                "verified": False,
                "register_status": "submitted",
                "cashier_id": user["_id"],
            },
            {
                "_id": verified,
                "verified": True,
                "cashier_id": user["_id"],
                "deletion_requested": False,
            },
            {
                "_id": pending_clone,
                "verified": False,
                "original_transaction_id": original,
                "cashier_id": user["_id"],
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        draft_resp = client.post(f"/v1/transactions/{draft}/request-delete", json={})
        submitted_resp = client.post(
            f"/v1/transactions/{submitted}/request-delete", json={}
        )
        soft = client.post(f"/v1/transactions/{verified}/request-delete", json={})
        clone = client.post(
            f"/v1/transactions/{pending_clone}/request-delete", json={}
        )

    assert draft_resp.json() == {"result": "trashed"}
    assert submitted_resp.json() == {"result": "trashed"}
    assert soft.json() == {"result": "deletion_requested"}
    assert clone.json() == {"result": "deleted"}

    by_id = {doc["_id"]: doc for doc in collection.documents}
    assert pending_clone not in by_id
    assert by_id[draft]["trashed"] is True
    assert by_id[draft]["verified"] is False
    assert by_id[submitted]["trashed"] is True
    assert by_id[verified]["deletion_requested"] is True
    assert by_id[verified].get("trashed") is not True


def test_cashier_cannot_request_delete_another_cashiers_row() -> None:
    actor = _cashier()
    owner = ObjectId()
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "verified": False,
                "register_status": "draft",
                "cashier_id": owner,
                "trashed": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: actor

    with TestClient(app) as client:
        resp = client.post(f"/v1/transactions/{tx_id}/request-delete", json={})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert collection.documents[0].get("trashed") is not True


def test_accountant_can_request_delete_any_cashiers_row() -> None:
    actor = _accountant()
    owner = ObjectId()
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "verified": False,
                "register_status": "draft",
                "cashier_id": owner,
                "trashed": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: actor

    with TestClient(app) as client:
        resp = client.post(f"/v1/transactions/{tx_id}/request-delete", json={})

    assert resp.status_code == 200
    assert resp.json() == {"result": "trashed"}
    assert collection.documents[0]["trashed"] is True


def test_pending_edit_creates_clone() -> None:
    user = _cashier()
    original = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": original,
                "date": datetime(2026, 4, 4),
                "description": "Master",
                "amount": 10,
                "verified": True,
                "cashier_id": user["_id"],
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            f"/v1/transactions/{original}/pending-edit",
            json={"updates": {"description": "Edited", "amount": 12}},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "Edited"
    assert body["original_transaction_id"] == str(original)
    assert body["edited_after_verification"] is True
    assert body["verified"] is False
    assert len(collection.documents) == 2


def test_pending_edit_strips_idempotency_keys() -> None:
    """Clone must not keep client_row_id / import keys (unique index collision)."""
    user = _cashier()
    original = ObjectId()
    rid = "11111111-2222-3333-4444-555555555555"
    collection = FakeTransactions(
        [
            {
                "_id": original,
                "date": datetime(2026, 4, 4),
                "description": "Master",
                "amount": 10,
                "verified": True,
                "cashier_id": user["_id"],
                "client_row_id": rid,
                "daily_import_id": "imp-1",
                "import_row_key": "row-1",
                "master_import_source": "xlsx",
                "master_serial": 42,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            f"/v1/transactions/{original}/pending-edit",
            json={"updates": {"description": "Fixed mapping", "item": "Toll"}},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "Fixed mapping"
    assert body.get("client_row_id") is None
    assert body.get("daily_import_id") is None
    assert body.get("import_row_key") is None
    assert body.get("master_import_source") is None
    assert body.get("master_serial") is None
    clone = next(d for d in collection.documents if d["_id"] != original)
    for key in (
        "client_row_id",
        "daily_import_id",
        "import_row_key",
        "master_import_source",
        "master_serial",
    ):
        assert key not in clone
    original_doc = next(d for d in collection.documents if d["_id"] == original)
    assert original_doc["client_row_id"] == rid
    assert original_doc["daily_import_id"] == "imp-1"


def test_check_duplicates() -> None:
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "date": datetime.utcnow(),
                "truck_number": "T1",
                "amount": 5.0,
                "item": "Diesel",
                "description": "Fill",
                "rejected": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/check-duplicates",
            json={
                "truck_number": "T1",
                "amount": 5.0,
                "item": "Diesel",
                "description": "Fill",
                "days": 5,
            },
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_check_duplicates_bulk() -> None:
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "date": datetime.utcnow(),
                "truck_number": "T1",
                "amount": 5.0,
                "item": "Diesel",
                "description": "Fill",
                "rejected": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/check-duplicates/bulk",
            json={
                "days": 5,
                "candidates": [
                    {
                        "truck_number": "T1",
                        "amount": 5.0,
                        "item": "Diesel",
                        "description": "Fill",
                    },
                    {
                        "truck_number": "T9",
                        "amount": 1.0,
                        "item": "Other",
                        "description": "Nope",
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["results"][0]["total"] == 1
    assert body["results"][1]["total"] == 0


def test_bulk_create_transactions_idempotent() -> None:
    collection = FakeTransactions()
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    user = _cashier()
    app.dependency_overrides[current_user] = lambda: user
    rid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    rid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with TestClient(app) as client:
        first = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    {
                        "date": "2026-04-01T00:00:00",
                        "description": "A",
                        "amount": 10.0,
                        "currency": "TZS",
                        "client_row_id": rid_a,
                    },
                    {
                        "date": "2026-04-01T00:00:00",
                        "description": "B",
                        "amount": 20.0,
                        "currency": "TZS",
                        "client_row_id": rid_b,
                    },
                ]
            },
        )
        replay = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    {
                        "date": "2026-04-01T00:00:00",
                        "description": "A-retry",
                        "amount": 99.0,
                        "currency": "TZS",
                        "client_row_id": rid_a,
                    },
                ]
            },
        )

        assert first.status_code == 200
        body = first.json()
        assert body["created"] == 2
        assert body["already_exists"] == 0
        assert len(collection.documents) == 2
        assert any(e["action"] == "txn.bulk_create" for e in events.documents)

        assert replay.status_code == 200
        assert replay.json()["created"] == 0
        assert replay.json()["already_exists"] == 1
        assert replay.json()["results"][0]["transaction"]["description"] == "A"
        assert len(collection.documents) == 2

        # Mixed batch: one new + one already-known client_row_id in one RTT.
        mixed = client.post(
            "/v1/transactions/bulk-create",
            json={
                "items": [
                    {
                        "date": "2026-04-01T00:00:00",
                        "description": "B-retry",
                        "amount": 20.0,
                        "currency": "TZS",
                        "client_row_id": rid_b,
                    },
                    {
                        "date": "2026-04-01T00:00:00",
                        "description": "C",
                        "amount": 30.0,
                        "currency": "TZS",
                        "client_row_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    },
                ]
            },
        )

        assert mixed.status_code == 200
        mixed_body = mixed.json()
        assert mixed_body["created"] == 1
        assert mixed_body["already_exists"] == 1
        assert mixed_body["results"][0]["status"] == "already_exists"
        assert mixed_body["results"][1]["status"] == "created"
        assert len(collection.documents) == 3


def test_check_duplicates_bulk_case_insensitive() -> None:
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "date": datetime.utcnow(),
                "truck_number": "t1",
                "amount": 5.0,
                "item": "diesel",
                "description": "FILL",
                "rejected": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/check-duplicates/bulk",
            json={
                "days": 5,
                "candidates": [
                    {
                        "truck_number": "T1",
                        "amount": 5.0,
                        "item": "Diesel",
                        "description": "fill",
                    },
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["results"][0]["total"] == 1


def test_search_and_browse_and_meta() -> None:
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "date": datetime(2026, 5, 1, 9, 0, 0),
                "description": "Office supplies",
                "truck_number": "T9",
                "amount": 20,
                "item": "Stationery",
                "category_name": "Stationery",
                "trashed": False,
                "deletion_requested": False,
                "verified": False,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        search = client.get("/v1/transactions/search?keyword=Office")
        flat = client.get("/v1/transactions/flat?keyword=Office")
        items = client.get("/v1/transactions/browse/items")
        descs = client.get("/v1/transactions/browse/descriptions")
        months = client.get("/v1/transactions/meta/months")
        years = client.get("/v1/transactions/meta/years")
        boot = client.get("/v1/transactions/meta/bootstrap")
        overview = client.get("/v1/transactions/overview-stats")
        resolve = client.get("/v1/transactions/resolve-item?description=Office%20supplies")
        prefix = client.get("/v1/transactions/descriptions?prefix=OFF")

    assert search.status_code == 200 and search.json()["total"] == 1
    assert flat.status_code == 200 and flat.json()["total"] == 1
    assert "cashier_names" in flat.json()
    assert "Stationery" in items.json()["items"]
    assert "Office supplies" in descs.json()["items"]
    assert months.json()["total"] >= 1
    assert years.json()["total"] >= 1
    assert boot.status_code == 200
    assert boot.json()["total_months"] >= 1
    assert 2026 in boot.json()["years"]
    assert overview.status_code == 200
    assert "today" in overview.json() and "recent" in overview.json()
    assert resolve.json()["item"] == "Stationery"
    assert prefix.status_code == 200


def test_cashier_daily_uploads_endpoint() -> None:
    user = _cashier()
    other = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "cashier_id": user["_id"],
                "daily_import_id": "mine",
                "daily_import_source": "mine.xlsx",
                "date": datetime(2026, 3, 1),
                "created_at": datetime(2026, 3, 2),
                "amount": -1000,
                "currency": "TZS",
            },
            {
                "_id": ObjectId(),
                "cashier_id": user["_id"],
                "daily_import_id": "mine",
                "daily_import_source": "mine.xlsx",
                "date": datetime(2026, 3, 1),
                "created_at": datetime(2026, 3, 2),
                "amount": -25,
                "currency": "USD",
                "amount_usd": -25,
            },
            {
                "_id": ObjectId(),
                "cashier_id": other,
                "daily_import_id": "theirs",
                "daily_import_source": "theirs.xlsx",
                "date": datetime(2026, 3, 1),
                "created_at": datetime(2026, 3, 2),
                "amount": -500,
                "currency": "TZS",
                "amount_usd": -10,
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        resp = client.get("/v1/transactions/daily-uploads?limit=50")

    assert resp.status_code == 200
    body = resp.json()
    # Phase 1 composite: {items, total, cashier_names}
    # Browse Uploads is shared — cashiers see every batch.
    assert isinstance(body, dict)
    assert body["total"] == 2
    by_id = {row["_id"]: row for row in body["items"]}
    assert set(by_id) == {"mine", "theirs"}
    assert by_id["mine"]["count"] == 2
    assert by_id["mine"]["total_tzs"] == -1000.0
    assert by_id["mine"]["total_usd"] == -25.0
    assert by_id["theirs"]["total_tzs"] == -500.0
    assert by_id["theirs"]["total_usd"] == -10.0
    assert "cashier_names" in body

    with TestClient(app) as client:
        page = client.get("/v1/transactions/daily-uploads?limit=1&skip=0")
        page2 = client.get("/v1/transactions/daily-uploads?limit=1&skip=1")
    assert page.status_code == 200
    assert page2.status_code == 200
    p1, p2 = page.json(), page2.json()
    assert p1["total"] == 2
    assert p2["total"] == 2
    assert len(p1["items"]) == 1
    assert len(p2["items"]) == 1
    assert {p1["items"][0]["_id"], p2["items"][0]["_id"]} == {"mine", "theirs"}

    with TestClient(app) as client:
        scoped = client.get(
            f"/v1/transactions/daily-uploads?limit=50&cashier_id={user['_id']}"
        )
    assert scoped.status_code == 200
    scoped_body = scoped.json()
    assert scoped_body["total"] == 1
    assert scoped_body["items"][0]["_id"] == "mine"


def test_overview_stats_includes_all_cashiers() -> None:
    user = _cashier()
    other = ObjectId()
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "cashier_id": user["_id"],
                "date": today,
                "amount": 1_000,
                "trashed": False,
                "verified": True,
                "receipt_status": "received",
            },
            {
                "_id": ObjectId(),
                "cashier_id": other,
                "date": today,
                "amount": 4_000,
                "trashed": False,
                "verified": False,
                "receipt_status": "pending",
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        all_cashiers = client.get("/v1/transactions/overview-stats")
        scoped = client.get(
            f"/v1/transactions/overview-stats?cashier_id={user['_id']}"
        )

    assert all_cashiers.status_code == 200
    month = all_cashiers.json()["month"]
    assert month["count"] == 2
    assert month["tzs_total"] == 5_000.0
    assert month["verified"] == 1
    assert month["receipt_pending"] == 1

    assert scoped.status_code == 200
    scoped_month = scoped.json()["month"]
    assert scoped_month["count"] == 1
    assert scoped_month["tzs_total"] == 1_000.0


def test_overview_charts() -> None:
    user = _cashier()
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "cashier_id": user["_id"],
                "date": datetime(2026, 3, 12, 9, 0, 0),
                "amount": 40_000,
                "currency": "TZS",
                "item": "DIESEL",
                "payee": "INFINITY",
                "trashed": False,
                "discarded": False,
            },
            {
                "_id": ObjectId(),
                "cashier_id": user["_id"],
                "date": datetime(2026, 4, 2, 9, 0, 0),
                "amount": 10_000,
                "currency": "TZS",
                "category_name": "MILEAGE",
                "payee": "LAKE ZAMBIA",
                "trashed": False,
                "discarded": False,
            },
            {
                "_id": ObjectId(),
                "cashier_id": ObjectId(),
                "date": datetime(2026, 4, 2, 9, 0, 0),
                "amount": 99_000,
                "currency": "TZS",
                "item": "OTHER CASHIER",
                "payee": "SOMEONE",
                "trashed": False,
                "discarded": False,
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        resp = client.get("/v1/transactions/overview-charts?year=2026")

    assert resp.status_code == 200
    body = resp.json()
    assert body["year"] == 2026
    names = [row["name"] for row in body["items"]]
    assert "DIESEL" in names
    assert "MILEAGE" in names
    # Overview aggregates all cashiers (Merged-style), not only the actor.
    assert "OTHER CASHIER" in names
    payee_labels = [row["label"] for row in body["payees"]["series"]]
    assert "INFINITY" in payee_labels
    assert "SOMEONE" in payee_labels
    assert "Unassigned" not in payee_labels
    assert body["daily"]["2026-03-12"]["tzs"] == 40_000.0
    assert body["daily"]["2026-03-12"]["count"] == 1.0
    assert body["daily"]["2026-04-02"]["tzs"] == 109_000.0
    assert body["daily"]["2026-04-02"]["count"] == 2.0
    assert body["payees"]["top_n"] == 5

    with TestClient(app) as client:
        scoped = client.get(
            f"/v1/transactions/overview-charts?year=2026&cashier_id={user['_id']}"
        )
    assert scoped.status_code == 200
    scoped_names = [row["name"] for row in scoped.json()["items"]]
    assert "DIESEL" in scoped_names
    assert "OTHER CASHIER" not in scoped_names


def test_rejected_discarded_and_drafts() -> None:
    user = _cashier()
    rejected_id = ObjectId()
    discarded_id = ObjectId()
    draft_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": rejected_id,
                "cashier_id": user["_id"],
                "rejected": True,
                "discarded": False,
                "date": datetime(2026, 6, 1),
                "description": "rej",
            },
            {
                "_id": discarded_id,
                "cashier_id": user["_id"],
                "rejected": True,
                "discarded": True,
                "date": datetime(2026, 6, 2),
                "description": "disc",
            },
            {
                "_id": draft_id,
                "cashier_id": user["_id"],
                "register_status": "draft",
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
                "date": datetime(2026, 6, 3, 10, 0, 0),
                "import_primary_date": None,
                "amount": 3,
                "description": "draft",
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        rejected = client.get("/v1/transactions/cashier/rejected")
        discarded = client.get("/v1/transactions/cashier/discarded")
        discard = client.post(
            "/v1/transactions/cashier/discard",
            json={"ids": [str(rejected_id)]},
        )
        restore = client.post(
            "/v1/transactions/cashier/restore-discarded",
            json={"ids": [str(discarded_id)]},
        )
        drafts_count = client.get("/v1/transactions/drafts/count")
        drafts = client.get("/v1/transactions/drafts?date=2026-06-03")
        draft_days = client.get("/v1/transactions/drafts/day-summaries")
        drop_drafts = client.post(
            "/v1/transactions/drafts/discard",
            json={"ids": [str(draft_id)]},
        )
        # Re-seed a second draft day with two rows, then discard-by-day.
        d1, d2 = ObjectId(), ObjectId()
        collection.documents.extend(
            [
                {
                    "_id": d1,
                    "cashier_id": user["_id"],
                    "register_status": "draft",
                    "verified": False,
                    "rejected": False,
                    "discarded": False,
                    "deletion_requested": False,
                    "trashed": False,
                    "date": datetime(2026, 6, 4, 9, 0, 0),
                    "import_primary_date": None,
                    "amount": 1,
                    "description": "draft-a",
                },
                {
                    "_id": d2,
                    "cashier_id": user["_id"],
                    "register_status": "draft",
                    "verified": False,
                    "rejected": False,
                    "discarded": False,
                    "deletion_requested": False,
                    "trashed": False,
                    "date": datetime(2026, 6, 4, 10, 0, 0),
                    "import_primary_date": None,
                    "amount": 2,
                    "description": "draft-b",
                },
            ]
        )
        drop_day = client.post(
            "/v1/transactions/drafts/discard-day",
            json={"date": "2026-06-04"},
        )
        resubmit = client.post(
            "/v1/transactions/cashier/resubmit",
            json={"updates_by_id": {str(discarded_id): {"description": "fixed"}}},
        )

    assert rejected.status_code == 200 and rejected.json()["total"] == 1
    assert discarded.status_code == 200 and discarded.json()["total"] == 1
    assert discard.json()["updated"] == 1
    assert restore.json()["updated"] == 1
    assert drafts_count.json()["count"] == 1
    assert drafts.json()["total"] == 1
    assert draft_days.status_code == 200
    assert drop_drafts.json()["deleted"] == 1
    assert drop_day.status_code == 200
    assert drop_day.json()["deleted"] == 2
    # discarded_id was restored to rejected=False via restore, so resubmit of
    # discarded_id as rejected may be 0; after restore discarded=False and rejected
    # still True on discarded_id — wait, restore only sets discarded False, keeps rejected True.
    # resubmit requires rejected True and discarded ne True — after restore that matches.
    assert resubmit.json()["updated"] == 1


def test_bulk_patch_transactions_one_audit() -> None:
    cat_id = ObjectId()
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": a,
                "description": "OLD A",
                "payee": "",
                "item": "",
                "verified": False,
            },
            {
                "_id": b,
                "description": "OLD B",
                "payee": "",
                "item": "",
                "verified": False,
            },
        ]
    )
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: _cashier()

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/bulk-patch",
            json={
                "items": [
                    {
                        "id": str(a),
                        "values": {
                            "payee": "CITY",
                            "item": "Parking",
                            "category_name": "Parking",
                            "category_id": str(cat_id),
                        },
                    },
                    {
                        "id": str(b),
                        "values": {"description": "LATRA FEE"},
                    },
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 2
    assert body["updated"] == 2
    docs = {d["_id"]: d for d in collection.documents}
    assert docs[a]["payee"] == "CITY"
    assert docs[a]["item"] == "Parking"
    assert docs[a]["category_id"] == cat_id
    assert docs[b]["description"] == "LATRA FEE"
    assert sum(1 for e in events.documents if e["action"] == "txn.bulk_update") == 1


def test_apply_description_mapping_cashier_scoped() -> None:
    cash_a = ObjectId()
    cash_b = ObjectId()
    cat_id = ObjectId()
    day = datetime(2026, 10, 13, 8, 0, 0)
    own = ObjectId()
    other = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": own,
                "cashier_id": cash_a,
                "description": "  mileage  trip ",
                "date": day,
                "import_primary_date": None,
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
                "payee": "",
                "item": "",
            },
            {
                "_id": other,
                "cashier_id": cash_b,
                "description": "MILEAGE TRIP",
                "date": day,
                "import_primary_date": None,
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
                "payee": "",
                "item": "",
            },
        ]
    )
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: _cashier(cash_a)

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/apply-description-mapping",
            json={
                "description_keys": ["MILEAGE TRIP"],
                "payee": "MILEAGE",
                "category_name": "DIRECT COSTS:MILEAGE EXPENSE",
                "category_id": str(cat_id),
                "dates": ["2026-10-13"],
                "client_job_id": "job-1",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 1
    assert body["updated"] == 1
    own_doc = next(d for d in collection.documents if d["_id"] == own)
    other_doc = next(d for d in collection.documents if d["_id"] == other)
    assert own_doc["payee"] == "MILEAGE"
    assert own_doc["item"] == "DIRECT COSTS:MILEAGE EXPENSE"
    assert own_doc["category_id"] == cat_id
    assert other_doc["payee"] == ""
    assert any(e["action"] == "txn.bulk_mapping_apply" for e in events.documents)


def test_apply_description_mapping_accountant_all_cashiers() -> None:
    cash_a = ObjectId()
    cash_b = ObjectId()
    cat_id = ObjectId()
    day = datetime(2026, 10, 13, 8, 0, 0)
    collection = FakeTransactions(
        [
            {
                "_id": ObjectId(),
                "cashier_id": cash_a,
                "description": "LATRA FEE",
                "date": day,
                "import_primary_date": None,
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
            },
            {
                "_id": ObjectId(),
                "cashier_id": cash_b,
                "description": "LATRA FEE",
                "date": day,
                "import_primary_date": None,
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
            },
            {
                "_id": ObjectId(),
                "cashier_id": cash_a,
                "description": "LATRA FEE",
                "date": day,
                "import_primary_date": None,
                "verified": True,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _accountant()

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/apply-description-mapping",
            json={
                "description_keys": ["LATRA FEE"],
                "payee": "CITY",
                "category_name": "Parking",
                "category_id": str(cat_id),
                "dates": ["2026-10-13"],
            },
        )
        again = client.post(
            "/v1/transactions/apply-description-mapping",
            json={
                "description_keys": ["LATRA FEE"],
                "payee": "CITY",
                "category_name": "Parking",
                "category_id": str(cat_id),
                "dates": ["2026-10-13"],
            },
        )

    assert response.status_code == 200
    assert response.json()["matched"] == 2
    assert response.json()["updated"] == 2
    # Idempotent second apply still matches open rows.
    assert again.status_code == 200
    assert again.json()["matched"] == 2
    stamped = [
        d for d in collection.documents if d.get("payee") == "CITY" and not d.get("verified")
    ]
    assert len(stamped) == 2


def test_apply_description_mapping_clear_wipes_payee_item() -> None:
    cash_a = ObjectId()
    cat_id = ObjectId()
    day = datetime(2026, 10, 13, 8, 0, 0)
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "cashier_id": cash_a,
                "description": "LATRA FEE",
                "date": day,
                "import_primary_date": None,
                "verified": False,
                "rejected": False,
                "discarded": False,
                "deletion_requested": False,
                "trashed": False,
                "payee": "CITY",
                "item": "Parking",
                "category_name": "Parking",
                "category_id": cat_id,
            },
        ]
    )
    events = FakeOperationEvents()
    app = _app(collection, events=events)
    app.dependency_overrides[current_user] = lambda: _cashier(cash_a)

    with TestClient(app) as client:
        response = client.post(
            "/v1/transactions/apply-description-mapping",
            json={
                "description_keys": ["LATRA FEE"],
                "clear": True,
                "dates": ["2026-10-13"],
            },
        )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    doc = next(d for d in collection.documents if d["_id"] == tx_id)
    assert doc["payee"] == ""
    assert doc["item"] == ""
    assert doc["category_name"] == ""
    assert doc["category_id"] is None
    assert any(e["action"] == "txn.bulk_mapping_clear" for e in events.documents)
