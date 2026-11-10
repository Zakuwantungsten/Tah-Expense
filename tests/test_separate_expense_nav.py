"""Separate Expenses: instant tab switch then async load."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from tahmeed.ui.accountant.dashboard import (
    _SEPARATE_EXPENSE_KEYS,
    AccountantDashboard,
)
from tahmeed.ui.accountant.separate_expenses import TollPlazaWidget


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_separate_expense_keys_cover_feeds() -> None:
    assert "toll_plaza" in _SEPARATE_EXPENSE_KEYS
    assert "parking_congo" in _SEPARATE_EXPENSE_KEYS
    assert "rahntech" in _SEPARATE_EXPENSE_KEYS


def test_toll_plaza_init_does_not_refresh(qapp, monkeypatch) -> None:
    calls = {"n": 0}

    def _boom(self):
        calls["n"] += 1
        raise AssertionError("refresh must not run during __init__")

    monkeypatch.setattr(TollPlazaWidget, "refresh", _boom)
    w = TollPlazaWidget()
    assert calls["n"] == 0
    # Default tab built; secondary panes still lazy.
    assert w._browse is None
    assert w._skipped is None
    assert w._all_entries is not None
    w.close()


def test_toll_plaza_lazy_uploads_on_tab(qapp, monkeypatch) -> None:
    w = TollPlazaWidget()
    assert w._browse is None

    # Avoid needing a running asyncio loop for browse.refresh().
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._TollUploadBrowse.refresh",
        lambda self: None,
    )
    w._on_main_tab(1)
    assert w._browse is not None
    assert w._detail is not None
    assert w._upload_stack is not None
    w.close()


def test_open_separate_expense_first_shows_placeholder(qapp, monkeypatch) -> None:
    """Loader shell appears before create_page; real page swaps in on next tick."""
    host = QWidget()
    host.resize(800, 600)
    stack = QStackedWidget(host)
    stack.addWidget(QWidget())  # index 0 stand-in for overview
    host.show()
    qapp.processEvents()

    created = {"n": 0}

    class _Dash:
        def __init__(self) -> None:
            self._stack = stack
            self._pages: dict = {}
            self._page_indices: dict = {}
            self._nav_key = "toll_plaza"

        def _mount_page(self, key: str, widget: QWidget) -> QWidget:
            return AccountantDashboard._mount_page(self, key, widget)

        def _create_page(self, key: str) -> QWidget:
            created["n"] += 1
            stub = QWidget()
            stub.setObjectName(f"stub_{key}")
            stub.refresh = lambda: None  # type: ignore[attr-defined]
            return stub

        def _activate_page(self, key: str) -> QWidget:
            return AccountantDashboard._activate_page(self, key)

    dash = _Dash()
    # Bind the real method onto our lightweight stand-in.
    AccountantDashboard._open_separate_expense_first(dash, "toll_plaza")

    assert created["n"] == 0
    current = stack.currentWidget()
    assert current is not None
    assert current.objectName() == "separateExpenseOpening"
    overlay = getattr(current, "_loading_overlay", None)
    assert overlay is not None
    assert overlay.isVisible()

    for _ in range(30):
        if "toll_plaza" in dash._pages:
            break
        qapp.processEvents()

    assert "toll_plaza" in dash._pages
    assert created["n"] == 1
    assert stack.currentWidget() is dash._pages["toll_plaza"]
    page = stack.currentWidget()
    inner = getattr(page, "content", page)
    assert inner is not None
    assert inner.objectName() == "stub_toll_plaza"
    host.close()


def test_stale_separate_expense_build_does_not_steal_stack(qapp) -> None:
    """A late first-open must not override a later sidebar selection."""
    host = QWidget()
    host.resize(800, 600)
    stack = QStackedWidget(host)
    overview = QWidget()
    overview.setObjectName("overview")
    stack.addWidget(overview)
    host.show()
    qapp.processEvents()

    created: list[str] = []

    class _Dash:
        def __init__(self) -> None:
            self._stack = stack
            self._pages: dict = {}
            self._page_indices: dict = {}
            self._nav_key = "overview"

        def _mount_page(self, key: str, widget: QWidget) -> QWidget:
            return AccountantDashboard._mount_page(self, key, widget)

        def _create_page(self, key: str) -> QWidget:
            created.append(key)
            stub = QWidget()
            stub.setObjectName(f"stub_{key}")
            stub.refresh = lambda: None  # type: ignore[attr-defined]
            return stub

        def _activate_page(self, key: str) -> QWidget:
            return AccountantDashboard._activate_page(self, key)

    dash = _Dash()
    dash._nav_key = "parking_congo"
    AccountantDashboard._open_separate_expense_first(dash, "parking_congo")

    # User leaves before Parking Congo finishes building.
    dash._nav_key = "toll_plaza"
    stack.setCurrentWidget(overview)

    for _ in range(30):
        if "parking_congo" in dash._pages:
            break
        qapp.processEvents()

    assert "parking_congo" in dash._pages
    assert created == ["parking_congo"]
    # Stale build must leave the stack on the page the user chose next.
    assert stack.currentWidget() is overview
    host.close()


def test_revisit_after_second_feed_uses_widget_not_stale_index(qapp) -> None:
    """Opening shell removal must not make going back show the other feed."""
    host = QWidget()
    host.resize(800, 600)
    stack = QStackedWidget(host)
    stack.addWidget(QWidget())  # overview
    host.show()
    qapp.processEvents()

    class _Dash:
        def __init__(self) -> None:
            self._stack = stack
            self._pages: dict = {}
            self._page_indices: dict = {}
            self._nav_key = "toll_plaza"

        def _mount_page(self, key: str, widget: QWidget) -> QWidget:
            return AccountantDashboard._mount_page(self, key, widget)

        def _create_page(self, key: str) -> QWidget:
            stub = QWidget()
            stub.setObjectName(f"stub_{key}")
            stub.refresh = lambda: None  # type: ignore[attr-defined]
            return stub

        def _activate_page(self, key: str) -> QWidget:
            return AccountantDashboard._activate_page(self, key)

    dash = _Dash()
    AccountantDashboard._open_separate_expense_first(dash, "toll_plaza")
    for _ in range(30):
        if "toll_plaza" in dash._pages:
            break
        qapp.processEvents()
    assert stack.currentWidget() is dash._pages["toll_plaza"]

    dash._nav_key = "parking_congo"
    AccountantDashboard._open_separate_expense_first(dash, "parking_congo")
    for _ in range(30):
        if "parking_congo" in dash._pages:
            break
        qapp.processEvents()
    assert stack.currentWidget() is dash._pages["parking_congo"]

    # Going back must resolve by widget identity, not a hole-shifted index.
    dash._nav_key = "toll_plaza"
    AccountantDashboard._open_separate_expense_first(dash, "toll_plaza")
    assert stack.currentWidget() is dash._pages["toll_plaza"]
    page = stack.currentWidget()
    inner = getattr(page, "content", page)
    assert inner is not None
    assert inner.objectName() == "stub_toll_plaza"
    host.close()
