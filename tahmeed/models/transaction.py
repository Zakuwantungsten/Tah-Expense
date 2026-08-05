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
    receipt_status: str = "pending"    # "pending" | "received" | "missing" | "no_receipt"
    notes_flag: bool = False           # True when Ref_Float is "REFUND TO FLOAT"
    ref_float: str = ""                # free-text Ref_Float (autocomplete suggests REFUND TO FLOAT)
    ownership: str = ""
    approver: str = ""
    payee: str = ""
    cheque: str = ""
    reported_date: Optional[datetime] = None
    cashier_id: Optional[ObjectId] = None
    verified: bool = False
    verified_by: Optional[ObjectId] = None
    verified_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    edited_after_verification: bool = False    # True if cashier edited a saved row awaiting re-approval
    last_edited_at: Optional[datetime] = None  # Timestamp of last cashier edit
    last_edited_by: Optional[ObjectId] = None  # Cashier who made the edit
    rejected: bool = False                     # True when accountant explicitly rejects the entry
    discarded: bool = False                    # True when cashier soft-discards a rejected entry
    deletion_requested: bool = False           # True when cashier requested delete of an approved row
    deletion_requested_at: Optional[datetime] = None
    deletion_requested_by: Optional[ObjectId] = None
    original_transaction_id: Optional[ObjectId] = None  # Points to the original approved doc when this is a pending edit
    day_order: Optional[int] = None            # Sequence within the calendar day (Merged register)
    register_status: str = "draft"             # "draft" | "submitted" — gate before Verify
    month: Optional[str] = None   # e.g. "Jan 25"
    year: Optional[int] = None    # e.g. 2025
    created_at: datetime = field(default_factory=datetime.utcnow)
    possible_duplicate: bool = False   # set True when cashier overrides a duplicate warning
    daily_import_id: Optional[str] = None       # shared id for one Excel upload batch
    daily_import_source: Optional[str] = None   # original filename
    date_discrepancy: bool = False              # row date differs from import primary date
    import_primary_date: Optional[datetime] = None
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
            "ref_float": self.ref_float,
            "ownership": self.ownership,
            "approver": self.approver,
            "payee": self.payee,
            "cheque": self.cheque,
            "reported_date": self.reported_date,
            "cashier_id": self.cashier_id,
            "verified": self.verified,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "rejection_reason": self.rejection_reason,
            "edited_after_verification": self.edited_after_verification,
            "last_edited_at": self.last_edited_at,
            "last_edited_by": self.last_edited_by,
            "rejected": self.rejected,
            "discarded": self.discarded,
            "deletion_requested": self.deletion_requested,
            "deletion_requested_at": self.deletion_requested_at,
            "deletion_requested_by": self.deletion_requested_by,
            "original_transaction_id": self.original_transaction_id,
            "day_order": self.day_order,
            "register_status": self.register_status or "draft",
            "month": self.month,
            "year": self.year,
            "created_at": self.created_at,
            "possible_duplicate": self.possible_duplicate,
            "date_discrepancy": self.date_discrepancy,
        }
        # Only persist import batch fields when this row came from an Excel upload.
        # Writing null here used to create an undeleteable phantom "upload" group.
        if self.daily_import_id:
            doc["daily_import_id"] = self.daily_import_id
        if self.daily_import_source:
            doc["daily_import_source"] = self.daily_import_source
        if self.import_primary_date is not None:
            doc["import_primary_date"] = self.import_primary_date
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Transaction":
        # Legacy docs without register_status were already in the Verify queue.
        status = doc.get("register_status") or "submitted"
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
            ref_float=doc.get("ref_float", ""),
            ownership=doc.get("ownership", ""),
            approver=doc.get("approver", ""),
            payee=doc.get("payee", ""),
            cheque=doc.get("cheque", ""),
            reported_date=doc.get("reported_date"),
            cashier_id=doc.get("cashier_id"),
            verified=doc.get("verified", False),
            verified_by=doc.get("verified_by"),
            verified_at=doc.get("verified_at"),
            rejection_reason=doc.get("rejection_reason"),
            edited_after_verification=doc.get("edited_after_verification", False),
            last_edited_at=doc.get("last_edited_at"),
            last_edited_by=doc.get("last_edited_by"),
            rejected=doc.get("rejected", False),
            discarded=doc.get("discarded", False),
            deletion_requested=doc.get("deletion_requested", False),
            deletion_requested_at=doc.get("deletion_requested_at"),
            deletion_requested_by=doc.get("deletion_requested_by"),
            original_transaction_id=doc.get("original_transaction_id"),
            day_order=doc.get("day_order"),
            register_status=status,
            month=doc.get("month"),
            year=doc.get("year"),
            created_at=doc.get("created_at", datetime.utcnow()),
            possible_duplicate=doc.get("possible_duplicate", False),
            daily_import_id=doc.get("daily_import_id"),
            daily_import_source=doc.get("daily_import_source"),
            date_discrepancy=doc.get("date_discrepancy", False),
            import_primary_date=doc.get("import_primary_date"),
        )
