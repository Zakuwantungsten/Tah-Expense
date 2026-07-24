from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.cli.migrate import create_indexes
from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeTransactions:
    def __init__(self, count: int = 0, deletion_count: int = 0) -> None:
        self.count = count
        self.deletion_count = deletion_count
        self.queries: list[dict] = []

    async def count_documents(self, query: dict) -> int:
        self.queries.append(query)
        if query.get("deletion_requested") is True:
            return self.deletion_count
        return self.count


class FakeIndexCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict]] = []

    async def create_index(self, fields: list[tuple[str, int]], **kwargs) -> None:
        self.indexes.append((fields, kwargs))

    async def create_indexes(self, _indexes: list) -> None:
        return None


class FakeIndexDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeIndexCollection] = {}

    def __getitem__(self, name: str) -> FakeIndexCollection:
        return self.collections.setdefault(name, FakeIndexCollection())

    def __getattr__(self, name: str) -> FakeIndexCollection:
        return self[name]


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


@pytest.mark.parametrize("role", ["admin", "accountant"])
def test_notification_counts_uses_pending_inbox_predicate(role: str) -> None:
    transactions = FakeTransactions(count=7, deletion_count=3)
    database = SimpleNamespace(db=SimpleNamespace(transactions=transactions))
    app = create_app(settings=settings(), database=database)
    app.dependency_overrides[current_user] = lambda: {"role": role}

    with TestClient(app) as client:
        response = client.get("/v1/accountant/notification-counts")

    assert response.status_code == 200
    assert response.json() == {"verify": 10}
    assert transactions.queries == [
        {"verified": False, "rejected": {"$ne": True}},
        {"deletion_requested": True},
    ]

def test_notification_counts_rejects_cashier() -> None:
    transactions = FakeTransactions(count=7)
    database = SimpleNamespace(db=SimpleNamespace(transactions=transactions))
    app = create_app(settings=settings(), database=database)
    app.dependency_overrides[current_user] = lambda: {"role": "cashier"}

    with TestClient(app) as client:
        response = client.get("/v1/accountant/notification-counts")

    assert response.status_code == 403
    assert transactions.queries == []


@pytest.mark.asyncio
async def test_migration_indexes_pending_verification_predicate() -> None:
    database = FakeIndexDatabase()

    await create_indexes(database)

    assert database.transactions.indexes == [
        (
            [("verified", 1), ("rejected", 1)],
            {"name": "pending_verification"},
        )
    ]
