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
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "username": self.username,
            "password_hash": self.password_hash,
            "role": self.role,
            "full_name": self.full_name,
            "active": self.active,
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
            password_hash=doc["password_hash"],
            role=doc["role"],
            full_name=doc["full_name"],
            active=doc.get("active", True),
            created_at=doc.get("created_at", datetime.utcnow()),
            last_login=doc.get("last_login"),
        )
