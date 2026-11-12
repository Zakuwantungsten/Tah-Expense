"""Create a local mobile test user + one needed receipt request."""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId
from pymongo import MongoClient

from app.config import get_settings
from app.security import hash_password

USERNAME = "owner"
PASSWORD = "Receipt1!"
FULL_NAME = "OWNER TEST"


def main() -> None:
    settings = get_settings()
    db = MongoClient(settings.mongodb_uri)[settings.db_name]
    now = datetime.now(UTC).replace(tzinfo=None)

    existing = db.users.find_one({"username": USERNAME, "deleted_at": None})
    if existing:
        db.users.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "password_hash": hash_password(PASSWORD),
                    "full_name": FULL_NAME,
                    "role": "cashier",
                    "active": True,
                    "banned": False,
                    "deleted_at": None,
                }
            },
        )
        uid = existing["_id"]
        print("updated user", uid)
    else:
        uid = db.users.insert_one(
            {
                "username": USERNAME,
                "password_hash": hash_password(PASSWORD),
                "role": "cashier",
                "full_name": FULL_NAME,
                "active": True,
                "banned": False,
                "ban_reason": "",
                "banned_at": None,
                "banned_by": None,
                "deleted_at": None,
                "created_at": now,
                "last_login": None,
            }
        ).inserted_id
        print("created user", uid)

    person = db.people.find_one({"name": FULL_NAME})
    if person is None:
        pid = db.people.insert_one(
            {
                "name": FULL_NAME,
                "active": True,
                "receives_receipt_requests": True,
                "user_id": uid,
                "phone": "",
            }
        ).inserted_id
        print("created person", pid)
    else:
        db.people.update_one(
            {"_id": person["_id"]},
            {
                "$set": {
                    "user_id": uid,
                    "receives_receipt_requests": True,
                    "active": True,
                }
            },
        )
        pid = person["_id"]
        print("updated person", pid)

    cat = db.categories.find_one({"requires_receipt": True}) or db.categories.find_one(
        {"name": {"$regex": "LATRA", "$options": "i"}}
    )
    item = (cat or {}).get("name") or "LATRA"
    tx_id = ObjectId()
    db.transactions.insert_one(
        {
            "_id": tx_id,
            "date": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "amount": 25000,
            "currency": "TZS",
            "item": item,
            "category_id": (cat or {}).get("_id"),
            "category_name": item,
            "payee": "SIM VENDOR",
            "ownership": FULL_NAME,
            "approver": "MKALI",
            "description": "Mobile owner upload test",
            "receipt_status": "pending",
            "attachments": [],
            "verified": False,
            "rejected": False,
            "trashed": False,
            "discarded": False,
            "cashier_id": uid,
            "created_at": now,
            "updated_at": now,
        }
    )
    rid = db.receipt_requests.insert_one(
        {
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": FULL_NAME,
            "asked_of_person_id": pid,
            "amount": 25000,
            "currency": "TZS",
            "item": item,
            "payee": "SIM VENDOR",
            "date": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "opened_at": now,
            "updated_at": now,
            "reminder_count": 0,
            "waiver_allowed": True,
        }
    ).inserted_id
    print("tx", tx_id)
    print("request", rid)
    print(f"LOGIN {USERNAME} / {PASSWORD}")


if __name__ == "__main__":
    main()
