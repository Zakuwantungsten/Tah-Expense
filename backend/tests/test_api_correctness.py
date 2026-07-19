import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.api import (
    acquire_operation_lock,
    bootstrap,
    create_user,
    list_collection,
    list_fleet,
    release_operation_lock,
    update_user,
)
from app.config import Settings
from app.dependencies import current_user
from app.errors import ApiError
from app.main import create_app
from app.schemas import UserCreate, UserUpdate
from app.security import create_access_token, utcnow


class FakeLocks:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def insert_one(self, document: dict) -> None:
        key = document["_id"]
        if key in self.documents:
            raise DuplicateKeyError("duplicate lock")
        self.documents[key] = dict(document)

    async def find_one_and_update(self, query: dict, update: dict, **_kwargs: object) -> dict | None:
        document = self.documents.get(query["_id"])
        if not document or document["expires_at"] > query["expires_at"]["$lte"]:
            return None
        document.update(update["$set"])
        return dict(document)

    async def delete_one(self, query: dict) -> None:
        document = self.documents.get(query["_id"])
        if document and document["owner"] == query["owner"]:
            del self.documents[query["_id"]]


class FakeUsers:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])
        self.fail_insert = False

    async def count_documents(self, query: dict) -> int:
        return sum(
            all(document.get(key) == value for key, value in query.items())
            for document in self.documents
        )

    async def insert_one(self, document: dict) -> SimpleNamespace:
        if self.fail_insert:
            raise RuntimeError("insert failed")
        await asyncio.sleep(0)
        inserted_id = ObjectId()
        stored = dict(document)
        stored["_id"] = inserted_id
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=inserted_id)

    async def find_one(self, query: dict) -> dict | None:
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )

    async def find_one_and_update(self, query: dict, update: dict, **_kwargs: object) -> dict | None:
        document = await self.find_one(query)
        if document:
            document.update(update["$set"])
            return dict(document)
        return None


class FakeSessions:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.query: dict | None = None

    async def insert_one(self, document: dict) -> None:
        self.documents.append(dict(document))

    async def find_one(self, query: dict) -> dict | None:
        self.query = query
        if not self.documents:
            return None
        document = self.documents[0]
        expires_after = query.get("expires_at", {}).get("$gt")
        if expires_after is not None:
            expires_at = document.get("expires_at")
            if expires_at is None or expires_at <= expires_after:
                return None
        return document

    async def update_many(self, _query: dict, _update: dict) -> None:
        return None


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def request_for(db: object) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(database=SimpleNamespace(db=db), settings=settings())
        ),
        state=SimpleNamespace(),
        headers={},
        client=None,
    )


@pytest.mark.asyncio
async def test_operation_lock_is_exclusive_and_recovers_stale_lease() -> None:
    db = SimpleNamespace(operation_locks=FakeLocks())
    owner = await acquire_operation_lock(db, "bootstrap")
    assert owner
    assert await acquire_operation_lock(db, "bootstrap") is None

    db.operation_locks.documents["bootstrap"]["expires_at"] = utcnow() - timedelta(seconds=1)
    recovered = await acquire_operation_lock(db, "bootstrap")
    assert recovered and recovered != owner
    await release_operation_lock(db, "bootstrap", recovered)
    assert db.operation_locks.documents == {}


@pytest.mark.asyncio
async def test_bootstrap_is_atomic_and_failed_insert_releases_lock() -> None:
    users = FakeUsers()
    db = SimpleNamespace(
        operation_locks=FakeLocks(),
        users=users,
        auth_sessions=FakeSessions(),
    )
    request = request_for(db)
    users.fail_insert = True
    with pytest.raises(RuntimeError, match="insert failed"):
        await bootstrap(
            UserCreate(
                username="first",
                password="long-enough-password",
                role="admin",
                full_name="First Admin",
            ),
            request,
        )
    assert db.operation_locks.documents == {}

    users.fail_insert = False
    results = await asyncio.gather(
        bootstrap(
            UserCreate(
                username="first",
                password="long-enough-password",
                role="admin",
                full_name="First Admin",
            ),
            request,
        ),
        bootstrap(
            UserCreate(
                username="second",
                password="long-enough-password",
                role="admin",
                full_name="Second Admin",
            ),
            request,
        ),
        return_exceptions=True,
    )
    assert len(users.documents) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    conflict = next(result for result in results if isinstance(result, ApiError))
    assert conflict.code in {"initialization_in_progress", "already_initialized"}


@pytest.mark.asyncio
async def test_final_active_admin_cannot_be_removed() -> None:
    admin_id = ObjectId()
    db = SimpleNamespace(
        operation_locks=FakeLocks(),
        users=FakeUsers(
            [
                {
                    "_id": admin_id,
                    "username": "admin",
                    "role": "admin",
                    "active": True,
                }
            ]
        ),
        auth_sessions=FakeSessions(),
    )
    with pytest.raises(ApiError) as raised:
        await update_user(
            str(admin_id),
            UserUpdate(active=False),
            request_for(db),
            {"role": "admin"},
        )
    assert raised.value.code == "last_admin_required"
    assert db.users.documents[0]["active"] is True


@pytest.mark.asyncio
async def test_accountant_can_manage_non_admin_users_only() -> None:
    db = SimpleNamespace(users=FakeUsers())
    request = request_for(db)
    cashier = await create_user(
        UserCreate(
            username="cashier",
            password="long-enough-password",
            role="cashier",
            full_name="Cashier User",
        ),
        request,
        {"role": "accountant"},
    )
    assert cashier["role"] == "cashier"

    with pytest.raises(ApiError) as raised:
        await create_user(
            UserCreate(
                username="another-admin",
                password="long-enough-password",
                role="admin",
                full_name="Another Admin",
            ),
            request,
            {"role": "accountant"},
        )
    assert raised.value.code == "forbidden"


@pytest.mark.asyncio
async def test_access_authentication_requires_unexpired_session() -> None:
    user_id, session_id = ObjectId(), ObjectId()
    sessions = FakeSessions()
    sessions.documents.append(
        {
            "_id": session_id,
            "user_id": user_id,
            "revoked_at": None,
            "expires_at": utcnow() + timedelta(minutes=5),
        }
    )
    users = FakeUsers(
        [
            {
                "_id": user_id,
                "username": "admin",
                "role": "admin",
                "active": True,
            }
        ]
    )
    configured = settings()
    token, _ = create_access_token(
        configured,
        user_id=str(user_id),
        role="admin",
        session_id=str(session_id),
    )
    request = request_for(SimpleNamespace(auth_sessions=sessions, users=users))
    await current_user(
        request,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
    )
    assert sessions.query is not None
    assert sessions.query["expires_at"]["$gt"].tzinfo is not None


@pytest.mark.asyncio
async def test_access_authentication_rejects_expired_session_immediately() -> None:
    user_id, session_id = ObjectId(), ObjectId()
    sessions = FakeSessions()
    sessions.documents.append(
        {
            "_id": session_id,
            "user_id": user_id,
            "revoked_at": None,
            "expires_at": utcnow() - timedelta(seconds=1),
        }
    )
    users = FakeUsers(
        [
            {
                "_id": user_id,
                "username": "admin",
                "role": "admin",
                "active": True,
            }
        ]
    )
    configured = settings()
    token, _ = create_access_token(
        configured,
        user_id=str(user_id),
        role="admin",
        session_id=str(session_id),
    )

    with pytest.raises(ApiError) as raised:
        await current_user(
            request_for(SimpleNamespace(auth_sessions=sessions, users=users)),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        )

    assert raised.value.status == 401
    assert raised.value.code == "session_revoked"


class FakeCategories:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def test_cashier_has_only_narrow_category_creation_permission() -> None:
    categories = FakeCategories()
    database = SimpleNamespace(db=SimpleNamespace(categories=categories))
    app = create_app(settings=settings(), database=database)
    app.dependency_overrides[current_user] = lambda: {"role": "cashier"}
    body = {
        "name": "Road Expense",
        "color": "#123456",
        "requires_receipt": False,
        "requires_truck": True,
    }
    with TestClient(app) as client:
        narrow = client.post("/v1/categories/cashier-create", json=body)
        manager = client.post("/v1/categories", json=body)
    assert narrow.status_code == 201
    assert manager.status_code == 403
    assert len(categories.documents) == 1


class FakeCursor:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.sort_spec: list[tuple[str, int]] = []
        self.documents = documents or []

    def sort(self, spec: list[tuple[str, int]]) -> "FakeCursor":
        self.sort_spec = spec
        return self

    def skip(self, _offset: int) -> "FakeCursor":
        return self

    def limit(self, _limit: int) -> "FakeCursor":
        return self

    async def to_list(self) -> list[dict]:
        return self.documents


class FakePagedCollection:
    def __init__(self, documents: list[dict] | None = None, total: int = 0) -> None:
        self.cursor = FakeCursor(documents)
        self.total = total

    async def count_documents(self, _query: dict) -> int:
        return self.total

    def find(self, _query: dict) -> FakeCursor:
        return self.cursor


class FakeCollections:
    def __init__(self) -> None:
        self.collections: dict[str, FakePagedCollection] = {}

    def __getitem__(self, name: str) -> FakePagedCollection:
        return self.collections.setdefault(name, FakePagedCollection())


@pytest.mark.asyncio
async def test_collection_pagination_uses_stable_domain_sorts() -> None:
    db = FakeCollections()
    request = request_for(db)
    await list_collection(request, "keyword_rules", include_inactive=True)
    await list_collection(request, "description_mappings", include_inactive=True)

    assert db["keyword_rules"].cursor.sort_spec == [
        ("priority", -1),
        ("pattern", 1),
        ("_id", 1),
    ]
    assert db["description_mappings"].cursor.sort_spec == [
        ("description", 1),
        ("_id", 1),
    ]


@pytest.mark.asyncio
async def test_fleet_endpoint_returns_page_metadata_and_stable_sort() -> None:
    db = FakeCollections()
    db.collections["trucks"] = FakePagedCollection(
        [{"_id": ObjectId(), "number": "T050", "active": True}],
        total=73,
    )

    page = await list_fleet("trucks", request_for(db), "", None, 25, 50)

    assert page["total"] == 73
    assert page["limit"] == 25
    assert page["offset"] == 50
    assert page["items"][0]["number"] == "T050"
    assert db["trucks"].cursor.sort_spec == [("number", 1), ("_id", 1)]
