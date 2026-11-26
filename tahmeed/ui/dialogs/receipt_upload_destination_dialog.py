"""Upload destination — dense Windows Save As–style park / Records picker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

import qtawesome as qta
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.api_client import ApiError
from tahmeed.services.receipt_records_service import (
    create_custom_folder,
    list_destinations,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.popup_styles import with_light_popups
from tahmeed.ui.widgets.windows_command_bar import WindowsExplorerChrome

_BG = "#F0F0F0"
_CARD = "#FFFFFF"
_BORDER = "#D0D0D0"
_TEXT = "#1A1A1A"
_MUTED = "#5A5A5A"
_ACCENT = "#0077C5"
_FOLDER = "#D97706"

_KIND_ICONS: dict[str, tuple[str, str]] = {
    "custom": ("mdi.folder-star", _FOLDER),
    "item": ("mdi.package-variant-closed", _ACCENT),
    "supplier": ("mdi.truck-outline", "#0F766E"),
    "separate": ("mdi.file-cabinet", "#7C3AED"),
}
_KINDS: tuple[tuple[str, str], ...] = (
    ("custom", "Custom"),
    ("item", "Items"),
    ("supplier", "Suppliers"),
    ("separate", "Separate"),
)

_BTN_GHOST = with_light_popups(
    f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:3px;padding:4px 14px;font-weight:600; font-size:12px; font-family:\"Segoe UI\";min-height:26px;}}"
    f"QPushButton:hover{{background:#F5F5F5;}}"
)
_BTN_PRIMARY = with_light_popups(
    f"QPushButton{{background:{_ACCENT};color:#FFFFFF;border:none;"
    f"border-radius:3px;padding:4px 16px;font-weight:600; font-size:12px; font-family:\"Segoe UI\";min-height:26px;}}"
    f"QPushButton:hover{{background:#005EA3;}}"
    f"QPushButton:disabled{{background:#93C5FD;}}"
)
_INPUT_SS = (
    f"QLineEdit,QDateEdit,QComboBox{{font-size:12px; font-family:\"Segoe UI\";color:{_TEXT};"
    f"border:1px solid {_BORDER};border-radius:3px;padding:2px 6px;"
    f"background:{_CARD};min-height:24px;}}"
)
_COMBO_SS = with_combo_popup(_INPUT_SS)
_RIBBON_DATE = (
    "QDateEdit{"
    f"font-size:11px; font-family:\"Segoe UI\";color:{_TEXT};"
    f"border:1px solid {_BORDER};border-radius:3px;padding:1px 4px;"
    f"background:{_CARD};min-height:22px;max-height:24px;min-width:92px;}}"
)


def _tree_stylesheet() -> str:
    from PySide6.QtWidgets import QApplication

    closed_url = opened_url = ""
    if QApplication.instance() is not None:
        cache = Path(__file__).resolve().parents[2] / ".cache" / "tree_branch"
        cache.mkdir(parents=True, exist_ok=True)
        closed = cache / "chevron_right.png"
        opened = cache / "chevron_down.png"
        if not closed.exists():
            qta.icon("mdi.chevron-right", color=_MUTED).pixmap(10, 10).save(str(closed))
        if not opened.exists():
            qta.icon("mdi.chevron-down", color=_MUTED).pixmap(10, 10).save(str(opened))
        closed_url, opened_url = closed.as_posix(), opened.as_posix()
    branch_closed = f'image: url("{closed_url}");' if closed_url else "image: none;"
    branch_open = f'image: url("{opened_url}");' if opened_url else "image: none;"
    return with_light_popups(f"""
QTreeWidget {{
  background:{_CARD}; border:none; font-size:12px; font-family:\"Segoe UI\"; color:{_TEXT};
  outline:none; padding:0; show-decoration-selected:0;
}}
QTreeWidget::item {{
  padding:1px 4px; border-radius:2px; min-height:20px;
  border: none; outline: none;
}}
QTreeWidget::item:selected,
QTreeWidget::item:selected:active,
QTreeWidget::item:selected:!active {{
  background:#CCE8FF; color:{_TEXT}; border: none; outline: none;
}}
QTreeWidget::item:hover:!selected {{ background:#F2F2F2; }}
QTreeWidget::branch {{
  background: transparent; border: none; border-image: none;
}}
QTreeWidget::branch:selected,
QTreeWidget::branch:selected:active {{
  background: transparent; border: none;
}}
QTreeWidget::branch:has-siblings:!adjoins-item,
QTreeWidget::branch:!has-children:!has-siblings:adjoins-item,
QTreeWidget::branch:!has-children:has-siblings:adjoins-item {{
  border-image: none; image: none; background: transparent;
}}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {{
  border-image: none; background: transparent; {branch_closed}
}}
QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings {{
  border-image: none; background: transparent; {branch_open}
}}
""")


_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  background:{_CARD}; border:none; font-size:12px; font-family:\"Segoe UI\"; color:{_TEXT};
  gridline-color:transparent; outline:none;
  selection-background-color:#CCE8FF; selection-color:{_TEXT};
}}
QHeaderView::section {{
  background:#F5F5F5; color:{_MUTED}; font-weight:600; font-size:11px; font-family:\"Segoe UI\";
  border:none; border-bottom:1px solid {_BORDER}; padding:3px 8px;
}}
""")

UploadMode = Literal["park", "records"]

_KIND_DEST_LABEL = {
    "item": "Item",
    "supplier": "Supplier",
    "separate": "Separate expense",
    "custom": "Custom folder",
}


@dataclass(frozen=True)
class UploadDestinationChoice:
    mode: UploadMode
    kind: str = ""
    ref_key: str = ""
    folder_id: str = ""
    label: str = ""
    note: str = ""
    file_date: str = ""


class ReceiptUploadDestinationDialog(QDialog):
    """Dense Save As picker: Records tree fills the window; park is a save-type."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        file_count: int = 1,
        allow_records: bool = True,
    ) -> None:
        super().__init__(parent)
        self._allow_records = bool(allow_records)
        self._file_count = max(1, int(file_count))
        self._choice: UploadDestinationChoice | None = None
        self._accepting = False
        self._kind = "item"
        self._selected: dict[str, Any] | None = None
        self._kind_roots: dict[str, QTreeWidgetItem] = {}
        self._expanded_kinds: set[str] = {"item"}
        self._content_rows: list[dict[str, Any]] = []
        self._filter = ""
        self._destinations: dict[str, list[dict[str, Any]]] = {
            "custom": [],
            "item": [],
            "supplier": [],
            "separate": [],
        }
        self.setWindowTitle("Save to…")
        self.setModal(True)
        self.setStyleSheet(f"QDialog{{background:{_BG};}}")
        if self._allow_records:
            self.setMinimumSize(640, 420)
            self.resize(700, 460)
        else:
            self.setMinimumSize(360, 140)
            self.resize(420, 150)
        self._build()
        if self._allow_records:
            asyncio.ensure_future(self._load_destinations())

    def result_choice(self) -> UploadDestinationChoice | None:
        return self._choice

    def _is_park(self) -> bool:
        if not self._allow_records:
            return True
        return str(self._save_type.currentData() or "records") == "park"

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if not self._allow_records:
            self._build_park_only(root)
            return

        # Explorer fills the dialog (no outer padding void)
        self._chrome = WindowsExplorerChrome(
            search_placeholder="Search folders",
            address="Records",
            compact=True,
            search_width=180,
            framed=False,
        )
        self._chrome.set_address_icon("mdi.folder-multiple-outline")
        self._chrome.back_clicked.connect(self._nav_back)
        self._chrome.forward_clicked.connect(self._nav_forward)
        self._chrome.up_clicked.connect(self._go_up)
        self._chrome.refresh_clicked.connect(
            lambda: asyncio.ensure_future(self._load_destinations())
        )
        self._chrome.search_submitted.connect(self._on_search)
        self._chrome.search.textChanged.connect(self._on_search_live)

        ribbon = self._chrome.ribbon
        ribbon.add_button(
            "new",
            "mdi.folder-plus-outline",
            "New folder",
            lambda: asyncio.ensure_future(self._new_folder()),
        )
        ribbon.add_button(
            "sub",
            "mdi.folder-pound-outline",
            "New subfolder",
            lambda: asyncio.ensure_future(self._new_subfolder()),
        )
        ribbon.add_sep()
        ribbon.add_label("File as")
        self._file_date = QDateEdit()
        self._file_date.setCalendarPopup(True)
        self._file_date.setDisplayFormat("yyyy-MM-dd")
        self._file_date.setDate(QDate.currentDate())
        self._file_date.setFixedHeight(24)
        self._file_date.setStyleSheet(_RIBBON_DATE)
        self._file_date.setToolTip("Records browse month (YYYY/MM)")
        style_calendar_popup(self._file_date)
        ribbon.add_widget(self._file_date)
        ribbon.add_sep()
        ribbon.add_label("Note")
        self._note = QLineEdit()
        self._note.setPlaceholderText("Optional")
        self._note.setFixedHeight(24)
        self._note.setFixedWidth(140)
        self._note.setStyleSheet(_INPUT_SS)
        ribbon.add_widget(self._note)
        ribbon.add_stretch()
        n = self._file_count
        count_lbl = QLabel(f"{n} file{'s' if n != 1 else ''}")
        count_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px; font-family:\"Segoe UI\";background:transparent;padding:0 6px;"
        )
        ribbon.add_widget(count_lbl)
        root.addWidget(self._chrome)

        # Split: nav | contents — edge to edge
        split_host = QFrame()
        split_host.setStyleSheet(
            f"QFrame{{background:{_CARD};border-top:1px solid {_BORDER};"
            f"border-bottom:1px solid {_BORDER};}}"
        )
        split_lay = QHBoxLayout(split_host)
        split_lay.setContentsMargins(0, 0, 0, 0)
        split_lay.setSpacing(0)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.setStyleSheet(
            f"QSplitter::handle{{background:{_BORDER};width:1px;}}"
        )

        left = QWidget()
        left.setStyleSheet(f"background:{_CARD};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(2, 2, 2, 2)
        left_lay.setSpacing(0)
        self._tree = QTreeWidget()
        self._tree.setStyleSheet(_tree_stylesheet())
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setAnimated(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(12)
        self._tree.setIconSize(QSize(16, 16))
        self._tree.setUniformRowHeights(True)
        self._tree.setAllColumnsShowFocus(False)
        self._tree.setFrameShape(QFrame.NoFrame)
        self._tree.currentItemChanged.connect(self._on_tree_changed)
        self._tree.itemExpanded.connect(self._on_kind_expanded)
        self._tree.itemCollapsed.connect(self._on_kind_collapsed)
        self._tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        left_lay.addWidget(self._tree, 1)
        split.addWidget(left)

        right = QWidget()
        right.setStyleSheet(f"background:{_CARD};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Type"])
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setIconSize(QSize(16, 16))
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._table.cellDoubleClicked.connect(self._on_content_double_clicked)
        self._table.itemSelectionChanged.connect(self._on_content_selection)
        right_lay.addWidget(self._table, 1)
        split.addWidget(right)
        split.setSizes([200, 480])
        split_lay.addWidget(split)
        root.addWidget(split_host, 1)

        # Footer — classic Save As: Save in + Save as type + buttons
        foot = QWidget()
        foot.setStyleSheet(f"background:{_BG};")
        foot_lay = QVBoxLayout(foot)
        foot_lay.setContentsMargins(10, 8, 10, 10)
        foot_lay.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        save_in_lbl = QLabel("Save in:")
        save_in_lbl.setFixedWidth(78)
        save_in_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:12px; font-family:\"Segoe UI\";background:transparent;"
        )
        row1.addWidget(save_in_lbl)
        self._dest_path = QLineEdit()
        self._dest_path.setReadOnly(True)
        self._dest_path.setPlaceholderText("Select a folder")
        self._dest_path.setStyleSheet(_INPUT_SS)
        row1.addWidget(self._dest_path, 1)
        foot_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        type_lbl = QLabel("Save as type:")
        type_lbl.setFixedWidth(78)
        type_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:12px; font-family:\"Segoe UI\";background:transparent;"
        )
        row2.addWidget(type_lbl)
        self._save_type = QComboBox()
        self._save_type.setStyleSheet(_COMBO_SS)
        self._save_type.addItem("File to Records (folder)", "records")
        self._save_type.addItem("Park in Receipts inbox", "park")
        self._save_type.currentIndexChanged.connect(self._sync_save_type)
        row2.addWidget(self._save_type, 1)
        foot_lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{_MUTED};font-size:11px; font-family:\"Segoe UI\";background:transparent;"
        )
        row3.addWidget(self._status, 1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(_BTN_GHOST)
        cancel.clicked.connect(self.reject)
        self._ok_btn = QPushButton("Save")
        self._ok_btn.setIcon(qta.icon("mdi.content-save-outline", color="#FFFFFF"))
        self._ok_btn.setIconSize(QSize(14, 14))
        self._ok_btn.setCursor(Qt.PointingHandCursor)
        self._ok_btn.setStyleSheet(_BTN_PRIMARY)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(lambda: asyncio.ensure_future(self._accept_async()))
        row3.addWidget(cancel)
        row3.addWidget(self._ok_btn)
        foot_lay.addLayout(row3)
        root.addWidget(foot)

        self._sync_save_type()
        self._sync_folder_actions()

    def _build_park_only(self, root: QVBoxLayout) -> None:
        wrap = QVBoxLayout()
        wrap.setContentsMargins(14, 12, 14, 12)
        wrap.setSpacing(8)
        n = self._file_count
        msg = QLabel(
            f"Park {n} file{'s' if n != 1 else ''} in the Receipts inbox "
            "(link to an expense later)."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color:{_TEXT};font-size:12px; font-family:\"Segoe UI\";")
        wrap.addWidget(msg)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(_BTN_GHOST)
        cancel.clicked.connect(self.reject)
        self._ok_btn = QPushButton("Park")
        self._ok_btn.setIcon(qta.icon("mdi.file-upload-outline", color="#FFFFFF"))
        self._ok_btn.setIconSize(QSize(14, 14))
        self._ok_btn.setStyleSheet(_BTN_PRIMARY)
        self._ok_btn.clicked.connect(lambda: asyncio.ensure_future(self._accept_async()))
        actions.addWidget(cancel)
        actions.addWidget(self._ok_btn)
        wrap.addLayout(actions)
        # placeholders so attribute access is safe
        self._save_type = None  # type: ignore[assignment]
        self._file_date = QDateEdit()
        self._file_date.setDate(QDate.currentDate())
        self._note = QLineEdit()
        self._status = QLabel("")
        self._dest_path = QLineEdit()
        root.addLayout(wrap)

    def _sync_save_type(self) -> None:
        if not self._allow_records:
            return
        park = self._is_park()
        # Dim explorer when parking — still visible so the dialog never looks empty
        self._tree.setEnabled(not park)
        self._table.setEnabled(not park)
        self._chrome.setEnabled(not park)
        if park:
            self._dest_path.setText("Receipts inbox (park)")
            self._ok_btn.setText("Park")
            self._ok_btn.setIcon(qta.icon("mdi.file-upload-outline", color="#FFFFFF"))
            self._status.setText("Files land in Inbox — link to an expense when ready.")
        else:
            self._ok_btn.setText("Save")
            self._ok_btn.setIcon(qta.icon("mdi.content-save-outline", color="#FFFFFF"))
            self._sync_address()
            self._fill_contents()

    # ── Tree / destinations ───────────────────────────────────────────────

    async def _load_destinations(self) -> None:
        try:
            payload = await list_destinations()
            self._destinations = {
                "custom": list(payload.get("custom") or []),
                "item": list(payload.get("items") or []),
                "supplier": list(payload.get("suppliers") or []),
                "separate": list(payload.get("separate") or []),
            }
            self._populate_tree()
            if not self._is_park():
                self._status.setText(
                    f"Custom {len(self._destinations['custom'])} · "
                    f"Items {len(self._destinations['item'])} · "
                    f"Suppliers {len(self._destinations['supplier'])} · "
                    f"Separate {len(self._destinations['separate'])}"
                )
        except Exception as exc:
            self._status.setText(f"Could not load destinations: {exc}")

    def _make_tree_item(self, data: dict[str, Any], *, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setToolTip(0, str(data.get("display_label") or label))
        item.setData(0, Qt.UserRole, data)
        if data.get("_kind_root"):
            icon_name, color = _KIND_ICONS.get(
                str(data.get("kind") or ""), ("mdi.folder", _FOLDER)
            )
            item.setIcon(0, qta.icon(icon_name, color=color))
        else:
            item.setIcon(0, qta.icon("mdi.folder", color=_FOLDER))
        return item

    def _row_matches(self, row: dict[str, Any], needle: str) -> bool:
        if not needle:
            return True
        hay = " ".join(
            str(row.get(k) or "")
            for k in ("label", "ref_key", "display_label", "slug")
        ).casefold()
        path = " ".join(str(x) for x in (row.get("path_labels") or [])).casefold()
        return needle in hay or needle in path

    def _visible_rows(self, kind: str) -> list[dict[str, Any]]:
        rows = list(self._destinations.get(kind) or [])
        needle = self._filter
        if kind == "custom":
            by_id = {
                str(r.get("id") or ""): r for r in rows if str(r.get("id") or "")
            }
            keep: set[str] = set()
            for row in rows:
                fid = str(row.get("id") or "")
                if not fid or not self._row_matches(row, needle):
                    continue
                keep.add(fid)
                parent_id = str(row.get("parent_id") or "")
                while parent_id and parent_id in by_id:
                    if parent_id in keep:
                        break
                    keep.add(parent_id)
                    parent_id = str(by_id[parent_id].get("parent_id") or "")
            return [
                {**r, "kind": "custom"}
                for r in rows
                if str(r.get("id") or "") in keep
            ]
        return [
            {**row, "kind": kind}
            for row in rows
            if self._row_matches(row, needle)
        ]

    def _add_custom_children(
        self, parent: QTreeWidgetItem, visible: list[dict[str, Any]]
    ) -> None:
        by_id = {
            str(r.get("id") or ""): r for r in visible if str(r.get("id") or "")
        }
        nodes: dict[str, QTreeWidgetItem] = {}
        pending = list(visible)
        guard = 0
        while pending and guard < len(visible) + 5:
            guard += 1
            next_pending: list[dict[str, Any]] = []
            for row in pending:
                fid = str(row.get("id") or "")
                if not fid:
                    continue
                parent_id = str(row.get("parent_id") or "")
                leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                node = self._make_tree_item(
                    {**row, "kind": "custom", "_all": False}, label=leaf
                )
                if parent_id and parent_id in nodes:
                    nodes[parent_id].addChild(node)
                    nodes[fid] = node
                elif not parent_id or parent_id not in by_id:
                    parent.addChild(node)
                    nodes[fid] = node
                else:
                    next_pending.append(row)
            if len(next_pending) == len(pending):
                for row in next_pending:
                    fid = str(row.get("id") or "")
                    if not fid or fid in nodes:
                        continue
                    leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                    node = self._make_tree_item(
                        {**row, "kind": "custom", "_all": False}, label=leaf
                    )
                    parent.addChild(node)
                    nodes[fid] = node
                break
            pending = next_pending

    def _populate_tree(self) -> None:
        if not self._allow_records:
            return
        tree = self._tree
        prev_kind = self._kind
        prev = dict(self._selected) if self._selected else None
        tree.blockSignals(True)
        tree.clear()
        self._kind_roots = {}

        for key, label in _KINDS:
            visible = self._visible_rows(key)
            total = len(self._destinations.get(key) or [])
            root = self._make_tree_item(
                {"_kind_root": True, "_all": True, "kind": key},
                label=(
                    f"{label}  ({len(visible)}/{total})"
                    if self._filter
                    else f"{label}  ({total})"
                ),
            )
            if key == "custom":
                self._add_custom_children(root, visible)
            else:
                for row in visible:
                    leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                    node = self._make_tree_item(
                        {**row, "kind": key, "_all": False}, label=leaf
                    )
                    root.addChild(node)
            tree.addTopLevelItem(root)
            self._kind_roots[key] = root
            if visible and (
                key in self._expanded_kinds or key == prev_kind or bool(self._filter)
            ):
                root.setExpanded(True)

        tree.blockSignals(False)
        if prev:
            self._reselect(prev)
        else:
            root = self._kind_roots.get(prev_kind)
            if root is not None:
                tree.setCurrentItem(root)
        self._sync_address()
        self._fill_contents()
        self._sync_folder_actions()

    def _on_kind_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_kind_root"):
            self._expanded_kinds.add(str(data.get("kind") or ""))

    def _on_kind_collapsed(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_kind_root"):
            self._expanded_kinds.discard(str(data.get("kind") or ""))

    def _on_tree_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            self._selected = None
        else:
            data = current.data(0, Qt.UserRole) or {}
            kind = str(data.get("kind") or self._kind)
            if kind in {"custom", "item", "supplier", "separate"}:
                self._kind = kind
            if data.get("_kind_root") or data.get("_all"):
                self._selected = None
            else:
                self._selected = dict(data)
        if hasattr(self, "_chrome"):
            self._chrome.set_address(self._address_text(), push_history=True)
            self._chrome.set_nav_enabled(up=self._can_go_up())
        self._sync_address()
        self._fill_contents()
        self._sync_folder_actions()

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_kind_root"):
            item.setExpanded(not item.isExpanded())
            return
        if item.childCount() > 0:
            item.setExpanded(True)

    def _iter_tree_items(self, root: QTreeWidgetItem | None = None):
        if root is None:
            for i in range(self._tree.topLevelItemCount()):
                item = self._tree.topLevelItem(i)
                yield item
                yield from self._iter_tree_items(item)
            return
        for i in range(root.childCount()):
            child = root.child(i)
            yield child
            yield from self._iter_tree_items(child)

    def _reselect(self, prev: dict[str, Any]) -> None:
        target_id = str(prev.get("id") or "")
        target_key = str(prev.get("ref_key") or "")
        for item in self._iter_tree_items():
            data = item.data(0, Qt.UserRole) or {}
            if data.get("_kind_root") or data.get("_all"):
                continue
            hit = False
            if target_id and str(data.get("id") or "") == target_id:
                hit = True
            elif target_key and str(data.get("ref_key") or "") == target_key:
                hit = True
            if not hit:
                continue
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self._tree.setCurrentItem(item)
            self._tree.scrollToItem(item)
            return

    def _child_folders(self) -> list[dict[str, Any]]:
        rows = self._visible_rows(self._kind)
        if self._kind == "custom":
            if self._selected and self._selected.get("id"):
                parent_id = str(self._selected["id"])
                return [
                    {**r, "kind": "custom"}
                    for r in rows
                    if str(r.get("parent_id") or "") == parent_id
                ]
            return [
                {**r, "kind": "custom"}
                for r in rows
                if not r.get("parent_id")
            ]
        if self._selected is None:
            return [{**r, "kind": self._kind} for r in rows]
        return []

    def _fill_contents(self) -> None:
        if not self._allow_records or self._is_park():
            return
        folders = self._child_folders()
        self._content_rows = folders
        self._table.setRowCount(0)
        type_label = _KIND_DEST_LABEL.get(self._kind, "Folder")
        for row in folders:
            r = self._table.rowCount()
            self._table.insertRow(r)
            name = str(
                row.get("display_label")
                or row.get("label")
                or row.get("ref_key")
                or "—"
            )
            name_item = QTableWidgetItem(qta.icon("mdi.folder", color=_FOLDER), name)
            name_item.setData(Qt.UserRole, row)
            type_item = QTableWidgetItem(
                "File folder" if self._kind == "custom" else type_label
            )
            type_item.setData(Qt.UserRole, row)
            type_item.setForeground(Qt.gray)
            self._table.setItem(r, 0, name_item)
            self._table.setItem(r, 1, type_item)

    def _on_content_selection(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        row = items[0].data(Qt.UserRole) or {}
        if row:
            self._reselect(row)

    def _on_content_double_clicked(self, row: int, _col: int) -> None:
        cell = self._table.item(row, 0)
        if cell is None:
            return
        data = cell.data(Qt.UserRole) or {}
        if not data:
            return
        self._reselect(data)
        if self._kind == "custom" and data.get("id"):
            for item in self._iter_tree_items():
                d = item.data(0, Qt.UserRole) or {}
                if str(d.get("id") or "") == str(data.get("id") or ""):
                    item.setExpanded(True)
                    break

    def _address_text(self) -> str:
        kind_label = next((lab for k, lab in _KINDS if k == self._kind), self._kind)
        parts = ["Records", kind_label]
        if self._selected:
            name = (
                self._selected.get("display_label")
                or self._selected.get("label")
                or self._selected.get("ref_key")
                or ""
            )
            if name:
                parts.append(str(name))
        return " > ".join(parts)

    def _sync_address(self) -> None:
        if not self._allow_records or self._is_park():
            return
        text = self._address_text()
        self._dest_path.setText(text)
        if hasattr(self, "_chrome"):
            self._chrome.set_search_placeholder(
                f"Search {next((lab for k, lab in _KINDS if k == self._kind), 'folders')}"
            )

    def _can_go_up(self) -> bool:
        return self._selected is not None

    def _go_up(self) -> None:
        if not self._selected:
            return
        if self._kind == "custom":
            parent_id = str(self._selected.get("parent_id") or "")
            if parent_id:
                parent = next(
                    (
                        r
                        for r in (self._destinations.get("custom") or [])
                        if str(r.get("id") or "") == parent_id
                    ),
                    None,
                )
                if parent:
                    self._reselect(parent)
                    return
        root = self._kind_roots.get(self._kind)
        if root is not None:
            self._tree.setCurrentItem(root)

    def _nav_back(self) -> None:
        path = self._chrome.history_back()
        if path:
            self._apply_history_path(path)

    def _nav_forward(self) -> None:
        path = self._chrome.history_forward()
        if path:
            self._apply_history_path(path)

    def _apply_history_path(self, path: str) -> None:
        parts = [p.strip() for p in path.split(">") if p.strip()]
        if len(parts) >= 2:
            for key, lab in _KINDS:
                if lab.casefold() == parts[1].casefold():
                    self._kind = key
                    break
        if len(parts) >= 3:
            name = parts[-1]
            rows = self._destinations.get(self._kind) or []
            hit = next(
                (
                    r
                    for r in rows
                    if str(r.get("display_label") or r.get("label") or "").casefold()
                    == name.casefold()
                    or str(r.get("ref_key") or "").casefold() == name.casefold()
                ),
                None,
            )
            if hit:
                self._reselect({**hit, "kind": self._kind})
                return
        root = self._kind_roots.get(self._kind)
        if root is not None:
            self._tree.setCurrentItem(root)

    def _on_search(self, text: str) -> None:
        self._filter = (text or "").strip().casefold()
        self._populate_tree()

    def _on_search_live(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        if needle == self._filter:
            return
        self._filter = needle
        self._populate_tree()

    def _sync_folder_actions(self) -> None:
        if not self._allow_records:
            return
        is_custom = self._kind == "custom" and not self._is_park()
        ribbon = self._chrome.ribbon
        ribbon.set_enabled("new", is_custom)
        has_sel = bool(self._selected and self._selected.get("id"))
        ribbon.set_enabled("sub", is_custom and has_sel)

    async def _reload_custom_and_select(self, folder: dict[str, Any]) -> None:
        try:
            payload = await list_destinations()
            self._destinations["custom"] = list(payload.get("custom") or [])
        except Exception:
            fid = str(folder.get("id") or "")
            self._destinations["custom"] = [
                folder,
                *[
                    r
                    for r in self._destinations.get("custom") or []
                    if str(r.get("id")) != fid
                ],
            ]
        self._kind = "custom"
        self._expanded_kinds.add("custom")
        self._populate_tree()
        self._reselect(folder)

    async def _new_folder(self) -> None:
        if self._is_park():
            return
        if self._kind != "custom":
            self._kind = "custom"
            root = self._kind_roots.get("custom")
            if root is not None:
                root.setExpanded(True)
                self._tree.setCurrentItem(root)
        label, ok = QInputDialog.getText(
            self, "New folder", "Folder name (or Parent/Child):"
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        try:
            folder = await create_custom_folder(label)
        except ApiError as exc:
            QMessageBox.warning(self, "Save to…", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Save to…", f"Could not create folder:\n{exc}")
            return
        await self._reload_custom_and_select(folder)

    async def _new_subfolder(self) -> None:
        if self._is_park():
            return
        parent = self._selected
        if not parent or not parent.get("id"):
            QMessageBox.information(
                self, "Save to…", "Select a parent custom folder first."
            )
            return
        parent_name = str(
            parent.get("display_label") or parent.get("label") or "folder"
        )
        label, ok = QInputDialog.getText(
            self, "New subfolder", f"Name under “{parent_name}”:"
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        if "/" in label or "\\" in label:
            QMessageBox.information(self, "Save to…", "Enter a single folder name.")
            return
        try:
            folder = await create_custom_folder(label, parent_id=str(parent["id"]))
        except ApiError as exc:
            QMessageBox.warning(self, "Save to…", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Save to…", f"Could not create subfolder:\n{exc}")
            return
        await self._reload_custom_and_select(folder)

    async def _accept_async(self) -> None:
        if self._accepting:
            return
        if self._is_park():
            self._choice = UploadDestinationChoice(mode="park")
            self.accept()
            return

        kind = self._kind
        selected = dict(self._selected) if self._selected else None
        if selected is None:
            QMessageBox.information(
                self,
                "Save to…",
                f"Select a {_KIND_DEST_LABEL.get(kind, 'destination').lower()} "
                "before saving — or switch Save as type to Park.",
            )
            self._tree.setFocus()
            return

        folder_id = ""
        ref_key = str(selected.get("ref_key") or "")
        label = str(
            selected.get("display_label")
            or selected.get("label")
            or selected.get("ref_key")
            or ""
        )
        if kind == "custom":
            folder_id = str(selected.get("id") or "")
            if not folder_id:
                QMessageBox.information(
                    self, "Save to…", "Select a custom folder, or create one."
                )
                return
        else:
            if not ref_key:
                ref_key = label
            if not ref_key:
                QMessageBox.information(
                    self, "Save to…", "Select a destination in the tree."
                )
                return

        self._accepting = True
        self._ok_btn.setEnabled(False)
        try:
            self._choice = UploadDestinationChoice(
                mode="records",
                kind=kind,
                ref_key=ref_key,
                folder_id=folder_id,
                label=label or ref_key,
                note=self._note.text().strip(),
                file_date=self._file_date.date().toString("yyyy-MM-dd"),
            )
            self.accept()
        finally:
            self._accepting = False
            self._ok_btn.setEnabled(True)
