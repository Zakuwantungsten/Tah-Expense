from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from bson import ObjectId


@dataclass
class Transaction:
    date: datetime
    description: str
    truck_number: str
    amount: float
    currency: str = "TZS"
    category_id: Optional[ObjectId] = None
    category_name: Optional[str] = None
    category_confidence: float = 0.0   # 0.0–1.0
    item: str = ""
    lpo_do: str = ""
    do_number: str = ""
    memo: str = ""
    receipt_status: str = "pending"    # "pending" | "received" | "missing"
    notes_flag: bool = False           # NOTES checkbox
    ownership: str = ""
    approver: str = ""
    cashier_id: Optional[ObjectId] = None
    verified: bool = False
    verified_by: Optional[ObjectId] = None
    verified_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "date": self.date,
            "description": self.description,
            "truck_number": self.truck_number,
            "amount": self.amount,
            "currency": self.currency,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "category_confidence": self.category_confidence,
            "item": self.item,
            "lpo_do": self.lpo_do,
            "do_number": self.do_number,
            "memo": self.memo,
            "receipt_status": self.receipt_status,
            "notes_flag": self.notes_flag,
            "ownership": self.ownership,
            "approver": self.approver,
            "cashier_id": self.cashier_id,
            "verified": self.verified,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Transaction":
        return cls(
            _id=doc.get("_id"),
            date=doc["date"],
            description=doc["description"],
            truck_number=doc.get("truck_number", ""),
            amount=doc["amount"],
            currency=doc.get("currency", "TZS"),
            category_id=doc.get("category_id"),
            category_name=doc.get("category_name"),
            category_confidence=doc.get("category_confidence", 0.0),
            item=doc.get("item", ""),
            lpo_do=doc.get("lpo_do", ""),
            do_number=doc.get("do_number", ""),
            memo=doc.get("memo", ""),
            receipt_status=doc.get("receipt_status", "pending"),
            notes_flag=doc.get("notes_flag", False),
            ownership=doc.get("ownership", ""),
            approver=doc.get("approver", ""),
            cashier_id=doc.get("cashier_id"),
            verified=doc.get("verified", False),
            verified_by=doc.get("verified_by"),
            verified_at=doc.get("verified_at"),
            created_at=doc.get("created_at", datetime.utcnow()),
        )
