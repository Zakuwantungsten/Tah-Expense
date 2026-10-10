"""Smoke tests: catalog / fleet / people / diesel mutations write operation_events."""

from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


class FakeInsertCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        oid = ObjectId()
        stored["_id"] = oid
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=oid)

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        values = dict(update.get("$set") or {})
        key = next(iter(query.values()), None)
        for doc in self.documents:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(values)
                return dict(doc)
        if upsert:
            stored = dict(values)
            stored["_id"] = ObjectId()
            if key is not None and list(query) == ["description_key"]:
                stored["description_key"] = key
            self.documents.append(stored)
            return dict(stored)
        return None

    async def delete_one(self, query):
        before = len(self.documents)
        self.documents = [
            d for d in self.documents if not all(d.get(k) == v for k, v in query.items())
        ]
        return SimpleNamespace(deleted_count=before - len(self.documents))

    async def delete_many(self, query):
        before = len(self.documents)
        if query == {}:
            self.documents = []
        return SimpleNamespace(deleted_count=before - len(self.documents))


class FakeUpsertCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        values = dict(update.get("$set") or {})
        for doc in self.documents:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(values)
                return dict(doc)
        if upsert:
            stored = dict(values)
            stored["_id"] = ObjectId()
            self.documents.append(stored)
            return dict(stored)
        return None

    async def delete_one(self, query):
        before = len(self.documents)
        self.documents = [
            d for d in self.documents if not all(d.get(k) == v for k, v in query.items())
        ]
        return SimpleNamespace(deleted_count=before - len(self.documents))


class FakeSystemSettings:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def find_one(self, query):
        key = query.get("key")
        return self.documents.get(key)

    async def update_one(self, query, update, upsert=False):
        key = query.get("key")
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = values
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDb:
    def __init__(self, **collections) -> None:
        for name, collection in collections.items():
            setattr(self, name, collection)

    def __getitem__(self, key: str):
        return getattr(self, key)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "mgr1", "role": "accountant"}


def _actions(events: FakeOperationEvents) -> list[str]:
    return [e["action"] for e in events.documents]


def test_category_create_writes_audit() -> None:
    events = FakeOperationEvents()
    categories = FakeInsertCollection()
    db = FakeDb(categories=categories, operation_events=events)
    app = create_app(settings=settings(), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.post(
            "/v1/categories",
            json={
                "name": "Road Expense",
                "color": "#123456",
                "requires_receipt": False,
                "requires_truck": True,
            },
        )

    assert response.status_code == 201
    assert "category.create" in _actions(events)
    assert events.documents[-1]["entity_type"] == "category"


def test_rule_create_and_mapping_upsert_write_audit() -> None:
    events = FakeOperationEvents()
    rules = FakeInsertCollection()
    mappings = FakeInsertCollection()
    db = FakeDb(
        keyword_rules=rules,
        description_mappings=mappings,
        operation_events=events,
    )
    app = create_app(settings=settings(), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: _manager()
    category_id = str(ObjectId())

    with TestClient(app) as client:
        rule = client.post(
            "/v1/keyword-rules",
            json={
                "pattern": "DIESEL",
                "category_id": category_id,
                "category_name": "Fuel",
                "priority": 5,
            },
        )
        mapping = client.put(
            "/v1/description-mappings",
            json={
                "description": "cash diesel",
                "category_id": category_id,
                "category_name": "Fuel",
            },
        )

    assert rule.status_code == 201
    assert mapping.status_code == 200
    assert "rule.create" in _actions(events)
    assert "mapping.upsert" in _actions(events)


def test_fleet_and_people_mutations_write_audit() -> None:
    events = FakeOperationEvents()
    trucks = FakeUpsertCollection()
    people = FakeUpsertCollection()
    db = FakeDb(trucks=trucks, people=people, operation_events=events)
    app = create_app(settings=settings(), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        truck = client.put("/v1/trucks/T100 AAA", json={"number": "T100 AAA", "active": True})
        person = client.put("/v1/people/JOHN DOE", json={"name": "JOHN DOE", "active": True})
        deleted = client.delete("/v1/trucks/T100 AAA")
        person_del = client.delete("/v1/people/JOHN DOE")

    assert truck.status_code == 200
    assert person.status_code == 200
    assert deleted.status_code == 204
    assert person_del.status_code == 204
    actions = _actions(events)
    assert "fleet.upsert" in actions
    assert "people.upsert" in actions
    assert "fleet.delete" in actions
    assert "people.delete" in actions


def test_diesel_items_update_writes_audit() -> None:
    events = FakeOperationEvents()
    settings_col = FakeSystemSettings()
    db = FakeDb(
        transactions=SimpleNamespace(),
        system_settings=settings_col,
        operation_events=events,
    )
    app = create_app(settings=settings(), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.put(
            "/v1/accountant/diesel-cash/items",
            json={"names": ["Diesel Cash", "Diesel"]},
        )

    assert response.status_code == 200
    assert "diesel.items_update" in _actions(events)
    assert events.documents[-1]["details"]["count"] == 2
