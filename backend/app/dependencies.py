from collections.abc import Callable
from typing import Annotated, Any

import jwt
from bson import ObjectId
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .errors import ApiError
from .security import decode_access_token, utcnow

bearer = HTTPBearer(auto_error=False)


def settings(request: Request) -> Settings:
    return request.app.state.settings


def db(request: Request) -> Any:
    return request.app.state.database.db


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    if credentials is None:
        raise ApiError(401, "authentication_required", "Bearer token required")
    try:
        claims = decode_access_token(request.app.state.settings, credentials.credentials)
        if claims.get("type") != "access":
            raise ApiError(401, "invalid_token", "Invalid access token")
        user_id = ObjectId(claims["sub"])
        session_id = ObjectId(claims["sid"])
    except (jwt.PyJWTError, ValueError, TypeError):
        raise ApiError(401, "invalid_token", "Access token is invalid or expired") from None

    database = request.app.state.database.db
    session = await database.auth_sessions.find_one(
        {
            "_id": session_id,
            "user_id": user_id,
            "revoked_at": None,
            "expires_at": {"$gt": utcnow()},
        }
    )
    if not session:
        raise ApiError(401, "session_revoked", "Session is no longer active")
    user = await database.users.find_one({"_id": user_id, "active": True})
    if not user:
        raise ApiError(401, "user_inactive", "User is inactive or unavailable")
    request.state.session = session
    return user


def require_roles(*roles: str) -> Callable:
    async def dependency(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user.get("role") not in roles:
            raise ApiError(403, "forbidden", "Your role cannot perform this action")
        return user

    return dependency


Authenticated = Annotated[dict[str, Any], Depends(current_user)]
Admin = Annotated[dict[str, Any], Depends(require_roles("admin"))]
Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]
Cashier = Annotated[dict[str, Any], Depends(require_roles("cashier"))]
