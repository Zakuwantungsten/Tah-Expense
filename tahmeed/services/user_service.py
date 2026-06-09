import bcrypt
from typing import List
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.user import User


async def get_all_users() -> List[User]:
    db = get_db()
    cursor = db.users.find({}).sort("full_name", 1)
    docs = await cursor.to_list(length=None)
    return [User.from_doc(d) for d in docs]


async def update_user(user_id: ObjectId, **fields) -> None:
    db = get_db()
    await db.users.update_one({"_id": user_id}, {"$set": fields})


async def reset_password(user_id: ObjectId, new_password: str) -> None:
    hash_ = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    await update_user(user_id, password_hash=hash_)


async def toggle_active(user_id: ObjectId, active: bool) -> None:
    await update_user(user_id, active=active)
