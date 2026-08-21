"""Shared Map-Description prompts for daily / master Excel imports."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from PySide6.QtWidgets import QWidget

from tahmeed.models.category import Category
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.mapping_assignment_service import (
    MappingAssignment,
    materialize_mapping_assignment,
    remember_category,
)
from tahmeed.ui.dialog_theme import show_warning
from tahmeed.ui.dialogs.description_mapping_dialog import (
    ACTION_ASSIGN,
    ACTION_SKIP,
    ACTION_SKIP_ALL,
    DescriptionMappingDialog,
)


def _display_for_key(rows: list, key: str) -> str:
    for row in rows:
        if normalize_description(getattr(row, "description", "") or "") == key:
            return row.description
    return key


async def prompt_unmapped_descriptions(
    preview: Any,
    categories: List[Category],
    parent: QWidget,
    *,
    allow_skip: bool,
    apply_mapping: Callable,
    skip_one: Optional[Callable] = None,
    skip_all: Optional[Callable] = None,
    scope_label: str = "in this import",
    cancel_label: str = "Cancel Import",
) -> bool:
    """Run the mapping dialog for each unknown description.

    ``preview`` must have ``.unmapped`` (dict) and ``.rows`` (description attr).
    Returns False if the user cancels the import.
    """
    if not preview.unmapped:
        return True

    total_unmapped = len(preview.unmapped)
    cats = categories
    while preview.unmapped:
        key = next(iter(preview.unmapped))
        count = preview.unmapped[key]
        display = _display_for_key(preview.rows, key)
        remaining = len(preview.unmapped)
        dlg = DescriptionMappingDialog(
            display,
            count,
            cats,
            remaining,
            parent=parent,
            allow_skip=allow_skip,
            total=total_unmapped,
            scope_label=scope_label,
            cancel_label=cancel_label,
        )
        if dlg.exec() != DescriptionMappingDialog.Accepted:
            return False
        action = dlg.action()
        if action == ACTION_SKIP:
            if skip_one is not None:
                skip_one(preview, key)
            else:
                preview.unmapped.pop(key, None)
            continue
        if action == ACTION_SKIP_ALL:
            if skip_all is not None:
                skip_all(preview)
            else:
                preview.unmapped.clear()
            break
        if action != ACTION_ASSIGN:
            return False
        assignment = dlg.assignment()
        try:
            chosen = await materialize_mapping_assignment(assignment, cats)
        except Exception as exc:
            show_warning(
                parent,
                "Could Not Save Item",
                f"Could not create the item:\n\n{exc}",
            )
            continue
        if chosen is None or chosen._id is None:
            show_warning(
                parent,
                "Select Item",
                "Please choose or create an item for this description.",
            )
            continue
        remember_category(cats, chosen)
        await apply_mapping(preview, key, chosen._id, chosen.name)
    return True


def remember_created_from_assignment(
    categories: List[Category],
    assignment: MappingAssignment,
) -> None:
    """Let later dialogs in a sync prompt loop pick a just-named new item."""
    remember_category(categories, assignment.placeholder_category())
