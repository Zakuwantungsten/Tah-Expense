import bcrypt
from datetime import datetime
from typing import Optional

from tahmeed.db.connection import get_db
from tahmeed.models.user import User


async def authenticate(username: str, password: str) -> Optional[User]:
    db = get_db()
    doc = await db.users.find_one({"username": username, "active": True})
    if not doc:
        return None
    if not bcrypt.checkpw(password.encode(), doc["password_hash"].encode()):
        return None
    await db.users.update_one(
        {"_id": doc["_id"]}, {"$set": {"last_login": datetime.utcnow()}}
    )
    return User.from_doc(doc)


async def create_user(
    username: str, password: str, role: str, full_name: str
) -> User:
    db = get_db()
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        username=username,
        password_hash=password_hash,
        role=role,
        full_name=full_name,
    )
    result = await db.users.insert_one(user.to_doc())
    user._id = result.inserted_id
    return user


async def any_user_exists() -> bool:
    db = get_db()
    return await db.users.count_documents({}) > 0
