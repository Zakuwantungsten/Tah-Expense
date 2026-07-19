from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from .config import Settings

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), encoded_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def new_refresh_secret() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def create_access_token(
    settings: Settings, *, user_id: str, role: str, session_id: str
) -> tuple[str, datetime]:
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "sid": session_id,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": expires,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "sid", "type", "exp", "iat"]},
    )


def refresh_token(session_id: str, secret: str) -> str:
    return f"{session_id}.{secret}"


def split_refresh_token(token: str) -> tuple[str, str]:
    session_id, separator, secret = token.partition(".")
    if not separator or not session_id or not secret:
        raise ValueError("Malformed refresh token")
    return session_id, secret
