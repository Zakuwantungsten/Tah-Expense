"""Phase 10.2–10.3 — Chart of Accounts import API."""

from __future__ import annotations

from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def delete_many(self, query: dict) -> SimpleNamespace:
        if query == {}:
            count = len(self.documents)
            self.documents.clear()
            return SimpleNamespace(deleted_count=count)
        keep = []
        deleted = 0
        for doc in self.documents:
            # only {} wipe is used by CoA
            keep.append(doc)
        self.documents = keep
        return SimpleNamespace(deleted_count=deleted)

    async def insert_many(
        self, documents: list[dict], ordered: bool = True
    ) -> SimpleNamespace:
        ids = []
        for document in documents:
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            self.documents.append(stored)
            ids.append(stored["_id"])
        return SimpleNamespace(inserted_ids=ids)

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


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
    *,
    categories: FakeColl | None = None,
    subtables: FakeColl | None = None,
    rules: FakeColl | None = None,
    mappings: FakeColl | None = None,
):
    cats = categories or FakeColl([{"name": "OLD"}])
    database = SimpleNamespace(
        db=SimpleNamespace(
            categories=cats,
            category_subtables=subtables or FakeColl([{"name": "SUB"}]),
            keyword_rules=rules or FakeColl([{"pattern": "x"}]),
            description_mappings=mappings or FakeColl([{"description": "y"}]),
            operation_events=FakeEvents(),
        )
    )
    return create_app(settings=settings(), database=database), cats


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct", "role": "accountant"}


def test_coa_import_replace() -> None:
    app, cats = _app()
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        response = client.post(
            "/v1/imports/chart-of-accounts",
            json={
                "replace_existing": True,
                "source_filename": "Chart.xlsx",
                "rows": [
                    {
                        "name": "fuel",
                        "ref_num": "1",
                        "account_type": "expense",
                        "coa_description": "diesel",
                        "account_number": "5000",
                        "currency": "tzs",
                    },
                    {"name": "toll"},
                ],
            },
        )
    assert response.status_code == 201
    body = response.json()
    assert body["imported"] == 2
    assert body["removed"]["categories"] == 1
    assert body["removed"]["subtables"] == 1
    assert body["removed"]["keyword_rules"] == 1
    assert body["removed"]["mappings"] == 1
    assert body["source_filename"] == "Chart.xlsx"
    assert len(cats.documents) == 2
    assert cats.documents[0]["name"] == "FUEL"
    assert cats.documents[0]["currency"] == "TZS"
    assert cats.documents[0]["coa_description"] == "DIESEL"


def test_coa_import_empty_rejected() -> None:
    app, _cats = _app()
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        response = client.post(
            "/v1/imports/chart-of-accounts",
            json={"rows": [], "replace_existing": True},
        )
        blank = client.post(
            "/v1/imports/chart-of-accounts",
            json={"rows": [{"name": "  "}], "replace_existing": True},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_rows"
    assert blank.status_code == 422


def test_coa_import_without_replace_keeps_existing() -> None:
    app, cats = _app(categories=FakeColl([{"name": "KEEP"}]))
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        response = client.post(
            "/v1/imports/chart-of-accounts",
            json={
                "replace_existing": False,
                "rows": [{"name": "NEW"}],
            },
        )
    assert response.status_code == 201
    assert response.json()["removed"]["categories"] == 0
    assert {d["name"] for d in cats.documents} == {"KEEP", "NEW"}
