"""Parse and import the MASTER EXPENSES Excel sheet into verified transactions."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
from bson import ObjectId

from tahmeed.models.transaction import pack_money
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, jsonable_document
from tahmeed.services.description_mapping_service import (
    get_mappings_for_descriptions,
    normalize_description,
    save_mapping,
)
from tahmeed.services.excel_dates import parse_excel_date
from tahmeed.services.daily_import_service import parse_amount


@dataclass
class MasterImportRow:
    """One parsed row from the master Excel sheet."""

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
    receipt_raw: str
    ownership: str
    approver: str
    category_id: Optional[ObjectId] = None
    category_name: Optional[str] = None
    payee: str = ""
    amount_usd: Optional[float] = None


@dataclass
class MasterImportPreview:
    source_filename: str
    rows: List[MasterImportRow] = field(default_factory=list)
    unmapped: Dict[str, int] = field(default_factory=dict)  # description_key -> row count
    skipped: int = 0


def _cell_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _parse_date(val) -> Optional[datetime]:
    parsed = parse_excel_date(val)
    if parsed is None:
        return None
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_master_expenses_excel(
    path: str | Path,
    *,
    sheet_name: Optional[str] = None,
) -> Tuple[List[MasterImportRow], int]:
    """Return parsed rows and a count of skipped blank rows."""
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    skipped = 0
    rows: List[MasterImportRow] = []
    try:
        if sheet_name is None:
            for candidate in wb.sheetnames:
                if "MASTER EXPENSE" in candidate.upper():
                    sheet_name = candidate
                    break
            sheet_name = sheet_name or wb.sheetnames[0]
        if sheet_name not in wb.sheetnames:
            raise ValueError(f"Sheet '{sheet_name}' not found in workbook.")
        ws = wb[sheet_name]

        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row:
                skipped += 1
                continue
            desc = _cell_str(row[3]) if len(row) > 3 else ""
            if not desc:
                skipped += 1
                continue
            dt = _parse_date(row[1] if len(row) > 1 else None)
            if dt is None:
                skipped += 1
                continue

            # Positional TZS (11) / USD (12). Short rows without col 12 are
            # treated as TZS-only — same as sheets that omit the USD column.
            tzs = parse_amount(row[11] if len(row) > 11 else None)
            usd = parse_amount(row[12]) if len(row) > 12 else None
            amount, amount_usd, currency = pack_money(tzs, usd)

            serial = None
            try:
                if row[0] is not None:
                    serial = int(float(row[0]))
            except (TypeError, ValueError):
                pass

            memo = _cell_str(row[9]) if len(row) > 9 else ""
            notes = _cell_str(row[10]) if len(row) > 10 else ""
            rows.append(MasterImportRow(
                serial=serial,
                date=dt,
                description=desc,
                truck_number=_cell_str(row[4]) if len(row) > 4 else "",
                lpo_do=_cell_str(row[5]) if len(row) > 5 else "",
                do_number=_cell_str(row[6]) if len(row) > 6 else "",
                memo=memo,
                notes=notes,
                amount=amount,
                currency=currency,
                amount_usd=amount_usd,
                receipt_raw=_cell_str(row[13]) if len(row) > 13 else "",
                ownership=_cell_str(row[14]) if len(row) > 14 else "",
                approver=_cell_str(row[15]) if len(row) > 15 else "",
            ))
    finally:
        wb.close()
    return rows, skipped


async def preview_master_import(path: str | Path) -> MasterImportPreview:
    """Parse the workbook and resolve description mappings."""
    path = Path(path)
    parsed, skipped = parse_master_expenses_excel(path)
    unique_descs = list({r.description for r in parsed})
    mappings = await get_mappings_for_descriptions(unique_descs)

    unmapped: Dict[str, int] = {}
    for row in parsed:
        key = normalize_description(row.description)
        mapping = mappings.get(key)
        if mapping:
            row.category_id = mapping.category_id
            row.category_name = mapping.category_name
            row.payee = " ".join((mapping.payee or "").strip().upper().split())
        else:
            unmapped[key] = unmapped.get(key, 0) + 1

    return MasterImportPreview(
        source_filename=path.name,
        rows=parsed,
        unmapped=unmapped,
        skipped=skipped,
    )


async def apply_mapping_to_preview(
    preview: MasterImportPreview,
    description_key: str,
    category_id: ObjectId,
    category_name: str,
    *,
    payee: str = "",
) -> None:
    """Assign item/payee to all preview rows sharing this description key."""
    payee_text = " ".join((payee or "").strip().upper().split())
    display = description_key
    for row in preview.rows:
        if normalize_description(row.description) == description_key:
            display = row.description
            break
    await save_mapping(display, category_id, category_name, payee=payee_text)
    for row in preview.rows:
        if normalize_description(row.description) == description_key:
            row.category_id = category_id
            row.category_name = category_name
            row.payee = payee_text
    preview.unmapped.pop(description_key, None)


def _row_commit_payload(row: MasterImportRow) -> dict:
    """JSON-ready staged row for ``POST /v1/imports/master/commit``."""
    return jsonable_document(
        {
            "serial": row.serial,
            "date": row.date,
            "description": row.description,
            "truck_number": row.truck_number,
            "lpo_do": row.lpo_do,
            "do_number": row.do_number,
            "memo": row.memo,
            "notes": row.notes,
            "amount": row.amount,
            "currency": row.currency,
            "amount_usd": row.amount_usd,
            "receipt_raw": row.receipt_raw,
            "ownership": row.ownership,
            "approver": row.approver,
            "payee": row.payee or "",
            "category_id": row.category_id,
            "category_name": row.category_name,
        }
    )


async def commit_master_import(
    preview: MasterImportPreview,
    *,
    verified_by: Optional[ObjectId] = None,
    skip_duplicates: bool = True,
) -> dict:
    """Insert all preview rows as verified master transactions via API.

    ``verified_by`` is attributed server-side from the authenticated manager;
    the argument is kept for call-site compatibility.
    """
    del verified_by
    if preview.unmapped:
        raise ValueError(
            f"{len(preview.unmapped)} description(s) still need an item mapping."
        )

    payload = await api_client.request(
        "POST",
        "v1/imports/master/commit",
        json={
            "source_filename": preview.source_filename,
            "skip_duplicates": skip_duplicates,
            "rows": [_row_commit_payload(row) for row in preview.rows],
        },
    )
    return desktop_document(payload)
