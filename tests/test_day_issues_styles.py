"""Startup / Day Issues QSS must parse (no stray braces from plain-string segments)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from tahmeed.models.user import User


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _capture_stylesheet_errors(build) -> list[str]:
    msgs: list[str] = []

    def _handler(mode, context, message):
        msgs.append(str(message))

    previous = qInstallMessageHandler(_handler)
    try:
        build()
    finally:
        qInstallMessageHandler(previous)
    return [m for m in msgs if "Could not parse stylesheet" in m]


def test_day_issues_autofit_and_checkbox_style_constants(qapp) -> None:
    from tahmeed.ui.cashier.day_issues import (
        _BLUE_L,
        _COL_DESC,
        _COL_KEY,
        _FIXED_COLS,
        _checkbox_style,
        payee_needs_create,
    )

    assert _COL_KEY == "day_issues_table_v3"
    assert _COL_DESC not in _FIXED_COLS
    style = _checkbox_style()
    assert _BLUE_L in style
    assert "indicator:checked" in style
    assert "image:url(" in style
    assert payee_needs_create("NEW CO", ["CITY", "BANK"]) is True
    assert payee_needs_create("city", ["CITY", "BANK"]) is False
    assert payee_needs_create("", ["CITY"]) is False


def test_day_issues_widget_stylesheets_parse(qapp):
    from tahmeed.ui.cashier.day_issues import DayIssuesWidget

    def build():
        user = User(
            username="t",
            password_hash="x",
            role="cashier",
            full_name="Tester",
        )
        page = DayIssuesWidget(user)
        page.show()
        qapp.processEvents()
        page.close()

    assert _capture_stylesheet_errors(build) == []


def test_cashier_dashboard_stylesheets_parse(qapp):
    from tahmeed.ui.cashier.dashboard import CashierDashboard

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        def build():
            user = User(
                username="c",
                password_hash="x",
                role="cashier",
                full_name="Cashier",
            )
            dash = CashierDashboard(user)
            dash.show()
            qapp.processEvents()
            dash.close()

        assert _capture_stylesheet_errors(build) == []
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_accountant_dashboard_stylesheets_parse(qapp, monkeypatch):
    from tahmeed.ui.accountant.dashboard import AccountantDashboard

    # Avoid qasync/network side effects; this test only checks QSS parse.
    monkeypatch.setattr(
        AccountantDashboard, "_poll_notification_counts", lambda self: None
    )
    monkeypatch.setattr(
        AccountantDashboard,
        "_warm_import_separate_expenses",
        staticmethod(lambda: None),
    )

    def build():
        user = User(
            username="a",
            password_hash="x",
            role="accountant",
            full_name="Accountant",
        )
        dash = AccountantDashboard(user)
        dash.show()
        qapp.processEvents()
        # Shell only — do not _on_nav here (that needs a running qasync loop).
        dash.close()

    assert _capture_stylesheet_errors(build) == []
