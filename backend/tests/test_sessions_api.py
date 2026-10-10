"""Tests for admin session list and force-logout."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bson import ObjectId
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.security import utcnow

UTC = timezone.utc


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        key = args[0]
        reverse = len(args) > 1 and args[1] < 0
        self._docs.sort(
            key=lambda d: d.get(key) or datetime.min.replace(tzinfo=UTC),
            reverse=reverse,
        )
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


def _matches(doc: dict, query: dict) -> bool:
    for key, value in query.items():
        if key == "expires_at" and isinstance(value, dict) and "$gt" in value:
            if not (doc.get("expires_at") and doc["expires_at"] > value["$gt"]):
                return False
        elif key == "_id" and isinstance(value, dict) and "$ne" in value:
            if doc.get("_id") == value["$ne"]:
                return False
        elif key == "user_id" and isinstance(value, dict) and "$in" in value:
            if doc.get("user_id") not in value["$in"]:
                return False
        elif key == "_id" and isinstance(value, dict) and "$in" in value:
            if doc.get("_id") not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class FakeSessions:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = list(docs or [])

    def find(self, query=None):
        query = query or {}
        return FakeCursor([doc for doc in self.docs if _matches(doc, query)])

    async def find_one(self, query):
        docs = await self.find(query).to_list()
        return docs[0] if docs else None

    async def update_one(self, query, update):
        doc = await self.find_one(query)
        if not doc:
            if "_id" in query and any(d.get("_id") == query["_id"] for d in self.docs):
                return SimpleNamespace(matched_count=1, modified_count=0)
            return SimpleNamespace(matched_count=0, modified_count=0)
        doc.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def update_many(self, query, update):
        docs = [doc for doc in self.docs if _matches(doc, query)]
        for doc in docs:
            doc.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=len(docs), matched_count=len(docs))


class FakeUsers:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    def find(self, query=None):
        query = query or {}
        matched = []
        for user in self.users:
            if "_id" in query and isinstance(query["_id"], dict) and "$in" in query["_id"]:
                if user["_id"] not in query["_id"]["$in"]:
                    continue
            elif "_id" in query and user["_id"] != query["_id"]:
                continue
            matched.append(user)
        return FakeCursor(matched)


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document):
        self.documents.append(document)
        return SimpleNamespace(inserted_id=ObjectId())


class FakeSettingsFind:
    async def to_list(self, length=None):
        return []


def _settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    sessions: FakeSessions,
    users: list[dict],
    events: FakeEvents | None = None,
    *,
    session_state: dict | None = None,
):
    events = events or FakeEvents()
    db = SimpleNamespace(
        auth_sessions=sessions,
        users=FakeUsers(users),
        operation_events=events,
        system_settings=SimpleNamespace(find=lambda *a, **k: FakeSettingsFind()),
    )
    app = create_app(settings=_settings(), database=SimpleNamespace(db=db))

    async def override_user(request: Request):
        if session_state is not None:
            request.state.session = session_state
        return {
            "_id": session_state["user_id"] if session_state else users[0]["_id"],
            "username": "admin",
            "role": "admin",
        }

    app.dependency_overrides[current_user] = override_user
    return app, events


def test_list_sessions_admin_only() -> None:
    now = utcnow()
    admin_id = ObjectId()
    session_id = ObjectId()
    sessions = FakeSessions(
        [
            {
                "_id": session_id,
                "user_id": admin_id,
                "ip": "203.0.113.10",
                "device_mac": "AA:BB:CC:DD:EE:FF",
                "user_agent": "Tahmeed",
                "created_at": now,
                "last_used_at": now,
                "expires_at": now + timedelta(days=7),
                "revoked_at": None,
            }
        ]
    )
    users = [
        {
            "_id": admin_id,
            "username": "admin",
            "full_name": "Admin",
            "role": "admin",
            "active": True,
        }
    ]
    app, _ = _app(
        sessions, users, session_state={"_id": session_id, "user_id": admin_id}
    )

    # Accountant denied
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        assert client.get("/v1/sessions").status_code == 403

    async def admin_user(request: Request):
        request.state.session = {"_id": session_id, "user_id": admin_id}
        return {"_id": admin_id, "username": "admin", "role": "admin"}

    app.dependency_overrides[current_user] = admin_user
    with TestClient(app) as client:
        response = client.get("/v1/sessions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["username"] == "admin"
    assert body[0]["device_mac"] == "AA:BB:CC:DD:EE:FF"
    assert body[0]["is_current"] is True


def test_revoke_session_writes_audit() -> None:
    now = utcnow()
    admin_id = ObjectId()
    target_user = ObjectId()
    current_sid = ObjectId()
    target_sid = ObjectId()
    sessions = FakeSessions(
        [
            {
                "_id": current_sid,
                "user_id": admin_id,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
            {
                "_id": target_sid,
                "user_id": target_user,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
        ]
    )
    app, events = _app(
        sessions,
        [
            {"_id": admin_id, "username": "admin", "full_name": "A", "role": "admin"},
            {"_id": target_user, "username": "c1", "full_name": "C", "role": "cashier"},
        ],
        session_state={"_id": current_sid, "user_id": admin_id},
    )
    with TestClient(app) as client:
        response = client.post(f"/v1/sessions/{target_sid}/revoke")
    assert response.status_code == 204
    target = next(s for s in sessions.docs if s["_id"] == target_sid)
    assert target["revoked_at"] is not None
    assert any(d.get("action") == "session.revoke" for d in events.documents)


def test_cannot_revoke_own_session_via_api() -> None:
    now = utcnow()
    admin_id = ObjectId()
    current_sid = ObjectId()
    sessions = FakeSessions(
        [
            {
                "_id": current_sid,
                "user_id": admin_id,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            }
        ]
    )
    app, _ = _app(
        sessions,
        [{"_id": admin_id, "username": "admin", "full_name": "A", "role": "admin"}],
        session_state={"_id": current_sid, "user_id": admin_id},
    )
    with TestClient(app) as client:
        response = client.post(f"/v1/sessions/{current_sid}/revoke")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cannot_revoke_current"


def test_revoke_all_keeps_caller_session() -> None:
    now = utcnow()
    admin_id = ObjectId()
    other = ObjectId()
    current_sid = ObjectId()
    other_sid = ObjectId()
    sessions = FakeSessions(
        [
            {
                "_id": current_sid,
                "user_id": admin_id,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
            {
                "_id": other_sid,
                "user_id": other,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
        ]
    )
    app, events = _app(
        sessions,
        [
            {"_id": admin_id, "username": "admin", "full_name": "A", "role": "admin"},
            {"_id": other, "username": "c1", "full_name": "C", "role": "cashier"},
        ],
        session_state={"_id": current_sid, "user_id": admin_id},
    )
    with TestClient(app) as client:
        response = client.post("/v1/sessions/revoke-all")
    assert response.status_code == 204
    assert sessions.docs[0]["revoked_at"] is None
    assert sessions.docs[1]["revoked_at"] is not None
    assert any(d.get("action") == "session.revoke_all" for d in events.documents)


def test_revoke_user_sessions() -> None:
    now = utcnow()
    admin_id = ObjectId()
    target = ObjectId()
    current_sid = ObjectId()
    t1 = ObjectId()
    t2 = ObjectId()
    sessions = FakeSessions(
        [
            {
                "_id": current_sid,
                "user_id": admin_id,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
            {
                "_id": t1,
                "user_id": target,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
            {
                "_id": t2,
                "user_id": target,
                "revoked_at": None,
                "expires_at": now + timedelta(days=1),
                "last_used_at": now,
                "created_at": now,
            },
        ]
    )
    app, events = _app(
        sessions,
        [
            {"_id": admin_id, "username": "admin", "full_name": "A", "role": "admin"},
            {"_id": target, "username": "c1", "full_name": "C", "role": "cashier"},
        ],
        session_state={"_id": current_sid, "user_id": admin_id},
    )
    with TestClient(app) as client:
        response = client.post(f"/v1/sessions/revoke-user/{target}")
    assert response.status_code == 204
    assert sessions.docs[1]["revoked_at"] is not None
    assert sessions.docs[2]["revoked_at"] is not None
    assert any(d.get("action") == "session.revoke_user" for d in events.documents)
