from __future__ import annotations

import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from .audit_ops import parse_audit_filters, record_operation_event
from .catalog_snapshot import (
    bump_catalog_version,
    catalog_snapshot_body,
    etag_for,
)
from .dependencies import Admin, Authenticated, Cashier, Manager, require_roles
from .errors import ApiError
from .security_policy import (
    DEVICE_MAC_HEADER,
    SECURITY_SETTING_DEFAULTS,
    assert_not_locked,
    clear_login_failures,
    client_ip,
    enforce_password_policy,
    is_admin_only_setting,
    normalize_mac,
    record_login_failure,
)
from .sessions_api import sessions as sessions_router
from .schemas import (
    AuditEventWrite,
    BackupRestoreRequest,
    CategoryWrite,
    ChangePasswordRequest,
    FleetWrite,
    LoginRequest,
    MappingBulkDelete,
    MappingBulkUpsert,
    MappingWrite,
    PatchDocument,
    PayeeWrite,
    PeopleWrite,
    RefreshRequest,
    RuleWrite,
    SettingWrite,
    SubtableWrite,
    UserBanRequest,
    UserCreate,
    UserResetPasswordRequest,
    UserUpdate,
)
from .security import (
    create_access_token,
    hash_password,
    hash_refresh_secret,
    new_refresh_secret,
    refresh_token,
    split_refresh_token,
    utcnow,
    verify_password,
)
from .serialization import json_safe, object_id
from .accountant_actions_api import actions as accountant_actions_router
from .accountant_deletions_api import deletions as accountant_deletions_router
from .accountant_inbox_api import inbox as accountant_inbox_router
from .accountant_diesel_api import diesel_cash as accountant_diesel_router
from .accountant_master_api import master as accountant_master_router
from .accountant_overview_api import overview as accountant_overview_router
from .accountant_rejected_api import rejected as accountant_rejected_router
from .accountant_reports_api import reports as accountant_reports_router
from .accountant_restores_api import restores as accountant_restores_router
from .accountant_trash_api import trash as accountant_trash_router
from .accountant_fuel_overview_api import (
    fuel_overview as accountant_fuel_overview_router,
)
from .accountant_truck_overview_api import (
    truck_overview as accountant_truck_overview_router,
)
from .feeds_api import feeds as feeds_router
from .imports_api import imports as imports_router
from .reconciliation_api import reconciliation as reconciliation_router
from .transactions_api import transactions as transactions_router

api = APIRouter(prefix="/v1")
auth = APIRouter(prefix="/auth", tags=["auth"])
users = APIRouter(prefix="/users", tags=["users"])
categories = APIRouter(prefix="/categories", tags=["categories"])
subtables = APIRouter(prefix="/subtables", tags=["subtables"])
rules = APIRouter(prefix="/keyword-rules", tags=["keyword-rules"])
mappings = APIRouter(prefix="/description-mappings", tags=["description-mappings"])
fleet = APIRouter(tags=["fleet"])
people = APIRouter(prefix="/people", tags=["people"])
payees = APIRouter(prefix="/payees", tags=["payees"])
backups = APIRouter(prefix="/backups", tags=["backups"])
accountant = APIRouter(prefix="/accountant", tags=["accountant"])
accountant.include_router(accountant_inbox_router)
accountant.include_router(accountant_actions_router)
accountant.include_router(accountant_rejected_router)
accountant.include_router(accountant_deletions_router)
accountant.include_router(accountant_restores_router)
accountant.include_router(accountant_trash_router)
accountant.include_router(accountant_master_router)
accountant.include_router(accountant_reports_router)
accountant.include_router(accountant_diesel_router)
accountant.include_router(accountant_overview_router)
accountant.include_router(accountant_truck_overview_router)
accountant.include_router(accountant_fuel_overview_router)
settings_api = APIRouter(prefix="/settings", tags=["settings"])
catalog_api = APIRouter(prefix="/catalog", tags=["catalog"])
audit_api = APIRouter(prefix="/audit", tags=["audit"])

_OPERATION_LOCK_SECONDS = 60

# Keep in sync with tahmeed.services.settings_service.DEFAULTS
SETTINGS_DEFAULTS: dict[str, Any] = {
    "default_currency": "TZS",
    "confidence_threshold": 75,
    "restrict_items": False,
    "restrict_payees": False,
    "defer_item_to_verify": False,
    # When True, cashiers may Submit for Verify even if Day Issues remain.
    "allow_submit_with_day_issues": False,
    # When True, typing/pasting a description auto-fills blank Item/Payee from maps.
    "auto_fill_from_description_maps": True,
    "restrict_trucks": True,
    "duplicate_check_days": 5,
    "allowed_truck_labels": ["YARD", "GARAGE"],
    "diesel_cash_items": ["Diesel Cash"],
    "export_restrict_surfaces": [
        "cashier_register",
        "cashier_print",
        "master_expenses",
        "category_tables",
        "truck_overview",
        "diesel_cash",
    ],
    **SECURITY_SETTING_DEFAULTS,
}

_SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_setting_key(key: str) -> str:
    if not _SETTING_KEY_RE.match(key):
        raise ApiError(422, "invalid_setting_key", "Setting key is invalid")
    return key


async def merged_settings(db: Any) -> dict[str, Any]:
    result = dict(SETTINGS_DEFAULTS)
    try:
        docs = await db.system_settings.find({}).to_list(length=None)
    except Exception:
        return result
    result.update({doc["key"]: doc["value"] for doc in docs if "key" in doc})
    return result


def database(request: Request) -> Any:
    return request.app.state.database.db


def public_user(user: dict) -> dict:
    return json_safe({key: value for key, value in user.items() if key != "password_hash"})


async def acquire_operation_lock(db: Any, key: str) -> str | None:
    """Acquire a recoverable, database-wide lease without requiring transactions."""
    owner = secrets.token_urlsafe(24)
    now = utcnow()
    lease = {
        "_id": key,
        "owner": owner,
        "acquired_at": now,
        "expires_at": now + timedelta(seconds=_OPERATION_LOCK_SECONDS),
    }
    try:
        await db.operation_locks.insert_one(lease)
        return owner
    except DuplicateKeyError:
        updated = await db.operation_locks.find_one_and_update(
            {"_id": key, "expires_at": {"$lte": now}},
            {"$set": {field: value for field, value in lease.items() if field != "_id"}},
            return_document=ReturnDocument.AFTER,
        )
        return owner if updated and updated.get("owner") == owner else None


async def release_operation_lock(db: Any, key: str, owner: str) -> None:
    await db.operation_locks.delete_one({"_id": key, "owner": owner})


async def issue_session(request: Request, user: dict) -> dict:
    settings = request.app.state.settings
    db = database(request)
    now = utcnow()
    policy = await merged_settings(db)

    idle_days = int(policy.get("security_idle_session_days") or 0)
    if idle_days > 0:
        await db.auth_sessions.update_many(
            {
                "user_id": user["_id"],
                "revoked_at": None,
                "last_used_at": {"$lt": now - timedelta(days=idle_days)},
            },
            {"$set": {"revoked_at": now}},
        )

    max_sessions = int(policy.get("security_max_sessions_per_user") or 0)
    if max_sessions > 0:
        active = (
            await db.auth_sessions.find(
                {
                    "user_id": user["_id"],
                    "revoked_at": None,
                    "expires_at": {"$gt": now},
                }
            )
            .sort("created_at", 1)
            .to_list()
        )
        overflow = len(active) - (max_sessions - 1)
        if overflow > 0:
            for stale in active[:overflow]:
                await db.auth_sessions.update_one(
                    {"_id": stale["_id"]}, {"$set": {"revoked_at": now}}
                )

    session_id = ObjectId()
    secret = new_refresh_secret()
    expires = now + timedelta(days=settings.refresh_token_days)
    await db.auth_sessions.insert_one(
        {
            "_id": session_id,
            "user_id": user["_id"],
            "refresh_hash": hash_refresh_secret(secret),
            "created_at": now,
            "last_used_at": now,
            "expires_at": expires,
            "revoked_at": None,
            "user_agent": request.headers.get("user-agent", "")[:500],
            "ip": getattr(request.state, "client_ip", None)
            or client_ip(request),
            "device_mac": normalize_mac(request.headers.get(DEVICE_MAC_HEADER)),
        }
    )
    access, access_expires = create_access_token(
        settings, user_id=str(user["_id"]), role=user["role"], session_id=str(session_id)
    )
    return {
        "token_type": "bearer",
        "access_token": access,
        "access_expires_at": access_expires,
        "refresh_token": refresh_token(str(session_id), secret),
        "refresh_expires_at": expires,
    }


@auth.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    db = database(request)
    settings_map = await merged_settings(db)
    ip = getattr(request.state, "client_ip", None) or client_ip(request)
    await assert_not_locked(db, body.username, ip, settings_map)
    user = await db.users.find_one({"username": body.username})
    if (
        not user
        or user.get("deleted_at")
        or not verify_password(body.password, user.get("password_hash", ""))
    ):
        await record_login_failure(db, body.username, ip, settings_map)
        await record_operation_event(
            db,
            action="auth.login",
            outcome="fail",
            actor_username=body.username,
            error="invalid_credentials",
            request=request,
            details={"reason": "invalid_credentials"},
        )
        raise ApiError(401, "invalid_credentials", "Invalid username or password")
    if user.get("banned"):
        reason = (user.get("ban_reason") or "").strip()
        message = "Your account is banned"
        if reason:
            message = f"Your account is banned: {reason}"
        await record_operation_event(
            db,
            action="auth.login",
            outcome="fail",
            actor=user,
            error="user_banned",
            request=request,
            details={"reason": "user_banned"},
        )
        raise ApiError(403, "user_banned", message)
    if not user.get("active", True):
        await record_operation_event(
            db,
            action="auth.login",
            outcome="fail",
            actor=user,
            error="user_inactive",
            request=request,
            details={"reason": "user_inactive"},
        )
        raise ApiError(401, "user_inactive", "Your account is inactive")
    await clear_login_failures(db, body.username, ip)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": utcnow()}})
    await record_operation_event(
        db,
        action="auth.login",
        outcome="ok",
        actor=user,
        request=request,
    )
    return json_safe(await issue_session(request, user))


@auth.get("/bootstrap-status")
async def bootstrap_status(request: Request) -> dict:
    return {"has_users": await database(request).users.count_documents({}) > 0}


@auth.post("/bootstrap", status_code=201)
async def bootstrap(body: UserCreate, request: Request) -> dict:
    if body.role != "admin":
        raise ApiError(422, "admin_required", "The first user must be an admin")
    db = database(request)
    enforce_password_policy(body.password, await merged_settings(db))
    lock_key = "bootstrap"
    owner = await acquire_operation_lock(db, lock_key)
    if owner is None:
        if await db.users.count_documents({}) > 0:
            raise ApiError(409, "already_initialized", "The application is already initialized")
        raise ApiError(409, "initialization_in_progress", "Application initialization is in progress")
    try:
        if await db.users.count_documents({}) > 0:
            raise ApiError(409, "already_initialized", "The application is already initialized")
        doc = body.model_dump(exclude={"password"})
        doc.update(
            password_hash=hash_password(body.password),
            created_at=utcnow(),
            last_login=None,
            banned=False,
            ban_reason="",
            banned_at=None,
            banned_by=None,
            deleted_at=None,
        )
        result = await db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
        await record_operation_event(
            db,
            action="auth.bootstrap",
            outcome="ok",
            actor=doc,
            entity_type="user",
            entity_ids=[str(doc["_id"])],
            request=request,
        )
        return {
            "user": public_user(doc),
            "tokens": json_safe(await issue_session(request, doc)),
        }
    finally:
        # A normal failure before insert is immediately retryable. If the process
        # dies, another instance can take over after the bounded lease expires.
        await release_operation_lock(db, lock_key, owner)


@auth.post("/refresh")
async def refresh(body: RefreshRequest, request: Request) -> dict:
    try:
        session_id_text, old_secret = split_refresh_token(body.refresh_token)
        session_id = object_id(session_id_text)
    except ValueError:
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid") from None
    db = database(request)
    session = await db.auth_sessions.find_one({"_id": session_id})
    now = utcnow()
    if not session or session.get("revoked_at") or session.get("expires_at") <= now:
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid or expired")
    if not hmac.compare_digest(
        session.get("refresh_hash", ""), hash_refresh_secret(old_secret)
    ):
        await db.auth_sessions.update_one({"_id": session_id}, {"$set": {"revoked_at": now}})
        await record_operation_event(
            db,
            action="auth.refresh_reuse",
            outcome="fail",
            actor_id=str(session.get("user_id") or ""),
            error="refresh_reuse_detected",
            request=request,
            entity_type="session",
            entity_ids=[str(session_id)],
        )
        raise ApiError(401, "refresh_reuse_detected", "Refresh token reuse revoked the session")
    user = await db.users.find_one({"_id": session["user_id"], "active": True})
    if not user:
        raise ApiError(401, "user_inactive", "User is inactive or unavailable")
    new_secret = new_refresh_secret()
    updated = await db.auth_sessions.find_one_and_update(
        {"_id": session_id, "refresh_hash": session["refresh_hash"], "revoked_at": None},
        {"$set": {"refresh_hash": hash_refresh_secret(new_secret), "last_used_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise ApiError(401, "refresh_reuse_detected", "Refresh token was already rotated")
    access, access_expires = create_access_token(
        request.app.state.settings,
        user_id=str(user["_id"]),
        role=user["role"],
        session_id=str(session_id),
    )
    return json_safe(
        {
            "token_type": "bearer",
            "access_token": access,
            "access_expires_at": access_expires,
            "refresh_token": refresh_token(str(session_id), new_secret),
            "refresh_expires_at": updated["expires_at"],
        }
    )


@auth.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, user: Authenticated) -> Response:
    db = database(request)
    await db.auth_sessions.update_one(
        {"_id": request.state.session["_id"]}, {"$set": {"revoked_at": utcnow()}}
    )
    await record_operation_event(
        db,
        action="auth.logout",
        outcome="ok",
        actor=user,
        request=request,
        entity_type="session",
        entity_ids=[str(request.state.session["_id"])],
    )
    return Response(status_code=204)


@auth.get("/me")
@auth.get("/session")
async def me(request: Request, user: Authenticated) -> dict:
    return {
        "user": public_user(user),
        "session": json_safe(
            {
                key: value
                for key, value in request.state.session.items()
                if key != "refresh_hash"
            }
        ),
    }


@auth.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, request: Request, user: Authenticated
) -> Response:
    if not verify_password(body.current_password, user.get("password_hash", "")):
        raise ApiError(400, "incorrect_password", "Current password is incorrect")
    now = utcnow()
    db = database(request)
    enforce_password_policy(body.new_password, await merged_settings(db))
    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"password_hash": hash_password(body.new_password)}}
    )
    await db.auth_sessions.update_many(
        {"user_id": user["_id"], "_id": {"$ne": request.state.session["_id"]}},
        {"$set": {"revoked_at": now}},
    )
    await record_operation_event(
        db,
        action="auth.change_password",
        outcome="ok",
        actor=user,
        request=request,
        entity_type="user",
        entity_ids=[str(user["_id"])],
    )
    return Response(status_code=204)


@accountant.get("/notification-counts")
async def accountant_notification_counts(
    request: Request,
    _manager: Annotated[dict, Depends(require_roles("admin", "accountant"))],
) -> dict[str, int]:
    """Badge total from the stored counter, not a collection scan.

    ``If-None-Match`` of the badge ``updated_at`` returns 304. The client
    keeps the badges it already has.
    """
    from .badge_counters import (
        badge_etag,
        badge_etag_matches,
        read_badges,
        verify_badge_total,
    )

    doc = await read_badges(database(request))
    etag = badge_etag(doc)
    headers = {"ETag": etag}
    if badge_etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        {"verify": verify_badge_total(doc)},
        headers=headers,
    )


@settings_api.get("")
async def get_all_settings(request: Request, _user: Authenticated) -> dict[str, Any]:
    return json_safe(await merged_settings(database(request)))


@settings_api.get("/{key}")
async def get_setting(key: str, request: Request, _user: Authenticated) -> dict[str, Any]:
    key = validate_setting_key(key)
    db = database(request)
    doc = await db.system_settings.find_one({"key": key})
    if doc is not None and "value" in doc:
        return json_safe({"key": key, "value": doc["value"]})
    if key in SETTINGS_DEFAULTS:
        return {"key": key, "value": SETTINGS_DEFAULTS[key]}
    raise ApiError(404, "setting_not_found", f"Unknown setting '{key}'")


@settings_api.put("/{key}")
async def put_setting(
    key: str, body: SettingWrite, request: Request, user: Authenticated
) -> dict[str, Any]:
    # Matches desktop: cashiers may update labels / force restrict_trucks; managers
    # update the rest. security_* and backup_* settings require admin.
    key = validate_setting_key(key)
    if is_admin_only_setting(key) and user.get("role") != "admin":
        raise ApiError(403, "forbidden", "Only an administrator can change this setting")
    if key not in SETTINGS_DEFAULTS:
        raise ApiError(404, "setting_not_found", f"Unknown setting '{key}'")
    db = database(request)
    value = body.value
    if key == "backup_schedule_mode" and value not in {"hourly", "daily", "disabled"}:
        raise ApiError(422, "invalid_setting_value", "backup_schedule_mode must be hourly, daily, or disabled")
    if key == "backup_keep_count":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ApiError(422, "invalid_setting_value", "backup_keep_count must be an integer") from None
        if value < 1 or value > 365:
            raise ApiError(422, "invalid_setting_value", "backup_keep_count must be between 1 and 365")
    if key == "backup_daily_time_utc":
        text = str(value).strip()
        parts = text.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ApiError(422, "invalid_setting_value", "backup_daily_time_utc must be HH:MM")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ApiError(422, "invalid_setting_value", "backup_daily_time_utc must be HH:MM")
        value = f"{hour:02d}:{minute:02d}"
    await db.system_settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value}},
        upsert=True,
    )
    await record_operation_event(
        db,
        action="settings.update",
        outcome="ok",
        actor=user,
        request=request,
        entity_type="setting",
        entity_ids=[key],
        details={"key": key},
    )
    await bump_catalog_version(request)
    return json_safe({"key": key, "value": value})


@audit_api.get("/events")
async def list_audit_events(
    request: Request,
    _admin: Admin,
    limit: int = Query(100, ge=1, le=500),
    action: str | None = Query(None, max_length=200),
    actor: str | None = Query(None, max_length=100),
    outcome: str | None = Query(None, max_length=40),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
) -> list[dict]:
    """Admin-only newest-first audit trail viewer."""
    query = parse_audit_filters(
        action=action, actor=actor, outcome=outcome, since=since, until=until
    )
    docs = (
        await database(request)
        .operation_events.find(query)
        .sort("ts", -1)
        .limit(limit)
        .to_list()
    )
    return json_safe(docs)


@audit_api.post("/events", status_code=201)
async def create_audit_event(
    body: AuditEventWrite, request: Request, user: Authenticated
) -> dict[str, Any]:
    """Append-only operation_events row (desktop audit trail)."""
    actor_id = body.actor_id or str(user.get("_id") or "")
    actor_username = body.actor_username or user.get("username")
    actor_role = body.actor_role or user.get("role")
    doc = {
        "ts": utcnow(),
        "action": body.action,
        "actor_id": actor_id or None,
        "actor_username": actor_username,
        "actor_role": actor_role,
        "entity_type": body.entity_type,
        "entity_ids": list(body.entity_ids),
        "upload_id": body.upload_id,
        "outcome": body.outcome,
        "error": body.error,
        "request_id": body.request_id or uuid.uuid4().hex,
        "client": body.client or "desktop",
        "app_version": body.app_version,
        "details": body.details or {},
    }
    result = await database(request).operation_events.insert_one(doc)
    return json_safe({"id": result.inserted_id})


@users.get("")
async def list_users(
    request: Request,
    manager: Manager,
    include_deleted: bool = Query(False),
) -> list[dict]:
    query: dict[str, Any] = {}
    if not include_deleted or manager.get("role") != "admin":
        query["deleted_at"] = None
    docs = (
        await database(request)
        .users.find(query, {"password_hash": 0})
        .sort("full_name", 1)
        .to_list()
    )
    return json_safe(docs)


@users.post("", status_code=201)
async def create_user(body: UserCreate, request: Request, manager: Manager) -> dict:
    if body.role == "admin" and manager.get("role") != "admin":
        raise ApiError(403, "forbidden", "Only an administrator can create administrators")
    db = database(request)
    enforce_password_policy(body.password, await merged_settings(db))
    doc = body.model_dump(exclude={"password"})
    doc.update(
        password_hash=hash_password(body.password),
        created_at=utcnow(),
        last_login=None,
        banned=False,
        ban_reason="",
        banned_at=None,
        banned_by=None,
        deleted_at=None,
    )
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    await record_operation_event(
        db,
        action="user.create",
        outcome="ok",
        actor=manager,
        request=request,
        entity_type="user",
        entity_ids=[str(doc["_id"])],
        details={"username": doc.get("username"), "role": doc.get("role")},
    )
    return public_user(doc)


async def _require_manageable_user(
    db: Any, oid: ObjectId, actor: dict, *, allow_self: bool = False
) -> dict:
    current = await db.users.find_one({"_id": oid})
    if not current or current.get("deleted_at"):
        raise ApiError(404, "not_found", "User not found")
    if not allow_self and actor.get("_id") == oid:
        raise ApiError(400, "cannot_target_self", "You cannot perform this action on yourself")
    if current.get("role") == "admin" and actor.get("role") != "admin":
        raise ApiError(403, "forbidden", "Only an administrator can manage administrators")
    return current


async def _assert_not_last_active_admin(db: Any, user: dict) -> None:
    if user.get("role") != "admin" or not user.get("active", True):
        return
    if user.get("banned") or user.get("deleted_at"):
        return
    active_admins = await db.users.count_documents(
        {
            "role": "admin",
            "active": True,
            "banned": {"$ne": True},
            "deleted_at": None,
        }
    )
    if active_admins <= 1:
        raise ApiError(
            409,
            "last_admin_required",
            "The final active administrator cannot be banned, deactivated, or deleted",
        )


async def _revoke_user_sessions(db: Any, user_id: ObjectId) -> int:
    result = await db.auth_sessions.update_many(
        {"user_id": user_id, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )
    return int(result.modified_count)


@users.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, request: Request, manager: Manager) -> dict:
    values = body.model_dump(exclude_none=True)
    password = values.pop("password", None)
    if password:
        enforce_password_policy(password, await merged_settings(database(request)))
        values["password_hash"] = hash_password(password)
    oid = valid_id(user_id)
    db = database(request)
    lock_owner: str | None = None
    if "role" in values or "active" in values:
        lock_owner = await acquire_operation_lock(db, "admin-user-update")
        if lock_owner is None:
            raise ApiError(409, "user_update_in_progress", "Another user update is in progress")
    try:
        current = await db.users.find_one({"_id": oid})
        if not current or current.get("deleted_at"):
            raise ApiError(404, "not_found", "User not found")
        if manager.get("role") != "admin" and (
            current.get("role") == "admin" or values.get("role") == "admin"
        ):
            raise ApiError(403, "forbidden", "Only an administrator can modify administrators")
        if values.get("active") is True:
            values.update(banned=False, ban_reason="", banned_at=None, banned_by=None)
        removes_active_admin = (
            current.get("role") == "admin"
            and current.get("active", True)
            and not current.get("banned")
            and (values.get("role", "admin") != "admin" or values.get("active", True) is False)
        )
        if removes_active_admin:
            await _assert_not_last_active_admin(db, current)
        doc = await db.users.find_one_and_update(
            {"_id": oid}, {"$set": values}, return_document=ReturnDocument.AFTER
        )
    finally:
        if lock_owner is not None:
            await release_operation_lock(db, "admin-user-update", lock_owner)
    if not doc:
        raise ApiError(404, "not_found", "User not found")
    if password or values.get("active") is False:
        await _revoke_user_sessions(db, oid)
    changed = {key: values[key] for key in values if key != "password_hash"}
    if password:
        changed["password_reset"] = True
    action = "user.update"
    if list(changed.keys()) == ["active"] or set(changed.keys()) <= {
        "active",
        "banned",
        "ban_reason",
        "banned_at",
        "banned_by",
    }:
        if values.get("active") is True:
            action = "user.activate"
        elif values.get("active") is False:
            action = "user.deactivate"
    await record_operation_event(
        db,
        action=action,
        outcome="ok",
        actor=manager,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={"username": doc.get("username"), "changed": changed},
    )
    return public_user(doc)


@users.post("/{user_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_user(
    user_id: str, body: UserBanRequest, request: Request, admin: Admin
) -> Response:
    oid = valid_id(user_id)
    db = database(request)
    current = await _require_manageable_user(db, oid, admin)
    await _assert_not_last_active_admin(db, current)
    now = utcnow()
    await db.users.update_one(
        {"_id": oid},
        {
            "$set": {
                "active": False,
                "banned": True,
                "banned_at": now,
                "banned_by": admin.get("_id"),
                "ban_reason": body.reason.strip(),
            }
        },
    )
    revoked = await _revoke_user_sessions(db, oid)
    await record_operation_event(
        db,
        action="user.ban",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={
            "username": current.get("username"),
            "reason": body.reason.strip(),
            "revoked_sessions": revoked,
        },
    )
    return Response(status_code=204)


@users.post("/{user_id}/unban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(user_id: str, request: Request, admin: Admin) -> Response:
    oid = valid_id(user_id)
    db = database(request)
    current = await _require_manageable_user(db, oid, admin, allow_self=True)
    await db.users.update_one(
        {"_id": oid},
        {
            "$set": {
                "banned": False,
                "ban_reason": "",
                "banned_at": None,
                "banned_by": None,
                # Leave inactive until an explicit activate.
                "active": False,
            }
        },
    )
    await record_operation_event(
        db,
        action="user.unban",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={"username": current.get("username")},
    )
    return Response(status_code=204)


@users.post("/{user_id}/force-logout", status_code=status.HTTP_204_NO_CONTENT)
async def force_logout_user(user_id: str, request: Request, admin: Admin) -> Response:
    oid = valid_id(user_id)
    db = database(request)
    current = await _require_manageable_user(db, oid, admin, allow_self=True)
    query: dict[str, Any] = {"user_id": oid, "revoked_at": None}
    current_sid = request.state.session.get("_id") if hasattr(request.state, "session") else None
    if current_sid and admin.get("_id") == oid:
        query["_id"] = {"$ne": current_sid}
    result = await db.auth_sessions.update_many(query, {"$set": {"revoked_at": utcnow()}})
    await record_operation_event(
        db,
        action="user.force_logout",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={
            "username": current.get("username"),
            "revoked_count": int(result.modified_count),
        },
    )
    return Response(status_code=204)


@users.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: str, body: UserResetPasswordRequest, request: Request, admin: Admin
) -> Response:
    oid = valid_id(user_id)
    db = database(request)
    current = await _require_manageable_user(db, oid, admin, allow_self=True)
    enforce_password_policy(body.password, await merged_settings(db))
    await db.users.update_one(
        {"_id": oid}, {"$set": {"password_hash": hash_password(body.password)}}
    )
    revoked = await _revoke_user_sessions(db, oid)
    await record_operation_event(
        db,
        action="user.reset_password",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={"username": current.get("username"), "revoked_sessions": revoked},
    )
    return Response(status_code=204)


@users.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, request: Request, admin: Admin) -> Response:
    oid = valid_id(user_id)
    db = database(request)
    current = await _require_manageable_user(db, oid, admin)
    await _assert_not_last_active_admin(db, current)
    now = utcnow()
    await db.users.update_one(
        {"_id": oid},
        {
            "$set": {
                "deleted_at": now,
                "active": False,
                "banned": True,
                "banned_at": now,
                "banned_by": admin.get("_id"),
                "ban_reason": current.get("ban_reason") or "Account deleted",
            }
        },
    )
    revoked = await _revoke_user_sessions(db, oid)
    await record_operation_event(
        db,
        action="user.delete",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={"username": current.get("username"), "revoked_sessions": revoked},
    )
    return Response(status_code=204)


def valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


_PATCH_FIELDS = {
    "categories": {
        "name", "description", "color", "icon", "sidebar_name", "show_in_sidebar",
        "show_in_cashier_sidebar", "sort_order",
        "requires_receipt", "requires_truck", "lock_description",
        "restrict_in_pdf", "restrict_in_excel", "is_supplier", "active",
        "account_type", "ref_num", "account_number", "currency", "coa_description",
    },
    "category_subtables": {
        "parent_key", "parent_category", "name", "match", "active", "sort_order",
    },
    "keyword_rules": {
        "pattern", "category_id", "category_name", "priority", "match_type", "active",
    },
}


def allowed_patch(collection: str, values: dict[str, Any]) -> dict[str, Any]:
    unsupported = set(values) - _PATCH_FIELDS[collection]
    if unsupported:
        raise ApiError(
            422,
            "unsupported_fields",
            f"Unsupported fields: {', '.join(sorted(unsupported))}",
        )
    return dict(values)


async def list_collection(
    request: Request,
    collection: str,
    *,
    search: str = "",
    include_inactive: bool = False,
    limit: int = 100,
    offset: int = 0,
    exact: dict[str, Any] | None = None,
) -> dict:
    query: dict[str, Any] = dict(exact or {})
    if not include_inactive:
        query["active"] = {"$ne": False}
    if search:
        search_fields = ("name", "number", "pattern", "description")
        if collection == "description_mappings":
            search_fields = ("description", "payee", "category_name")
        query["$or"] = [
            {field: {"$regex": re.escape(search), "$options": "i"}}
            for field in search_fields
        ]
    db = database(request)
    total = await db[collection].count_documents(query)
    collection_sorts = {
        "categories": [("sort_order", 1), ("name", 1), ("_id", 1)],
        "category_subtables": [("sort_order", 1), ("name", 1), ("_id", 1)],
        "keyword_rules": [("priority", -1), ("pattern", 1), ("_id", 1)],
        "description_mappings": [("description", 1), ("_id", 1)],
    }
    docs = (
        await db[collection]
        .find(query)
        .sort(collection_sorts.get(collection, [("_id", 1)]))
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    return {"items": json_safe(docs), "total": total, "limit": limit, "offset": offset}


def page_params(
    search: str = "", include_inactive: bool = False,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
) -> dict:
    return dict(search=search, include_inactive=include_inactive, limit=limit, offset=offset)


@categories.get("")
async def list_categories(
    request: Request,
    _user: Authenticated,
    page: Annotated[dict, Depends(page_params)],
    is_supplier: bool | None = Query(None),
) -> dict:
    exact: dict[str, Any] | None = None
    if is_supplier is True:
        exact = {"is_supplier": True}
    elif is_supplier is False:
        # Legacy docs omit the flag — treat missing as expense items.
        exact = {"is_supplier": {"$ne": True}}
    return await list_collection(request, "categories", exact=exact, **page)


async def insert_category(
    body: CategoryWrite, request: Request, *, actor: dict | None = None
) -> dict:
    doc = body.model_dump()
    db = database(request)
    result = await db.categories.insert_one(doc)
    doc["_id"] = result.inserted_id
    if actor is not None:
        await record_operation_event(
            db,
            action="category.create",
            actor=actor,
            request=request,
            entity_type="category",
            entity_ids=[str(doc["_id"])],
            details={"name": doc.get("name")},
        )
    await bump_catalog_version(request)
    return json_safe(doc)


@categories.post("", status_code=201)
async def create_category(body: CategoryWrite, request: Request, manager: Manager) -> dict:
    return await insert_category(body, request, actor=manager)


@categories.post("/cashier-create", status_code=201)
async def cashier_create_category(
    body: CategoryWrite, request: Request, cashier: Cashier
) -> dict:
    """Allow cashier/accountant unknown-item creation from the daily register."""
    return await insert_category(body, request, actor=cashier)


@categories.patch("/{item_id}")
async def patch_category(item_id: str, body: PatchDocument, request: Request, manager: Manager) -> dict:
    values = allowed_patch("categories", body.values)
    db = database(request)
    doc = await db.categories.find_one_and_update(
        {"_id": valid_id(item_id)}, {"$set": values}, return_document=ReturnDocument.AFTER
    )
    if not doc:
        raise ApiError(404, "not_found", "Category not found")
    await record_operation_event(
        db,
        action="category.patch",
        actor=manager,
        request=request,
        entity_type="category",
        entity_ids=[item_id],
        details={"fields": sorted(values.keys())[:40]},
    )
    await bump_catalog_version(request)
    return json_safe(doc)


@categories.delete("/{item_id}", status_code=204)
async def delete_category(item_id: str, request: Request, manager: Manager) -> Response:
    db = database(request)
    result = await db.categories.delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Category not found")
    await record_operation_event(
        db,
        action="category.delete",
        actor=manager,
        request=request,
        entity_type="category",
        entity_ids=[item_id],
    )
    await bump_catalog_version(request)
    return Response(status_code=204)


_COLLECTION_AUDIT = {
    "category_subtables": "subtable",
    "keyword_rules": "rule",
    "description_mappings": "mapping",
}


async def create_named(
    request: Request, collection: str, body: Any, *, actor: dict | None = None
) -> dict:
    doc = body.model_dump()
    db = database(request)
    if collection in {"category_subtables", "description_mappings"}:
        doc["created_at"] = utcnow()
    if collection == "description_mappings":
        doc["description_key"] = " ".join(doc["description"].upper().split())
        doc = await db[collection].find_one_and_update(
            {"description_key": doc["description_key"]},
            {"$set": doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if actor is not None:
            await record_operation_event(
                db,
                action="mapping.upsert",
                actor=actor,
                request=request,
                entity_type="mapping",
                entity_ids=[str(doc.get("_id") or "")],
            )
        return json_safe(doc)
    if "category_id" in doc:
        doc["category_id"] = valid_id(doc["category_id"])
    result = await db[collection].insert_one(doc)
    doc["_id"] = result.inserted_id
    if actor is not None:
        entity = _COLLECTION_AUDIT.get(collection, collection)
        await record_operation_event(
            db,
            action=f"{entity}.create",
            actor=actor,
            request=request,
            entity_type=entity,
            entity_ids=[str(doc["_id"])],
        )
    return json_safe(doc)


@subtables.get("")
async def list_subtables(
    request: Request,
    _user: Authenticated,
    page: Annotated[dict, Depends(page_params)],
    parent_key: str = "",
) -> dict:
    exact = {"parent_key": parent_key} if parent_key else None
    return await list_collection(request, "category_subtables", exact=exact, **page)


@subtables.post("", status_code=201)
async def create_subtable(body: SubtableWrite, request: Request, manager: Manager) -> dict:
    return await create_named(request, "category_subtables", body, actor=manager)


@subtables.post("/cashier-create", status_code=201)
async def cashier_create_subtable(
    body: SubtableWrite, request: Request, cashier: Cashier
) -> dict:
    """Allow cashier/accountant sub-item creation from import mapping."""
    return await create_named(request, "category_subtables", body, actor=cashier)


@rules.get("")
async def list_rules(request: Request, _user: Authenticated, page: Annotated[dict, Depends(page_params)]) -> dict:
    return await list_collection(request, "keyword_rules", **page)


@rules.post("", status_code=201)
async def create_rule(body: RuleWrite, request: Request, manager: Manager) -> dict:
    return await create_named(request, "keyword_rules", body, actor=manager)


@mappings.get("")
async def list_mappings(
    request: Request,
    _user: Authenticated,
    page: Annotated[dict, Depends(page_params)],
    description_keys: Annotated[list[str] | None, Query()] = None,
) -> dict:
    page["include_inactive"] = True
    exact = None
    if description_keys:
        exact = {"description_key": {"$in": description_keys}}
    return await list_collection(request, "description_mappings", exact=exact, **page)


@mappings.put("", status_code=200)
async def put_mapping(body: MappingWrite, request: Request, user: Authenticated) -> dict:
    """Cashiers save maps during daily import; accountants/admins manage them too."""
    return await create_named(request, "description_mappings", body, actor=user)


@mappings.post("/bulk", status_code=200)
async def bulk_upsert_mappings(
    body: MappingBulkUpsert,
    request: Request,
    user: Authenticated,
) -> dict:
    """Upsert many description maps in one ``bulk_write`` (cashier + accountant)."""
    db = database(request)
    now = utcnow()
    ops: list[UpdateOne] = []
    keys: list[str] = []
    seen: set[str] = set()
    for item in body.mappings:
        description = " ".join((item.description or "").strip().split())
        key = " ".join(description.upper().split())
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
        payee = " ".join((item.payee or "").strip().upper().split())
        doc = {
            "description": description,
            "description_key": key,
            "category_id": valid_id(item.category_id),
            "category_name": item.category_name,
            "payee": payee,
            "created_at": now,
        }
        ops.append(
            UpdateOne(
                {"description_key": key},
                {"$set": doc},
                upsert=True,
            )
        )
    if not ops:
        return {"items": [], "upserted_count": 0}
    await db.description_mappings.bulk_write(ops, ordered=False)
    docs = await db.description_mappings.find(
        {"description_key": {"$in": keys}}
    ).to_list(length=len(keys))
    await record_operation_event(
        db,
        action="mapping.bulk_upsert",
        actor=user,
        request=request,
        entity_type="mapping",
        entity_ids=[str(doc.get("_id") or "") for doc in docs[:40]],
        details={"upserted_count": len(docs), "requested": len(body.mappings)},
    )
    return {"items": json_safe(docs), "upserted_count": len(docs)}


async def patch_named(
    collection: str,
    item_id: str,
    body: PatchDocument,
    request: Request,
    *,
    actor: dict | None = None,
) -> dict:
    values = allowed_patch(collection, body.values)
    if "category_id" in values:
        values["category_id"] = valid_id(str(values["category_id"]))
    db = database(request)
    doc = await db[collection].find_one_and_update(
        {"_id": valid_id(item_id)}, {"$set": values}, return_document=ReturnDocument.AFTER
    )
    if not doc:
        raise ApiError(404, "not_found", "Resource not found")
    if actor is not None:
        entity = _COLLECTION_AUDIT.get(collection, collection)
        await record_operation_event(
            db,
            action=f"{entity}.patch",
            actor=actor,
            request=request,
            entity_type=entity,
            entity_ids=[item_id],
            details={"fields": sorted(values.keys())[:40]},
        )
    return json_safe(doc)


@subtables.patch("/{item_id}")
async def patch_subtable(item_id: str, body: PatchDocument, request: Request, manager: Manager) -> dict:
    return await patch_named("category_subtables", item_id, body, request, actor=manager)


@rules.patch("/{item_id}")
async def patch_rule(item_id: str, body: PatchDocument, request: Request, manager: Manager) -> dict:
    return await patch_named("keyword_rules", item_id, body, request, actor=manager)


async def delete_named(
    collection: str,
    item_id: str,
    request: Request,
    *,
    actor: dict | None = None,
) -> Response:
    db = database(request)
    result = await db[collection].delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Resource not found")
    if actor is not None:
        entity = _COLLECTION_AUDIT.get(collection, collection)
        await record_operation_event(
            db,
            action=f"{entity}.delete",
            actor=actor,
            request=request,
            entity_type=entity,
            entity_ids=[item_id],
        )
    return Response(status_code=204)


@subtables.delete("/{item_id}", status_code=204)
async def delete_subtable(item_id: str, request: Request, manager: Manager) -> Response:
    return await delete_named("category_subtables", item_id, request, actor=manager)


@rules.delete("/{item_id}", status_code=204)
async def delete_rule(item_id: str, request: Request, manager: Manager) -> Response:
    return await delete_named("keyword_rules", item_id, request, actor=manager)


def _mapping_search_query(search: str) -> dict[str, Any]:
    text = (search or "").strip()
    if not text:
        return {}
    return {
        "$or": [
            {field: {"$regex": re.escape(text), "$options": "i"}}
            for field in ("description", "payee", "category_name")
        ]
    }


@mappings.delete("")
async def delete_mappings(
    request: Request,
    manager: Manager,
    search: str = "",
) -> dict:
    db = database(request)
    query = _mapping_search_query(search)
    result = await db.description_mappings.delete_many(query)
    await record_operation_event(
        db,
        action="mapping.delete_all",
        actor=manager,
        request=request,
        entity_type="mapping",
        details={
            "deleted_count": int(result.deleted_count),
            "search": (search or "").strip() or None,
        },
    )
    return {"deleted_count": result.deleted_count}


@mappings.post("/bulk-delete")
async def bulk_delete_mappings(
    body: MappingBulkDelete,
    request: Request,
    manager: Manager,
) -> dict:
    db = database(request)
    ids = [valid_id(item_id) for item_id in body.ids]
    result = await db.description_mappings.delete_many({"_id": {"$in": ids}})
    if result.deleted_count:
        await record_operation_event(
            db,
            action="mapping.bulk_delete",
            actor=manager,
            request=request,
            entity_type="mapping",
            entity_ids=[str(item_id) for item_id in ids[:40]],
            details={"deleted_count": int(result.deleted_count), "requested": len(ids)},
        )
    return {"deleted_count": int(result.deleted_count)}


@mappings.delete("/{item_id}", status_code=204)
async def delete_mapping(item_id: str, request: Request, manager: Manager) -> Response:
    db = database(request)
    result = await db.description_mappings.delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Mapping not found")
    await record_operation_event(
        db,
        action="mapping.delete",
        actor=manager,
        request=request,
        entity_type="mapping",
        entity_ids=[item_id],
    )
    return Response(status_code=204)


async def list_fleet(
    kind: str,
    request: Request,
    search: str,
    active: bool | None,
    limit: int,
    offset: int,
) -> dict:
    query: dict[str, Any] = {}
    if search:
        query["number"] = {"$regex": re.escape(search), "$options": "i"}
    if active is not None:
        query["active"] = active
    collection = database(request)[kind]
    total = await collection.count_documents(query)
    docs = (
        await collection.find(query)
        .sort([("number", 1), ("_id", 1)])
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    return {
        "items": json_safe(docs),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


_FLEET_ENTITY = {
    "trucks": "truck",
    "trailers": "trailer",
    "motor_vehicles": "motor_vehicle",
}


async def put_fleet(
    kind: str, body: FleetWrite, request: Request, *, actor: dict | None = None
) -> dict:
    db = database(request)
    doc = await db[kind].find_one_and_update(
        {"number": body.number},
        {"$set": body.model_dump()},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if actor is not None:
        await record_operation_event(
            db,
            action="fleet.upsert",
            actor=actor,
            request=request,
            entity_type=_FLEET_ENTITY.get(kind, kind),
            entity_ids=[body.number],
            details={"kind": kind, "active": body.active},
        )
    await bump_catalog_version(request)
    return json_safe(doc)


async def delete_fleet(
    kind: str, number: str, request: Request, *, actor: dict | None = None
) -> Response:
    normalized = " ".join(number.upper().split())
    db = database(request)
    await db[kind].delete_one({"number": normalized})
    if actor is not None:
        await record_operation_event(
            db,
            action="fleet.delete",
            actor=actor,
            request=request,
            entity_type=_FLEET_ENTITY.get(kind, kind),
            entity_ids=[normalized],
            details={"kind": kind},
        )
    await bump_catalog_version(request)
    return Response(status_code=204)


@fleet.get("/trucks")
async def get_trucks(
    request: Request,
    _user: Authenticated,
    search: str = "",
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    return await list_fleet("trucks", request, search, active, limit, offset)


@fleet.put("/trucks/{number}")
async def upsert_truck(number: str, body: FleetWrite, request: Request, manager: Manager) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("trucks", body, request, actor=manager)


@fleet.delete("/trucks/{number}", status_code=204)
async def delete_truck(number: str, request: Request, manager: Manager) -> Response:
    return await delete_fleet("trucks", number, request, actor=manager)


@fleet.get("/trailers")
async def get_trailers(
    request: Request,
    _user: Authenticated,
    search: str = "",
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    return await list_fleet("trailers", request, search, active, limit, offset)


@fleet.put("/trailers/{number}")
async def upsert_trailer(number: str, body: FleetWrite, request: Request, manager: Manager) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("trailers", body, request, actor=manager)


@fleet.delete("/trailers/{number}", status_code=204)
async def delete_trailer(number: str, request: Request, manager: Manager) -> Response:
    return await delete_fleet("trailers", number, request, actor=manager)


@fleet.get("/motor_vehicles")
async def get_motor_vehicles(
    request: Request,
    _user: Authenticated,
    search: str = "",
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    return await list_fleet("motor_vehicles", request, search, active, limit, offset)


@fleet.put("/motor_vehicles/{number}")
async def upsert_motor_vehicle(
    number: str, body: FleetWrite, request: Request, manager: Manager
) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("motor_vehicles", body, request, actor=manager)


@fleet.delete("/motor_vehicles/{number}", status_code=204)
async def delete_motor_vehicle(number: str, request: Request, manager: Manager) -> Response:
    return await delete_fleet("motor_vehicles", number, request, actor=manager)


async def list_people(
    request: Request,
    search: str,
    active: bool | None,
    limit: int,
    offset: int,
) -> dict:
    query: dict[str, Any] = {}
    if search:
        query["name"] = {"$regex": re.escape(search), "$options": "i"}
    if active is not None:
        query["active"] = active
    collection = database(request)["people"]
    total = await collection.count_documents(query)
    docs = (
        await collection.find(query)
        .sort([("name", 1), ("_id", 1)])
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    return {
        "items": json_safe(docs),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@people.get("")
async def get_people(
    request: Request,
    _user: Authenticated,
    search: str = "",
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    return await list_people(request, search, active, limit, offset)


@people.put("/{name}")
async def upsert_person(name: str, body: PeopleWrite, request: Request, manager: Manager) -> dict:
    normalized = " ".join(name.upper().split())
    if normalized != body.name:
        raise ApiError(422, "name_mismatch", "Path and body names must match")
    db = database(request)
    doc = await db["people"].find_one_and_update(
        {"name": body.name},
        {"$set": body.model_dump()},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    await record_operation_event(
        db,
        action="people.upsert",
        actor=manager,
        request=request,
        entity_type="person",
        entity_ids=[body.name],
        details={"active": body.active},
    )
    return json_safe(doc)


@people.delete("/{name}", status_code=204)
async def delete_person(name: str, request: Request, manager: Manager) -> Response:
    normalized = " ".join(name.upper().split())
    db = database(request)
    await db["people"].delete_one({"name": normalized})
    await record_operation_event(
        db,
        action="people.delete",
        actor=manager,
        request=request,
        entity_type="person",
        entity_ids=[normalized],
    )
    return Response(status_code=204)


async def list_payees(
    request: Request,
    search: str,
    active: bool | None,
    limit: int,
    offset: int,
) -> dict:
    query: dict[str, Any] = {}
    if search:
        query["name"] = {"$regex": re.escape(search), "$options": "i"}
    if active is not None:
        query["active"] = active
    collection = database(request)["payees"]
    total = await collection.count_documents(query)
    docs = (
        await collection.find(query)
        .sort([("name", 1), ("_id", 1)])
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    return {
        "items": json_safe(docs),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@payees.get("")
async def get_payees(
    request: Request,
    _user: Authenticated,
    search: str = "",
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    return await list_payees(request, search, active, limit, offset)


@payees.put("/{name}")
async def upsert_payee(name: str, body: PayeeWrite, request: Request, manager: Manager) -> dict:
    normalized = " ".join(name.upper().split())
    if normalized != body.name:
        raise ApiError(422, "name_mismatch", "Path and body names must match")
    db = database(request)
    payload = body.model_dump()
    doc = await db["payees"].find_one_and_update(
        {"name": body.name},
        {"$set": payload},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    await record_operation_event(
        db,
        action="payees.upsert",
        actor=manager,
        request=request,
        entity_type="payee",
        entity_ids=[body.name],
        details={"active": body.active},
    )
    await bump_catalog_version(request)
    return json_safe(doc)


@payees.delete("/{name}", status_code=204)
async def delete_payee(name: str, request: Request, manager: Manager) -> Response:
    normalized = " ".join(name.upper().split())
    db = database(request)
    await db["payees"].delete_one({"name": normalized})
    await record_operation_event(
        db,
        action="payees.delete",
        actor=manager,
        request=request,
        entity_type="payee",
        entity_ids=[normalized],
    )
    await bump_catalog_version(request)
    return Response(status_code=204)


@catalog_api.get("/snapshot")
async def get_catalog_snapshot(
    request: Request, response: Response, _user: Authenticated
):
    version, body, not_modified = await catalog_snapshot_body(request)
    tag = etag_for(version)
    if not_modified:
        return Response(status_code=304, headers={"ETag": tag})
    response.headers["ETag"] = tag
    return body


@backups.get("")
async def list_backup_jobs(
    request: Request,
    _admin: Admin,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    docs = await database(request).backup_jobs.find().sort("created_at", -1).limit(limit).to_list()
    return json_safe(docs)


@backups.post("/run")
async def run_backup_now(
    request: Request,
    admin: Admin,
) -> dict:
    """Create an immediate database dump (admin only), then upload/prune if configured."""
    from .cli.backup import (
        create_local_backup,
        distributed_lease,
        exclusive_lock,
        prune,
        upload_pending,
    )

    settings = request.app.state.settings
    db = database(request)
    try:
        with exclusive_lock(settings.backup_lock_file):
            async with distributed_lease(settings, db):
                archive = await create_local_backup(settings, db, "manual")
                uploaded = await upload_pending(settings, db)
                pruned = await prune(settings, db)
    except RuntimeError as exc:
        message = str(exc)
        code = "backup_failed"
        status_code = 409
        if "lock" in message.lower() or "lease" in message.lower():
            code = "backup_busy"
        raise ApiError(status_code, code, message) from exc
    except ValueError as exc:
        raise ApiError(422, "invalid_backup", str(exc)) from exc

    job = await db.backup_jobs.find_one({"filename": archive.name})
    await record_operation_event(
        db,
        action="backup.run_manual",
        actor=admin,
        request=request,
        entity_type="backup",
        entity_ids=[archive.name],
        details={
            "filename": archive.name,
            "uploaded": uploaded,
            "pruned": pruned,
            "status": (job or {}).get("status"),
        },
    )
    return json_safe(
        {
            "filename": archive.name,
            "status": (job or {}).get("status", "pending_upload"),
            "size": (job or {}).get("size", 0),
            "uploaded": uploaded,
            "pruned": pruned,
        }
    )


@backups.post("/restore")
async def restore_backup_job(
    body: BackupRestoreRequest,
    request: Request,
    admin: Admin,
) -> dict:
    """Replace the live database with a verified uploaded backup (admin only)."""
    from .cli.backup import exclusive_lock, distributed_lease, restore_database

    settings = request.app.state.settings
    db = database(request)
    try:
        with exclusive_lock(settings.backup_lock_file):
            async with distributed_lease(settings, db):
                result = await restore_database(
                    settings,
                    db,
                    filename=body.filename,
                    confirm_filename=body.confirm_filename,
                    actor=admin,
                )
    except RuntimeError as exc:
        message = str(exc)
        code = "restore_failed"
        status_code = 409
        if "not found" in message.lower():
            code = "backup_not_found"
            status_code = 404
        elif "confirmation" in message.lower():
            code = "confirmation_mismatch"
            status_code = 422
        elif "only uploaded" in message.lower():
            code = "backup_not_ready"
            status_code = 409
        elif "lock" in message.lower():
            code = "backup_busy"
            status_code = 409
        raise ApiError(status_code, code, message) from exc
    await record_operation_event(
        db,
        action="backup.restore",
        actor=admin,
        request=request,
        entity_type="backup",
        entity_ids=[body.filename],
        details={
            "filename": body.filename,
            "status": result.get("status"),
            "archive_sha256": result.get("archive_sha256"),
        },
    )
    return json_safe(result)


for router in (
    auth,
    users,
    categories,
    subtables,
    rules,
    mappings,
    fleet,
    people,
    payees,
    backups,
    accountant,
    settings_api,
    catalog_api,
    audit_api,
    sessions_router,
    transactions_router,
    reconciliation_router,
    imports_router,
    feeds_router,
):
    api.include_router(router)
