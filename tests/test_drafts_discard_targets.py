"""Drafts discard: whole day vs selected-row subset."""

from __future__ import annotations

from bson import ObjectId

from tahmeed.ui.cashier.drafts_view import (
    discard_target_ids,
    is_ambiguous_single_row_discard,
    should_discard_whole_day,
)


def test_whole_day_when_no_row_selection() -> None:
    a, b, c = ObjectId(), ObjectId(), ObjectId()
    assert should_discard_whole_day(
        selected_row_ids=[],
        day_row_ids=[a, b, c],
    )
    assert discard_target_ids(
        selected_row_ids=[],
        day_row_ids=[a, b, c],
    ) == [a, b, c]


def test_whole_day_when_all_rows_selected() -> None:
    """Qt may auto-select every visible row; still treat as whole day."""
    a, b = ObjectId(), ObjectId()
    assert should_discard_whole_day(
        selected_row_ids=[a, b],
        day_row_ids=[a, b],
    )


def test_subset_row_selection_is_partial_discard() -> None:
    """Explicit multi-row subset stays a row discard."""
    a, b, c = ObjectId(), ObjectId(), ObjectId()
    assert not should_discard_whole_day(
        selected_row_ids=[a, b],
        day_row_ids=[a, b, c],
    )
    assert discard_target_ids(
        selected_row_ids=[a, b],
        day_row_ids=[a, b, c],
    ) == [a, b]


def test_single_of_many_is_ambiguous_not_auto_whole() -> None:
    """One selected of many is ambiguous (Qt auto-select); UI confirms."""
    a, b, c = ObjectId(), ObjectId(), ObjectId()
    assert not should_discard_whole_day(
        selected_row_ids=[a],
        day_row_ids=[a, b, c],
    )
    assert is_ambiguous_single_row_discard(
        selected_row_ids=[a],
        day_row_ids=[a, b, c],
    )
    assert discard_target_ids(
        selected_row_ids=[a],
        day_row_ids=[a, b, c],
    ) == [a]


def test_whole_day_when_day_selected_but_rows_not_loaded() -> None:
    """Left-pane day selected while detail rows still empty → discard by date."""
    assert should_discard_whole_day(
        selected_row_ids=[],
        day_row_ids=[],
        day_selected=True,
    )
    assert discard_target_ids(
        selected_row_ids=[],
        day_row_ids=[],
        day_selected=True,
    ) == []


def test_discard_empty_when_no_day_selected() -> None:
    assert not should_discard_whole_day(
        selected_row_ids=[],
        day_row_ids=[],
        day_selected=False,
    )
    assert discard_target_ids(
        selected_row_ids=[],
        day_row_ids=[],
        day_selected=False,
    ) == []
