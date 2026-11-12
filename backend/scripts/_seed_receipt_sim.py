"""One-shot local seed: person + needed receipt request for mobile sim."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from pymongo import MongoClient

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    db = MongoClient(settings.mongodb_uri)[settings.db_name]

    cat = db.categories.find_one({"requires_receipt": True})
    if cat is None:
        cat = db.categories.find_one({"name": {"$regex": "LATRA", "$options": "i"}})
        if cat is not None:
            db.categories.update_one(
                {"_id": cat["_id"]}, {"$set": {"requires_receipt": True}}
            )
            cat["requires_receipt"] = True

    saada = db.users.find_one({"username": "saada"})
    if saada is None:
        raise SystemExit("No saada user — create a cashier first.")

    name = "SAADA"
    person = db.people.find_one({"name": name})
    if person is None:
        person_id = db.people.insert_one(
            {
                "name": name,
                "active": True,
                "receives_receipt_requests": True,
                "user_id": saada["_id"],
                "phone": "",
            }
        ).inserted_id
        print("created person", person_id)
    else:
        db.people.update_one(
            {"_id": person["_id"]},
            {
                "$set": {
                    "user_id": saada["_id"],
                    "receives_receipt_requests": True,
                    "active": True,
                }
            },
        )
        person_id = person["_id"]
        print("updated person", person_id)

    now = datetime.utcnow()
    tx_id = ObjectId()
    item = (cat or {}).get("name") or "LATRA"
    db.transactions.insert_one(
        {
            "_id": tx_id,
            "date": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "amount": 45000,
            "currency": "TZS",
            "item": item,
            "category_id": (cat or {}).get("_id"),
            "category_name": item,
            "payee": "TEST VENDOR",
            "ownership": name,
            "approver": "MKALI",
            "description": "Receipt sim test",
            "receipt_status": "pending",
            "attachments": [],
            "verified": False,
            "rejected": False,
            "trashed": False,
            "discarded": False,
            "cashier_id": saada["_id"],
            "created_at": now,
            "updated_at": now,
        }
    )
    req_id = db.receipt_requests.insert_one(
        {
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": name,
            "asked_of_person_id": person_id,
            "amount": 45000,
            "currency": "TZS",
            "item": item,
            "payee": "TEST VENDOR",
            "date": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "opened_at": now,
            "updated_at": now,
            "reminder_count": 0,
            "waiver_allowed": True,
        }
    ).inserted_id
    print("cat", item)
    print("tx", tx_id)
    print("request", req_id)
    print("needed", db.receipt_requests.count_documents({"status": "needed"}))


if __name__ == "__main__":
    main()
