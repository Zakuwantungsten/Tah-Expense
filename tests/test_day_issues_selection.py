"""Phase 7: search / VALUE filter / checked+highlighted map targets."""

from __future__ import annotations

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.day_issues_service import DayIssue
from tahmeed.ui.cashier.day_issues import filter_day_issues, map_target_issues


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
