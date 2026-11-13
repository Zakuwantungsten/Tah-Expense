"""Regression: lifted chrome filters must never become top-level meedexpe HWNDs."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QWidget,
)

from tahmeed.ui.accountant.separate_expenses import (
    ZambiaParkingWidget,
    _chrome_clear_host,
    _chrome_set_filters,
)
from tahmeed.ui.widgets.feed_ribbon_toolbar import FeedRibbonToolbar


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_chrome_clear_host_does_not_promote_windows(qapp) -> None:
    host = QWidget()
    host.resize(640, 80)
    host.show()
    hl = QHBoxLayout(host)
    search = QLineEdit()
    search.setPlaceholderText("Search plate, ticket, destination, type…")
    years = QComboBox()
    years.addItem("All Years", 0)
    months = QComboBox()
    months.addItem("All Months", 0)
    for w in (search, years, months):
        hl.addWidget(w)
        w.show()

    _chrome_clear_host(hl)

    for w in (search, years, months):
        assert not w.isWindow(), "cleared chrome filter became a top-level window"
        assert w.isHidden()

    _chrome_set_filters(hl, [search, years, months])
    for w in (search, years, months):
        assert not w.isWindow()
        assert w.isVisible()
    host.close()


def test_ribbon_summary_clear_does_not_promote_window(qapp) -> None:
    host = QWidget()
    host.resize(800, 120)
    host.show()
    ribbon = FeedRibbonToolbar(host)
    host_layout = QHBoxLayout(host)
    host_layout.addWidget(ribbon)

    kpi = QWidget()
    kpi.setStyleSheet("background:transparent;")
    ribbon.set_summary_widget(kpi)
    assert not kpi.isWindow()
    assert not kpi.isHidden()

    ribbon.set_summary_widget(None)
    assert not kpi.isWindow(), "cleared ribbon KPI became a top-level window"
    assert kpi.isHidden()
    host.close()


def test_zambia_tab_switch_keeps_filters_as_children(qapp, monkeypatch) -> None:
    """Uploads tab clears All Entries filters — must not spawn taskbar windows."""
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingAllEntries.refresh",
        lambda self: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingMoneyInEntries.refresh",
        lambda self: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.separate_expenses._ZambiaParkingUploadBrowse.refresh",
        lambda self: None,
    )

    shell = QWidget()
    shell.resize(1100, 700)
    shell.show()
    w = ZambiaParkingWidget(shell)
    w.show()

    filters = list(w._all_entries.filter_widgets())
    summary = w._all_entries.summary_widget()
    assert filters
    assert summary is not None
    for f in filters:
        assert not f.isWindow()
    assert not summary.isWindow()

    # Drive through the tab bar so current_index matches (Uploads = 2).
    w._tabs.set_index(2)

    for f in filters:
        assert not f.isWindow(), f"filter promoted to window after Uploads tab: {f}"
        assert f.isHidden()
    assert not summary.isWindow()
    assert summary.isHidden()

    # Back to All Entries — filters reappear as children, not new windows.
    w._tabs.set_index(0)
    for f in filters:
        assert not f.isWindow()
        assert not f.isHidden()
    assert not summary.isWindow()
    assert not summary.isHidden()

    w.close()
    shell.close()
