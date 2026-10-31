"""Day Issues performance: dirty flag, skip redundant scans, all-days cache."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from tahmeed.models.user import User
from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    collect_day_issues_all_days,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.cashier.day_issues import DayIssuesWidget


def _app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _issue(desc: str, *, kind: str = "Unmapped") -> DayIssue:
    return DayIssue(
        kind=kind,
        description=desc,
        description_key=normalize_description(desc),
        count=1,
        detail="No payee + item mapping",
    )


def _tx(**kwargs):
    base = dict(
        date=datetime(2026, 3, 15),
        description="TRIANGLE",
        truck_number="T1",
        amount=10.0,
        item="",
        category_name="",
        payee="",
        verified=False,
        trashed=False,
        discarded=False,
        cashier_id=ObjectId(),
        _id=ObjectId(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_set_date_skips_refresh_when_clean() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []

    def capture() -> None:
        calls.append("refresh")

    widget._schedule_refresh = capture  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    day = date(2026, 3, 15)
    widget._target = day

    widget.set_date(day)
    assert calls == []

    widget.set_date(day, force=True)
    assert calls == ["refresh"]


def test_set_date_refreshes_when_dirty_or_date_changed() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = True
    day = date(2026, 3, 15)
    widget._target = day

    widget.set_date(day)
    assert calls == ["refresh"]

    calls.clear()
    widget._dirty = False
    widget._loaded = True
    widget.set_date(date(2026, 3, 16))
    assert calls == ["refresh"]
    assert widget._target == date(2026, 3, 16)


def test_open_for_date_forces_this_day_and_refreshes() -> None:
    """Table deep-link: SHOW=This day + table date, always reload."""
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)
    # Leave the page on All days — open_for_date must snap back to This day.
    widget._scope_combo.blockSignals(True)
    all_idx = widget._scope_combo.findData("all")
    assert all_idx >= 0
    widget._scope_combo.setCurrentIndex(all_idx)
    widget._scope_combo.blockSignals(False)
    widget._scope = "all"
    widget._date.setEnabled(False)

    day = date(2026, 3, 15)
    widget.open_for_date(day)

    assert widget._scope == "day"
    assert widget._scope_combo.currentData() == "day"
    assert widget._target == day
    assert widget._date.isEnabled()
    assert widget._date.date().toPython() == day
    assert calls == ["refresh"]


def test_on_page_shown_preserves_verify_handoff() -> None:
    """Sidebar tab return must not wipe Verify VALUE filter / All days."""
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    keys = (
        normalize_description("AGENCY FEE"),
        normalize_description("ALLOWANCE"),
    )
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=keys,
        all_days=True,
    )
    assert calls == ["refresh"]
    calls.clear()
    # Simulate successful scan after handoff.
    widget._loaded = True
    widget._dirty = False

    widget.on_page_shown(date(2026, 1, 1))

    assert widget._scope == "all"
    assert widget._value_filter == set(keys)
    assert widget._checked_keys == set(keys)
    assert widget._target == date(2026, 3, 15)
    assert calls == []  # clean + loaded → no re-scan


def test_on_page_shown_clean_return_does_not_scan() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    refresh_calls: list[str] = []
    widget._schedule_refresh = lambda: refresh_calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._lookups_loaded = True

    widget.on_page_shown(date(2026, 4, 2))

    assert refresh_calls == []
    assert widget._lookups_loaded is True
    assert widget._dirty is False


def test_on_page_shown_first_visit_opens_for_date() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = False
    widget._dirty = True

    widget.on_page_shown(date(2026, 4, 2))

    assert widget._scope == "day"
    assert widget._target == date(2026, 4, 2)
    assert calls == ["refresh"]


def test_on_page_shown_preserves_handoff_before_first_scan_finishes() -> None:
    """Leaving mid-handoff must not reset via open_for_date."""
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = False
    widget._dirty = False
    key = normalize_description("CHANNELS")
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=[key],
        all_days=True,
    )
    calls.clear()
    # Scan not finished yet.
    widget._loaded = False
    widget._dirty = True

    widget.on_page_shown(date.today())

    assert widget._scope == "all"
    assert widget._value_filter == {key}
    assert widget._target == date(2026, 3, 15)
    assert calls == ["refresh"]


def test_open_from_verify_filters_and_uses_all_days() -> None:
    """Verify handoff: VALUE-filter blocked keys + All days when multi-day."""
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)
    widget._scope = "day"

    keys = (
        normalize_description("AGENCY FEE"),
        normalize_description("ALLOWANCE"),
    )
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=keys,
        all_days=True,
    )

    assert widget._scope == "all"
    assert widget._scope_combo.currentData() == "all"
    assert widget._target == date(2026, 3, 15)
    assert widget._value_filter == set(keys)
    assert widget._checked_keys == set(keys)
    assert calls == ["refresh"]


def test_open_from_verify_limits_all_days_scan_to_handoff_days() -> None:
    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    widget._schedule_refresh = lambda: None  # type: ignore[method-assign]
    days = (date(2026, 3, 14), date(2026, 3, 15))

    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=[normalize_description("CHANNELS")],
        all_days=True,
        days=days,
    )
    assert widget._scan_days == days

    # Picking SHOW by hand goes back to every open day.
    widget._scope_combo.setCurrentIndex(widget._scope_combo.findData("day"))
    assert widget._scan_days is None


def test_all_days_local_rows_cover_every_local_register_day() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    target, open_day, draft_day = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)
    sources = {open_day: (["open"], None), draft_day: (None, ["draft"])}
    widget = DayIssuesWidget(
        user,
        row_source=lambda d: sources.get(d, (None, None)),
        local_days=lambda: [target, open_day, draft_day],
    )
    widget._target = target

    rows = widget._all_days_local_rows(None, ["picked"])

    assert rows == {
        target: (None, ["picked"]),
        open_day: (["open"], None),
        draft_day: (None, ["draft"]),
    }

    widget._scan_days = (target, draft_day)
    assert set(widget._all_days_local_rows(None, None)) == {target, draft_day}


def test_scan_progress_shows_days_on_overlay() -> None:
    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)

    widget._show_scan_progress(2, 5)

    panel = widget._loading_overlay._panel
    assert "2 of 5 days" in panel.base_message()
    widget._loading_overlay.hide_loading()


def test_open_from_verify_single_day_keeps_this_day() -> None:
    from tahmeed.services.description_mapping_service import normalize_description

    _app()
    user = User(username="a1", password_hash="x", role="accountant", full_name="A1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._target = date(2026, 3, 1)

    key = normalize_description("CHANNELS")
    widget.open_from_verify(
        date(2026, 3, 15),
        description_keys=[key],
        all_days=False,
    )

    assert widget._scope == "day"
    assert widget._value_filter == {key}
    assert widget._checked_keys == {key}
    assert widget._target == date(2026, 3, 15)
    assert calls == ["refresh"]


def test_on_register_source_changed_marks_dirty_without_scan_when_hidden() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    calls: list[str] = []
    widget._schedule_refresh = lambda: calls.append("refresh")  # type: ignore[method-assign]
    widget._loaded = True
    widget._dirty = False
    widget._day_snap_cache[date(2026, 3, 15)] = DayIssuesSnapshot(
        target_date=date(2026, 3, 15), issues=[]
    )
    widget._target = date(2026, 3, 15)

    widget.on_register_source_changed(visible=False)
    assert widget._dirty is True
    assert date(2026, 3, 15) not in widget._day_snap_cache
    assert calls == []
    assert not widget._source_refresh_timer.isActive()


def test_drop_issue_keys_updates_list_and_badge_signal() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    a = _issue("LATRA FEE")
    b = _issue("DIESEL")
    widget._issues_all = [a, b]
    widget._issue_dates = {
        a.description_key: [date(2026, 3, 15)],
        b.description_key: [date(2026, 3, 15)],
    }
    counts: list[int] = []
    widget.issues_changed.connect(counts.append)

    widget._drop_issue_keys({a.description_key})
    assert [i.description for i in widget._issues_all] == ["DIESEL"]
    assert counts == [1]


@pytest.mark.asyncio
async def test_finish_mapping_ui_drops_row_without_rescan() -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    issue = _issue("DIESEL 30 LTRS")
    widget._loaded = True
    widget._dirty = False
    widget._issues_all = [issue]
    widget._issue_dates = {issue.description_key: [date(2026, 3, 15)]}
    widget._day_snap_cache[date(2026, 3, 15)] = DayIssuesSnapshot(
        target_date=date(2026, 3, 15),
        issues=[issue],
        issue_dates={issue.description_key: [date(2026, 3, 15)]},
    )
    calls: list[str] = []

    async def refresh(*, quiet: bool = False) -> None:
        calls.append("refresh")

    widget.refresh = refresh  # type: ignore[method-assign]
    await widget._finish_mapping_ui(["DIESEL 30 LTRS"])

    assert calls == []
    assert widget._issues_all == []
    assert widget._dirty is False
    cached = widget._day_snap_cache[date(2026, 3, 15)]
    assert cached.issues == []


def test_hide_applying_issues_keeps_pending_maps_off_the_list(monkeypatch) -> None:
    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    pending = _issue("DIESEL 30 LTRS")
    ready = _issue("LATRA FEE")
    snap = DayIssuesSnapshot(
        target_date=date(2026, 3, 15),
        issues=[pending, ready],
        issue_dates={
            pending.description_key: [date(2026, 3, 15)],
            ready.description_key: [date(2026, 3, 15)],
        },
    )
    monkeypatch.setattr(
        "tahmeed.services.mapping_apply_outbox.applying_description_keys",
        lambda: {pending.description_key},
    )

    widget._hide_applying_issues(snap)

    assert [issue.description for issue in snap.issues] == ["LATRA FEE"]
    assert pending.description_key not in snap.issue_dates


@pytest.mark.asyncio
async def test_reload_catalogs_replaces_lists_without_touching_scan(monkeypatch) -> None:
    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._categories = [Category(name="Old Item")]
    widget._payees = ["OLD PAYEE"]
    widget._lookups_loaded = True
    widget._dirty = False

    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.get_payment_target_categories",
        AsyncMock(return_value=[Category(name="Custom Bonds")]),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.get_payee_names",
        AsyncMock(return_value=["NEW PAYEE"]),
    )

    await widget._reload_catalogs()

    assert widget._dirty is False
    assert [c.name for c in widget._categories] == ["Custom Bonds"]
    assert widget._payees == ["NEW PAYEE"]
    assert widget._lookups_loaded is True


@pytest.mark.asyncio
async def test_do_map_opens_dialog_with_reloaded_catalogs(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    seen: dict = {}

    async def reload() -> None:
        widget._categories = [Category(name="Custom Bonds")]
        widget._payees = ["NEW PAYEE"]

    widget._reload_catalogs = reload  # type: ignore[method-assign]

    class FakeDlg:
        def __init__(self, descriptions, categories, payees, parent=None, **kwargs):
            seen["descriptions"] = list(descriptions)
            seen["cats"] = [c.name for c in categories]
            seen["payees"] = list(payees)

        def exec(self) -> int:
            return QDialog.Rejected

    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.MapDescriptionsDialog",
        FakeDlg,
    )
    await widget._do_map(["LATRA FEE"], [_issue("LATRA FEE")])

    assert seen["descriptions"] == ["LATRA FEE"]
    assert seen["cats"] == ["Custom Bonds"]
    assert seen["payees"] == ["NEW PAYEE"]


@pytest.mark.asyncio
async def test_do_map_skips_catalog_reload_when_lists_are_warm(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._lookups_loaded = True
    widget._categories = [Category(name="Parking")]
    widget._payees = ["CITY COUNCIL"]
    calls: list[str] = []

    async def reload() -> None:
        calls.append("reload")

    widget._reload_catalogs = reload  # type: ignore[method-assign]

    class FakeDlg:
        def __init__(self, descriptions, categories, payees, parent=None, **kwargs):
            calls.append("dialog")

        def exec(self) -> int:
            return QDialog.Rejected

    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.MapDescriptionsDialog",
        FakeDlg,
    )
    await widget._do_map(["LATRA FEE"], [_issue("LATRA FEE")])

    assert calls == ["dialog"]


@pytest.mark.real_await_ui
def test_await_ui_runs_dialog_on_a_qt_tick_not_inside_the_task() -> None:
    import asyncio

    from PySide6.QtTest import QTest

    app = _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    ran: list[str] = []

    def dialog() -> str:
        ran.append("dialog")
        return "accepted"

    async def exercise() -> str:
        task = asyncio.create_task(widget._await_ui(dialog))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Not opened inside the task step; it waits for the Qt tick.
        assert ran == [] and not task.done()
        for _ in range(40):
            app.processEvents()
            if task.done():
                break
            await asyncio.sleep(0)
            QTest.qWait(10)
        return await task

    assert asyncio.run(exercise()) == "accepted"
    assert ran == ["dialog"]


@pytest.mark.asyncio
async def test_do_map_ignores_second_map_while_one_is_open(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._lookups_loaded = True
    widget._categories = [Category(name="Parking")]
    widget._payees = ["CITY COUNCIL"]
    seen: list[bool] = []

    class FakeDlg:
        def __init__(self, *_a, **_k):
            seen.append(widget._map_in_flight)

        def exec(self) -> int:
            return QDialog.Rejected

    monkeypatch.setattr("tahmeed.ui.cashier.day_issues.MapDescriptionsDialog", FakeDlg)

    widget._map_in_flight = True
    await widget._do_map(["LATRA FEE"], [_issue("LATRA FEE")])
    assert seen == []

    widget._map_in_flight = False
    await widget._do_map(["LATRA FEE"], [_issue("LATRA FEE")])
    assert seen == [True]
    assert widget._map_in_flight is False


@pytest.mark.asyncio
async def test_do_map_shows_saving_before_creating_item(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QMessageBox

    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._lookups_loaded = True
    widget._categories = [Category(name="Parking")]
    widget._payees = ["CITY COUNCIL"]
    events: list[str] = []

    class FakeDlg:
        result_assignment = object()
        result_payee = "NEW PAYEE"
        result_create_payee = True
        result_currency = "TZS"

        def __init__(self, *_a, **_k):
            pass

        def exec(self) -> int:
            return QDialog.Accepted

    async def materialize(*_a, **_k):
        events.append("materialize")
        raise RuntimeError("server down")

    monkeypatch.setattr("tahmeed.ui.cashier.day_issues.MapDescriptionsDialog", FakeDlg)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.materialize_mapping_assignment", materialize
    )
    monkeypatch.setattr(
        widget._loading_overlay, "show_loading",
        lambda text, **_k: events.append(f"show:{text}"),
    )
    monkeypatch.setattr(
        widget._loading_overlay, "hide_loading", lambda: events.append("hide")
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _p, title, _m: events.append(f"warn:{title}")),
    )

    await widget._do_map(["LATRA FEE"], [_issue("LATRA FEE")])

    assert events == [
        "show:Saving mappings…",
        "materialize",
        "hide",
        "warn:Could Not Save Item",
    ]


@pytest.mark.asyncio
async def test_manual_refresh_redownloads_item_and_payee_lists(monkeypatch) -> None:
    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._categories = [Category(name="Old Item")]
    widget._payees = ["OLD PAYEE"]
    widget._lookups_loaded = True
    calls: list[str] = []

    async def fetch(*, refresh: bool = False):
        calls.append(f"fetch:{refresh}")
        return {}

    monkeypatch.setattr("tahmeed.services.catalog_service.fetch_catalog_snapshot", fetch)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.invalidate_payee_cache",
        lambda: calls.append("invalidate"),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.get_payment_target_categories",
        AsyncMock(return_value=[Category(name="New Item")]),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.get_payee_names",
        AsyncMock(return_value=["NEW PAYEE"]),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.collect_day_issues",
        AsyncMock(return_value=DayIssuesSnapshot(target_date=widget._target)),
    )
    widget._schedule_refresh = lambda: None  # type: ignore[method-assign]

    widget.force_refresh()
    await widget.refresh(quiet=True)

    assert calls == ["fetch:True", "invalidate"]
    assert [c.name for c in widget._categories] == ["New Item"]
    assert widget._payees == ["NEW PAYEE"]

    # A later soft refresh keeps using the cached lists.
    calls.clear()
    widget._lookups_loaded = False
    await widget.refresh(quiet=True)
    assert calls == []


@pytest.mark.asyncio
async def test_all_days_reuses_day_cache(monkeypatch) -> None:
    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 2)
    me = ObjectId()
    collect_calls: list[date] = []

    async def fake_summaries(*_a, **_k):
        return [{"date": d1}, {"date": d2}]

    async def fake_by_date(target, merged=True, cashier_id=None, **_k):
        collect_calls.append(target)
        return [_tx(cashier_id=me, description=f"FEE {target}", item="", payee="")]

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.get_open_day_summaries",
        fake_summaries,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_transactions_by_date",
        fake_by_date,
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_mappings_for_descriptions",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "tahmeed.services.day_issues_service.get_payee_names",
        AsyncMock(return_value=[]),
    )

    cache: dict = {}
    snap1 = await collect_day_issues_all_days(
        cashier_id=me, anchor_date=d1, day_cache=cache
    )
    assert snap1.total == 2
    assert set(collect_calls) == {d1, d2}

    collect_calls.clear()
    snap2 = await collect_day_issues_all_days(
        cashier_id=me, anchor_date=d1, day_cache=cache
    )
    assert snap2.total == 2
    assert collect_calls == []  # both days served from cache

    # Live target always re-scans that day (in-memory; no by-date call).
    live_snap = await collect_day_issues_all_days(
        cashier_id=me,
        anchor_date=d1,
        live_target=d1,
        live_transactions=[_tx(cashier_id=me, description="LIVE ONLY", item="", payee="")],
        day_cache=cache,
    )
    assert collect_calls == []  # live path replaces DB load
    assert any(i.description == "LIVE ONLY" for i in live_snap.issues)
    assert d1 in cache
    assert any(i.description == "LIVE ONLY" for i in cache[d1].issues)


@pytest.mark.asyncio
async def test_bulk_map_creates_names_then_saves_each_row(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from tahmeed.models.category import Category

    _app()
    user = User(username="c1", password_hash="x", role="cashier", full_name="C1")
    widget = DayIssuesWidget(user)
    widget._lookups_loaded = True
    parking = Category(name="Parking", _id=ObjectId())
    widget._categories = [parking]
    widget._payees = ["CITY COUNCIL"]
    calls: dict = {"payees": [], "items": [], "maps": [], "stamped": [], "applied": []}

    class FakeDlg:
        result_rows = [
            ("JOHN DOE", "JOHN DOE", "GET VALVE"),
            ("LATRA FEE", "CITY COUNCIL", "Parking"),
        ]
        result_new_payees = {"JOHN DOE": "USD"}
        result_new_items = {"GET VALVE": {"name": "GET VALVE", "requires_truck": False}}

        def __init__(self, *_a, **_k):
            pass

        def exec(self) -> int:
            return QDialog.Accepted

    async def add_payee(name, currency="TZS"):
        calls["payees"].append((name, currency))

    async def materialize(assignment, categories):
        calls["items"].append((assignment.new_item_name, assignment.new_item_fields))
        return Category(name=assignment.new_item_name, _id=ObjectId())

    async def save_bulk(entries):
        calls["maps"].extend((d, n, p) for d, _cid, n, p in entries)
        return []

    async def no_prior(_descs):
        return {}

    async def finish(_descs):
        return None

    monkeypatch.setattr("tahmeed.ui.cashier.day_issues.MapDescriptionsDialog", FakeDlg)
    monkeypatch.setattr("tahmeed.ui.cashier.day_issues.add_payee", add_payee)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.day_issues.materialize_mapping_assignment", materialize
    )
    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.save_mappings_bulk", save_bulk
    )
    widget._snapshot_prior_maps = no_prior  # type: ignore[method-assign]
    widget._finish_mapping_ui = finish  # type: ignore[method-assign]
    widget._stamp_live_rows = (  # type: ignore[method-assign]
        lambda descs, payee, item, _cid: calls["stamped"].append((descs[0], payee, item)) or 1
    )
    widget._enqueue_saved_row_apply = (  # type: ignore[method-assign]
        lambda descs, payee, item, _cid: calls["applied"].append((tuple(descs), payee, item))
    )
    messages: list[str] = []
    widget._confirm_mapping_undo = (  # type: ignore[method-assign]
        lambda msg: messages.append(msg) or False
    )

    await widget._do_map(["JOHN DOE", "LATRA FEE"], [_issue("JOHN DOE"), _issue("LATRA FEE")])

    assert calls["payees"] == [("JOHN DOE", "USD")]
    assert calls["items"] == [("GET VALVE", {"name": "GET VALVE", "requires_truck": False})]
    assert calls["maps"] == [
        ("JOHN DOE", "GET VALVE", "JOHN DOE"),
        ("LATRA FEE", "Parking", "CITY COUNCIL"),
    ]
    assert calls["stamped"] == [
        ("JOHN DOE", "JOHN DOE", "GET VALVE"),
        ("LATRA FEE", "CITY COUNCIL", "Parking"),
    ]
    assert len(calls["applied"]) == 2
    assert "JOHN DOE" in widget._payees
    assert widget._last_mapping_undo is not None
    assert "Created 1 new Payee(s): JOHN DOE" in messages[-1]
    assert "Mapped 2 description(s)" in messages[-1]
