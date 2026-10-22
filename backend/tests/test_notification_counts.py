from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.cli.migrate import create_indexes
from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeTransactions:
    def __init__(
        self,
        count: int = 0,
        deletion_count: int = 0,
        restore_count: int = 0,
    ) -> None:
        self.count = count
        self.deletion_count = deletion_count
        self.restore_count = restore_count
        self.queries: list[dict] = []

    async def count_documents(self, query: dict) -> int:
        self.queries.append(query)
        if query.get("deletion_requested") is True:
            return self.deletion_count
        if query.get("restore_requested") is True:
            return self.restore_count
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


class FakeBadges:
    def __init__(self, document: dict | None) -> None:
        self.document = document
        self.queries: list[dict] = []

    async def find_one(self, query: dict) -> dict | None:
        self.queries.append(query)
        if self.document is None:
            return None
        if query.get("_id") != self.document.get("_id"):
            return None
        return dict(self.document)


@pytest.mark.parametrize("role", ["admin", "accountant"])
def test_notification_counts_reads_stored_badges(role: str) -> None:
    transactions = FakeTransactions(count=7, deletion_count=3)
    badges = FakeBadges(
        {
            "_id": "badges",
            "verify": 7,
            "deletion_requests": 3,
            "restore_requests": 0,
        }
    )
    database = SimpleNamespace(
        db=SimpleNamespace(transactions=transactions, system_counters=badges)
    )
    app = create_app(settings=settings(), database=database)
    app.dependency_overrides[current_user] = lambda: {"role": role}

    with TestClient(app) as client:
        response = client.get("/v1/accountant/notification-counts")

    assert response.status_code == 200
    assert response.json() == {"verify": 10}
    assert response.headers["etag"] == '"0"'
    assert transactions.queries == []
    assert badges.queries == [{"_id": "badges"}]

    with TestClient(app) as client:
        same = client.get(
            "/v1/accountant/notification-counts",
            headers={"If-None-Match": '"0"'},
        )
    assert same.status_code == 304
    assert same.content == b""
    assert len(badges.queries) == 2

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

    assert (
        [("verified", 1), ("rejected", 1)],
        {"name": "pending_verification"},
    ) in database.transactions.indexes
