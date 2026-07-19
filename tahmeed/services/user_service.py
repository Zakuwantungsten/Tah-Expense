from typing import List

from bson import ObjectId

from tahmeed.models.user import User
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document


def _user(document: dict) -> User:
    data = desktop_document(document)
    data.setdefault("password_hash", "")
    return User.from_doc(data)


async def get_all_users() -> List[User]:
    documents = await api_client.request("GET", "v1/users")
    return [_user(document) for document in documents]


async def update_user(user_id: ObjectId, **fields) -> None:
    fields.pop("password_hash", None)
    await api_client.request("PATCH", f"v1/users/{user_id}", json=fields)


async def reset_password(user_id: ObjectId, new_password: str) -> None:
    await api_client.request(
        "PATCH", f"v1/users/{user_id}", json={"password": new_password}
    )


async def toggle_active(user_id: ObjectId, active: bool) -> None:
    await api_client.request(
        "PATCH", f"v1/users/{user_id}", json={"active": active}
    )
