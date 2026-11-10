"""Seeded circuit page canvas helpers."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.ui.widgets.circuit_geometry import (
    CircuitGeometryPage,
    seed_from_key,
    wrap_circuit_page,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_seed_from_key_stable_and_distinct() -> None:
    assert seed_from_key("toll_plaza") == seed_from_key("toll_plaza")
    assert seed_from_key("toll_plaza") != seed_from_key("parking_congo")
    assert seed_from_key("verify") != seed_from_key("table")


def test_wrap_skips_overview(qapp) -> None:
    page = QWidget()
    page.setObjectName("overviewPage")
    wrapped = wrap_circuit_page(page, seed="overview")
    assert wrapped is page


def test_wrap_returns_host_with_content(qapp) -> None:
    page = QWidget()
    page.setStyleSheet("background:#F4F6F8;")
    page.setObjectName("tollPlaza")
    page.refresh = lambda: None  # type: ignore[attr-defined]
    wrapped = wrap_circuit_page(page, seed="toll_plaza")
    assert isinstance(wrapped, CircuitGeometryPage)
    assert wrapped.content is page
    assert callable(wrapped.refresh)  # forwarded to content
    wrapped.close()


def test_circuit_chrome_is_pass_through(qapp) -> None:
    from tahmeed.ui.widgets.circuit_geometry import CircuitChromeFrame

    chrome = CircuitChromeFrame("drafts", bg="#FFFFFF", object_name="draftsHeader")
    assert chrome.objectName() == "draftsHeader"
    assert "transparent" in chrome.styleSheet().lower()
    assert not hasattr(chrome, "_circuit_seed")  # no local motif painter
    chrome.close()


def test_prepare_neutralizes_class_and_qwidget_fills(qapp) -> None:
    from tahmeed.ui.widgets.circuit_geometry import prepare_circuit_content

    form = QWidget()
    form.setObjectName("entryForm")
    form.setStyleSheet("EntryForm { background:#f3f4f6; }")
    # Pretend class name by using a subclass-like selector already in SS;
    # prepare uses type(widget).__name__ == "QWidget".
    form.setStyleSheet("QWidget { background: #ffffff; color:#111827; }")
    prepare_circuit_content(form)
    ss = form.styleSheet().lower()
    assert "background: transparent" in ss
    assert form.objectName() == "entryForm"
