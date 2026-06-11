from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from bson import ObjectId


@dataclass
class SubTable:
    """A user-created sub-table (e.g. a Mileage route) under a parent category.

    Identified at query time by matching the transaction ``description`` against
    ``match`` (case-insensitive contains). ``match`` defaults to ``name``.
    """

    parent_key: str               # sidebar key, e.g. "mileage"
    parent_category: str          # category_name filter, e.g. "Mileage"
    name: str                     # display name, e.g. "Dar to Congo"
    match: str = ""               # description match text; defaults to name
    active: bool = True
    sort_order: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    _id: Optional[ObjectId] = None

    def __post_init__(self) -> None:
        if not self.match:
            self.match = self.name

    def to_doc(self) -> dict:
        doc = {
            "parent_key": self.parent_key,
            "parent_category": self.parent_category,
            "name": self.name,
            "match": self.match,
            "active": self.active,
            "sort_order": self.sort_order,
            "created_at": self.created_at,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "SubTable":
        return cls(
            _id=doc.get("_id"),
            parent_key=doc["parent_key"],
            parent_category=doc.get("parent_category", ""),
            name=doc["name"],
            match=doc.get("match", "") or doc["name"],
            active=doc.get("active", True),
            sort_order=doc.get("sort_order", 0),
            created_at=doc.get("created_at", datetime.utcnow()),
        )
