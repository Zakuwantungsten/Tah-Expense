from __future__ import annotations

import hmac
import re
import secrets
from datetime import timedelta
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .dependencies import Authenticated, Cashier, Manager, require_roles
from .errors import ApiError
from .schemas import (
    BackupRestoreRequest,
    CategoryWrite,
    ChangePasswordRequest,
    FleetWrite,
    LoginRequest,
    MappingWrite,
    PatchDocument,
    PeopleWrite,
    RefreshRequest,
    RuleWrite,
    SubtableWrite,
    UserCreate,
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

api = APIRouter(prefix="/v1")
auth = APIRouter(prefix="/auth", tags=["auth"])
users = APIRouter(prefix="/users", tags=["users"])
categories = APIRouter(prefix="/categories", tags=["categories"])
subtables = APIRouter(prefix="/subtables", tags=["subtables"])
rules = APIRouter(prefix="/keyword-rules", tags=["keyword-rules"])
mappings = APIRouter(prefix="/description-mappings", tags=["description-mappings"])
fleet = APIRouter(tags=["fleet"])
people = APIRouter(prefix="/people", tags=["people"])
backups = APIRouter(prefix="/backups", tags=["backups"])
accountant = APIRouter(prefix="/accountant", tags=["accountant"])

_OPERATION_LOCK_SECONDS = 60


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
            "ip": request.client.host if request.client else None,
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
    user = await db.users.find_one({"username": body.username, "active": True})
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise ApiError(401, "invalid_credentials", "Invalid username or password")
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": utcnow()}})
    return json_safe(await issue_session(request, user))


@auth.get("/bootstrap-status")
async def bootstrap_status(request: Request) -> dict:
    return {"has_users": await database(request).users.count_documents({}) > 0}


@auth.post("/bootstrap", status_code=201)
async def bootstrap(body: UserCreate, request: Request) -> dict:
    if body.role != "admin":
        raise ApiError(422, "admin_required", "The first user must be an admin")
    db = database(request)
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
        doc.update(password_hash=hash_password(body.password), created_at=utcnow(), last_login=None)
        result = await db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
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
async def logout(request: Request, _user: Authenticated) -> Response:
    await database(request).auth_sessions.update_one(
        {"_id": request.state.session["_id"]}, {"$set": {"revoked_at": utcnow()}}
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
    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"password_hash": hash_password(body.new_password)}}
    )
    await db.auth_sessions.update_many(
        {"user_id": user["_id"], "_id": {"$ne": request.state.session["_id"]}},
        {"$set": {"revoked_at": now}},
    )
    return Response(status_code=204)


@accountant.get("/notification-counts")
async def accountant_notification_counts(
    request: Request,
    _manager: Annotated[dict, Depends(require_roles("admin", "accountant"))],
) -> dict[str, int]:
    db = database(request)
    # Match desktop get_pending_count: submitted/legacy inbox + deletion requests
    # (exclude cashier drafts that are not yet submitted for verify).
    pending = await db.transactions.count_documents(
        {
            "verified": False,
            "rejected": {"$ne": True},
            "$or": [
                {"register_status": "submitted"},
                {"register_status": {"$exists": False}},
            ],
        }
    )
    deletions = await db.transactions.count_documents({"deletion_requested": True})
    return {"verify": int(pending) + int(deletions)}


@users.get("")
async def list_users(request: Request, _manager: Manager) -> list[dict]:
    docs = await database(request).users.find({}, {"password_hash": 0}).sort("full_name", 1).to_list()
    return json_safe(docs)


@users.post("", status_code=201)
async def create_user(body: UserCreate, request: Request, manager: Manager) -> dict:
    if body.role == "admin" and manager.get("role") != "admin":
        raise ApiError(403, "forbidden", "Only an administrator can create administrators")
    doc = body.model_dump(exclude={"password"})
    doc.update(password_hash=hash_password(body.password), created_at=utcnow(), last_login=None)
    result = await database(request).users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return public_user(doc)


@users.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, request: Request, manager: Manager) -> dict:
    values = body.model_dump(exclude_none=True)
    password = values.pop("password", None)
    if password:
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
        if not current:
            raise ApiError(404, "not_found", "User not found")
        if manager.get("role") != "admin" and (
            current.get("role") == "admin" or values.get("role") == "admin"
        ):
            raise ApiError(403, "forbidden", "Only an administrator can modify administrators")
        removes_active_admin = (
            current.get("role") == "admin"
            and current.get("active", True)
            and (values.get("role", "admin") != "admin" or values.get("active", True) is False)
        )
        if removes_active_admin:
            active_admins = await db.users.count_documents({"role": "admin", "active": True})
            if active_admins <= 1:
                raise ApiError(
                    409,
                    "last_admin_required",
                    "The final active administrator cannot be demoted or deactivated",
                )
        doc = await db.users.find_one_and_update(
            {"_id": oid}, {"$set": values}, return_document=ReturnDocument.AFTER
        )
    finally:
        if lock_owner is not None:
            await release_operation_lock(db, "admin-user-update", lock_owner)
    if not doc:
        raise ApiError(404, "not_found", "User not found")
    if password or values.get("active") is False:
        await database(request).auth_sessions.update_many(
            {"user_id": oid, "revoked_at": None}, {"$set": {"revoked_at": utcnow()}}
        )
    return public_user(doc)


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
        "restrict_in_pdf", "restrict_in_excel", "active",
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
        query["$or"] = [
            {field: {"$regex": re.escape(search), "$options": "i"}}
            for field in ("name", "number", "pattern", "description")
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
async def list_categories(request: Request, _user: Authenticated, page: Annotated[dict, Depends(page_params)]) -> dict:
    return await list_collection(request, "categories", **page)


@categories.post("", status_code=201)
async def create_category(body: CategoryWrite, request: Request, _manager: Manager) -> dict:
    return await insert_category(body, request)


async def insert_category(body: CategoryWrite, request: Request) -> dict:
    doc = body.model_dump()
    result = await database(request).categories.insert_one(doc)
    doc["_id"] = result.inserted_id
    return json_safe(doc)


@categories.post("/cashier-create", status_code=201)
async def cashier_create_category(
    body: CategoryWrite, request: Request, _cashier: Cashier
) -> dict:
    """Allow cashier/accountant unknown-item creation from the daily register."""
    return await insert_category(body, request)


@categories.patch("/{item_id}")
async def patch_category(item_id: str, body: PatchDocument, request: Request, _manager: Manager) -> dict:
    values = allowed_patch("categories", body.values)
    doc = await database(request).categories.find_one_and_update(
        {"_id": valid_id(item_id)}, {"$set": values}, return_document=ReturnDocument.AFTER
    )
    if not doc:
        raise ApiError(404, "not_found", "Category not found")
    return json_safe(doc)


@categories.delete("/{item_id}", status_code=204)
async def delete_category(item_id: str, request: Request, _manager: Manager) -> Response:
    result = await database(request).categories.delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Category not found")
    return Response(status_code=204)


async def create_named(request: Request, collection: str, body: Any) -> dict:
    doc = body.model_dump()
    if collection in {"category_subtables", "description_mappings"}:
        doc["created_at"] = utcnow()
    if collection == "description_mappings":
        doc["description_key"] = " ".join(doc["description"].upper().split())
        doc = await database(request)[collection].find_one_and_update(
            {"description_key": doc["description_key"]},
            {"$set": doc},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return json_safe(doc)
    if "category_id" in doc:
        doc["category_id"] = valid_id(doc["category_id"])
    result = await database(request)[collection].insert_one(doc)
    doc["_id"] = result.inserted_id
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
async def create_subtable(body: SubtableWrite, request: Request, _manager: Manager) -> dict:
    return await create_named(request, "category_subtables", body)


@subtables.post("/cashier-create", status_code=201)
async def cashier_create_subtable(
    body: SubtableWrite, request: Request, _cashier: Cashier
) -> dict:
    """Allow cashier/accountant sub-item creation from import mapping."""
    return await create_named(request, "category_subtables", body)


@rules.get("")
async def list_rules(request: Request, _user: Authenticated, page: Annotated[dict, Depends(page_params)]) -> dict:
    return await list_collection(request, "keyword_rules", **page)


@rules.post("", status_code=201)
async def create_rule(body: RuleWrite, request: Request, _manager: Manager) -> dict:
    return await create_named(request, "keyword_rules", body)


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
async def put_mapping(body: MappingWrite, request: Request, _user: Authenticated) -> dict:
    """Cashiers save maps during daily import; accountants/admins manage them too."""
    return await create_named(request, "description_mappings", body)


async def patch_named(collection: str, item_id: str, body: PatchDocument, request: Request) -> dict:
    values = allowed_patch(collection, body.values)
    if "category_id" in values:
        values["category_id"] = valid_id(str(values["category_id"]))
    doc = await database(request)[collection].find_one_and_update(
        {"_id": valid_id(item_id)}, {"$set": values}, return_document=ReturnDocument.AFTER
    )
    if not doc:
        raise ApiError(404, "not_found", "Resource not found")
    return json_safe(doc)


@subtables.patch("/{item_id}")
async def patch_subtable(item_id: str, body: PatchDocument, request: Request, _manager: Manager) -> dict:
    return await patch_named("category_subtables", item_id, body, request)


@rules.patch("/{item_id}")
async def patch_rule(item_id: str, body: PatchDocument, request: Request, _manager: Manager) -> dict:
    return await patch_named("keyword_rules", item_id, body, request)


async def delete_named(collection: str, item_id: str, request: Request) -> Response:
    result = await database(request)[collection].delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Resource not found")
    return Response(status_code=204)


@subtables.delete("/{item_id}", status_code=204)
async def delete_subtable(item_id: str, request: Request, _manager: Manager) -> Response:
    return await delete_named("category_subtables", item_id, request)


@rules.delete("/{item_id}", status_code=204)
async def delete_rule(item_id: str, request: Request, _manager: Manager) -> Response:
    return await delete_named("keyword_rules", item_id, request)


@mappings.delete("")
async def delete_mappings(request: Request, _manager: Manager) -> dict:
    result = await database(request).description_mappings.delete_many({})
    return {"deleted_count": result.deleted_count}


@mappings.delete("/{item_id}", status_code=204)
async def delete_mapping(item_id: str, request: Request, _manager: Manager) -> Response:
    result = await database(request).description_mappings.delete_one({"_id": valid_id(item_id)})
    if not result.deleted_count:
        raise ApiError(404, "not_found", "Mapping not found")
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


async def put_fleet(kind: str, body: FleetWrite, request: Request) -> dict:
    return json_safe(
        await database(request)[kind].find_one_and_update(
            {"number": body.number},
            {"$set": body.model_dump()},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    )


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
async def upsert_truck(number: str, body: FleetWrite, request: Request, _manager: Manager) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("trucks", body, request)


@fleet.delete("/trucks/{number}", status_code=204)
async def delete_truck(number: str, request: Request, _manager: Manager) -> Response:
    await database(request).trucks.delete_one({"number": " ".join(number.upper().split())})
    return Response(status_code=204)


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
async def upsert_trailer(number: str, body: FleetWrite, request: Request, _manager: Manager) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("trailers", body, request)


@fleet.delete("/trailers/{number}", status_code=204)
async def delete_trailer(number: str, request: Request, _manager: Manager) -> Response:
    await database(request).trailers.delete_one({"number": " ".join(number.upper().split())})
    return Response(status_code=204)


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
async def upsert_motor_vehicle(number: str, body: FleetWrite, request: Request, _manager: Manager) -> dict:
    if number.upper().strip() != body.number:
        raise ApiError(422, "number_mismatch", "Path and body numbers must match")
    return await put_fleet("motor_vehicles", body, request)


@fleet.delete("/motor_vehicles/{number}", status_code=204)
async def delete_motor_vehicle(number: str, request: Request, _manager: Manager) -> Response:
    await database(request).motor_vehicles.delete_one(
        {"number": " ".join(number.upper().split())}
    )
    return Response(status_code=204)


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
async def upsert_person(name: str, body: PeopleWrite, request: Request, _manager: Manager) -> dict:
    normalized = " ".join(name.upper().split())
    if normalized != body.name:
        raise ApiError(422, "name_mismatch", "Path and body names must match")
    return json_safe(
        await database(request)["people"].find_one_and_update(
            {"name": body.name},
            {"$set": body.model_dump()},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    )


@people.delete("/{name}", status_code=204)
async def delete_person(name: str, request: Request, _manager: Manager) -> Response:
    await database(request)["people"].delete_one({"name": " ".join(name.upper().split())})
    return Response(status_code=204)


@backups.get("")
async def list_backup_jobs(
    request: Request,
    _manager: Annotated[dict, Depends(require_roles("admin", "accountant"))],
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    docs = await database(request).backup_jobs.find().sort("created_at", -1).limit(limit).to_list()
    return json_safe(docs)


@backups.post("/restore")
async def restore_backup_job(
    body: BackupRestoreRequest,
    request: Request,
    manager: Manager,
) -> dict:
    """Replace the live database with a verified uploaded backup (admin/accountant)."""
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
                    actor=manager,
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
    return json_safe(result)


for router in (auth, users, categories, subtables, rules, mappings, fleet, people, backups, accountant):
    api.include_router(router)
