"""Fuel Consumption All Entries — FILE NAME / description multi-select filter."""

import re

from tahmeed.services.accountant_service import (
    _diesel_all_query,
    _diesel_file_labels_clause,
    diesel_display_label,
    unique_diesel_file_labels,
)


def test_display_label_prefers_import_description() -> None:
    assert diesel_display_label({
        "upload_label": "16th - 31st Mar 2026",
        "source_filename": "Infinity May.xlsx",
    }) == "16th - 31st Mar 2026"
    assert diesel_display_label({
        "upload_label": "  ",
        "source_filename": "Infinity May.xlsx",
    }) == "Infinity May.xlsx"
    assert diesel_display_label({"source_filename": "Infinity May.xlsx"}) == (
        "Infinity May.xlsx"
    )
    assert diesel_display_label({}) == ""


def test_unique_labels_dedupe_case_and_blanks() -> None:
    assert unique_diesel_file_labels([
        "May.xlsx", "may.xlsx", "  ", "", "June.xlsx",
    ]) == ["June.xlsx", "May.xlsx"]


def test_empty_file_labels_do_not_filter() -> None:
    assert _diesel_file_labels_clause(None) is None
    assert _diesel_file_labels_clause([]) is None
    assert _diesel_file_labels_clause(["  "]) is None
    assert _diesel_all_query("diesel_infinity") == {"feed_type": "diesel_infinity"}


def test_file_label_clause_matches_description_or_filename_fallback() -> None:
    label = "16th - 31st Mar 2026"
    clause = _diesel_file_labels_clause([label])
    assert clause is not None
    exact = {"$regex": f"^{re.escape(label)}$", "$options": "i"}
    assert {"upload_label": exact} in clause["$or"]
    filename_fallback = next(
        branch for branch in clause["$or"] if "$and" in branch
    )
    assert {"source_filename": exact} in filename_fallback["$and"]
    empty = next(branch for branch in filename_fallback["$and"] if "$or" in branch)
    assert {"upload_label": {"$regex": r"^\s*$"}} in empty["$or"]


def test_all_query_and_combines_feed_with_file_labels() -> None:
    query = _diesel_all_query(
        "diesel_lake_zambia",
        file_labels=["16th - 31st Mar 2026", "April 2026"],
    )
    assert query["$and"][0] == {"feed_type": "diesel_lake_zambia"}
    patterns = _regex_patterns(query["$and"][1])
    assert f"^{re.escape('16th - 31st Mar 2026')}$" in patterns
    assert f"^{re.escape('April 2026')}$" in patterns


def test_filename_fallback_does_not_match_custom_description_row() -> None:
    """A row with its own description must not match a different file's name."""
    clause = _diesel_file_labels_clause(["Infinity May.xlsx"])
    assert clause is not None
    custom = {
        "upload_label": "16th - 31st Mar 2026",
        "source_filename": "Infinity May.xlsx",
    }
    filename_only = {
        "upload_label": "",
        "source_filename": "Infinity May.xlsx",
    }
    assert not _row_matches_file_clause(custom, clause)
    assert _row_matches_file_clause(filename_only, clause)
    assert _row_matches_file_clause(
        {"upload_label": "Infinity May.xlsx", "source_filename": "other.xlsx"},
        clause,
    )


def _regex_patterns(clause: dict) -> set:
    found: set = set()

    def walk(obj) -> None:
        if isinstance(obj, dict):
            if "$regex" in obj:
                found.add(obj["$regex"])
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(clause)
    return found


def _row_matches_file_clause(rec: dict, clause: dict) -> bool:
    """Evaluate the file-label Mongo clause against one in-memory row."""
    label = str(rec.get("upload_label") or "").strip()
    filename = str(rec.get("source_filename") or "").strip()
    for branch in clause.get("$or", [clause]):
        if "upload_label" in branch:
            pattern = branch["upload_label"]["$regex"]
            flags = re.IGNORECASE if "i" in branch["upload_label"].get("$options", "") else 0
            if re.match(pattern, str(rec.get("upload_label") or ""), flags):
                return True
            continue
        if "$and" not in branch:
            continue
        empty_ok = not label
        name_ok = False
        for part in branch["$and"]:
            if "source_filename" in part:
                pattern = part["source_filename"]["$regex"]
                flags = (
                    re.IGNORECASE
                    if "i" in part["source_filename"].get("$options", "")
                    else 0
                )
                name_ok = bool(re.match(pattern, filename, flags))
        if empty_ok and name_ok:
            return True
    return False
