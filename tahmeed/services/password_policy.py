"""Desktop-side password policy helpers (mirrors backend security_policy)."""

from __future__ import annotations

import re
from typing import Any

from tahmeed.services.settings_service import DEFAULTS

_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


def password_min_length(settings: dict[str, Any] | None = None) -> int:
    raw = (settings or DEFAULTS).get(
        "security_password_min_length",
        DEFAULTS["security_password_min_length"],
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return int(DEFAULTS["security_password_min_length"])


def password_policy_errors(password: str, settings: dict[str, Any] | None = None) -> list[str]:
    cfg = settings or DEFAULTS
    errors: list[str] = []
    min_length = password_min_length(cfg)
    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters.")
    if cfg.get("security_password_require_upper", True) and not any(c.isupper() for c in password):
        errors.append("Password must include an uppercase letter.")
    if cfg.get("security_password_require_lower", True) and not any(c.islower() for c in password):
        errors.append("Password must include a lowercase letter.")
    if cfg.get("security_password_require_digit", True) and not any(c.isdigit() for c in password):
        errors.append("Password must include a digit.")
    if cfg.get("security_password_require_special", False) and not _SPECIAL_RE.search(password):
        errors.append("Password must include a special character.")
    return errors


def password_placeholder(*, editing: bool = False, settings: dict[str, Any] | None = None) -> str:
    if editing:
        return "Leave blank to keep current"
    return f"Min {password_min_length(settings)} characters"
