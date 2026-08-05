import asyncio

from PySide6.QtWidgets import QMainWindow, QStatusBar, QLabel, QWidget
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QEvent
from PySide6.QtGui import QCloseEvent

from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard
from tahmeed.ui.accountant.dashboard import AccountantDashboard

_EDGE = 6


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self._force_close = False
        self._close_pending = False
        self._edge_resize = False
        self._resize_edges = Qt.Edges()
        self._resize_origin = QPoint()
        self._geo_origin = QRect()
        self._resize_filters_ready = False
        self.setWindowTitle(f"Tahmeed Expense — {user.full_name}")
        self.setMinimumSize(1100, 700)
        if user.role == "accountant":
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

        # Accountant chrome has its own status strip; avoid a second bar.
        if self.user.role != "accountant":
            bar = QStatusBar()
            bar.showMessage(f"  {self.user.full_name}  ·  {self.user.role.title()}")
            self.setStatusBar(bar)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.user.role == "accountant" and not self._resize_filters_ready:
            self._resize_filters_ready = True
            self._install_edge_filters(self.centralWidget())

    def _install_edge_filters(self, root: QWidget | None) -> None:
        if root is None:
            return
        # Only track chrome + shell widgets so we don't fight grid cell editors.
        dash = root
        targets = [dash]
        for name in ("_title_bar", "_menu_bar", "_sidebar"):
            w = getattr(dash, name, None)
            if w is not None:
                targets.append(w)
        status = dash.findChild(QWidget, "accountantStatusBar")
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
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()
        finally:
            self._close_pending = False

    # ── Frameless edge resize (accountant only) ─────────────────────────────

    def eventFilter(self, obj, event):
        if self.user.role != "accountant" or self.isMaximized():
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._hit_edges(pos)
            if edges:
                self._edge_resize = True
                self._resize_edges = edges
                self._resize_origin = event.globalPosition().toPoint()
                self._geo_origin = self.geometry()
                return True
        elif et == QEvent.Type.MouseMove:
            if self._edge_resize and event.buttons() & Qt.LeftButton:
                delta = event.globalPosition().toPoint() - self._resize_origin
                geo = QRect(self._geo_origin)
                if self._resize_edges & Qt.LeftEdge:
                    geo.setLeft(geo.left() + delta.x())
                if self._resize_edges & Qt.RightEdge:
                    geo.setRight(geo.right() + delta.x())
                if self._resize_edges & Qt.TopEdge:
                    geo.setTop(geo.top() + delta.y())
                if self._resize_edges & Qt.BottomEdge:
                    geo.setBottom(geo.bottom() + delta.y())
                if (
                    geo.width() >= self.minimumWidth()
                    and geo.height() >= self.minimumHeight()
                ):
                    self.setGeometry(geo)
                return True
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._hit_edges(pos)
            if edges:
                self.setCursor(self._cursor_for_edges(edges))
            else:
                self.unsetCursor()
        elif et == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._edge_resize:
                self._edge_resize = False
                self._resize_edges = Qt.Edges()
                return True
        return super().eventFilter(obj, event)

    def _hit_edges(self, pos: QPoint) -> Qt.Edges:
        if self.user.role != "accountant" or self.isMaximized():
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
