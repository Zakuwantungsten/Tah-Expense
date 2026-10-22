"""Catalog snapshot: one sorted payload, an ETag, and a 304."""

from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)
        self.skipped = False

    def sort(self, keys):
        field = keys[0][0]
        self.documents.sort(key=lambda doc: str(doc.get(field) or ""))
        return self

    def skip(self, _count: int):
        self.skipped = True
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        assert self.skipped is False
        if length is None:
            return list(self.documents)
        return list(self.documents)[:length]


class FakeCatalogCollection:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = [dict(doc) for doc in (documents or [])]
        self.finds = 0

    def find(self, query: dict) -> FakeCursor:
        self.finds += 1
        matched: list[dict] = []
        for doc in self.documents:
            active = query.get("active")
            if active is True and doc.get("active") is not True:
                continue
            if isinstance(active, dict) and active.get("$ne") is False and doc.get("active") is False:
                continue
            matched.append(dict(doc))
        return FakeCursor(matched)

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


class FakeSettings:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def find(self, _query: dict) -> FakeCursor:
        return FakeCursor(list(self.documents.values()))


class FakeCounters:
    def __init__(self) -> None:
        self.doc: dict | None = None

    async def find_one(self, query: dict) -> dict | None:
        if self.doc and self.doc.get("_id") == query.get("_id"):
            return dict(self.doc)
        return None

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        inc = int((update.get("$inc") or {}).get("catalog_version") or 0)
        if self.doc is None and upsert:
            self.doc = {"_id": query["_id"], "catalog_version": 0}
        if self.doc is None:
            return None
        self.doc["catalog_version"] = int(self.doc.get("catalog_version") or 0) + inc
        return dict(self.doc)


class FakeEvents:
    async def insert_one(self, document: dict) -> SimpleNamespace:
        return SimpleNamespace(inserted_id=ObjectId())


def _app():
    categories = FakeCatalogCollection(
        [
            {"_id": ObjectId(), "name": "Zebra", "active": True},
            {"_id": ObjectId(), "name": "Alpha", "active": True},
            {"_id": ObjectId(), "name": "Hidden", "active": False},
        ]
    )
    empty = FakeCatalogCollection()
    database = SimpleNamespace(
        db=SimpleNamespace(
            categories=categories,
            payees=empty,
            trucks=empty,
            trailers=empty,
            motor_vehicles=empty,
            system_settings=FakeSettings(),
            system_counters=FakeCounters(),
            operation_events=FakeEvents(),
        )
    )
    app = create_app(
        settings=Settings(
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret="a-test-secret-that-is-definitely-32-characters",
        ),
        database=database,
    )
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    return app, categories


def test_snapshot_returns_active_categories_in_name_order_with_etag() -> None:
    app, categories = _app()
    with TestClient(app) as client:
        response = client.get("/v1/catalog/snapshot")

    assert response.status_code == 200
    assert response.headers["etag"] == '"0"'
    names = [item["name"] for item in response.json()["categories"]]
    assert names == ["Alpha", "Zebra"]
    assert categories.finds == 1
    body = response.json()
    assert "restrict_items" in body["settings"]
    assert body["allowed_truck_labels"] == ["YARD", "GARAGE"]
    assert body["catalog_version"] == 0


def test_category_write_changes_etag_and_snapshot() -> None:
    app, _categories = _app()
    with TestClient(app) as client:
        first = client.get("/v1/catalog/snapshot")
        created = client.post("/v1/categories", json={"name": "Fuel"})
        second = client.get("/v1/catalog/snapshot")

    assert created.status_code == 201
    assert first.headers["etag"] == '"0"'
    assert second.headers["etag"] == '"1"'
    assert "Fuel" in [item["name"] for item in second.json()["categories"]]


def test_if_none_match_returns_304() -> None:
    app, categories = _app()
    with TestClient(app) as client:
        first = client.get("/v1/catalog/snapshot")
        finds = categories.finds
        again = client.get(
            "/v1/catalog/snapshot",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]
    assert categories.finds == finds
