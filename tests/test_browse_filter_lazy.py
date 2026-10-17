"""Browse Advanced Phase 2 — Item/Sub-Item distincts are not fetched on Find."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from PySide6.QtWidgets import QApplication

from tahmeed.ui.cashier.transactions_table import TransactionBrowser, _MODE_ADVANCED


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_advanced_find_skips_eager_filter_option_fetch() -> None:
    _app()
    browser = TransactionBrowser()
    browser._current_mode = _MODE_ADVANCED
    browser._filter_stack.setCurrentIndex(_MODE_ADVANCED)
    browser._table_stack.setCurrentIndex(_MODE_ADVANCED)

    page = type("P", (), {"items": [], "total": 0, "cashier_names": {}})()
    with (
        patch.object(browser, "_ensure_period_ready", new=AsyncMock()),
        patch(
            "tahmeed.ui.cashier.transactions_table.get_transactions_flat_page",
            new=AsyncMock(return_value=page),
        ) as flat,
        patch.object(
            browser, "_load_item_filter_options", new=AsyncMock(return_value=[]),
        ) as items,
        patch.object(
            browser, "_load_desc_filter_options", new=AsyncMock(return_value=[]),
        ) as descs,
        patch.object(browser, "_populate_advanced"),
    ):
        import asyncio

        asyncio.run(browser._async_find())

    flat.assert_awaited()
    items.assert_not_awaited()
    descs.assert_not_awaited()
    browser.close()
