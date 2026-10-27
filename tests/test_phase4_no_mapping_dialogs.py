"""Phase 4: Master/Verify no longer open one-by-one mapping dialogs."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_daily_to_master_blocks_unmapped_without_prompt() -> None:
    src = _source("tahmeed/ui/accountant/daily_to_master_flow.py")
    assert "prompt_unmapped_descriptions" not in src
    assert "DescriptionMappingDialog" not in src
    assert "Map descriptions first" in src
    assert "_unmapped_block_message" in src


def test_master_excel_import_blocks_unmapped_without_prompt() -> None:
    src = _source("tahmeed/ui/accountant/master_expenses.py")
    assert "prompt_unmapped_descriptions" not in src
    # Import body still warns and returns when unmapped.
    assert "Map descriptions first" in src


def test_verify_resolve_items_has_no_mapping_dialog() -> None:
    src = _source("tahmeed/ui/accountant/verify_inbox.py")
    assert "DescriptionMappingDialog" not in src
    assert "prompt_unmapped_descriptions" not in src
    assert "— map on approve —" not in src
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_ensure_item_and_payee":
            body = ast.get_source_segment(src, node) or ""
            assert "DescriptionMappingDialog" not in body
            # Unmapped rows block approve and offer Day Issues instead.
            assert "_ask_open_day_issues" in body
            assert "open_day_issues.emit" in body
            found = True
            break
    assert found
    assert "Item and Payee required" in src


def test_cashier_daily_import_still_skips_to_day_issues() -> None:
    src = _source("tahmeed/ui/cashier/daily_import_flow.py")
    assert "skip_all_unmapped" in src
    assert "prompt_unmapped_descriptions" not in src
    assert "Day Issues" in src


def test_unmapped_block_message_lists_samples() -> None:
    from tahmeed.ui.accountant.daily_to_master_flow import _unmapped_block_message

    msg = _unmapped_block_message({"TRIANGLE": 2, "LATRA": 1})
    assert "2 description" in msg
    assert "TRIANGLE" in msg
    assert "LATRA" in msg
    assert "Description Maps" in msg
