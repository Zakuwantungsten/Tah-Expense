from dataclasses import dataclass
from typing import Optional
from bson import ObjectId


@dataclass
class Category:
    name: str
    color: str = "#4A90D9"
    requires_receipt: bool = False
    requires_truck: bool = True
    active: bool = True
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "name": self.name,
            "color": self.color,
            "requires_receipt": self.requires_receipt,
            "requires_truck": self.requires_truck,
            "active": self.active,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Category":
        return cls(
            _id=doc.get("_id"),
            name=doc["name"],
            color=doc.get("color", "#4A90D9"),
            requires_receipt=doc.get("requires_receipt", False),
            requires_truck=doc.get("requires_truck", True),
            active=doc.get("active", True),
        )
