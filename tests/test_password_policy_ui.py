"""Desktop password-policy helpers mirror security settings."""

from __future__ import annotations

from tahmeed.services.password_policy import (
    password_min_length,
    password_placeholder,
    password_policy_errors,
)


def test_placeholder_uses_configured_min_length() -> None:
    settings = {"security_password_min_length": 6}
    assert password_min_length(settings) == 6
    assert password_placeholder(settings=settings) == "Min 6 characters"
    assert password_placeholder(editing=True, settings=settings) == (
        "Leave blank to keep current"
    )


def test_policy_errors_respect_min_length_and_rules() -> None:
    settings = {
        "security_password_min_length": 6,
        "security_password_require_upper": True,
        "security_password_require_lower": True,
        "security_password_require_digit": True,
        "security_password_require_special": False,
    }
    assert password_policy_errors("abc", settings)[0].startswith(
        "Password must be at least 6"
    )
    assert password_policy_errors("abcdef", settings)
    assert not password_policy_errors("Abcdef1", settings)
