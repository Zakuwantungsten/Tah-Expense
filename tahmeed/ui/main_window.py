import asyncio
import sys
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow,
    QStatusBar,
    QLabel,
    QWidget,
    QToolButton,
    QApplication,
)
from PySide6.QtCore import Qt, Signal, QPoint, QEvent, QRect
from PySide6.QtGui import QCloseEvent

from tahmeed.config import APP_NAME
from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard
from tahmeed.ui.accountant.dashboard import AccountantDashboard

_EDGE = 8

# Win32 WM_NCHITTEST result codes (frameless Aero Snap / edge resize).
_HTCLIENT = 1
_HTCAPTION = 2
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17
_WM_NCHITTEST = 0x0084


def edges_to_ht(edges: Qt.Edges) -> int:
    """Map Qt edge flags to a Win32 HT* code (0 = not an edge)."""
    if not edges:
        return 0
    left = bool(edges & Qt.LeftEdge)
    right = bool(edges & Qt.RightEdge)
    top = bool(edges & Qt.TopEdge)
    bottom = bool(edges & Qt.BottomEdge)
    if top and left:
        return _HTTOPLEFT
    if top and right:
        return _HTTOPRIGHT
    if bottom and left:
        return _HTBOTTOMLEFT
    if bottom and right:
        return _HTBOTTOMRIGHT
    if left:
        return _HTLEFT
    if right:
        return _HTRIGHT
    if top:
        return _HTTOP
    if bottom:
        return _HTBOTTOM
    return 0


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self._force_close = False
        self._close_pending = False
        self._resize_filters_ready = False
        self.setWindowTitle(f"{APP_NAME} — {user.full_name}")
        self.setMinimumSize(1100, 700)
        if user.role in {"accountant", "cashier"}:
            # Frameless chrome; Windows snap/resize via WM_NCHITTEST +
            # startSystemMove / startSystemResize fallbacks.
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setMouseTracking(True)
            self.setStyleSheet(
                "QMainWindow {"
                "  background: #1B2B4B;"
                "  border: 1px solid #0F1A2E;"
                "}"
            )
        self._build_ui()

    def _build_ui(self) -> None:
        if self.user.role == "admin":
            dash = AdminDashboard(self.user)
        elif self.user.role == "cashier":
            dash = CashierDashboard(self.user)
        elif self.user.role == "accountant":
            dash = AccountantDashboard(self.user)
        else:
            dash = QLabel(f"{self.user.role.title()} dashboard — coming soon")

        if hasattr(dash, "logout_requested"):
            dash.logout_requested.connect(self.logout_requested)
        self.setCentralWidget(dash)

        # Accountant/cashier chrome has its own status strip; avoid a second bar.
        # Admin puts user context in the page header / sidebar.
        if self.user.role not in {"accountant", "cashier", "admin"}:
            bar = QStatusBar()
            bar.showMessage(f"  {self.user.full_name}  ·  {self.user.role.title()}")
            self.setStatusBar(bar)
        elif self.user.role == "admin":
            self.setStyleSheet(
                "QMainWindow{background:#F8FAFC;}"
                "QStatusBar{background:#FFFFFF;color:#6B7280;"
                "border-top:1px solid #E5E7EB;}"
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (
            self.user.role in {"accountant", "cashier"}
            and not self._resize_filters_ready
        ):
            self._resize_filters_ready = True
            self._install_edge_filters(self.centralWidget())

    def _install_edge_filters(self, root: QWidget | None) -> None:
        if root is None:
            return
        # Chrome widgets only — content grids keep their own mouse handling.
        # Full-window edge hits on Windows come from nativeEvent (WM_NCHITTEST).
        dash = root
        targets = [dash]
        for name in ("_title_bar", "_menu_bar", "_sidebar"):
            w = getattr(dash, name, None)
            if w is not None:
                targets.append(w)
        for status_name in ("accountantStatusBar", "cashierStatusBar"):
            status = dash.findChild(QWidget, status_name)
            if status is not None:
                targets.append(status)
        for w in targets:
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            dash = self.centralWidget()
            sync = getattr(dash, "sync_chrome_maximized", None)
            if callable(sync):
                sync(self.isMaximized())

    async def prepare_to_leave(self) -> bool:
        """Ask the dashboard to save/discard unsaved work. True = OK to leave."""
        dash = self.centralWidget()
        if hasattr(dash, "prepare_to_leave"):
            return await dash.prepare_to_leave()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close:
            event.accept()
            return
        # Defer close until unsaved work is handled (async).
        event.ignore()
        if self._close_pending:
            return
        self._close_pending = True
        asyncio.ensure_future(self._close_after_prepare())

    async def _close_after_prepare(self) -> None:
        try:
            if not await self.prepare_to_leave():
                return
            self._force_close = True
            self.close()
            # Window X / Alt+F4: leave the process. Logout sets _force_close and
            # closes without going through this path, then shows the login window.
            app = QApplication.instance()
            if app is not None:
                app.quit()
        finally:
            self._close_pending = False

    # ── Frameless: native hit-test (Windows) + Qt resize fallback ───────────

    def _frameless(self) -> bool:
        return self.user.role in {"accountant", "cashier"}

    def _hit_edges(self, pos: QPoint) -> Qt.Edges:
        if not self._frameless() or self.isMaximized():
            return Qt.Edges()
        edges = Qt.Edges()
        r = self.rect()
        if pos.x() <= _EDGE:
            edges |= Qt.LeftEdge
        if pos.x() >= r.width() - _EDGE:
            edges |= Qt.RightEdge
        if pos.y() <= _EDGE:
            edges |= Qt.TopEdge
        if pos.y() >= r.height() - _EDGE:
            edges |= Qt.BottomEdge
        return edges

    def _title_bar_widget(self) -> Optional[QWidget]:
        dash = self.centralWidget()
        if dash is None:
            return None
        return getattr(dash, "_title_bar", None)

    def _over_title_bar_controls(self, local: QPoint) -> bool:
        title = self._title_bar_widget()
        if title is None or not title.isVisible():
            return False
        origin = title.mapTo(self, QPoint(0, 0))
        if not QRect(origin, title.size()).contains(local):
            return False
        for btn in title.findChildren(QToolButton):
            if not btn.isVisible():
                continue
            br = QRect(btn.mapTo(self, QPoint(0, 0)), btn.size())
            if br.contains(local):
                return True
        return False

    def _over_title_bar_caption(self, local: QPoint) -> bool:
        title = self._title_bar_widget()
        if title is None or not title.isVisible():
            return False
        if self._over_title_bar_controls(local):
            return False
        origin = title.mapTo(self, QPoint(0, 0))
        return QRect(origin, title.size()).contains(local)

    def nchit_test(self, global_pos: QPoint) -> int:
        """Win32 HT* code for a global cursor position (also used in tests)."""
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return _HTCLIENT
        edges = self._hit_edges(local)
        ht = edges_to_ht(edges)
        if ht:
            return ht
        if self._over_title_bar_caption(local):
            return _HTCAPTION
        return _HTCLIENT

    def nativeEvent(self, eventType, message):
        if (
            sys.platform == "win32"
            and self._frameless()
            and eventType in (b"windows_generic_MSG", "windows_generic_MSG")
        ):
            try:
                from ctypes import c_short
                from ctypes.wintypes import MSG

                addr = int(message)
                msg = MSG.from_address(addr)
                if msg.message == _WM_NCHITTEST:
                    x = c_short(msg.lParam & 0xFFFF).value
                    y = c_short((msg.lParam >> 16) & 0xFFFF).value
                    return True, self.nchit_test(QPoint(x, y))
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def eventFilter(self, obj, event):
        # Non-Windows / when native hit-test is unavailable: startSystemResize
        # from chrome widgets near the frame edge.
        if not self._frameless() or self.isMaximized() or sys.platform == "win32":
            return super().eventFilter(obj, event)

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.LeftButton
        ):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._hit_edges(pos)
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    return True
        elif event.type() == QEvent.Type.MouseMove:
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._hit_edges(pos)
            if edges:
                self.setCursor(self._cursor_for_edges(edges))
            else:
                self.unsetCursor()
        return super().eventFilter(obj, event)

    def _cursor_for_edges(self, edges: Qt.Edges) -> Qt.CursorShape:
        if not edges:
            return Qt.ArrowCursor
        left = bool(edges & Qt.LeftEdge)
        right = bool(edges & Qt.RightEdge)
        top = bool(edges & Qt.TopEdge)
        bottom = bool(edges & Qt.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor
