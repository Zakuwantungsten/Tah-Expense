from dataclasses import dataclass
from typing import Optional
from bson import ObjectId


@dataclass
class Truck:
    number: str   # uppercase, e.g. "T688 EAF"
    active: bool = True
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {"number": self.number, "active": self.active}
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Truck":
        return cls(
            _id=doc.get("_id"),
            number=doc["number"],
            active=doc.get("active", True),
        )
