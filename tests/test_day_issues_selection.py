"""Phase 7 / density Phase 5: search / VALUE filter / checked+highlighted map targets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.day_issues_service import DayIssue
from tahmeed.models.user import User
from tahmeed.ui.cashier.day_issues import (
    DayIssuesWidget,
    ValueFilterDialog,
    description_displays_for_value_filter,
    filter_day_issues,
    map_target_issues,
    resolve_value_filter_selection,
    value_filter_from_descriptions,
)
from tahmeed.ui.widgets.excel_column_filter import ExcelFilterHeaderView


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _issue(desc: str, kind: str = "Unmapped", count: int = 1) -> DayIssue:
    return DayIssue(
        kind=kind,
        description=desc,
        description_key=normalize_description(desc),
        count=count,
        detail="No payee + item mapping",
    )


def test_filter_day_issues_search_and_value() -> None:
    issues = [
        _issue("LATRA FEE"),
        _issue("DIESEL", kind="Bad map"),
        _issue("TOLLS"),
    ]
    assert [i.description for i in filter_day_issues(issues, search="lat")] == [
        "LATRA FEE"
    ]
    keys = {normalize_description("DIESEL"), normalize_description("TOLLS")}
    filtered = filter_day_issues(issues, value_filter=keys)
    assert {i.description for i in filtered} == {"DIESEL", "TOLLS"}
    both = filter_day_issues(issues, search="d", value_filter=keys)
    assert [i.description for i in both] == ["DIESEL"]


def test_resolve_value_filter_selection_show_all() -> None:
    all_keys = {"a", "b", "c"}
    assert resolve_value_filter_selection(set(), all_keys) is None
    assert resolve_value_filter_selection({"a", "b", "c"}, all_keys) is None
    assert resolve_value_filter_selection({"a", "c"}, all_keys) == {"a", "c"}


def test_value_filter_dialog_search_preserves_checks() -> None:
    _app()
    issues = [
        _issue("LATRA FEE"),
        _issue("DIESEL", kind="Bad map"),
        _issue("TOLLS"),
    ]
    dlg = ValueFilterDialog(issues, selected_keys=None)
    assert dlg._list.count() == 3
    assert all(
        dlg._list.item(i).checkState() == Qt.Checked
        for i in range(dlg._list.count())
    )

    # Uncheck DIESEL, then search so it is hidden — check must stick.
    diesel_key = normalize_description("DIESEL")
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.data(Qt.UserRole) == diesel_key:
            item.setCheckState(Qt.Unchecked)
            break
    dlg._refilter("lat")
    assert dlg._list.count() == 1
    assert diesel_key not in dlg._checked_keys

    dlg._refilter("")
    assert dlg._list.count() == 3
    diesel_item = next(
        dlg._list.item(i)
        for i in range(dlg._list.count())
        if dlg._list.item(i).data(Qt.UserRole) == diesel_key
    )
    assert diesel_item.checkState() == Qt.Unchecked

    dlg._accept()
    resolved = resolve_value_filter_selection(
        dlg.result_keys or set(),
        {i.description_key for i in issues},
    )
    assert resolved == {
        normalize_description("LATRA FEE"),
        normalize_description("TOLLS"),
    }


def test_value_filter_from_descriptions_and_displays() -> None:
    issues = [
        _issue("LATRA FEE"),
        _issue("DIESEL", kind="Bad map"),
        _issue("TOLLS"),
    ]
    assert value_filter_from_descriptions(
        {"LATRA FEE", "DIESEL", "TOLLS"}, issues,
    ) is None
    keys = value_filter_from_descriptions({"DIESEL", "TOLLS"}, issues)
    assert keys == {
        normalize_description("DIESEL"),
        normalize_description("TOLLS"),
    }
    assert description_displays_for_value_filter(None, issues) == set()
    assert description_displays_for_value_filter(keys, issues) == {
        "DIESEL", "TOLLS",
    }


def test_desc_header_filter_syncs_with_value_filter() -> None:
    _app()
    user = User(
        username="cashier1",
        password_hash="x",
        role="cashier",
        full_name="C1",
    )
    widget = DayIssuesWidget(user)
    assert isinstance(widget._table.horizontalHeader(), ExcelFilterHeaderView)

    widget._issues_all = [
        _issue("LATRA FEE"),
        _issue("DIESEL", kind="Bad map"),
        _issue("TOLLS"),
    ]
    widget._on_desc_header_filter(
        3,  # _COL_DESC
        {"DIESEL", "TOLLS"},
    )
    assert widget._value_filter == {
        normalize_description("DIESEL"),
        normalize_description("TOLLS"),
    }
    assert not widget._value_chip.isHidden()
    assert "VALUE" in widget._value_chip.text()
    hdr = widget._table.horizontalHeader()
    assert hdr.active_filters().get(3) == {"DIESEL", "TOLLS"}

    # Toolbar Clear clears ▾ state too.
    widget._clear_value_filter()
    assert widget._value_filter is None
    assert not hdr.active_filters()

    # VALUE dialog path keeps ▾ in sync.
    widget._value_filter = {normalize_description("LATRA FEE")}
    widget._sync_value_filter_chrome()
    assert hdr.active_filters().get(3) == {"LATRA FEE"}


def test_map_target_issues_unions_checked_and_selected() -> None:
    a = _issue("A")
    b = _issue("B")
    c = _issue("C")
    visible = [a, b, c]
    targets = map_target_issues(
        visible,
        checked_keys={a.description_key},
        selected_keys={b.description_key, a.description_key},
    )
    assert {t.description for t in targets} == {"A", "B"}
    assert map_target_issues(visible, checked_keys=set(), selected_keys=set()) == []
