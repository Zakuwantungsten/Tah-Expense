"""Browse Simple / Uploads should open the Daily Register table, not Advanced."""

from datetime import date

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from tahmeed.ui.cashier.transactions_table import (
    TransactionBrowser,
    _MODE_SIMPLE,
    _MODE_SMART,
    _MODE_UPLOADS,
    _M_COL_PAYEE,
    _U_COL_CASHIER,
    _U_COL_SN,
    _U_COL_TOTAL,
    _as_py_date,
    _format_upload_total,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _browser() -> TransactionBrowser:
    _app()
    browser = TransactionBrowser()
    browser._do_find = lambda: None
    return browser


def test_as_py_date_datetime_and_date():
    from datetime import datetime

    assert _as_py_date(date(2026, 7, 21)) == date(2026, 7, 21)
    assert _as_py_date(datetime(2026, 7, 21, 15, 30)) == date(2026, 7, 21)
    assert _as_py_date(None) is None


def _status(**counts) -> str:
    return TransactionBrowser._simple_status_text(
        {"cashier_ids": ["c1"], **counts}, {"c1": "Aisha"}
    )


def test_simple_status_parked_rows_are_not_submitted():
    assert (
        _status(entries_count=8, draft_count=5, parked_count=3, submitted_count=0)
        == "Aisha · 5 draft, 3 parked"
    )


def test_simple_status_all_parked():
    assert (
        _status(entries_count=3, draft_count=0, parked_count=3, submitted_count=0)
        == "Aisha · All parked"
    )


def test_simple_status_mixed_with_legacy_rows():
    # 2 legacy rows have no register_status and count as submitted.
    assert (
        _status(entries_count=7, draft_count=2, parked_count=1, submitted_count=2)
        == "Aisha · 4 submitted, 2 draft, 1 parked"
    )


def test_simple_status_existing_labels_unchanged():
    assert _status(entries_count=4, draft_count=0, submitted_count=4) == "Aisha · All submitted"
    assert _status(entries_count=4, draft_count=4, submitted_count=0) == "Aisha · All draft"
    assert (
        _status(entries_count=5, draft_count=2, submitted_count=3)
        == "Aisha · 3 submitted, 2 draft"
    )


def test_open_upload_jumps_to_register_not_advanced():
    browser = _browser()
    jumped = []
    uploads = []
    browser.go_to_date.connect(lambda d, t="": jumped.append((d, t)))
    browser.go_to_upload.connect(lambda uid, d=None: uploads.append((uid, d)))
    browser._current_mode = _MODE_UPLOADS
    browser._results_uploads = [
        {
            "_id": "batch-1",
            "primary_date": date(2026, 7, 21),
            "min_date": date(2026, 7, 1),
            "max_date": date(2026, 7, 21),
        }
    ]
    browser._open_upload_at(0)
    assert jumped == []
    assert uploads == [("batch-1", date(2026, 7, 21))]
    assert browser._current_mode == _MODE_UPLOADS


def test_open_simple_day_jumps_to_register_not_advanced():
    from unittest.mock import patch

    browser = _browser()
    jumped = []
    browser.go_to_date.connect(lambda d, t="": jumped.append((d, t)))
    browser._current_mode = _MODE_SIMPLE
    browser._results_simple = [{"date": date(2026, 5, 4), "entries_count": 12}]
    with patch.object(browser._simple_table, "currentRow", return_value=0):
        browser._on_go_to()
    assert jumped == [(date(2026, 5, 4), "")]
    assert browser._current_mode == _MODE_SIMPLE


def test_cashier_delete_only_own_uploads():
    from types import SimpleNamespace

    me = SimpleNamespace(_id="cashier-me")
    browser = TransactionBrowser(user=me, can_manage_uploads=False)
    browser._do_find = lambda: None
    assert browser._can_delete_upload({"cashier_id": "cashier-me"}) is True
    assert browser._can_delete_upload({"cashier_id": "cashier-other"}) is False
    manager = TransactionBrowser(user=me, can_manage_uploads=True)
    manager._do_find = lambda: None
    assert manager._can_delete_upload({"cashier_id": "cashier-other"}) is True


def test_format_upload_total_tzs_usd_or_both():
    assert _format_upload_total({"total_tzs": -1900, "total_usd": 0}) == "TZS -1,900"
    assert _format_upload_total({"total_tzs": 0, "total_usd": -25.5}) == "USD -25.50"
    assert _format_upload_total(
        {"total_tzs": -1000, "total_usd": -10}
    ) == "TZS -1,000 · USD -10.00"
    assert _format_upload_total({}) == "—"


def test_header_bar_removed_and_smart_search_mode_present():
    from PySide6.QtWidgets import QLabel

    browser = _browser()
    texts = [lbl.text() for lbl in browser.findChildren(QLabel)]
    assert "Transaction Browser" not in texts
    assert [b.text() for b in browser._mode_btns] == [
        "Simple", "Advanced", "Smart Search", "Uploads",
    ]
    assert _MODE_SMART == 2 and _MODE_UPLOADS == 3


def _smart_tx(**overrides):
    from datetime import datetime

    from tahmeed.models.transaction import Transaction

    base = dict(
        date=datetime(2026, 3, 1),
        description="Diesel top-up",
        truck_number="T103 DVL",
        amount=5000,
        item="Fuel",
        payee="Puma Energy",
    )
    base.update(overrides)
    return Transaction(**base)


def _smart_browser() -> TransactionBrowser:
    _app()
    browser = TransactionBrowser()
    browser._current_mode = _MODE_SMART
    browser._filter_stack.setCurrentIndex(_MODE_SMART)
    browser._table_stack.setCurrentIndex(_MODE_SMART)
    return browser


def test_smart_find_without_filters_shows_hint_and_skips_api():
    from unittest.mock import AsyncMock, patch

    browser = _smart_browser()
    with patch(
        "tahmeed.ui.cashier.transactions_table.smart_search_page",
        new=AsyncMock(),
    ) as api:
        browser._do_find()
    api.assert_not_called()
    assert browser._smart_layout.currentWidget() is browser._smart_hint
    assert browser._count_label.text() == "Matches: —"


def test_smart_dates_default_to_any():
    browser = _smart_browser()
    assert browser._m_from.text() == "Any"
    params = browser._smart_form_params()
    assert params["date_from"] is None and params["date_to"] is None
    browser._m_from.setDate(QDate(2026, 3, 1))
    assert browser._smart_form_params()["date_from"] == date(2026, 3, 1)


def test_smart_clear_dates_resets_both_to_any():
    browser = _smart_browser()
    browser._m_from.setDate(QDate(2026, 3, 1))
    browser._m_to.setDate(QDate(2026, 3, 31))
    browser._m_payee.setText("puma")
    browser._m_clear_dates.click()
    params = browser._smart_form_params()
    assert params["date_from"] is None and params["date_to"] is None
    assert browser._m_from.text() == "Any" and browser._m_to.text() == "Any"
    assert params["payee"] == "puma"


def test_smart_item_and_payee_suggestions_load_from_catalog():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    browser = _smart_browser()
    cats = [SimpleNamespace(name="Tyres"), SimpleNamespace(name="Fuel")]
    with (
        patch(
            "tahmeed.services.category_service.get_payment_target_categories",
            new=AsyncMock(return_value=cats),
        ),
        patch(
            "tahmeed.services.payee_service.get_payee_names",
            new=AsyncMock(return_value=["LAKE OIL", "PUMA ENERGY"]),
        ),
    ):
        asyncio.run(browser._load_smart_suggestions())
    assert browser._m_item._values == ["Fuel", "Tyres"]
    assert browser._m_payee._values == ["LAKE OIL", "PUMA ENERGY"]


def test_smart_item_tab_accepts_suggestion_and_moves_on():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    browser = _smart_browser()
    browser._m_item.set_values(["Fuel", "Tyres"])
    browser.show()
    browser._m_item.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(browser._m_item, "fu")
    QApplication.processEvents()
    QTest.keyClick(browser._m_item, Qt.Key_Tab)
    QApplication.processEvents()
    assert browser._m_item.text() == "Fuel"
    assert browser._smart_form_params()["item"] == "Fuel"
    assert browser.focusWidget() is browser._m_desc
    browser.close()


def test_smart_item_field_is_wider_than_description():
    browser = _smart_browser()
    browser.resize(1500, 500)
    browser.show()
    QApplication.processEvents()
    assert browser._m_item.width() > browser._m_desc.width()
    assert browser._m_item.height() == 32
    browser.close()


def _filter_overlaps(browser) -> list:
    from PySide6.QtWidgets import QLabel, QWidget

    panel = browser._filter_stack.currentWidget()
    kids = [
        w for w in panel.findChildren(QWidget)
        if w.isVisible() and not isinstance(w, QLabel) and w.parentWidget() is panel
    ]
    bad = [
        (a, c) for i, a in enumerate(kids) for c in kids[i + 1:]
        if a.geometry().intersects(c.geometry())
    ]
    bad += [(k, None) for k in kids if k.geometry().right() >= panel.width()]
    return bad


def test_filters_wrap_on_narrow_page_and_never_overlap():
    from tahmeed.ui.cashier.transactions_table import _MODE_ADVANCED

    # 880 = smallest Browse page (1100px window − 220px sidebar).
    for width, expect_wrapped in ((880, True), (1400, False)):
        browser = _browser()
        browser._smart_suggestions_loaded = True
        browser.setFixedWidth(width)
        browser.resize(width, 400)
        browser.show()
        for mode in (_MODE_SIMPLE, _MODE_ADVANCED, _MODE_SMART, _MODE_UPLOADS):
            browser._show_mode(mode)
            QApplication.processEvents()
            assert _filter_overlaps(browser) == [], (width, mode)
        for mode in (_MODE_ADVANCED, _MODE_SMART):
            assert browser._wrap_rows[mode].wrapped is expect_wrapped, (width, mode)
        browser._show_mode(_MODE_SMART)
        QApplication.processEvents()
        assert browser._m_item.width() > browser._m_desc.width()
        browser.close()


def test_smart_load_populates_table_and_summary():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from tahmeed.services.cashier_service import SmartSearchPage, SmartSearchSummary

    browser = _smart_browser()
    browser._m_payee.setText("puma")
    page = SmartSearchPage(
        items=[_smart_tx(), _smart_tx(description="Diesel refill", amount=3000)],
        total=2,
        summary=SmartSearchSummary(total=2, days=1, total_tzs=8000, total_usd=0),
        cashier_names={},
    )
    with patch(
        "tahmeed.ui.cashier.transactions_table.smart_search_page",
        new=AsyncMock(return_value=page),
    ) as api:
        asyncio.run(browser._async_find())

    kwargs = api.await_args.kwargs
    assert kwargs["payee"] == "puma" and kwargs["skip"] == 0
    t = browser._smart_table
    assert t.rowCount() == 2
    assert t.item(0, _M_COL_PAYEE).text() == "Puma Energy"
    assert browser._smart_layout.currentWidget() is t
    assert browser._count_label.text() == "Matches: 2  ·  1 day  ·  TZS 8,000"


def test_smart_go_to_uses_register_day_and_row_description():
    from datetime import datetime
    from unittest.mock import patch

    browser = _smart_browser()
    jumped = []
    browser.go_to_date.connect(lambda d, t="": jumped.append((d, t)))
    browser._results_smart = [
        _smart_tx(
            date=datetime(2026, 2, 27),
            import_primary_date=datetime(2026, 3, 1),
        )
    ]
    with patch.object(browser._smart_table, "currentRow", return_value=0):
        browser._on_go_to()
    assert jumped == [(date(2026, 3, 1), "Diesel top-up")]


def test_advanced_loads_first_page_then_appends_on_scroll():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from tahmeed.services.cashier_service import BrowseFlatPage
    from tahmeed.ui.cashier.transactions_table import _ADV_PAGE_SIZE, _MODE_ADVANCED

    _app()
    browser = TransactionBrowser()
    browser._current_mode = _MODE_ADVANCED
    browser._filter_stack.setCurrentIndex(_MODE_ADVANCED)
    browser._table_stack.setCurrentIndex(_MODE_ADVANCED)
    first = BrowseFlatPage(
        items=[_smart_tx(description=f"Row {i}") for i in range(3)],
        total=5,
        cashier_names={},
    )
    second = BrowseFlatPage(
        items=[_smart_tx(description=f"Row {i}") for i in range(3, 5)],
        total=5,
        cashier_names={},
    )
    with (
        patch.object(browser, "_ensure_period_ready", new=AsyncMock()),
        patch.object(browser, "_schedule_adv_fill"),
        patch(
            "tahmeed.ui.cashier.transactions_table.get_transactions_flat_page",
            new=AsyncMock(side_effect=[first, second]),
        ) as flat,
    ):
        asyncio.run(browser._async_find())
        assert flat.await_args.kwargs["limit"] == _ADV_PAGE_SIZE
        assert "skip" not in flat.await_args.kwargs
        assert browser._adv_table.rowCount() == 3
        assert browser._count_label.text() == "Transactions: 3 of 5  ·  Scroll for more"

        asyncio.run(browser._load_more_advanced())

    kwargs = flat.await_args.kwargs
    assert kwargs["skip"] == 3 and kwargs["limit"] == _ADV_PAGE_SIZE
    assert kwargs["date_from"] == browser._adv_params["date_from"]
    assert browser._adv_table.rowCount() == 5
    assert browser._adv_table.item(4, 2).text() == "Row 4"
    assert browser._count_label.text() == "Transactions: 5"


def test_uploads_table_has_sn_and_total_after_cashier():
    browser = _browser()
    headers = [
        browser._uploads_table.horizontalHeaderItem(i).text()
        for i in range(browser._uploads_table.columnCount())
    ]
    assert headers[_U_COL_SN] == "SN"
    assert headers[_U_COL_CASHIER] == "Cashier"
    assert headers[_U_COL_TOTAL] == "Total"
    assert _U_COL_TOTAL == _U_COL_CASHIER + 1
    assert headers.index("Total") == headers.index("Cashier") + 1
