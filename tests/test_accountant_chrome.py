"""Smoke tests for accountant QuickBooks-style chrome."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMenuBar

from tahmeed.models.user import User
from tahmeed.ui.accountant.menu_bar import AccountantMenuBar
from tahmeed.ui.accountant.title_bar import AccountantTitleBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _user() -> User:
    return User(
        username="hamdu",
        password_hash="x",
        role="accountant",
        full_name="Hamdu",
    )


def test_title_bar_shows_company_and_version(qapp):
    from PySide6.QtWidgets import QLabel

    bar = AccountantTitleBar(_user())
    label = bar.findChild(QLabel, "accountantTitleText")
    assert label is not None
    assert "TAHMEED TRANSPORTERS" in label.text()
    assert "Accountant Edition" in label.text()
    assert "Hamdu" in label.text()


def test_menu_bar_has_quickbooks_style_menus(qapp):
    bar = AccountantMenuBar()
    titles = [a.text().replace("&", "") for a in bar.actions()]
    for expected in (
        "File",
        "Edit",
        "View",
        "Lists",
        "Accountant",
        "Company",
        "Expenses",
        "Fuel",
        "Reports",
        "Window",
        "Help",
    ):
        assert expected in titles


def test_menu_navigate_callback(qapp):
    seen: list[str] = []
    bar = AccountantMenuBar()
    bar.bind(navigate=seen.append)
    accountant_action = next(
        a for a in bar.actions() if a.text().replace("&", "") == "Accountant"
    )
    menu = accountant_action.menu()
    assert menu is not None
    overview = next(
        a
        for a in menu.actions()
        if a.text().replace("&", "") == "Overview"
    )
    overview.trigger()
    assert seen == ["overview"]


def test_menu_bar_is_qmenubar(qapp):
    assert isinstance(AccountantMenuBar(), QMenuBar)
