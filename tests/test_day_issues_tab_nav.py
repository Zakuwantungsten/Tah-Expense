"""Day Issues: Payee / Item boxes open on Tab / Shift+Tab / Enter without a click."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tahmeed.models.user import User
from tahmeed.services.day_issues_service import DayIssue
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.cashier.day_issues import (
    DayIssuesWidget,
    _COL_ITEM,
    _COL_PAYEE,
    inline_edit_target,
)
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _issue(desc: str) -> DayIssue:
    return DayIssue(
        kind="Unmapped",
        description=desc,
        description_key=normalize_description(desc),
        count=1,
        detail="No payee + item mapping",
    )


def _widget() -> DayIssuesWidget:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    w = DayIssuesWidget(user)
    w._payees = ["CITY COUNCIL", "SHELL"]
    w._categories = [SimpleNamespace(name="Parking"), SimpleNamespace(name="Fuel")]
    w._issues_all = [_issue("PARKING TAZARA"), _issue("DIESEL ISOKA")]
    w._apply_filters()
    w.resize(1100, 500)
    w.show()
    QTest.qWaitForWindowExposed(w)
    return w


def _editor_at(w: DayIssuesWidget, row: int, col: int):
    ed = w._table.indexWidget(w._table.model().index(row, col))
    return ed if isinstance(ed, CompleterLineEdit) else None


def _press(editor, key, modifiers=Qt.NoModifier) -> None:
    QApplication.sendEvent(editor, QKeyEvent(QEvent.KeyPress, key, modifiers))
    for _ in range(5):
        QApplication.processEvents()


def test_click_opens_box_and_tab_walks_payee_item_rows() -> None:
    w = _widget()
    try:
        w._on_issue_cell_clicked(w._table.model().index(0, _COL_PAYEE))
        ed = _editor_at(w, 0, _COL_PAYEE)
        assert ed is not None

        QTest.keyClicks(ed, "city")
        QApplication.processEvents()
        _press(ed, Qt.Key_Tab)
        assert w._table.item(0, _COL_PAYEE).text() == "CITY COUNCIL"
        ed = _editor_at(w, 0, _COL_ITEM)
        assert ed is not None

        QTest.keyClicks(ed, "park")
        QApplication.processEvents()
        _press(ed, Qt.Key_Tab)
        assert w._table.item(0, _COL_ITEM).text() == "Parking"
        assert _editor_at(w, 1, _COL_PAYEE) is not None
        assert w._save_filled_btn.isEnabled()
    finally:
        w.close()


def test_shift_tab_goes_back_and_enter_goes_down() -> None:
    w = _widget()
    try:
        w._open_inline_edit(1, _COL_PAYEE)
        ed = _editor_at(w, 1, _COL_PAYEE)
        assert ed is not None

        _press(ed, Qt.Key_Backtab, Qt.ShiftModifier)
        ed = _editor_at(w, 0, _COL_ITEM)
        assert ed is not None

        _press(ed, Qt.Key_Return)
        assert _editor_at(w, 1, _COL_ITEM) is not None
    finally:
        w.close()


def test_inline_edit_target_skips_non_box_columns() -> None:
    assert inline_edit_target(0, 3, "next") == (0, _COL_PAYEE)
    assert inline_edit_target(0, _COL_PAYEE, "next") == (0, _COL_ITEM)
    assert inline_edit_target(0, _COL_ITEM, "next") == (1, _COL_PAYEE)
    assert inline_edit_target(0, 7, "next") == (1, _COL_PAYEE)
    assert inline_edit_target(1, _COL_PAYEE, "prev") == (0, _COL_ITEM)
    assert inline_edit_target(1, 7, "prev") == (1, _COL_ITEM)
    assert inline_edit_target(1, 2, "prev") == (0, _COL_ITEM)


def test_tab_on_table_without_box_jumps_to_next_payee() -> None:
    w = _widget()
    try:
        w._table.setFocus()
        w._table.setCurrentIndex(w._table.model().index(0, _COL_ITEM))
        QApplication.processEvents()
        _press(w._table, Qt.Key_Tab)
        assert _editor_at(w, 1, _COL_PAYEE) is not None

        _press(_editor_at(w, 1, _COL_PAYEE), Qt.Key_Escape)
        assert _editor_at(w, 1, _COL_PAYEE) is None
        w._table.setCurrentIndex(w._table.model().index(1, 3))
        QApplication.processEvents()
        _press(w._table, Qt.Key_Tab)
        assert _editor_at(w, 1, _COL_PAYEE) is not None
    finally:
        w.close()


def test_tab_waits_for_not_in_list_prompt() -> None:
    w = _widget()
    try:
        w._not_in_list_prompting.add((0, _COL_PAYEE, "NEW GUY"))
        w._advance_inline_edit(0, _COL_PAYEE, "next")
        assert _editor_at(w, 0, _COL_ITEM) is None
        assert w._pending_inline_edit == (0, _COL_ITEM)

        w._not_in_list_prompting.clear()
        w._flush_pending_inline_edit()
        QApplication.processEvents()
        assert _editor_at(w, 0, _COL_ITEM) is not None
    finally:
        w.close()
