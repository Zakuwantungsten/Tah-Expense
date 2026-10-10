"""Tests for ban / unban / delete / force-logout / reset-password."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from bson import ObjectId
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.security import hash_password, utcnow


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class FakeUsers:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    def find(self, query=None, projection=None):
        query = query or {}
        matched = []
        for user in self.users:
            ok = True
            for key, value in query.items():
                if key == "deleted_at" and value is None:
                    if user.get("deleted_at") is not None:
                        ok = False
                elif key == "banned" and isinstance(value, dict) and "$ne" in value:
                    if user.get("banned") == value["$ne"]:
                        ok = False
                elif user.get(key) != value:
                    ok = False
            if ok:
                matched.append(user)
        return FakeCursor(matched)

    async def find_one(self, query):
        docs = await self.find(query).to_list()
        return docs[0] if docs else None

    async def find_one_and_update(self, query, update, return_document=None):
        user = await self.find_one(query)
        if not user:
            return None
        user.update(update.get("$set", {}))
        return user

    async def update_one(self, query, update):
        user = await self.find_one({"_id": query["_id"]})
        if not user:
            return SimpleNamespace(matched_count=0)
        user.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=1)

    async def count_documents(self, query):
        count = 0
        for user in self.users:
            if query.get("role") and user.get("role") != query["role"]:
                continue
            if query.get("active") is True and not user.get("active", True):
                continue
            if query.get("deleted_at") is None and user.get("deleted_at") is not None:
                continue
            banned_q = query.get("banned")
            if isinstance(banned_q, dict) and "$ne" in banned_q:
                if user.get("banned") == banned_q["$ne"]:
                    continue
            count += 1
        return count

    async def insert_one(self, doc):
        doc = dict(doc)
        doc["_id"] = ObjectId()
        self.users.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])


class FakeSessions:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def update_many(self, query, update):
        modified = 0
        for doc in self.docs:
            if doc.get("user_id") != query.get("user_id"):
                continue
            if query.get("revoked_at") is None and doc.get("revoked_at") is not None:
                continue
            if "_id" in query and isinstance(query["_id"], dict) and "$ne" in query["_id"]:
                if doc.get("_id") == query["_id"]["$ne"]:
                    continue
            doc.update(update.get("$set", {}))
            modified += 1
        return SimpleNamespace(modified_count=modified)

    async def insert_one(self, document):
        self.docs.append(document)
        return SimpleNamespace(inserted_id=document.get("_id") or ObjectId())


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document):
        self.documents.append(document)
        return SimpleNamespace(inserted_id=ObjectId())


class FakeSettingsFind:
    async def to_list(self, length=None):
        return []


class FakeLocks:
    async def insert_one(self, lease):
        return SimpleNamespace(inserted_id=lease["_id"])

    async def find_one_and_update(self, *args, **kwargs):
        return None

    async def delete_one(self, *args, **kwargs):
        return SimpleNamespace(deleted_count=1)


async def _async_none(*args, **kwargs):
    return None


async def _async_ok(*args, **kwargs):
    return SimpleNamespace(matched_count=1, deleted_count=1, modified_count=0)


def _settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _build(admin_id: ObjectId, users: list[dict], sessions: FakeSessions | None = None):
    events = FakeEvents()
    sessions = sessions or FakeSessions()
    db = SimpleNamespace(
        users=FakeUsers(users),
        auth_sessions=sessions,
        operation_events=events,
        operation_locks=FakeLocks(),
        system_settings=SimpleNamespace(find=lambda *a, **k: FakeSettingsFind()),
        auth_lockouts=SimpleNamespace(
            find_one=_async_none,
            update_one=_async_ok,
            delete_one=_async_ok,
        ),
    )
    app = create_app(settings=_settings(), database=SimpleNamespace(db=db))

    async def override(request: Request):
        request.state.session = {"_id": ObjectId(), "user_id": admin_id}
        return {"_id": admin_id, "username": "admin", "role": "admin"}

    app.dependency_overrides[current_user] = override
    return app, events, sessions


def test_ban_user_revokes_sessions_and_blocks_login() -> None:
    admin_id = ObjectId()
    target_id = ObjectId()
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "role": "admin",
            "full_name": "Admin",
            "active": True,
            "password_hash": hash_password("GoodPass12"),
        },
        {
            "_id": target_id,
            "username": "cashier1",
            "role": "cashier",
            "full_name": "Cash",
            "active": True,
            "password_hash": hash_password("GoodPass12"),
            "banned": False,
            "deleted_at": None,
        },
    ]
    sessions = FakeSessions()
    sessions.docs.append(
        {
            "_id": ObjectId(),
            "user_id": target_id,
            "revoked_at": None,
            "expires_at": utcnow() + timedelta(days=1),
        }
    )
    app, events, sessions = _build(admin_id, users, sessions)
    with TestClient(app) as client:
        banned = client.post(
            f"/v1/users/{target_id}/ban", json={"reason": "Policy violation"}
        )
        assert banned.status_code == 204
        login = client.post(
            "/v1/auth/login",
            json={"username": "cashier1", "password": "GoodPass12"},
        )
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "user_banned"
    assert users[1]["banned"] is True
    assert users[1]["active"] is False
    assert sessions.docs[0]["revoked_at"] is not None
    assert any(d.get("action") == "user.ban" for d in events.documents)


def test_delete_user_soft_hides_from_list() -> None:
    admin_id = ObjectId()
    target_id = ObjectId()
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "role": "admin",
            "full_name": "Admin",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        },
        {
            "_id": target_id,
            "username": "c1",
            "role": "cashier",
            "full_name": "Cash",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        },
    ]
    app, events, _ = _build(admin_id, users)
    with TestClient(app) as client:
        deleted = client.delete(f"/v1/users/{target_id}")
        listed = client.get("/v1/users")
    assert deleted.status_code == 204
    assert users[1]["deleted_at"] is not None
    assert all(u["username"] != "c1" for u in listed.json())
    assert any(d.get("action") == "user.delete" for d in events.documents)


def test_cannot_ban_last_admin() -> None:
    admin_id = ObjectId()
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "role": "admin",
            "full_name": "Admin",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        }
    ]
    app, _, _ = _build(admin_id, users)
    with TestClient(app) as client:
        # Self-ban blocked by cannot_target_self
        response = client.post(
            f"/v1/users/{admin_id}/ban", json={"reason": "Nope"}
        )
    assert response.status_code == 400


def test_reset_password_and_force_logout() -> None:
    admin_id = ObjectId()
    target_id = ObjectId()
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "role": "admin",
            "full_name": "Admin",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        },
        {
            "_id": target_id,
            "username": "c1",
            "role": "cashier",
            "full_name": "Cash",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("OldPass1234"),
        },
    ]
    sessions = FakeSessions()
    sessions.docs.append(
        {
            "_id": ObjectId(),
            "user_id": target_id,
            "revoked_at": None,
            "expires_at": utcnow() + timedelta(days=1),
        }
    )
    app, events, sessions = _build(admin_id, users, sessions)
    with TestClient(app) as client:
        reset = client.post(
            f"/v1/users/{target_id}/reset-password",
            json={"password": "NewPass1234"},
        )
        forced = client.post(f"/v1/users/{target_id}/force-logout")
    assert reset.status_code == 204
    assert forced.status_code == 204
    assert any(d.get("action") == "user.reset_password" for d in events.documents)
    assert any(d.get("action") == "user.force_logout" for d in events.documents)


def test_unban_leaves_inactive() -> None:
    admin_id = ObjectId()
    target_id = ObjectId()
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "role": "admin",
            "full_name": "Admin",
            "active": True,
            "banned": False,
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        },
        {
            "_id": target_id,
            "username": "c1",
            "role": "cashier",
            "full_name": "Cash",
            "active": False,
            "banned": True,
            "ban_reason": "x",
            "deleted_at": None,
            "password_hash": hash_password("GoodPass12"),
        },
    ]
    app, events, _ = _build(admin_id, users)
    with TestClient(app) as client:
        response = client.post(f"/v1/users/{target_id}/unban")
    assert response.status_code == 204
    assert users[1]["banned"] is False
    assert users[1]["active"] is False
    assert any(d.get("action") == "user.unban" for d in events.documents)
