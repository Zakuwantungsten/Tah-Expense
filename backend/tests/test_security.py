from bson import ObjectId

from app.config import Settings
from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_secret,
    split_refresh_token,
    verify_password,
)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def test_existing_bcrypt_format_is_compatible() -> None:
    encoded = hash_password("a strong password")
    assert encoded.startswith("$2")
    assert verify_password("a strong password", encoded)
    assert not verify_password("wrong", encoded)


def test_access_token_has_expected_identity_and_session() -> None:
    user_id, session_id = str(ObjectId()), str(ObjectId())
    token, _ = create_access_token(
        settings(), user_id=user_id, role="accountant", session_id=session_id
    )
    claims = decode_access_token(settings(), token)
    assert claims["sub"] == user_id
    assert claims["sid"] == session_id
    assert claims["role"] == "accountant"
    assert claims["type"] == "access"


def test_refresh_token_hashing_and_parsing() -> None:
    session_id, secret = str(ObjectId()), "secret"
    assert split_refresh_token(f"{session_id}.{secret}") == (session_id, secret)
    assert hash_refresh_secret(secret) != secret
