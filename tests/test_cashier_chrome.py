"""Smoke tests for cashier QuickBooks-style chrome."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMenuBar

from tahmeed.models.user import User
from tahmeed.ui.accountant.title_bar import AccountantTitleBar
from tahmeed.ui.cashier.menu_bar import CashierMenuBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _user() -> User:
    return User(
        username="tungsten",
        password_hash="x",
        role="cashier",
        full_name="Tungsten",
    )


def test_cashier_title_bar_shows_company_and_edition(qapp):
    from PySide6.QtWidgets import QLabel

    bar = AccountantTitleBar(_user(), edition="Cashier Edition")
    label = bar.findChild(QLabel, "accountantTitleText")
    assert label is not None
    assert "TAHMEED COACH" in label.text()
    assert "Cashier Edition" in label.text()
    assert "Tungsten" in label.text()


def test_cashier_menu_bar_has_scoped_menus(qapp):
    bar = CashierMenuBar()
    titles = [a.text().replace("&", "") for a in bar.actions()]
    for expected in (
        "File",
        "Edit",
        "View",
        "Cashier",
        "Reports",
        "Window",
        "Help",
    ):
        assert expected in titles
    for absent in ("Lists", "Manage", "Accountant", "Expenses", "Fuel", "Company"):
        assert absent not in titles


def test_cashier_menu_navigate_callback(qapp):
    seen: list[str] = []
    bar = CashierMenuBar()
    bar.bind(navigate=seen.append)
    cashier_action = next(
        a for a in bar.actions() if a.text().replace("&", "") == "Cashier"
    )
    menu = cashier_action.menu()
    assert menu is not None
    overview = next(
        a for a in menu.actions() if a.text().replace("&", "") == "Overview"
    )
    overview.trigger()
    assert seen == ["overview"]


def test_cashier_menu_items_submenu_updates(qapp):
    seen: list[str] = []
    bar = CashierMenuBar()
    bar.bind(navigate=seen.append)
    bar.set_item_entries([("toll", "Toll Plaza"), ("fuel", "Fuel")])

    cashier_action = next(
        a for a in bar.actions() if a.text().replace("&", "") == "Cashier"
    )
    menu = cashier_action.menu()
    items_action = next(
        a for a in menu.actions() if a.text().replace("&", "") == "Items"
    )
    items_menu = items_action.menu()
    assert items_menu is not None
    labels = [a.text().replace("&", "") for a in items_menu.actions()]
    assert labels == ["Toll Plaza", "Fuel"]

    items_menu.actions()[0].trigger()
    assert seen == ["toll"]


def test_cashier_menu_bar_is_qmenubar(qapp):
    assert isinstance(CashierMenuBar(), QMenuBar)
