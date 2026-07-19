from dataclasses import dataclass
from typing import Optional
from bson import ObjectId


@dataclass
class Category:
    """An accountant-managed expense *item*.

    (Stored in the ``categories`` collection for backward compatibility; the UI
    refers to these as "items".) When ``show_in_sidebar`` is True the item gets
    its own dedicated sidebar tab, using ``icon`` as its glyph.
    """

    name: str
    description: str = ""          # hint shown to cashier; auto-fills their description field
    color: str = "#4A90D9"
    icon: str = "mdi.tag-outline"  # qtawesome mdi.* glyph for the sidebar tab
    show_in_sidebar: bool = False  # if True, item gets its own sidebar tab
    sort_order: int = 0
    requires_receipt: bool = False
    requires_truck: bool = True
    lock_description: bool = False  # if True, cashier may only pick from this item's sub-items
    active: bool = True
    # QuickBooks Chart of Accounts metadata (populated by CoA import).
    account_type: str = ""
    ref_num: str = ""
    account_number: str = ""
    currency: str = ""
    coa_description: str = ""      # CoA "Description" column (distinct from cashier hint)
    _id: Optional[ObjectId] = None

    def to_doc(self) -> dict:
        doc = {
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "icon": self.icon,
            "show_in_sidebar": self.show_in_sidebar,
            "sort_order": self.sort_order,
            "requires_receipt": self.requires_receipt,
            "requires_truck": self.requires_truck,
            "lock_description": self.lock_description,
            "active": self.active,
            "account_type": self.account_type,
            "ref_num": self.ref_num,
            "account_number": self.account_number,
            "currency": self.currency,
            "coa_description": self.coa_description,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "Category":
        return cls(
            _id=doc.get("_id"),
            name=doc["name"],
            description=doc.get("description", ""),
            color=doc.get("color", "#4A90D9"),
            icon=doc.get("icon", "mdi.tag-outline"),
            show_in_sidebar=doc.get("show_in_sidebar", False),
            sort_order=doc.get("sort_order", 0),
            requires_receipt=doc.get("requires_receipt", False),
            requires_truck=doc.get("requires_truck", True),
            lock_description=doc.get("lock_description", False),
            active=doc.get("active", True),
            account_type=doc.get("account_type", ""),
            ref_num=doc.get("ref_num", ""),
            account_number=doc.get("account_number", ""),
            currency=doc.get("currency", ""),
            coa_description=doc.get("coa_description", ""),
        )
