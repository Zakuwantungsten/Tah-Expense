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
from PySide6.QtGui import QCloseEvent, QCursor

from tahmeed.config import APP_NAME
from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard
from tahmeed.ui.accountant.dashboard import AccountantDashboard

# Wider than a typical 1px border so edge-drag resize is easy to grab.
_EDGE = 12

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
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_WS_MAXIMIZEBOX = 0x00010000
_WS_MINIMIZEBOX = 0x00020000


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


def _lparam_screen_point(lparam: int) -> QPoint:
    """Decode WM_NCHITTEST lParam into a signed screen QPoint."""
    from ctypes import c_short

    x = c_short(lparam & 0xFFFF).value
    y = c_short((lparam >> 16) & 0xFFFF).value
    return QPoint(x, y)


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self._force_close = False
        self._close_pending = False
        self._resize_filters_ready = False
        self._thick_frame_applied = False
        # True while we applied a Size*Cursor on this shell. Content widgets
        # inherit that cursor, so it must be cleared on leave / interior moves.
        self._edge_cursor_active = False
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
        if self.user.role in {"accountant", "cashier"}:
            self._ensure_win32_thick_frame()
            if not self._resize_filters_ready:
                self._resize_filters_ready = True
                self._install_edge_filters(self.centralWidget())

    def _ensure_win32_thick_frame(self) -> None:
        """Keep WS_THICKFRAME so Windows still offers snap + edge resize."""
        if (
            self._thick_frame_applied
            or sys.platform != "win32"
            or not self._frameless()
        ):
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_STYLE)
            new_style = style | _WS_THICKFRAME | _WS_MAXIMIZEBOX | _WS_MINIMIZEBOX
            if new_style != style:
                ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_STYLE, new_style)
            self._thick_frame_applied = True
        except Exception:
            # Resize still works via WM_NCHITTEST / startSystemResize below.
            pass

    def _install_edge_filters(self, root: QWidget | None) -> None:
        if root is None:
            return
        # Chrome widgets + the shell itself. Content grids keep their own
        # mouse handling; full-window edges also use nativeEvent (WM_NCHITTEST).
        dash = root
        targets = [self, dash]
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
            if self.isMaximized():
                self._clear_edge_cursor()
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
        # Cursor can land 1px outside the client rect on DPI / frameless frames.
        if pos.x() <= _EDGE:
            edges |= Qt.LeftEdge
        if pos.x() >= r.width() - _EDGE - 1:
            edges |= Qt.RightEdge
        if pos.y() <= _EDGE:
            edges |= Qt.TopEdge
        if pos.y() >= r.height() - _EDGE - 1:
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
        # Inflate slightly — outer pixels often map just outside rect().
        hit_bounds = self.rect().adjusted(-_EDGE, -_EDGE, _EDGE, _EDGE)
        if not hit_bounds.contains(local):
            return _HTCLIENT
        edges = self._hit_edges(local)
        ht = edges_to_ht(edges)
        if ht:
            return ht
        if self.rect().contains(local) and self._over_title_bar_caption(local):
            return _HTCAPTION
        return _HTCLIENT

    def nativeEvent(self, eventType, message):
        if (
            sys.platform == "win32"
            and self._frameless()
            and eventType in (b"windows_generic_MSG", "windows_generic_MSG")
        ):
            try:
                from ctypes.wintypes import MSG

                addr = int(message)
                msg = MSG.from_address(addr)
                if msg.message == _WM_NCHITTEST:
                    return True, self.nchit_test(_lparam_screen_point(int(msg.lParam)))
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _shell_owns_object(self, obj) -> bool:
        if obj is self:
            return True
        if isinstance(obj, QWidget):
            try:
                return obj.window() is self
            except RuntimeError:
                return False
        return False

    def _clear_edge_cursor(self) -> None:
        """Drop a shell Size*Cursor so content no longer inherits it."""
        if not self._edge_cursor_active:
            return
        self._edge_cursor_active = False
        self.unsetCursor()
        app = QApplication.instance()
        if app is not None:
            # Remove the temporary app-wide filter installed while active.
            app.removeEventFilter(self)

    def _sync_edge_cursor(self, global_pos: QPoint | None = None) -> None:
        """Set or clear the edge resize cursor from a screen position."""
        if not self._frameless() or self.isMaximized() or not self.isVisible():
            self._clear_edge_cursor()
            return
        if global_pos is None:
            global_pos = QCursor.pos()
        edges = self._hit_edges(self.mapFromGlobal(global_pos))
        if not edges:
            self._clear_edge_cursor()
            return
        self.setCursor(self._cursor_for_edges(edges))
        if not self._edge_cursor_active:
            self._edge_cursor_active = True
            # While the shell cursor is active it is inherited by every child
            # that has no cursor of its own (tables, pages, …). Catch moves on
            # those widgets too so we clear as soon as the pointer leaves the
            # resize strip — chrome-only filters never see those moves.
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)

    def eventFilter(self, obj, event):
        # Edge cursor + startSystemResize fallback (all platforms, including
        # Windows when WM_NCHITTEST does not fire for a given press).
        if not self._frameless():
            return super().eventFilter(obj, event)

        if self.isMaximized():
            self._clear_edge_cursor()
            return super().eventFilter(obj, event)

        et = event.type()
        in_shell = self._shell_owns_object(obj)

        if (
            et == QEvent.Type.MouseButtonPress
            and in_shell
            and hasattr(event, "button")
            and event.button() == Qt.LeftButton
            and hasattr(event, "globalPosition")
        ):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._hit_edges(pos)
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    # OS resize grab finished (or was cancelled); re-sync so
                    # a Size*Cursor does not remain stuck afterward.
                    self._sync_edge_cursor(event.globalPosition().toPoint())
                    return True
        elif et == QEvent.Type.MouseMove and hasattr(event, "globalPosition"):
            if in_shell or self._edge_cursor_active:
                self._sync_edge_cursor(event.globalPosition().toPoint())
        elif et in (
            QEvent.Type.Leave,
            QEvent.Type.HoverLeave,
            QEvent.Type.MouseButtonRelease,
        ):
            if in_shell or self._edge_cursor_active:
                if et == QEvent.Type.MouseButtonRelease and hasattr(
                    event, "globalPosition"
                ):
                    self._sync_edge_cursor(event.globalPosition().toPoint())
                else:
                    self._sync_edge_cursor()
        elif et in (
            QEvent.Type.WindowDeactivate,
            QEvent.Type.Hide,
        ):
            # Only the shell itself — an app-wide filter also sees child
            # Hide/FocusOut noise and must not drop a still-valid edge cursor.
            if obj is self:
                self._clear_edge_cursor()
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
