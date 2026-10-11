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
    assert "TAHMEED COACH" in label.text()
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


def test_edges_to_ht_corners_and_sides():
    from PySide6.QtCore import Qt

    from tahmeed.ui.main_window import edges_to_ht

    assert edges_to_ht(Qt.Edges(0)) == 0
    assert edges_to_ht(Qt.LeftEdge) == 10
    assert edges_to_ht(Qt.RightEdge) == 11
    assert edges_to_ht(Qt.TopEdge) == 12
    assert edges_to_ht(Qt.BottomEdge) == 15
    assert edges_to_ht(Qt.LeftEdge | Qt.TopEdge) == 13
    assert edges_to_ht(Qt.RightEdge | Qt.BottomEdge) == 17


def test_lparam_screen_point_signed_coords():
    from tahmeed.ui.main_window import _lparam_screen_point

    # Low word x=10, high word y=20
    pt = _lparam_screen_point(20 << 16 | 10)
    assert pt.x() == 10
    assert pt.y() == 20
    # Negative screen coords (multi-monitor) must stay signed.
    neg = _lparam_screen_point(((-5) & 0xFFFF) << 16 | ((-12) & 0xFFFF))
    assert neg.x() == -12
    assert neg.y() == -5


def test_frameless_nchit_edges_are_resizable(qapp, monkeypatch):
    """Cashier/accountant shell reports HT* resize codes on the outer edge."""
    from PySide6.QtCore import QPoint, QRect

    from tahmeed.ui.main_window import MainWindow, _HTBOTTOM, _HTLEFT, _HTRIGHT, _HTTOP

    user = User(
        username="cashier1",
        password_hash="x",
        role="cashier",
        full_name="Cashier",
    )
    # Avoid building the full cashier dashboard in this unit test.
    monkeypatch.setattr(
        MainWindow,
        "_build_ui",
        lambda self: None,
    )
    win = MainWindow(user)
    win.setGeometry(100, 100, 1200, 800)

    monkeypatch.setattr(win, "mapFromGlobal", lambda g: QPoint(g.x() - 100, g.y() - 100))
    monkeypatch.setattr(win, "rect", lambda: QRect(0, 0, 1200, 800))
    monkeypatch.setattr(win, "isMaximized", lambda: False)

    assert win.nchit_test(QPoint(100, 500)) == _HTLEFT  # left edge
    assert win.nchit_test(QPoint(100 + 1199, 500)) == _HTRIGHT
    assert win.nchit_test(QPoint(700, 100)) == _HTTOP
    assert win.nchit_test(QPoint(700, 100 + 799)) == _HTBOTTOM
    # Interior is client (not a resize border).
    assert win.nchit_test(QPoint(700, 500)) == 1
    win._force_close = True
    win.close()


def test_title_bar_starts_system_move_helper(qapp, monkeypatch):
    """Drag threshold path calls QWindow.startSystemMove when available."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    bar = AccountantTitleBar(_user())
    calls: list[bool] = []

    class _Handle:
        def startSystemMove(self):
            calls.append(True)
            return True

    class _Win:
        def windowHandle(self):
            return _Handle()

        def isMaximized(self):
            return False

    monkeypatch.setattr(bar, "window", lambda: _Win())

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(20, 10),
        QPointF(120, 80),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    bar.mousePressEvent(press)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(40, 10),
        QPointF(140, 80),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    bar.mouseMoveEvent(move)
    assert calls == [True]


def _frameless_shell(monkeypatch, role: str = "accountant"):
    """Minimal frameless MainWindow without building a full dashboard."""
    from tahmeed.ui.main_window import MainWindow

    user = User(
        username=f"{role}1",
        password_hash="x",
        role=role,
        full_name=role.title(),
    )
    monkeypatch.setattr(MainWindow, "_build_ui", lambda self: None)
    win = MainWindow(user)
    win.setGeometry(100, 100, 1200, 800)
    win.show()
    return win


def test_edge_resize_cursor_clears_when_pointer_leaves_edge(qapp, monkeypatch):
    """SizeHorCursor on the shell must not stick after leaving the resize strip."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QWidget

    win = _frameless_shell(monkeypatch, role="cashier")
    chrome = QWidget(win)
    chrome.setGeometry(0, 0, 200, 800)
    chrome.setMouseTracking(True)
    chrome.installEventFilter(win)
    chrome.show()
    qapp.processEvents()

    # Hover the left window edge over chrome → double-arrow resize cursor.
    edge_global = win.mapToGlobal(QPoint(4, 400))
    move_edge = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(4, 400),
        edge_global,
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    assert win.eventFilter(chrome, move_edge) is False
    assert win._edge_cursor_active is True
    assert win.cursor().shape() == Qt.SizeHorCursor

    # Move into interior content (a child that is not chrome). Previously the
    # shell cursor stayed SizeHor and was inherited by every page/table.
    content = QWidget(win)
    content.setGeometry(300, 100, 400, 400)
    content.setMouseTracking(True)
    content.show()
    qapp.processEvents()

    interior_global = win.mapToGlobal(QPoint(500, 300))
    move_interior = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(200, 200),
        interior_global,
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    # App-wide filter (installed while edge cursor is active) must clear it.
    assert win.eventFilter(content, move_interior) is False
    assert win._edge_cursor_active is False
    assert win.cursor().shape() == Qt.ArrowCursor

    win._force_close = True
    win.close()


def test_edge_resize_cursor_clears_on_leave_and_maximize(qapp, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QWidget

    win = _frameless_shell(monkeypatch, role="accountant")
    chrome = QWidget(win)
    chrome.installEventFilter(win)
    chrome.show()

    win._sync_edge_cursor(win.mapToGlobal(QPoint(4, 400)))
    assert win._edge_cursor_active is True

    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: win.mapToGlobal(QPoint(500, 300))))
    leave = QEvent(QEvent.Type.Leave)
    win.eventFilter(chrome, leave)
    assert win._edge_cursor_active is False

    win._sync_edge_cursor(win.mapToGlobal(QPoint(4, 400)))
    assert win._edge_cursor_active is True
    monkeypatch.setattr(win, "isMaximized", lambda: True)
    win._sync_edge_cursor(win.mapToGlobal(QPoint(4, 400)))
    assert win._edge_cursor_active is False

    win._force_close = True
    win.close()


def test_edge_resize_cursor_helpers_work_for_cashier_and_accountant(qapp, monkeypatch):
    from PySide6.QtCore import QPoint, Qt

    for role in ("cashier", "accountant"):
        win = _frameless_shell(monkeypatch, role=role)
        win._sync_edge_cursor(win.mapToGlobal(QPoint(4, 400)))
        assert win._edge_cursor_active is True
        assert win.cursor().shape() == Qt.SizeHorCursor
        win._clear_edge_cursor()
        assert win._edge_cursor_active is False
        win._force_close = True
        win.close()
