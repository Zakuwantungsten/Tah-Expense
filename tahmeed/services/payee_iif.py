"""Parse QuickBooks Payee List IIF (OTHERNAME) exports."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List


def _normalize_name(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


def _header_map(header_row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, raw in enumerate(header_row):
        key = str(raw).lstrip("!").strip().upper()
        if key:
            mapping[key] = index
    return mapping


def _get_field(row: list[str], columns: dict[str, int], key: str, default: str = "") -> str:
    index = columns.get(key.upper())
    if index is None or index >= len(row):
        return default
    return str(row[index]).strip()


def parse_payee_iif(path: str | Path) -> List[dict]:
    """Return payee rows suitable for ``POST /v1/imports/payees``.

    Accepts QuickBooks ``!OTHERNAME`` / ``OTHERNAME`` tab-delimited IIF exports.
    Hidden records (HIDDEN=Y) are skipped.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    rows = list(csv.reader(text.splitlines(), delimiter="\t"))
    columns: dict[str, int] | None = None
    out: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        if not row:
            continue
        kind = str(row[0]).strip().upper()
        if kind.startswith("!") and kind.lstrip("!") == "OTHERNAME":
            columns = _header_map(row)
            continue
        if kind != "OTHERNAME":
            continue
        if columns is None:
            columns = {"OTHERNAME": 0, "NAME": 1}

        hidden = _get_field(row, columns, "HIDDEN", "N").upper()
        if hidden in {"Y", "1", "TRUE"}:
            continue

        name = _normalize_name(_get_field(row, columns, "NAME"))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name": name,
                "ref_num": _get_field(row, columns, "REFNUM"),
                "currency": _normalize_name(_get_field(row, columns, "CURRENCY")) or "TZS",
                "active": True,
            }
        )
    return out
