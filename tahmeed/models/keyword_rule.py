from dataclasses import dataclass
from typing import Optional
from bson import ObjectId


@dataclass
class KeywordRule:
    pattern: str
    category_id: ObjectId
    category_name: str        # denormalized for display
    priority: int = 5         # 1–10, higher = checked first
    match_type: str = "contains"  # "contains" | "exact" | "startswith" | "fuzzy"
    active: bool = True
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "pattern": self.pattern,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "priority": self.priority,
            "match_type": self.match_type,
            "active": self.active,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "KeywordRule":
        return cls(
            _id=doc.get("_id"),
            pattern=doc["pattern"],
            category_id=doc["category_id"],
            category_name=doc.get("category_name", ""),
            priority=doc.get("priority", 5),
            match_type=doc.get("match_type", "contains"),
            active=doc.get("active", True),
        )
