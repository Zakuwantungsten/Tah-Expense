from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from bson import ObjectId


@dataclass
class DescriptionMapping:
    """Maps a master-expense description text to an item (category)."""

    description_key: str          # normalized uppercase key for lookups
    description: str              # display text (first seen casing)
    category_id: ObjectId
    category_name: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "description_key": self.description_key,
            "description": self.description,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "created_at": self.created_at,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "DescriptionMapping":
        return cls(
            _id=doc.get("_id"),
            description_key=doc["description_key"],
            description=doc.get("description", doc["description_key"]),
            category_id=doc["category_id"],
            category_name=doc["category_name"],
            created_at=doc.get("created_at", datetime.utcnow()),
        )
