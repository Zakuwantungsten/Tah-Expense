"""Windows Explorer–style nav chrome + icon command ribbon."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_TEXT = "#111827"
_MUTED = "#6B7280"
_BORDER = "#D1D5DB"
_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_HOVER = "#E5E7EB"
_DISABLED = "#9CA3AF"
_ACCENT = "#0077C5"


def cmd_icon_button(
    icon: str,
    tip: str,
    *,
    color: str = _TEXT,
    size: int = 34,
    icon_px: int = 18,
    menu: bool = False,
) -> QToolButton:
    """Icon-only tool button (Windows 11 command-bar look)."""
    btn = QToolButton()
    btn.setIcon(qta.icon(icon, color=color))
    btn.setIconSize(QSize(icon_px, icon_px))
    btn.setToolTip(tip)
    btn.setAutoRaise(True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(size, 30)
    if menu:
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
    btn.setStyleSheet(
        "QToolButton {"
        f"  background: transparent; border: none; border-radius: 4px; color: {_TEXT};"
        "}"
        f"QToolButton:hover {{ background: {_HOVER}; }}"
        f"QToolButton:pressed {{ background: {_BORDER}; }}"
        f"QToolButton:disabled {{ color: {_DISABLED}; }}"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    return btn


def cmd_separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    line.setFixedHeight(22)
    line.setStyleSheet(f"background: {_BORDER}; border: none;")
    return line


class WindowsCommandBar(QWidget):
    """Horizontal icon command strip (ribbon row)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WindowsCommandBar")
        self.setStyleSheet(
            f"QWidget#WindowsCommandBar {{ background: {_CARD}; border: none;"
            f" border-top: 1px solid {_BORDER}; }}"
        )
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(8, 4, 8, 4)
        self._lay.setSpacing(2)
        self.buttons: dict[str, QToolButton] = {}

    def add_button(
        self,
        key: str,
        icon: str,
        tip: str,
        slot: Callable[[], None] | None = None,
        *,
        color: str = _TEXT,
    ) -> QToolButton:
        btn = cmd_icon_button(icon, tip, color=color)
        if slot is not None:
            btn.clicked.connect(slot)
        self.buttons[key] = btn
        self._lay.addWidget(btn)
        return btn

    def add_sep(self) -> None:
        self._lay.addWidget(cmd_separator())

    def add_stretch(self) -> None:
        self._lay.addStretch(1)

    def add_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent; padding:0 4px;"
        )
        lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._lay.addWidget(lbl)
        return lbl

    def add_widget(self, widget: QWidget) -> None:
        self._lay.addWidget(widget)

    def set_enabled(self, key: str, enabled: bool) -> None:
        btn = self.buttons.get(key)
        if btn is not None:
            btn.setEnabled(enabled)


class WindowsExplorerChrome(QWidget):
    """Windows 11 Explorer header: Back/Forward/Up/Refresh · address · search · ribbon.

    Signals:
      back_clicked / forward_clicked / up_clicked / refresh_clicked
      search_submitted(str)
      address_activated(str)  — user pressed Enter in the address field
    """

    back_clicked = Signal()
    forward_clicked = Signal()
    up_clicked = Signal()
    refresh_clicked = Signal()
    search_submitted = Signal(str)
    address_activated = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        search_placeholder: str = "Search",
        address: str = "",
        compact: bool = False,
        search_width: int = 260,
        framed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WindowsExplorerChrome")
        if framed:
            self.setStyleSheet(
                f"QWidget#WindowsExplorerChrome {{ background: {_BG}; border: 1px solid {_BORDER};"
                f" border-radius: 8px; }}"
            )
        else:
            self.setStyleSheet(
                f"QWidget#WindowsExplorerChrome {{ background: {_BG}; border: none; }}"
            )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Navigation + address + search ─────────────────────────────────
        nav_wrap = QWidget()
        nav_wrap.setStyleSheet(f"background: {_BG}; border: none;")
        nav = QHBoxLayout(nav_wrap)
        if compact:
            nav.setContentsMargins(6, 4, 6, 4)
            nav.setSpacing(4)
        else:
            nav.setContentsMargins(8, 8, 8, 6)
            nav.setSpacing(6)

        self.btn_back = cmd_icon_button("mdi.arrow-left", "Back")
        self.btn_forward = cmd_icon_button("mdi.arrow-right", "Forward")
        self.btn_up = cmd_icon_button("mdi.arrow-up", "Up")
        self.btn_refresh = cmd_icon_button("mdi.refresh", "Refresh")
        self.btn_back.clicked.connect(self.back_clicked.emit)
        self.btn_forward.clicked.connect(self.forward_clicked.emit)
        self.btn_up.clicked.connect(self.up_clicked.emit)
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        for b in (self.btn_back, self.btn_forward, self.btn_up, self.btn_refresh):
            nav.addWidget(b)

        # Address / breadcrumb bar
        addr_row = QHBoxLayout()
        addr_row.setContentsMargins(0, 0, 0, 0)
        addr_row.setSpacing(4)
        self._addr_icon = QLabel()
        self._addr_icon.setPixmap(
            qta.icon("mdi.desktop-classic", color=_MUTED).pixmap(16, 16)
        )
        self._addr_icon.setFixedSize(20, 20)
        self.address = QLineEdit()
        self.address.setText(address)
        self.address.setPlaceholderText("Path")
        self.address.setStyleSheet(
            f"QLineEdit {{"
            f"  background: {_CARD}; color: {_TEXT}; font-weight:600; font-size:12px; font-family:\"Segoe UI\";"
            f"  border: 1px solid {_BORDER}; border-radius: 4px; padding: 5px 8px;"
            f"}}"
            f"QLineEdit:focus {{ border: 1px solid {_ACCENT}; }}"
        )
        self.address.returnPressed.connect(self._emit_address)
        addr_host = QWidget()
        addr_host.setStyleSheet("background: transparent;")
        ah = QHBoxLayout(addr_host)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(0)
        # Put icon inside a framed field look via nested layout
        field = QFrame()
        field.setObjectName("AddrField")
        field.setStyleSheet(
            f"QFrame#AddrField {{ background: {_CARD}; border: 1px solid {_BORDER};"
            f" border-radius: 4px; }}"
        )
        fl = QHBoxLayout(field)
        pad_x = 6 if compact else 8
        pad_y = 3 if compact else 6
        fl.setContentsMargins(pad_x, 0, 4, 0)
        fl.setSpacing(4 if compact else 6)
        fl.addWidget(self._addr_icon)
        self.address.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {_TEXT};"
            f" font-weight:600; font-size:12px; font-family:\"Segoe UI\"; padding: {pad_y}px 4px; }}"
        )
        fl.addWidget(self.address, 1)
        ah.addWidget(field)
        nav.addWidget(addr_host, 3)

        # Search (Windows-style, right side)
        search_field = QFrame()
        search_field.setObjectName("SearchField")
        search_field.setStyleSheet(
            f"QFrame#SearchField {{ background: {_CARD}; border: 1px solid {_BORDER};"
            f" border-radius: 4px; }}"
        )
        search_field.setFixedWidth(max(140, int(search_width)))
        sf = QHBoxLayout(search_field)
        sf.setContentsMargins(6 if compact else 8, 0, 4 if compact else 6, 0)
        sf.setSpacing(2)
        self.search = QLineEdit()
        self.search.setPlaceholderText(search_placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {_TEXT};"
            f" font-size:12px; font-family:\"Segoe UI\"; padding: {pad_y}px 2px; }}"
        )
        self.search.returnPressed.connect(self._emit_search)
        search_btn = cmd_icon_button(
            "mdi.magnify", "Search", size=26 if compact else 28, icon_px=15
        )
        search_btn.clicked.connect(self._emit_search)
        sf.addWidget(self.search, 1)
        sf.addWidget(search_btn)
        nav.addWidget(search_field)

        root.addWidget(nav_wrap)

        # ── Ribbon ────────────────────────────────────────────────────────
        self.ribbon = WindowsCommandBar()
        if compact:
            self.ribbon.setStyleSheet(
                f"QWidget#WindowsCommandBar {{ background: {_CARD}; border: none;"
                f" border-top: 1px solid {_BORDER}; }}"
            )
            self.ribbon._lay.setContentsMargins(6, 2, 6, 2)
        root.addWidget(self.ribbon)

        self._history: list[str] = []
        self._hist_index = -1
        self._suppress_hist = False
        self.set_nav_enabled(back=False, forward=False, up=False)

    def _emit_search(self) -> None:
        self.search_submitted.emit(self.search.text().strip())

    def _emit_address(self) -> None:
        self.address_activated.emit(self.address.text().strip())

    def set_address(self, text: str, *, push_history: bool = True) -> None:
        """Update the address bar; optionally push onto back/forward history."""
        clean = (text or "").strip()
        self.address.setText(clean)
        if not push_history or self._suppress_hist:
            return
        if self._hist_index >= 0 and self._history and self._history[self._hist_index] == clean:
            return
        # Truncate forward stack
        if self._hist_index < len(self._history) - 1:
            self._history = self._history[: self._hist_index + 1]
        self._history.append(clean)
        self._hist_index = len(self._history) - 1
        self._sync_hist_buttons()

    def set_search_placeholder(self, text: str) -> None:
        self.search.setPlaceholderText(text)

    def set_address_icon(self, icon_name: str) -> None:
        self._addr_icon.setPixmap(
            qta.icon(icon_name, color=_MUTED).pixmap(16, 16)
        )

    def set_nav_enabled(
        self, *, back: bool | None = None, forward: bool | None = None, up: bool | None = None
    ) -> None:
        if back is not None:
            self.btn_back.setEnabled(back)
        if forward is not None:
            self.btn_forward.setEnabled(forward)
        if up is not None:
            self.btn_up.setEnabled(up)

    def _sync_hist_buttons(self) -> None:
        self.btn_back.setEnabled(self._hist_index > 0)
        self.btn_forward.setEnabled(self._hist_index < len(self._history) - 1)

    def history_back(self) -> str | None:
        if self._hist_index <= 0:
            return None
        self._hist_index -= 1
        self._sync_hist_buttons()
        path = self._history[self._hist_index]
        self._suppress_hist = True
        self.address.setText(path)
        self._suppress_hist = False
        return path

    def history_forward(self) -> str | None:
        if self._hist_index >= len(self._history) - 1:
            return None
        self._hist_index += 1
        self._sync_hist_buttons()
        path = self._history[self._hist_index]
        self._suppress_hist = True
        self.address.setText(path)
        self._suppress_hist = False
        return path
