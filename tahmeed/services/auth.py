from typing import Optional

from tahmeed.models.user import User
from tahmeed.services.api_client import ApiAuthenticationError, ApiError, api_client
from tahmeed.services.api_models import desktop_document


def _user(document: dict) -> User:
    data = desktop_document(document)
    data.setdefault("password_hash", "")
    return User.from_doc(data)


async def authenticate(username: str, password: str) -> Optional[User]:
    try:
        await api_client.login(username, password)
    except ApiAuthenticationError:
        return None
    try:
        payload = await api_client.request("GET", "v1/auth/me")
    except Exception:
        api_client.clear_tokens()
        raise
    return _user(payload["user"])


async def create_user(
    username: str, password: str, role: str, full_name: str
) -> User:
    body = {
        "username": username,
        "password": password,
        "role": role,
        "full_name": full_name,
        "active": True,
    }
    if api_client.is_authenticated:
        document = await api_client.request("POST", "v1/users", json=body)
    else:
        payload = await api_client.request(
            "POST", "v1/auth/bootstrap", auth=False, json=body
        )
        api_client.set_tokens(payload["tokens"])
        document = payload["user"]
    return _user(document)


async def change_password(
    username: str, current_password: str, new_password: str
) -> bool:
    """Preserve the desktop bool contract for an incorrect current password."""
    try:
        await api_client.request(
            "POST",
            "v1/auth/change-password",
            json={"current_password": current_password, "new_password": new_password},
        )
    except ApiError as exc:
        if exc.code == "incorrect_password":
            return False
        raise
    return True


async def any_user_exists() -> bool:
    payload = await api_client.request("GET", "v1/auth/bootstrap-status", auth=False)
    return bool(payload["has_users"])
