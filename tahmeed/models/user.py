from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from bson import ObjectId


@dataclass
class User:
    username: str
    password_hash: str
    role: str  # "admin" | "cashier" | "accountant"
    full_name: str
    active: bool = True
    banned: bool = False
    ban_reason: str = ""
    banned_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    _id: Optional[ObjectId] = None

    @property
    def status_label(self) -> str:
        if self.deleted_at:
            return "Deleted"
        if self.banned:
            return "Banned"
        return "Active" if self.active else "Inactive"

    def to_doc(self) -> dict:
        doc = {
            "username": self.username,
            "password_hash": self.password_hash,
            "role": self.role,
            "full_name": self.full_name,
            "active": self.active,
            "banned": self.banned,
            "ban_reason": self.ban_reason,
            "banned_at": self.banned_at,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
            "last_login": self.last_login,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "User":
        return cls(
            _id=doc.get("_id"),
            username=doc["username"],
            password_hash=doc.get("password_hash") or "",
            role=doc["role"],
            full_name=doc["full_name"],
            active=doc.get("active", True),
            banned=bool(doc.get("banned", False)),
            ban_reason=str(doc.get("ban_reason") or ""),
            banned_at=doc.get("banned_at"),
            deleted_at=doc.get("deleted_at"),
            created_at=doc.get("created_at", datetime.utcnow()),
            last_login=doc.get("last_login"),
        )
