"""Application security policy: IP/MAC allowlists, password rules, login lockout."""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import timedelta
from typing import Any

from fastapi import Request

from .errors import ApiError
from .security import utcnow

DEVICE_MAC_HEADER = "x-device-mac"
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")

SECURITY_SETTING_DEFAULTS: dict[str, Any] = {
    "security_ip_allowlist_enabled": False,
    "security_allowed_ips": [],
    "security_mac_allowlist_enabled": False,
    "security_allowed_macs": [],
    "security_password_min_length": 10,
    "security_password_require_upper": True,
    "security_password_require_lower": True,
    "security_password_require_digit": True,
    "security_password_require_special": False,
    "security_login_max_attempts": 5,
    "security_login_lockout_minutes": 15,
    "security_docs_enabled": False,
    "security_max_sessions_per_user": 0,
    "security_idle_session_days": 0,
    "security_show_sessions_in_security_tab": True,
    "backup_schedule_mode": "hourly",
    "backup_daily_time_utc": "00:00",
    "backup_keep_count": 30,
}

ADMIN_ONLY_SETTING_PREFIXES = ("security_", "backup_")

_ALLOWLIST_SKIP_PREFIXES = ("/health/",)
_MAC_SKIP_PATHS = {
    "/v1/auth/bootstrap",
    "/v1/auth/bootstrap-status",
}


def security_bypass_enabled() -> bool:
    return os.getenv("TAHMEED_SECURITY_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def docs_env_forced() -> bool:
    return os.getenv("TAHMEED_DOCS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def normalize_mac(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper().replace("-", ":")
    if "." in cleaned and ":" not in cleaned:
        # Windows getnode sometimes yields no separators; leave rejection to regex.
        pass
    if len(cleaned) == 12 and all(c in "0123456789ABCDEF" for c in cleaned):
        cleaned = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.match(cleaned):
        return None
    return cleaned


def mac_from_int(node: int) -> str:
    hex_value = f"{node & ((1 << 48) - 1):012X}"
    return ":".join(hex_value[i : i + 2] for i in range(0, 12, 2))


def client_ip(request: Request) -> str:
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    peer = request.client.host if request.client else ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded and peer in {"127.0.0.1", "::1", "localhost"}:
        return forwarded
    return peer or "0.0.0.0"


def _parse_network(entry: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    text = entry.strip()
    if not text:
        return None
    try:
        if "/" in text:
            return ipaddress.ip_network(text, strict=False)
        return ipaddress.ip_network(f"{text}/32" if ":" not in text else f"{text}/128", strict=False)
    except ValueError:
        return None


def ip_allowed(client: str, allowed: list[Any]) -> bool:
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    networks = []
    for raw in allowed or []:
        network = _parse_network(str(raw))
        if network is not None:
            networks.append(network)
    if not networks:
        return False
    return any(address in network for network in networks)


def mac_allowed(mac: str | None, allowed: list[Any]) -> bool:
    if not mac:
        return False
    normalized_allowed = {
        normalize_mac(str(item)) for item in (allowed or []) if normalize_mac(str(item))
    }
    return mac in normalized_allowed


def password_policy_errors(password: str, settings: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    min_length = int(settings.get("security_password_min_length") or 10)
    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters")
    if settings.get("security_password_require_upper") and not any(c.isupper() for c in password):
        errors.append("Password must include an uppercase letter")
    if settings.get("security_password_require_lower") and not any(c.islower() for c in password):
        errors.append("Password must include a lowercase letter")
    if settings.get("security_password_require_digit") and not any(c.isdigit() for c in password):
        errors.append("Password must include a digit")
    if settings.get("security_password_require_special") and not _SPECIAL_RE.search(password):
        errors.append("Password must include a special character")
    return errors


def enforce_password_policy(password: str, settings: dict[str, Any]) -> None:
    errors = password_policy_errors(password, settings)
    if errors:
        raise ApiError(422, "password_policy", errors[0], details=errors)


def lockout_key(username: str, ip: str) -> str:
    return f"{username.strip().lower()}|{ip}"


async def assert_not_locked(db: Any, username: str, ip: str, settings: dict[str, Any]) -> None:
    if security_bypass_enabled():
        return
    key = lockout_key(username, ip)
    doc = await db.auth_lockouts.find_one({"_id": key})
    if not doc:
        return
    locked_until = doc.get("locked_until")
    if locked_until and locked_until > utcnow():
        raise ApiError(
            429,
            "account_locked",
            "Too many failed login attempts. Try again later.",
            details={"locked_until": locked_until.isoformat()},
        )


async def record_login_failure(
    db: Any, username: str, ip: str, settings: dict[str, Any]
) -> None:
    if security_bypass_enabled():
        return
    key = lockout_key(username, ip)
    now = utcnow()
    max_attempts = max(1, int(settings.get("security_login_max_attempts") or 5))
    lock_minutes = max(1, int(settings.get("security_login_lockout_minutes") or 15))
    doc = await db.auth_lockouts.find_one({"_id": key})
    failures = int(doc.get("failures", 0)) + 1 if doc else 1
    update: dict[str, Any] = {
        "failures": failures,
        "username": username.strip().lower(),
        "ip": ip,
        "updated_at": now,
    }
    if failures >= max_attempts:
        update["locked_until"] = now + timedelta(minutes=lock_minutes)
        update["failures"] = 0
    await db.auth_lockouts.update_one(
        {"_id": key},
        {"$set": update, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


async def clear_login_failures(db: Any, username: str, ip: str) -> None:
    await db.auth_lockouts.delete_one({"_id": lockout_key(username, ip)})


def path_skips_allowlist(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _ALLOWLIST_SKIP_PREFIXES)


def path_skips_mac(path: str) -> bool:
    return path.rstrip("/") in _MAC_SKIP_PATHS or path in _MAC_SKIP_PATHS


async def enforce_request_allowlists(request: Request, settings: dict[str, Any]) -> None:
    if security_bypass_enabled():
        return
    path = request.url.path
    if path_skips_allowlist(path):
        return

    ip = client_ip(request)
    request.state.client_ip = ip

    if settings.get("security_ip_allowlist_enabled"):
        allowed_ips = settings.get("security_allowed_ips") or []
        if not ip_allowed(ip, list(allowed_ips)):
            raise ApiError(403, "ip_not_allowed", "Your IP address is not allowed")

    if settings.get("security_mac_allowlist_enabled") and not path_skips_mac(path):
        mac = normalize_mac(request.headers.get(DEVICE_MAC_HEADER))
        allowed_macs = settings.get("security_allowed_macs") or []
        if not mac_allowed(mac, list(allowed_macs)):
            raise ApiError(403, "mac_not_allowed", "This device is not allowed")


def is_admin_only_setting(key: str) -> bool:
    return key.startswith(ADMIN_ONLY_SETTING_PREFIXES)


def docs_enabled(settings: dict[str, Any]) -> bool:
    return docs_env_forced() or bool(settings.get("security_docs_enabled"))
