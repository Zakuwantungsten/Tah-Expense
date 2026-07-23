"""Regression tests for TruckLineEdit Tab / deferred suggestion behaviour."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_tab_blocks_late_suggestions_from_reopening_popup() -> None:
    """Tab must cancel pending work so a late apply cannot reopen the popup."""
    from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

    _app()
    host = QWidget()
    layout = QHBoxLayout(host)
    truck = TruckLineEdit(local_numbers=["T688 EAF", "T699 ABC"])
    nxt = QLineEdit()
    layout.addWidget(truck)
    layout.addWidget(nxt)
    host.show()
    truck.setFocus()
    QApplication.processEvents()

    truck._typed = "T6"
    truck.setText("T6")
    truck._debounce.stop()
    truck._block_suggestions = True
    truck._suppress_preview = True
    truck.clearFocus()
    nxt.setFocus()
    QApplication.processEvents()

    truck._apply_suggestions(["T688 EAF", "T699 ABC"])
    QApplication.processEvents()

    popup = truck._completer.popup()
    assert popup is None or not popup.isVisible()
    assert truck._block_suggestions is True


def test_tab_key_stops_debounce_and_moves_focus() -> None:
    from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

    _app()
    host = QWidget()
    layout = QHBoxLayout(host)
    truck = TruckLineEdit(local_numbers=["T688 EAF"])
    nxt = QLineEdit()
    layout.addWidget(truck)
    layout.addWidget(nxt)
    host.show()
    truck.setFocus()
    QApplication.processEvents()

    truck._on_text_edited("T6")
    assert truck._debounce.isActive()

    tab = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Tab, Qt.KeyboardModifier.NoModifier)
    truck.keyPressEvent(tab)
    QApplication.processEvents()
    QTest.qWait(20)

    assert not truck._debounce.isActive()
    assert truck._block_suggestions is True
    assert nxt.hasFocus()


def test_tab_accepts_preview_hides_popup_and_focuses_next() -> None:
    """Real QTest path: Qt's completer must not leave the popup stuck open."""
    from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

    app = _app()
    host = QWidget()
    host.resize(800, 200)
    outer = QVBoxLayout(host)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    row = QHBoxLayout(inner)
    truck = TruckLineEdit(local_numbers=["T688 EAF", "T699 ABC"])
    search = QLineEdit()
    search.setObjectName("search")
    row.addWidget(truck)
    row.addWidget(search)
    QWidget.setTabOrder(truck, search)
    scroll.setWidget(inner)
    outer.addWidget(scroll)
    host.show()
    truck.setFocus()
    app.processEvents()

    QTest.keyClicks(truck, "T6")
    QTest.qWait(120)
    app.processEvents()

    assert truck._preview_active is True
    assert truck.text() == "T688 EAF"
    assert truck._completer.popup().isVisible()

    QTest.keyClick(truck, Qt.Key_Tab)
    QTest.qWait(80)
    app.processEvents()

    assert truck.text() == "T688 EAF"
    assert not truck._completer.popup().isVisible()
    assert truck._block_suggestions is True
    assert search.hasFocus()
