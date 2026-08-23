"""CashierDashboard — Collapsible sidebar (navy palette matching accountant).

The ITEMS section is loaded dynamically from the DB (accountant-managed,
sidebar-flagged items). Items that have sub-items are expandable: clicking the
chevron reveals their sub-items. Clicking the item body opens the cashier's own
entries for that item (all sub-items combined); clicking a sub-item filters to
just that sub-item.
"""

from __future__ import annotations
import asyncio
from typing import Optional, Dict, List

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QToolButton, QSizePolicy, QSpacerItem,
)
from PySide6.QtCore import Qt, Signal, QSize

from tahmeed.models.user import User

# ── Design tokens (identical to accountant sidebar) ──────────────────────────────
_NAVY       = "#1B2B4B"
_ACTIVE_BG  = "#253A5C"
_SUB_BG     = "#16243f"   # slightly darker strip behind sub-items
_BLUE       = "#0077C5"
_WHITE      = "#F9FAFB"
_MUTED      = "#94A3B8"

EXPANDED_W  = 220
COLLAPSED_W = 64

# ── Nav sections ──────────────────────────────────────────────────────────────────
#  ITEMS is loaded dynamically from the DB; the list here is intentionally empty.
_SECTIONS: list[tuple[Optional[str], list[tuple]]] = [
    (None, [
        ("overview", "Overview", "mdi.view-dashboard-outline", {}),
        ("browse",   "Browse",   "mdi.magnify",                {}),
    ]),
    ("ENTRY", [
        ("table", "Table", "mdi.table-large", {}),
        ("form",  "Form",  "mdi.form-select", {}),
    ]),
    ("INBOX", [
        ("drafts",   "Drafts",   "mdi.file-document-edit-outline", {"badge": True}),
        ("rejected", "Rejected", "mdi.alert-circle-outline",       {}),
    ]),
    ("ITEMS", []),
]


def _qta(name: str, color: str) -> "QIcon":
    try:
        return qta.icon(name, color=color)
    except Exception:
        return qta.icon("mdi.circle-small", color=color)


class _NavItem(QWidget):
    """Single nav row: indicator | icon | label | [chevron]."""

    activated = Signal(str)
    toggle_requested = Signal(str)   # chevron clicked (expandable items only)

    def __init__(self, key: str, label: str, icon_name: str,
                 expandable: bool = False, badge: bool = False, parent=None):
        super().__init__(parent)
        self._key = key
        self._icon_name = icon_name
        self._active = False
        self._expandable = expandable
        self._expanded = False
        self._has_subtables = False
        self._is_collapsed = False
        self._has_badge = badge
        self._badge_count = 0
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self._build(label, expandable, badge)
        self._paint(hover=False)

    def _build(self, label: str, expandable: bool, badge: bool) -> None:
        self._label = label
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.setSpacing(0)

        # Stretches activate only while collapsed (centers the icon in the rail).
        self._left_stretch = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        hl.addSpacerItem(self._left_stretch)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        hl.addWidget(self._indicator)

        self._gap1 = QSpacerItem(10, 1, QSizePolicy.Fixed, QSizePolicy.Minimum)
        hl.addSpacerItem(self._gap1)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(18, 18)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        hl.addWidget(self._icon_lbl)

        self._gap2 = QSpacerItem(10, 1, QSizePolicy.Fixed, QSizePolicy.Minimum)
        hl.addSpacerItem(self._gap2)

        self._text_lbl = QLabel(label)
        self._text_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 13px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

        self._badge_lbl: Optional[QLabel] = None
        if badge:
            self._badge_lbl = QLabel("0")
            self._badge_lbl.setAlignment(Qt.AlignCenter)
            self._badge_lbl.setFixedHeight(18)
            self._badge_lbl.setMinimumWidth(24)
            self._badge_lbl.setStyleSheet(
                "background:#B45309;color:#FFFBEB;border-radius:9px;"
                "font-size:10px;font-weight:700;font-family:'Segoe UI';padding:0 6px;"
            )
            self._badge_lbl.setVisible(False)
            hl.addWidget(self._badge_lbl)

        self._chevron_lbl: Optional[QLabel] = None
        if expandable:
            self._chevron_lbl = QLabel()
            self._chevron_lbl.setFixedSize(16, 16)
            self._chevron_lbl.setAlignment(Qt.AlignCenter)
            self._chevron_lbl.setStyleSheet("background: transparent;")
            self._chevron_lbl.setPixmap(_qta("mdi.chevron-right", color=_MUTED).pixmap(14, 14))
            self._chevron_lbl.setVisible(False)   # hidden until sub-items confirmed
            hl.addWidget(self._chevron_lbl)

        self._right_stretch = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        hl.addSpacerItem(self._right_stretch)

        self._refresh_icon(active=False)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)
        self._refresh_icon(active=active)

    def set_has_subtables(self, has: bool) -> None:
        self._has_subtables = has
        if self._chevron_lbl:
            self._chevron_lbl.setVisible(has and not self._is_collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Icon-rail mode: hide chrome/labels and center a single icon."""
        self._is_collapsed = collapsed
        self._text_lbl.setVisible(not collapsed)
        self._indicator.setVisible(not collapsed)
        if self._badge_lbl:
            self._badge_lbl.setVisible(not collapsed and self._badge_count > 0)
        if self._chevron_lbl:
            self._chevron_lbl.setVisible(self._has_subtables and not collapsed)

        px = 22 if collapsed else 18
        self._icon_lbl.setFixedSize(px, px)
        self._refresh_icon(active=self._active)

        lay = self.layout()
        if collapsed:
            # Force a narrow sizeHint so QScrollArea cannot keep a 220px min width.
            self._text_lbl.setMaximumWidth(0)
            self._text_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lay.setContentsMargins(0, 0, 0, 0)
            self._gap1.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self._gap2.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self._left_stretch.changeSize(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum)
            self._right_stretch.changeSize(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum)
            self.setFixedWidth(COLLAPSED_W)
            self.setToolTip(self._label)
        else:
            self._text_lbl.setMaximumWidth(16777215)
            self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lay.setContentsMargins(0, 0, 8, 0)
            self._gap1.changeSize(10, 1, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self._gap2.changeSize(10, 1, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self._left_stretch.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self._right_stretch.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setToolTip("")
        lay.invalidate()
        lay.activate()
        self.updateGeometry()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        if self._chevron_lbl:
            icon = "mdi.chevron-down" if expanded else "mdi.chevron-right"
            self._chevron_lbl.setPixmap(_qta(icon, color=_MUTED).pixmap(14, 14))

    def set_badge(self, count: int) -> None:
        self._badge_count = max(0, int(count))
        if self._badge_lbl:
            self._badge_lbl.setText(str(self._badge_count))
            self._badge_lbl.setVisible(
                not self._is_collapsed and self._badge_count > 0
            )

    def _refresh_icon(self, active: bool) -> None:
        color = _WHITE if active else _MUTED
        px = 22 if self._is_collapsed else 18
        self._icon_lbl.setPixmap(_qta(self._icon_name, color=color).pixmap(px, px))

    def _paint(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet(f"background: {_BLUE};")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px; font-weight: 600;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        else:
            self.setStyleSheet("background: transparent;")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 13px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )

    def enterEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=True)
            self._refresh_icon(active=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=False)
            self._refresh_icon(active=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if (self._expandable and self._chevron_lbl is not None
                    and self._chevron_lbl.isVisible()
                    and event.position().x() >= self._chevron_lbl.x() - 4):
                self.toggle_requested.emit(self._key)
            else:
                self.activated.emit(self._key)
        super().mousePressEvent(event)


class _SubNavItem(QWidget):
    """Indented, read-only sub-item row beneath an expandable parent."""

    activated = Signal(object)   # passes self

    def __init__(self, parent_key: str, parent_category: str, name: str,
                 match: str = "", icon_name: str = "", parent=None):
        super().__init__(parent)
        self.parent_key = parent_key
        self.parent_category = parent_category
        self.name = name
        self.match = match or name
        self._icon_name = icon_name
        self._active = False
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self._build()
        self._paint(hover=False)

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.setSpacing(0)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        hl.addWidget(self._indicator)

        hl.addSpacing(38)   # indent under the parent icon+label

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(15, 15)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        icon = self._icon_name or "mdi.circle-medium"
        self._icon_lbl.setPixmap(_qta(icon, color=_MUTED).pixmap(15, 15))
        hl.addWidget(self._icon_lbl)

        hl.addSpacing(8)

        self._text_lbl = QLabel(self.name)
        self._text_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 12px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)

    def _refresh_icon(self, bright: bool) -> None:
        icon = self._icon_name or "mdi.circle-medium"
        self._icon_lbl.setPixmap(
            _qta(icon, color=_WHITE if bright else _MUTED).pixmap(15, 15)
        )

    def _paint(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet(f"background: {_BLUE};")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 12px; font-weight: 600;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 12px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        else:
            self.setStyleSheet(f"background: {_SUB_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 12px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        self._refresh_icon(bright=self._active or hover)

    def enterEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self)
        super().mousePressEvent(event)


class _SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; font-weight: 600;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
            " padding: 6px 16px 2px 16px; letter-spacing: 1px;"
        )


class CashierSidebarWidget(QFrame):
    """Collapsible cashier sidebar — same visual style as accountant sidebar."""

    nav_selected = Signal(str)
    subtable_selected = Signal(str, str, str, str)

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._collapsed = False
        self._items: Dict[str, _NavItem] = {}
        self._section_labels: List[_SectionLabel] = []
        self._separators: List[QFrame] = []
        self._child_containers: Dict[str, QWidget] = {}
        self._expanded: set[str] = set()
        self._loaded: set[str] = set()
        self._active_obj = None                  # active _NavItem or _SubNavItem
        self._item_keys: set[str] = set()        # keys of dynamic ITEMS rows
        self._item_defs: Dict[str, tuple] = {}   # key -> (name, icon, sidebar_label)
        self._items_host_vl = None
        self._items_empty_hint: Optional[QLabel] = None
        self._scroll: Optional[QScrollArea] = None
        self._nav_container: Optional[QWidget] = None
        self._build()
        self.select("overview")
        asyncio.ensure_future(self._load_items())

    def _build(self) -> None:
        self.setObjectName("cashierSidebar")
        self.setFixedWidth(EXPANDED_W)
        self.setStyleSheet(
            f"QFrame#cashierSidebar {{ background: {_NAVY}; border: none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable nav ────────────────────────────────────────────────────
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_NAVY}; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: {_NAVY}; }}
            QScrollBar:vertical {{
                background: {_NAVY}; width: 4px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(148,163,184,0.25);
                border-radius: 2px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        container = QWidget()
        self._nav_container = container
        container.setStyleSheet(f"background: {_NAVY};")
        vl = QVBoxLayout(container)
        vl.setContentsMargins(0, 6, 0, 6)
        vl.setSpacing(0)

        for section_label, items in _SECTIONS:
            if section_label is not None:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setFixedHeight(1)
                sep.setStyleSheet("background: rgba(148,163,184,0.12);")
                vl.addSpacing(6)
                vl.addWidget(sep)
                self._separators.append(sep)

                lbl = _SectionLabel(section_label)
                vl.addWidget(lbl)
                self._section_labels.append(lbl)

            if section_label == "ITEMS":
                items_host = QWidget()
                items_host.setStyleSheet(f"background: {_NAVY};")
                self._items_host_vl = QVBoxLayout(items_host)
                self._items_host_vl.setContentsMargins(0, 0, 0, 0)
                self._items_host_vl.setSpacing(0)
                vl.addWidget(items_host)
                continue

            for key, label, icon_name, opts in items:
                nav = _NavItem(
                    key=key,
                    label=label,
                    icon_name=icon_name,
                    badge=opts.get("badge", False),
                )
                nav.activated.connect(self._on_item_clicked)
                self._items[key] = nav
                vl.addWidget(nav)

        vl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # ── Collapse button ───────────────────────────────────────────────────
        bottom_sep = QFrame()
        bottom_sep.setFrameShape(QFrame.HLine)
        bottom_sep.setFixedHeight(1)
        bottom_sep.setStyleSheet("background: rgba(148,163,184,0.15);")
        root.addWidget(bottom_sep)

        self._collapse_btn = QToolButton()
        self._collapse_btn.setFixedHeight(40)
        self._collapse_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._collapse_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._collapse_btn.setIcon(_qta("mdi.chevron-left", color=_MUTED))
        self._collapse_btn.setIconSize(QSize(16, 16))
        self._collapse_btn.setText("  Collapse")
        self._collapse_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent; border: none;
                color: {_MUTED}; font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 14px; text-align: left;
            }}
            QToolButton:hover {{ background: {_ACTIVE_BG}; color: {_WHITE}; }}
        """)
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.clicked.connect(self.toggle_collapsed)
        root.addWidget(self._collapse_btn)

    # ── Public API ─────────────────────────────────────────────────────────────

    def _clear_active(self) -> None:
        if self._active_obj is not None:
            try:
                self._active_obj.set_active(False)
            except RuntimeError:
                pass
        self._active_obj = None

    def select(self, key: str) -> None:
        """Activate a top-level nav item (clears any sub-item active)."""
        self._clear_active()
        nav = self._items.get(key)
        if nav:
            nav.set_active(True)
            self._active_obj = nav

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        w = COLLAPSED_W if self._collapsed else EXPANDED_W
        self.setFixedWidth(w)

        # Keep scroll content locked to the rail width so icons are not clipped.
        if self._nav_container is not None:
            if self._collapsed:
                self._nav_container.setFixedWidth(w)
            else:
                self._nav_container.setMinimumWidth(0)
                self._nav_container.setMaximumWidth(16777215)
        if self._scroll is not None:
            if self._collapsed:
                self._scroll.setFixedWidth(w)
            else:
                self._scroll.setMinimumWidth(0)
                self._scroll.setMaximumWidth(16777215)

        for item in self._items.values():
            item.set_collapsed(self._collapsed)
        for lbl in self._section_labels:
            lbl.setVisible(not self._collapsed)
        for sep in self._separators:
            sep.setVisible(not self._collapsed)
        if self._items_empty_hint is not None:
            self._items_empty_hint.setVisible(not self._collapsed)
        # Hide sub-item strips while collapsed; restore expanded ones after.
        for key, child in self._child_containers.items():
            child.setVisible(not self._collapsed and key in self._expanded)
        if self._collapsed:
            self._collapse_btn.setText("")
            self._collapse_btn.setIcon(_qta("mdi.chevron-right", color=_MUTED))
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            self._collapse_btn.setStyleSheet(f"""
                QToolButton {{
                    background: transparent; border: none;
                    color: {_MUTED}; padding: 0;
                }}
                QToolButton:hover {{ background: {_ACTIVE_BG}; color: {_WHITE}; }}
            """)
        else:
            self._collapse_btn.setText("  Collapse")
            self._collapse_btn.setIcon(_qta("mdi.chevron-left", color=_MUTED))
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self._collapse_btn.setStyleSheet(f"""
                QToolButton {{
                    background: transparent; border: none;
                    color: {_MUTED}; font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                    padding: 0 14px; text-align: left;
                }}
                QToolButton:hover {{ background: {_ACTIVE_BG}; color: {_WHITE}; }}
            """)

    def set_drafts_badge(self, count: int) -> None:
        if "drafts" in self._items:
            self._items["drafts"].set_badge(count)

    # ── Internal: top-level nav ────────────────────────────────────────────────

    def _on_item_clicked(self, key: str) -> None:
        self.select(key)
        self.nav_selected.emit(key)
        # Auto-expand when this item has confirmed sub-items.
        nav = self._items.get(key)
        if (key in self._child_containers and key not in self._expanded
                and nav is not None and nav._has_subtables):
            self._on_toggle(key)

    # ── Internal: expand / collapse ────────────────────────────────────────────

    def _on_toggle(self, key: str) -> None:
        if self._collapsed or key not in self._child_containers:
            return
        if key in self._expanded:
            self._expanded.discard(key)
            self._child_containers[key].setVisible(False)
            self._items[key].set_expanded(False)
        else:
            self._expanded.add(key)
            self._items[key].set_expanded(True)
            self._child_containers[key].setVisible(True)
            if key not in self._loaded:
                asyncio.ensure_future(self._load_children(key))

    async def _load_children(self, key: str) -> None:
        from tahmeed.services.subtable_service import get_subtables
        try:
            subs = await get_subtables(key)
        except Exception:
            subs = []
        self._loaded.add(key)
        self._rebuild_children(key, subs)

    def _rebuild_children(self, key: str, subs) -> None:
        container = self._child_containers.get(key)
        if container is None:
            return
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        parent_category = (
            self._items[key]._text_lbl.text() if key in self._items else key
        )

        if key in self._items:
            self._items[key].set_has_subtables(len(subs) > 0)

        if not subs:
            self._expanded.discard(key)
            container.setVisible(False)
            if key in self._items:
                self._items[key].set_expanded(False)
            return

        for sub in subs:
            row = _SubNavItem(
                parent_key=key,
                parent_category=sub.parent_category or parent_category,
                name=sub.name,
                match=sub.match,
            )
            row.activated.connect(self._on_subitem_clicked)
            layout.addWidget(row)

    def _on_subitem_clicked(self, row: _SubNavItem) -> None:
        self._clear_active()
        row.set_active(True)
        self._active_obj = row
        self.subtable_selected.emit(
            row.parent_key, row.parent_category, row.name, row.match
        )

    # ── Dynamic ITEMS rows (accountant-managed) ────────────────────────────────

    def item_def(self, key: str):
        """Return (name, icon) for a dynamic item key, or None."""
        return self._item_defs.get(key)

    def refresh_items(self) -> None:
        asyncio.ensure_future(self._load_items())

    async def _load_items(self) -> None:
        from tahmeed.services.category_service import get_cashier_sidebar_categories
        try:
            cats = await get_cashier_sidebar_categories()
        except Exception:
            cats = []
        self._rebuild_items(cats)

    def _rebuild_items(self, cats: list) -> None:
        from tahmeed.services.category_service import item_key as _ik
        if self._items_host_vl is None:
            return

        for key in self._item_keys:
            self._items.pop(key, None)
            self._child_containers.pop(key, None)
            self._expanded.discard(key)
            self._loaded.discard(key)
            self._item_defs.pop(key, None)
        self._item_keys = set()

        while self._items_host_vl.count():
            it = self._items_host_vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        if not cats:
            hint = QLabel("  No items yet")
            hint.setStyleSheet(
                f"color: {_MUTED}; font-size: 11px; font-style: italic;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
                " padding: 4px 16px;"
            )
            hint.setVisible(not self._collapsed)
            self._items_empty_hint = hint
            self._items_host_vl.addWidget(hint)
            return

        self._items_empty_hint = None

        for cat in cats:
            key = _ik(cat.name)
            if not key or key in self._item_keys:
                continue
            icon = cat.icon or "mdi.tag-outline"
            label = cat.sidebar_label
            self._item_defs[key] = (cat.name, icon, label)
            self._item_keys.add(key)
            self._add_item_row(key, label, icon)

        if self._collapsed:
            for key in self._item_keys:
                self._items[key].set_collapsed(True)

        asyncio.ensure_future(self._load_item_chevrons())

    def _add_item_row(self, key: str, label: str, icon_name: str) -> None:
        nav = _NavItem(key=key, label=label, icon_name=icon_name, expandable=True)
        nav.activated.connect(self._on_item_clicked)
        nav.toggle_requested.connect(self._on_toggle)
        self._items[key] = nav
        self._items_host_vl.addWidget(nav)

        child = QWidget()
        child.setStyleSheet(f"background: {_SUB_BG};")
        cvl = QVBoxLayout(child)
        cvl.setContentsMargins(0, 0, 0, 0)
        cvl.setSpacing(0)
        child.setVisible(False)
        self._child_containers[key] = child
        self._items_host_vl.addWidget(child)

    async def _load_item_chevrons(self) -> None:
        from tahmeed.services.subtable_service import get_subtables
        for key in list(self._item_keys):
            if key not in self._items:
                continue
            try:
                subs = await get_subtables(key)
                self._items[key].set_has_subtables(len(subs) > 0)
            except Exception:
                pass
