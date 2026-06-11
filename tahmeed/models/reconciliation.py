"""Reconciliation entry model — one bonded entry on an SM Burhani schedule.

SM Burhani sends two kinds of Excel sheets that both land in the same
``reconciliation_entries`` collection, distinguished by ``table``:

  * ``bonds``        — the per-station BONDS schedule (Nakonde / Kasumbalesa /
                       Sakania), 10 columns.
  * ``rpa_schedule`` — the RPA SCHEDULE sheet, 12 columns (adds ASYCUDA AMOUNT,
                       EXPORTER and DESCRIPTION OF SHIPMENT).

Duplicate detection uses the composite ``dedup_key`` = ``PRN | ENTRY REG``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from bson import ObjectId


@dataclass
class ReconciliationEntry:
    entity: str = "sm_burhani"        # bonding company
    table: str = "bonds"              # "bonds" | "rpa_schedule"
    station: str = ""                 # "nakonde" | "kasumbalesa" | "sakania"
    schedule_period: str = ""         # e.g. "SCHEDULE FROM 01.05.2026 - 15.05.2026"

    sr_no: Optional[int] = None
    sm_ref_no: str = ""
    prn_number: str = ""
    entry_reg_no: str = ""
    t1_no: str = ""
    t1_date: Optional[datetime] = None
    importer: str = ""
    consignment: str = ""             # CONSIGNMENT (bonds) / DESCRIPTION OF SHIPMENT (rpa)
    truck_and_trailer: str = ""
    charge: float = 0.0

    # ── RPA-schedule-only extras ──
    asycuda_amount: Optional[float] = None
    exporter: str = ""

    # ── Reconciliation status ──
    confirmed: bool = False
    disputed: bool = False
    dispute_note: str = ""

    import_date: datetime = field(default_factory=datetime.utcnow)
    imported_by: Optional[ObjectId] = None
    _id: Optional[ObjectId] = None

    @property
    def dedup_key(self) -> str:
        return f"{self.prn_number.strip()}|{self.entry_reg_no.strip()}"

    def to_doc(self) -> dict:
        doc = {
            "entity": self.entity,
            "table": self.table,
            "station": self.station,
            "schedule_period": self.schedule_period,
            "sr_no": self.sr_no,
            "sm_ref_no": self.sm_ref_no,
            "prn_number": self.prn_number,
            "entry_reg_no": self.entry_reg_no,
            "t1_no": self.t1_no,
            "t1_date": self.t1_date,
            "importer": self.importer,
            "consignment": self.consignment,
            "truck_and_trailer": self.truck_and_trailer,
            "charge": self.charge,
            "asycuda_amount": self.asycuda_amount,
            "exporter": self.exporter,
            "confirmed": self.confirmed,
            "disputed": self.disputed,
            "dispute_note": self.dispute_note,
            "dedup_key": self.dedup_key,
            "import_date": self.import_date,
            "imported_by": self.imported_by,
        }
        if self._id:
            doc["_id"] = self._id
        return doc

    @classmethod
    def from_doc(cls, doc: dict) -> "ReconciliationEntry":
        return cls(
            _id=doc.get("_id"),
            entity=doc.get("entity", "sm_burhani"),
            table=doc.get("table", "bonds"),
            station=doc.get("station", ""),
            schedule_period=doc.get("schedule_period", ""),
            sr_no=doc.get("sr_no"),
            sm_ref_no=doc.get("sm_ref_no", ""),
            prn_number=doc.get("prn_number", ""),
            entry_reg_no=doc.get("entry_reg_no", ""),
            t1_no=doc.get("t1_no", ""),
            t1_date=doc.get("t1_date"),
            importer=doc.get("importer", ""),
            consignment=doc.get("consignment", ""),
            truck_and_trailer=doc.get("truck_and_trailer", ""),
            charge=doc.get("charge", 0.0) or 0.0,
            asycuda_amount=doc.get("asycuda_amount"),
            exporter=doc.get("exporter", ""),
            confirmed=doc.get("confirmed", False),
            disputed=doc.get("disputed", False),
            dispute_note=doc.get("dispute_note", ""),
            import_date=doc.get("import_date", datetime.utcnow()),
            imported_by=doc.get("imported_by"),
        )
