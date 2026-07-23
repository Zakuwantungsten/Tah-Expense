"""Tests for QuickBooks-style ranked contains completion."""

from PySide6.QtWidgets import QApplication, QLineEdit

from tahmeed.ui.widgets.completer_line_edit import (
    CompleterLineEdit,
    rank_completion_matches,
    show_completion_preview,
)


def test_rank_completion_matches_orders_exact_prefix_word_mid() -> None:
    values = [
        "Diesel CSH",
        "LATRA",
        "CSH FEE",
        "MILEAGE",
        "csh",
    ]
    ranked = rank_completion_matches("csh", values)
    assert ranked[0] == "csh"  # exact
    assert ranked[1] == "CSH FEE"  # prefix
    assert "Diesel CSH" in ranked  # word-boundary / mid
    assert ranked.index("CSH FEE") < ranked.index("Diesel CSH")
    assert "LATRA" not in ranked
    assert "MILEAGE" not in ranked


def test_rank_completion_matches_prefix_beats_mid() -> None:
    values = ["Diesel CSH", "LATRA", "LUBE"]
    ranked = rank_completion_matches("l", values)
    assert ranked[0] == "LATRA"
    assert ranked[1] == "LUBE"
    assert ranked[-1] == "Diesel CSH"


def test_rank_completion_matches_empty_query_keeps_order() -> None:
    values = ["B", "A"]
    assert rank_completion_matches("", values) == ["B", "A"]
    assert rank_completion_matches("   ", values) == ["B", "A"]


def test_show_completion_preview_prefix_only_by_default() -> None:
    _app = QApplication.instance() or QApplication([])
    edit = QLineEdit()
    assert show_completion_preview(edit, "Die", "Diesel CSH") is True
    assert edit.text() == "Diesel CSH"
    assert edit.selectedText() == "sel CSH"

    edit.clear()
    assert show_completion_preview(edit, "csh", "Diesel CSH") is False
    assert edit.text() == ""

    assert show_completion_preview(
        edit, "csh", "Diesel CSH", replace_on_contains=True
    ) is True
    assert edit.text() == "Diesel CSH"
    assert edit.selectedText() == "Diesel CSH"
    assert _app is not None


def test_ranked_contains_reorders_model_on_type() -> None:
    _app = QApplication.instance() or QApplication([])
    values = ["Diesel CSH", "LATRA", "LUBE"]
    ed = CompleterLineEdit(values, ranked_contains=True)
    ed._on_text_edited("l")
    model_values = [ed._model.data(ed._model.index(i)) for i in range(ed._model.rowCount())]
    assert model_values[0] == "LATRA"
    assert model_values[1] == "LUBE"
    assert "Diesel CSH" in model_values
    # Mid-only query: match still present, but no auto-preview rewrite.
    ed.setText("")
    ed._on_text_edited("csh")
    model_values = [ed._model.data(ed._model.index(i)) for i in range(ed._model.rowCount())]
    assert model_values[0] == "Diesel CSH"
    assert _app is not None


def test_ranked_contains_does_not_autofill_on_highlight() -> None:
    _app = QApplication.instance() or QApplication([])
    ed = CompleterLineEdit(["Diesel CSH", "LATRA"], ranked_contains=True)
    ed.setText("csh")
    ed._typed = "csh"
    ed._on_highlighted("Diesel CSH")
    assert ed.text() == "csh"
    ed._apply_first_suggestion_preview()
    assert ed.text() == "csh"
    assert _app is not None


def test_ranked_contains_uses_setwidget_not_setcompleter() -> None:
    """Item mode must not wire QLineEdit.setCompleter (that auto-inserts)."""
    _app = QApplication.instance() or QApplication([])
    ed = CompleterLineEdit(["LATRA"], ranked_contains=True)
    assert ed.completer() is None
    assert ed._completer.widget() is ed
    assert _app is not None
