"""Parse daily MATUMIZI-style Excel sheets into staged cashier transactions."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction
from tahmeed.services.description_mapping_service import (
    get_mappings_for_descriptions,
    normalize_description,
    save_mapping,
)

# Header aliases → logical field (first match wins per column)
_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "serial": ("s/no", "sno", "s no", "serial", "#"),
    "date": ("date",),
    "description": ("description", "desc"),
    "truck": ("truck no", "truck no.", "truck", "truck number"),
    "lpo": ("lpo nos", "lpo nos.", "lpo", "lpo/do"),
    "do": ("do no", "do no.", "do number"),
    "memo": ("memo",),
    "notes": ("notes", "ref_float", "ref float"),
    "tzs": ("tzs",),
    "usd": ("usd", "us$", "dollar"),
    "receipt": ("receipt status", "receipt", "rcpt"),
    "ownership": ("ownership", "own"),
    "approver": ("apr by", "approver", "apr"),
}

_RCPT_NORM = {
    "received": "received",
    "receipt": "received",
    "1": "received",
    "yes": "received",
    "rcvd": "received",
    "missing": "missing",
    "pending": "pending",
    "0": "pending",
    "no receipt": "no_receipt",
    "no_receipt": "no_receipt",
    "none": "no_receipt",
    "n/a": "no_receipt",
    "na": "no_receipt",
}


@dataclass
class DailyImportRow:
    serial: Optional[int]
    date: datetime
    description: str
    truck_number: str
    lpo_do: str
    do_number: str
    memo: str
    notes: str
    amount: float
    currency: str
    receipt_status: str
    ownership: str
    approver: str
    category_id: Optional[ObjectId] = None
    category_name: Optional[str] = None
    skipped_item: bool = False  # user chose Skip — leave without item


@dataclass
class DailyImportPreview:
    source_filename: str
    source_path: str
    rows: List[DailyImportRow] = field(default_factory=list)
    unmapped: Dict[str, int] = field(default_factory=dict)  # key -> count
    skipped_blank: int = 0
    primary_date: Optional[date] = None
    detected_dates: List[date] = field(default_factory=list)
    outlier_count: int = 0
    sheet_name: str = ""
    upload_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    force_primary_date: bool = False
    flag_date_discrepancy: bool = False


def cell_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def normalize_receipt(val: str) -> str:
    """Map Excel receipt text to stored status.

    Daily files use only RECEIPT / NO RECEIPT (shown uppercase in the grid).
    Stored keys remain received / no_receipt for compatibility.
    """
    s = " ".join((val or "").strip().lower().split())
    if not s:
        return "pending"
    if s in _RCPT_NORM:
        return _RCPT_NORM[s]
    if "no receipt" in s:
        return "no_receipt"
    if s == "receipt" or "received" in s:
        return "received"
    return "pending"


def parse_amount(val) -> Optional[float]:
    """Parse Excel/number/paste amounts; '(1,000)' → -1000."""
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s or s.upper() in {"NONE", "N/A", "-", "—"}:
        return None
    # Excel formula leftovers
    if s.startswith("="):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    s = s.replace(",", "").replace(" ", "").replace("TZS", "").replace("USD", "")
    s = s.replace("Tsh", "").replace("tsh", "")
    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    s = re.sub(r"[^\d.]", "", s)
    if not s or s.count(".") > 1:
        return None
    try:
        amount = float(s)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_date_value(val) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(val, date) and not isinstance(val, datetime):
        return datetime(val.year, val.month, val.day)
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d.%m.%Y",
        "%Y/%m/%d",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def detect_date_from_name(text: str) -> Optional[date]:
    """Pull DD-MM-YYYY / DD/MM/YYYY from filename or sheet title."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", text)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _norm_header(val) -> str:
    return " ".join(cell_str(val).lower().replace(".", " ").split())


def _map_headers(header_row: List) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, cell in enumerate(header_row or []):
        h = _norm_header(cell)
        if not h:
            continue
        for field, aliases in _HEADER_ALIASES.items():
            if field in mapping:
                continue
            if h in aliases or any(h == a or h.startswith(a) for a in aliases):
                mapping[field] = idx
                break
    return mapping


def _col(row: List, cols: Dict[str, int], key: str, default=None):
    idx = cols.get(key)
    if idx is None or idx >= len(row):
        return default
    return row[idx]


def pick_primary_date(
    row_dates: List[date],
    *,
    filename: str = "",
    sheet_name: str = "",
) -> Optional[date]:
    """Prefer majority row date; fall back to filename/sheet detection."""
    if row_dates:
        counts = Counter(row_dates)
        return counts.most_common(1)[0][0]
    return detect_date_from_name(filename) or detect_date_from_name(sheet_name)


def parse_daily_expenses_excel(
    path: str | Path,
    *,
    sheet_name: Optional[str] = None,
) -> Tuple[List[DailyImportRow], int, str]:
    """Return (rows, skipped_blank_count, sheet_name_used)."""
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        used_sheet = sheet_name or wb.sheetnames[0]
        if used_sheet not in wb.sheetnames:
            raise ValueError(f"Sheet '{used_sheet}' not found in workbook.")
        ws = wb[used_sheet]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = list(next(rows_iter))
        except StopIteration:
            return [], 0, used_sheet
        cols = _map_headers(header)
        if "description" not in cols or "date" not in cols:
            # Fallback to classic MATUMIZI positional layout
            cols = {
                "serial": 0,
                "date": 1,
                "description": 3,
                "truck": 4,
                "lpo": 5,
                "do": 6,
                "memo": 9,
                "notes": 10,
                "tzs": 11,
                "usd": 12,
                "receipt": 13,
                "ownership": 14,
                "approver": 15,
            }

        parsed: List[DailyImportRow] = []
        skipped = 0
        for row in rows_iter:
            if not row:
                skipped += 1
                continue
            row = list(row)
            desc = cell_str(_col(row, cols, "description"))
            if not desc:
                skipped += 1
                continue
            # Skip total/summary rows
            if desc.upper().startswith("TOTAL") or str(_col(row, cols, "serial", "")).upper() == "TOTAL":
                skipped += 1
                continue
            dt = parse_date_value(_col(row, cols, "date"))
            if dt is None:
                skipped += 1
                continue

            tzs = parse_amount(_col(row, cols, "tzs"))
            usd = parse_amount(_col(row, cols, "usd"))
            amount = 0.0
            currency = "TZS"
            if usd is not None and usd != 0:
                amount = usd
                currency = "USD"
            elif tzs is not None:
                amount = tzs
                currency = "TZS"
            elif usd is not None:
                amount = usd
                currency = "USD"

            serial = None
            raw_serial = _col(row, cols, "serial")
            try:
                if raw_serial is not None and cell_str(raw_serial):
                    serial = int(float(raw_serial))
            except (TypeError, ValueError):
                pass

            notes = cell_str(_col(row, cols, "notes"))
            parsed.append(
                DailyImportRow(
                    serial=serial,
                    date=dt,
                    description=desc,
                    truck_number=cell_str(_col(row, cols, "truck")).upper(),
                    lpo_do=cell_str(_col(row, cols, "lpo")),
                    do_number=cell_str(_col(row, cols, "do")),
                    memo=cell_str(_col(row, cols, "memo")),
                    notes=notes,
                    amount=amount,
                    currency=currency,
                    receipt_status=normalize_receipt(cell_str(_col(row, cols, "receipt"))),
                    ownership=cell_str(_col(row, cols, "ownership")),
                    approver=cell_str(_col(row, cols, "approver")),
                )
            )
    finally:
        wb.close()
    return parsed, skipped, used_sheet


async def preview_daily_import(path: str | Path) -> DailyImportPreview:
    path = Path(path)
    parsed, skipped, sheet = parse_daily_expenses_excel(path)
    unique_descs = list({r.description for r in parsed})
    mappings = await get_mappings_for_descriptions(unique_descs)

    unmapped: Dict[str, int] = {}
    for row in parsed:
        key = normalize_description(row.description)
        mapping = mappings.get(key)
        if mapping:
            row.category_id = mapping.category_id
            row.category_name = mapping.category_name
        else:
            unmapped[key] = unmapped.get(key, 0) + 1

    row_dates = [r.date.date() for r in parsed]
    primary = pick_primary_date(row_dates, filename=path.name, sheet_name=sheet)
    outliers = 0
    if primary is not None:
        outliers = sum(1 for d in row_dates if d != primary)

    return DailyImportPreview(
        source_filename=path.name,
        source_path=str(path),
        rows=parsed,
        unmapped=unmapped,
        skipped_blank=skipped,
        primary_date=primary,
        detected_dates=sorted(set(row_dates)),
        outlier_count=outliers,
        sheet_name=sheet,
    )


async def apply_mapping_to_preview(
    preview: DailyImportPreview,
    description_key: str,
    category_id: ObjectId,
    category_name: str,
) -> None:
    display = description_key
    for row in preview.rows:
        if normalize_description(row.description) == description_key:
            display = row.description
            break
    await save_mapping(display, category_id, category_name)
    for row in preview.rows:
        if normalize_description(row.description) == description_key:
            row.category_id = category_id
            row.category_name = category_name
            row.skipped_item = False
    preview.unmapped.pop(description_key, None)


def skip_description_in_preview(preview: DailyImportPreview, description_key: str) -> None:
    for row in preview.rows:
        if normalize_description(row.description) == description_key:
            row.skipped_item = True
            row.category_id = None
            row.category_name = None
    preview.unmapped.pop(description_key, None)


def skip_all_unmapped(preview: DailyImportPreview) -> None:
    for key in list(preview.unmapped.keys()):
        skip_description_in_preview(preview, key)


def apply_date_policy(
    preview: DailyImportPreview,
    *,
    force_primary: bool,
    flag_discrepancy: bool,
) -> None:
    preview.force_primary_date = force_primary
    preview.flag_date_discrepancy = flag_discrepancy and preview.outlier_count > 0
    if force_primary and preview.primary_date is not None:
        primary_dt = datetime(
            preview.primary_date.year,
            preview.primary_date.month,
            preview.primary_date.day,
        )
        for row in preview.rows:
            row.date = primary_dt


def _ref_float_from_notes(notes: str) -> str:
    low = (notes or "").strip().lower()
    if "refund" in low and "float" in low:
        return "Refund to Float"
    return (notes or "").strip()


def staged_row_payload(row: DailyImportRow, preview: DailyImportPreview) -> dict:
    """Dict used to populate the Daily Register grid before Save."""
    primary = preview.primary_date
    is_outlier = bool(
        primary is not None
        and row.date.date() != primary
        and not preview.force_primary_date
    )
    return {
        "date": row.date,
        "item": row.category_name or "",
        "description": row.description,
        "truck_number": row.truck_number,
        "memo": row.memo,
        "ref_float": _ref_float_from_notes(row.notes),
        "amount": row.amount,
        "currency": row.currency,
        "receipt_status": row.receipt_status,
        "ownership": row.ownership,
        "approver": row.approver,
        "category_id": row.category_id,
        "category_name": row.category_name,
        "lpo_do": row.lpo_do,
        "do_number": row.do_number,
        "daily_import_id": preview.upload_id,
        "daily_import_source": preview.source_filename,
        "date_discrepancy": bool(
            preview.flag_date_discrepancy and is_outlier
        ),
        "import_primary_date": (
            datetime(primary.year, primary.month, primary.day)
            if primary is not None
            else None
        ),
    }


async def list_daily_uploads(limit: int = 100) -> List[dict]:
    db = get_db()
    pipeline = [
        {"$match": {"daily_import_id": {"$exists": True, "$ne": ""}}},
        {
            "$group": {
                "_id": "$daily_import_id",
                "source_filename": {"$first": "$daily_import_source"},
                "count": {"$sum": 1},
                "primary_date": {"$first": "$import_primary_date"},
                "min_date": {"$min": "$date"},
                "max_date": {"$max": "$date"},
                "created_at": {"$min": "$created_at"},
                "cashier_id": {"$first": "$cashier_id"},
                "outlier_count": {
                    "$sum": {"$cond": ["$date_discrepancy", 1, 0]}
                },
                "duplicate_count": {
                    "$sum": {"$cond": ["$possible_duplicate", 1, 0]}
                },
            }
        },
        {"$sort": {"created_at": -1}},
        {"$limit": limit},
    ]
    return await db.transactions.aggregate(pipeline).to_list(length=limit)


async def delete_daily_upload(upload_id: str) -> int:
    if not upload_id:
        return 0
    db = get_db()
    result = await db.transactions.delete_many({"daily_import_id": upload_id})
    return result.deleted_count


def _build_issue_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id=None,
    date_from=None,
    date_to=None,
    item: str = "",
    description: str = "",
) -> dict:
    issue_or = {"$or": [
        {"possible_duplicate": True},
        {"date_discrepancy": True},
    ]}
    and_clauses: list = [issue_or]
    if search.strip():
        s = re.escape(search.strip())
        and_clauses.append({"$or": [
            {"description": {"$regex": s, "$options": "i"}},
            {"item": {"$regex": s, "$options": "i"}},
            {"category_name": {"$regex": s, "$options": "i"}},
            {"truck_number": {"$regex": s, "$options": "i"}},
        ]})
    if description.strip():
        and_clauses.append({
            "description": {
                "$regex": re.escape(description.strip()),
                "$options": "i",
            },
        })
    if item.strip():
        it = re.escape(item.strip())
        and_clauses.append({"$or": [
            {"item": {"$regex": f"^{it}$", "$options": "i"}},
            {"category_name": {"$regex": f"^{it}$", "$options": "i"}},
        ]})

    query: dict = {
        "verified": {"$ne": True},
        "rejected": {"$ne": True},
        "$and": and_clauses,
    }
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        query["date"] = df
    return query


async def get_issue_transactions(
    *,
    skip: int = 0,
    limit: int = 50,
    search: str = "",
    truck: str = "",
    cashier_id=None,
    date_from=None,
    date_to=None,
    item: str = "",
    description: str = "",
) -> List[Transaction]:
    db = get_db()
    query = _build_issue_query(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    cursor = (
        db.transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_issue_transactions(
    *,
    search: str = "",
    truck: str = "",
    cashier_id=None,
    date_from=None,
    date_to=None,
    item: str = "",
    description: str = "",
) -> int:
    db = get_db()
    query = _build_issue_query(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    return await db.transactions.count_documents(query)


async def clear_issue_flags(tx_id: ObjectId) -> bool:
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {"possible_duplicate": False, "date_discrepancy": False}},
    )
    return result.modified_count == 1
