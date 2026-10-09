"""Import QuickBooks Chart of Accounts into Items (categories) via the API.

Excel parsing stays on the desktop; wipe + insert runs on
``POST /v1/imports/chart-of-accounts``.
"""

from pathlib import Path
from typing import List

import openpyxl

from tahmeed.services.api_client import api_client


def _cell_str(val) -> str:
    """Strip and uppercase text cells (same as Toll Plaza import)."""
    if val is None:
        return ""
    return str(val).strip().upper()


def _parse_chart_of_accounts(path: str | Path) -> List[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_name = "Chart of Accounts"
        if sheet_name not in wb.sheetnames:
            sheet_name = wb.sheetnames[0]
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            name = _cell_str(row[0])
            if not name:
                continue
            rows.append({
                "name": name,
                "ref_num": _cell_str(row[1]) if len(row) > 1 else "",
                "account_type": _cell_str(row[3]) if len(row) > 3 else "",
                "coa_description": _cell_str(row[5]) if len(row) > 5 else "",
                "account_number": _cell_str(row[6]) if len(row) > 6 else "",
                "currency": _cell_str(row[14]) if len(row) > 14 else "",
            })
        return rows
    finally:
        wb.close()


async def import_chart_of_accounts(
    path: str | Path,
    *,
    replace_existing: bool = True,
) -> dict:
    """Load Chart of Accounts rows into ``categories`` (Items tab) via the API.

    When ``replace_existing`` is True (default), the server removes existing
    items, sub-items, keyword rules, and description mappings first.
    """
    parsed = _parse_chart_of_accounts(path)
    if not parsed:
        raise ValueError("No accounts found in the Chart of Accounts file.")

    payload = await api_client.request(
        "POST",
        "v1/imports/chart-of-accounts",
        json={
            "rows": parsed,
            "replace_existing": bool(replace_existing),
            "source_filename": Path(path).name,
        },
    )
    result = dict(payload or {})
    result.setdefault("imported", 0)
    result.setdefault(
        "removed",
        {
            "categories": 0,
            "subtables": 0,
            "keyword_rules": 0,
            "mappings": 0,
        },
    )
    result["source"] = str(path)
    return result
