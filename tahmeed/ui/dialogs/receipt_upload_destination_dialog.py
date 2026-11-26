"""Where should these files go? — Receipts park vs Records destinations.

Used from Receipts Main Upload (accountant). Customs created here are the same
folders as Paperwork → Records. File-to-Records uses an Explorer-style tree
plus optional typeahead jump.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Optional

import qtawesome as qta
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
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
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit
from tahmeed.ui.widgets.popup_styles import with_light_popups

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED = "#6B7280"
_ACCENT = "#0077C5"
_NAVY = "#1B2B4B"

_CARD_SS = f"""
QWidget#UploadDestCard {{
  background: {_CARD};
  border: 1px solid {_BORDER};
  border-radius: 8px;
}}
QWidget#UploadDestCard QLabel {{
  background: transparent;
  border: none;
  color: {_TEXT};
}}
QWidget#UploadDestCard QRadioButton {{
  background: transparent;
  border: none;
  color: {_TEXT};
  font: 600 12px 'Segoe UI';
}}
"""

_COMBO_SS = with_combo_popup(
    f"QComboBox{{font:12px 'Segoe UI';color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;padding:4px 8px;background:#FFFFFF;min-height:30px;}}"
)
_INPUT_SS = (
    f"QLineEdit,QDateEdit{{font:12px 'Segoe UI';color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;padding:4px 8px;background:#FFFFFF;min-height:30px;}}"
)
_TREE_SS = with_light_popups(
    f"QTreeWidget{{font:12px 'Segoe UI';color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;background:#FFFFFF;outline:none;padding:4px;}}"
    "QTreeWidget::item{padding:3px 6px;border-radius:3px;min-height:22px;}"
    f"QTreeWidget::item:selected{{background:#E0F2FE;color:{_TEXT};}}"
    "QTreeWidget::item:hover{background:#F8FAFC;}"
)
_FIELD_LBL_SS = (
    f"color:{_MUTED};font:600 11px 'Segoe UI';background:transparent;border:none;"
)
_HINT_SS = (
    f"color:{_MUTED};font:11px 'Segoe UI';background:transparent;border:none;"
)
_BTN_GHOST = with_light_popups(
    f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;padding:6px 12px;font:600 12px 'Segoe UI';}}"
    f"QPushButton:hover{{background:#F8FAFC;}}"
    f"QPushButton:disabled{{color:#9CA3AF;}}"
)
_BTN_PRIMARY = with_light_popups(
    f"QPushButton{{background:{_ACCENT};color:#FFFFFF;border:none;"
    f"border-radius:4px;padding:6px 14px;font:600 12px 'Segoe UI';}}"
    f"QPushButton:hover{{background:#005EA3;}}"
    f"QPushButton:disabled{{background:#93C5FD;}}"
)
_BTN_SMALL = with_light_popups(
    f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;padding:4px 8px;font:600 11px 'Segoe UI';}}"
    f"QPushButton:hover{{background:#F8FAFC;}}"
    f"QPushButton:disabled{{color:#9CA3AF;}}"
)

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
    file_date: str = ""  # YYYY-MM-DD for Records browse YYYY/MM; empty = today


class ReceiptUploadDestinationDialog(QDialog):
    """Pick Receipts inbox park vs a Records destination after multi-file select."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        file_count: int = 1,
        allow_records: bool = True,
    ) -> None:
        super().__init__(parent)
        self._allow_records = bool(allow_records)
        self._choice: UploadDestinationChoice | None = None
        self._accepting = False
        self._syncing_jump = False
        self._tree_selected: dict[str, Any] | None = None
        self._destinations: dict[str, list[dict[str, Any]]] = {
            "custom": [],
            "item": [],
            "supplier": [],
            "separate": [],
        }
        self.setWindowTitle("Upload destination")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setStyleSheet(f"QDialog{{background:{_BG};}}")
        self._build(file_count)
        if self._allow_records:
            asyncio.ensure_future(self._load_destinations())

    def result_choice(self) -> UploadDestinationChoice | None:
        return self._choice

    def _build(self, file_count: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        card = QWidget()
        card.setObjectName("UploadDestCard")
        card.setStyleSheet(_CARD_SS)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        title = QLabel("Where should these files go?")
        title.setStyleSheet(
            f"color:{_NAVY};font:700 16px 'Segoe UI';background:transparent;border:none;"
        )
        lay.addWidget(title)

        n = max(1, int(file_count))
        summary = QLabel(
            f"{n} file{'s' if n != 1 else ''} selected. "
            "Park in Receipts inbox (link later), or file straight into Records."
            if self._allow_records
            else f"{n} file{'s' if n != 1 else ''} selected. "
            "Files will be parked in the Receipts inbox."
        )
        summary.setWordWrap(True)
        summary.setStyleSheet(_HINT_SS)
        lay.addWidget(summary)

        self._mode_group = QButtonGroup(self)
        self._park_radio = QRadioButton("Park in Receipts inbox")
        self._park_radio.setChecked(True)
        self._mode_group.addButton(self._park_radio, 0)
        lay.addWidget(self._park_radio)
        park_hint = QLabel(
            "Early drop with no expense row yet — use Inbox → Link when ready."
        )
        park_hint.setWordWrap(True)
        park_hint.setStyleSheet(_HINT_SS + "padding-left:22px;")
        lay.addWidget(park_hint)

        self._records_radio = QRadioButton("File to Records")
        self._mode_group.addButton(self._records_radio, 1)
        if self._allow_records:
            lay.addWidget(self._records_radio)
            records_hint = QLabel(
                "Permanent company paper. Pick a folder in the tree (or jump by typing)."
            )
            records_hint.setWordWrap(True)
            records_hint.setStyleSheet(_HINT_SS + "padding-left:22px;")
            lay.addWidget(records_hint)
        else:
            self._records_radio.setVisible(False)

        self._records_box = QWidget()
        self._records_box.setStyleSheet("background:transparent;border:none;")
        rec_lay = QVBoxLayout(self._records_box)
        rec_lay.setContentsMargins(22, 6, 0, 0)
        rec_lay.setSpacing(8)

        # Type
        type_col = QVBoxLayout()
        type_col.setSpacing(4)
        type_lbl = QLabel("Type")
        type_lbl.setStyleSheet(_FIELD_LBL_SS)
        self._kind = QComboBox()
        self._kind.setStyleSheet(_COMBO_SS)
        for key, label in (
            ("item", "Item"),
            ("supplier", "Supplier"),
            ("separate", "Separate expense"),
            ("custom", "Custom folder"),
        ):
            self._kind.addItem(label, key)
        self._kind.currentIndexChanged.connect(self._on_kind_changed)
        type_col.addWidget(type_lbl)
        type_col.addWidget(self._kind)
        rec_lay.addLayout(type_col)

        # Jump / typeahead
        jump_col = QVBoxLayout()
        jump_col.setSpacing(4)
        self._dest_lbl = QLabel("Jump to folder")
        self._dest_lbl.setStyleSheet(_FIELD_LBL_SS)
        self._dest_edit = CompleterLineEdit([], ranked_contains=True)
        self._dest_edit.setStyleSheet(_INPUT_SS)
        self._dest_edit.setPlaceholderText("Type to search and select in the tree…")
        self._dest_edit.setClearButtonEnabled(True)
        self._dest_edit.editingFinished.connect(self._jump_from_typeahead)
        jump_col.addWidget(self._dest_lbl)
        jump_col.addWidget(self._dest_edit)
        self._dest_hint = QLabel("")
        self._dest_hint.setWordWrap(True)
        self._dest_hint.setStyleSheet(_HINT_SS)
        jump_col.addWidget(self._dest_hint)
        rec_lay.addLayout(jump_col)

        # Folder tree
        tree_hdr = QHBoxLayout()
        tree_lbl = QLabel("Folder tree")
        tree_lbl.setStyleSheet(_FIELD_LBL_SS)
        tree_hdr.addWidget(tree_lbl)
        tree_hdr.addStretch(1)
        self._new_btn = QPushButton("New…")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.setFixedHeight(26)
        self._new_btn.setStyleSheet(_BTN_SMALL)
        self._new_btn.clicked.connect(lambda: asyncio.ensure_future(self._new_folder()))
        self._sub_btn = QPushButton("Subfolder…")
        self._sub_btn.setCursor(Qt.PointingHandCursor)
        self._sub_btn.setFixedHeight(26)
        self._sub_btn.setStyleSheet(_BTN_SMALL)
        self._sub_btn.setToolTip("Create a nested folder under the selected custom folder")
        self._sub_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._new_subfolder())
        )
        tree_hdr.addWidget(self._new_btn)
        tree_hdr.addWidget(self._sub_btn)
        rec_lay.addLayout(tree_hdr)

        self._tree = QTreeWidget()
        self._tree.setStyleSheet(_TREE_SS)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setAnimated(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setIndentation(18)
        self._tree.setUniformRowHeights(True)
        self._tree.setMinimumHeight(160)
        self._tree.currentItemChanged.connect(self._on_tree_changed)
        rec_lay.addWidget(self._tree, 1)

        self._tree_status = QLabel("")
        self._tree_status.setStyleSheet(_HINT_SS)
        rec_lay.addWidget(self._tree_status)

        # File as date (backlog → YYYY/MM folder)
        date_col = QVBoxLayout()
        date_col.setSpacing(4)
        date_lbl = QLabel("File as date")
        date_lbl.setStyleSheet(_FIELD_LBL_SS)
        self._file_date = QDateEdit()
        self._file_date.setCalendarPopup(True)
        self._file_date.setDisplayFormat("yyyy-MM-dd")
        self._file_date.setDate(QDate.currentDate())
        self._file_date.setStyleSheet(_INPUT_SS)
        style_calendar_popup(self._file_date)
        date_col.addWidget(date_lbl)
        date_col.addWidget(self._file_date)
        date_tip = QLabel(
            "Browse folder month (YYYY/MM). Use an older date for backlog "
            "(e.g. January files). Upload time is still recorded separately."
        )
        date_tip.setWordWrap(True)
        date_tip.setStyleSheet(_HINT_SS)
        date_col.addWidget(date_tip)
        rec_lay.addLayout(date_col)

        # Note
        note_col = QVBoxLayout()
        note_col.setSpacing(4)
        note_lbl = QLabel("Note")
        note_lbl.setStyleSheet(_FIELD_LBL_SS)
        self._note = QLineEdit()
        self._note.setPlaceholderText("Optional — saved as a .txt in the folder")
        self._note.setStyleSheet(_INPUT_SS)
        note_col.addWidget(note_lbl)
        note_col.addWidget(self._note)
        note_tip = QLabel(
            "If you enter a note, it is stored as an upload-note .txt beside the files."
        )
        note_tip.setWordWrap(True)
        note_tip.setStyleSheet(_HINT_SS)
        note_col.addWidget(note_tip)
        rec_lay.addLayout(note_col)

        lay.addWidget(self._records_box)
        self._records_box.setEnabled(False)
        self._records_box.setVisible(False)

        self._park_radio.toggled.connect(self._sync_mode_ui)
        self._records_radio.toggled.connect(self._sync_mode_ui)

        root.addWidget(card, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.setStyleSheet(_BTN_GHOST)
        cancel.clicked.connect(self.reject)
        self._ok_btn = QPushButton("Park")
        self._ok_btn.setIcon(qta.icon("mdi.file-upload-outline", color="#FFFFFF"))
        self._ok_btn.setIconSize(QSize(14, 14))
        self._ok_btn.setCursor(Qt.PointingHandCursor)
        self._ok_btn.setFixedHeight(32)
        self._ok_btn.setStyleSheet(_BTN_PRIMARY)
        self._ok_btn.clicked.connect(lambda: asyncio.ensure_future(self._accept_async()))
        actions.addWidget(cancel)
        actions.addWidget(self._ok_btn)
        root.addLayout(actions)

        self._sync_mode_ui()
        self._on_kind_changed()

    def _sync_mode_ui(self) -> None:
        records = self._allow_records and self._records_radio.isChecked()
        self._records_box.setEnabled(records)
        self._records_box.setVisible(records)
        if records:
            self._ok_btn.setText("Upload to Records")
            self.resize(max(self.width(), 560), max(self.height(), 560))
        else:
            self._ok_btn.setText("Park")

    def _current_kind(self) -> str:
        return str(self._kind.currentData() or "item")

    def _on_kind_changed(self) -> None:
        kind = self._current_kind()
        self._dest_lbl.setText(f"Jump to {_KIND_DEST_LABEL.get(kind, 'folder').lower()}")
        self._dest_edit.clear()
        self._tree_selected = None
        self._refresh_dest_suggestions(kind)
        self._populate_tree(kind)
        self._sync_folder_actions()

    def _sync_folder_actions(self) -> None:
        is_custom = self._current_kind() == "custom"
        self._new_btn.setVisible(is_custom)
        self._sub_btn.setVisible(is_custom)
        has_sel = bool(self._tree_selected and self._tree_selected.get("id"))
        self._sub_btn.setEnabled(is_custom and has_sel)

    async def _load_destinations(self) -> None:
        try:
            payload = await list_destinations()
            self._destinations = {
                "custom": list(payload.get("custom") or []),
                "item": list(payload.get("items") or []),
                "supplier": list(payload.get("suppliers") or []),
                "separate": list(payload.get("separate") or []),
            }
            kind = self._current_kind()
            self._refresh_dest_suggestions(kind)
            self._populate_tree(kind)
        except Exception as exc:
            self._tree_status.setText(f"Could not load destinations: {exc}")

    def _make_tree_item(self, data: dict[str, Any], *, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        tip = str(data.get("display_label") or label)
        item.setToolTip(0, tip)
        item.setData(0, Qt.UserRole, data)
        item.setIcon(0, qta.icon("mdi.folder-outline", color=_ACCENT))
        return item

    def _populate_tree(self, kind: str) -> None:
        tree = self._tree
        tree.blockSignals(True)
        tree.clear()
        rows = list(self._destinations.get(kind) or [])

        if kind == "custom":
            by_id: dict[str, dict[str, Any]] = {}
            for row in rows:
                fid = str(row.get("id") or "")
                if fid:
                    by_id[fid] = row
            nodes: dict[str, QTreeWidgetItem] = {}
            pending = list(rows)
            guard = 0
            while pending and guard < len(rows) + 5:
                guard += 1
                next_pending: list[dict[str, Any]] = []
                for row in pending:
                    fid = str(row.get("id") or "")
                    if not fid:
                        continue
                    parent_id = str(row.get("parent_id") or "")
                    leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                    data = {**row, "kind": "custom"}
                    node = self._make_tree_item(data, label=leaf)
                    if parent_id and parent_id in nodes:
                        nodes[parent_id].addChild(node)
                        nodes[fid] = node
                    elif not parent_id or parent_id not in by_id:
                        tree.addTopLevelItem(node)
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
                            {**row, "kind": "custom"}, label=leaf
                        )
                        tree.addTopLevelItem(node)
                        nodes[fid] = node
                    break
                pending = next_pending
            tree.expandToDepth(1)
            n = len(rows)
            self._tree_status.setText(
                f"{n} custom folder{'s' if n != 1 else ''} — select one, or New / Subfolder."
                if n
                else "No custom folders yet — click New… or type Parent/Child above."
            )
        else:
            for row in rows:
                label = str(row.get("label") or row.get("ref_key") or "Untitled")
                node = self._make_tree_item({**row, "kind": kind}, label=label)
                tree.addTopLevelItem(node)
            noun = {
                "item": "item",
                "supplier": "supplier",
                "separate": "separate destination",
            }.get(kind, "folder")
            n = len(rows)
            self._tree_status.setText(
                f"{n} {noun}{'s' if n != 1 else ''} — select in the tree or jump by typing."
                if n
                else f"No {noun}s loaded — you can still type a folder name above."
            )

        tree.blockSignals(False)
        self._tree_selected = None
        self._sync_folder_actions()

    def _on_tree_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            self._tree_selected = None
        else:
            data = current.data(0, Qt.UserRole) or {}
            self._tree_selected = dict(data) if data else None
        self._sync_folder_actions()
        if self._syncing_jump or not self._tree_selected:
            return
        display = str(
            self._tree_selected.get("display_label")
            or self._tree_selected.get("label")
            or self._tree_selected.get("ref_key")
            or ""
        )
        if display and self._dest_edit.text().strip() != display:
            self._syncing_jump = True
            self._dest_edit.setText(display)
            self._syncing_jump = False

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

    def _select_tree_row(self, row: dict[str, Any]) -> bool:
        target_id = str(row.get("id") or "")
        target_key = str(row.get("ref_key") or "")
        target_display = str(row.get("display_label") or row.get("label") or "").lower()
        for item in self._iter_tree_items():
            data = item.data(0, Qt.UserRole) or {}
            hit = False
            if target_id and str(data.get("id") or "") == target_id:
                hit = True
            elif target_key and str(data.get("ref_key") or "") == target_key:
                hit = True
            elif target_display:
                blob = str(
                    data.get("display_label") or data.get("label") or ""
                ).lower()
                if blob == target_display:
                    hit = True
            if not hit:
                continue
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self._tree.setCurrentItem(item)
            self._tree.scrollToItem(item)
            return True
        return False

    def _resolve_row(self, kind: str, typed: str) -> dict[str, Any] | None:
        needle = " ".join((typed or "").split())
        if not needle:
            return None
        rows = list(self._destinations.get(kind) or [])
        lower = needle.lower()
        for row in rows:
            label = str(row.get("label") or "").strip()
            display = str(row.get("display_label") or "").strip()
            ref = str(row.get("ref_key") or "").strip()
            if (
                label.lower() == lower
                or display.lower() == lower
                or ref.lower() == lower
            ):
                return row
        hits = []
        for row in rows:
            label = str(row.get("label") or "").strip()
            display = str(row.get("display_label") or "").strip()
            ref = str(row.get("ref_key") or "").strip()
            blob = f"{label} {display} {ref}".lower()
            if lower in blob:
                hits.append(row)
        if len(hits) == 1:
            return hits[0]
        return None

    def _jump_from_typeahead(self) -> None:
        if self._syncing_jump:
            return
        kind = self._current_kind()
        typed = " ".join((self._dest_edit.text() or "").split())
        if not typed:
            return
        row = self._resolve_row(kind, typed)
        if row:
            self._select_tree_row(row)

    def _refresh_dest_suggestions(self, kind: str) -> None:
        rows = list(self._destinations.get(kind) or [])
        labels: list[str] = []
        for r in rows:
            name = str(
                r.get("display_label") or r.get("label") or r.get("ref_key") or ""
            ).strip()
            if name:
                labels.append(name)
        seen: set[str] = set()
        unique: list[str] = []
        for name in labels:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(name)
        self._dest_edit.set_values(unique)
        if kind == "custom":
            self._dest_edit.setPlaceholderText(
                "Search tree, or type new name / Parent/Child…"
            )
            self._dest_hint.setText(
                "Select a folder in the tree, jump by typing, or create with New / "
                "Subfolder (Parent/Child also works)."
            )
        elif kind == "separate":
            self._dest_edit.setPlaceholderText("Type to jump to a separate expense…")
            self._dest_hint.setText("Select in the tree or jump by typing.")
        else:
            noun = "item" if kind == "item" else "supplier"
            self._dest_edit.setPlaceholderText(f"Type to jump to an {noun}…")
            self._dest_hint.setText(
                f"Select an {noun} in the tree. Approved chase receipts for the "
                f"same {noun} also appear in Records."
            )

    async def _reload_custom_and_select(self, folder: dict[str, Any]) -> None:
        try:
            payload = await list_destinations()
            self._destinations["custom"] = list(payload.get("custom") or [])
        except Exception:
            # Fall back to merging the created folder locally
            fid = str(folder.get("id") or "")
            self._destinations["custom"] = [
                folder,
                *[
                    r
                    for r in self._destinations.get("custom") or []
                    if str(r.get("id")) != fid
                ],
            ]
        if self._current_kind() != "custom":
            idx = self._kind.findData("custom")
            if idx >= 0:
                self._kind.setCurrentIndex(idx)
        self._refresh_dest_suggestions("custom")
        self._populate_tree("custom")
        self._select_tree_row(folder)
        display = str(
            folder.get("display_label") or folder.get("label") or ""
        )
        if display:
            self._syncing_jump = True
            self._dest_edit.setText(display)
            self._syncing_jump = False

    async def _new_folder(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            "New custom folder",
            "Folder name (or Parent/Child for a nested path):",
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        try:
            folder = await create_custom_folder(label)
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not create folder:\n{exc}")
            return
        await self._reload_custom_and_select(folder)

    async def _new_subfolder(self) -> None:
        parent = self._tree_selected
        if not parent or not parent.get("id"):
            QMessageBox.information(
                self, "Records", "Select a parent custom folder in the tree first."
            )
            return
        parent_name = str(
            parent.get("display_label") or parent.get("label") or "folder"
        )
        label, ok = QInputDialog.getText(
            self,
            "New subfolder",
            f"Subfolder name under “{parent_name}”:",
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        if "/" in label or "\\" in label:
            QMessageBox.information(
                self,
                "Records",
                "Enter a single folder name (no /). Nest further with Subfolder again.",
            )
            return
        try:
            folder = await create_custom_folder(
                label, parent_id=str(parent["id"])
            )
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not create subfolder:\n{exc}")
            return
        await self._reload_custom_and_select(folder)

    async def _accept_async(self) -> None:
        if self._accepting:
            return
        if not self._allow_records or self._park_radio.isChecked():
            self._choice = UploadDestinationChoice(mode="park")
            self.accept()
            return

        kind = self._current_kind()
        typed = " ".join((self._dest_edit.text() or "").split())
        # Prefer explicit tree selection
        selected = dict(self._tree_selected) if self._tree_selected else None
        if selected is None and typed:
            self._jump_from_typeahead()
            selected = dict(self._tree_selected) if self._tree_selected else None

        self._accepting = True
        self._ok_btn.setEnabled(False)
        try:
            folder_id = ""
            ref_key = ""
            label = ""

            if selected:
                folder_id = str(selected.get("id") or "")
                ref_key = str(selected.get("ref_key") or "")
                label = str(
                    selected.get("display_label")
                    or selected.get("label")
                    or selected.get("ref_key")
                    or ""
                )
                if kind != "custom":
                    folder_id = ""  # only custom uses folder_id
                    if not ref_key:
                        ref_key = label
            elif kind == "custom" and typed:
                try:
                    folder = await create_custom_folder(typed)
                except ApiError as exc:
                    QMessageBox.warning(self, "Records", str(exc))
                    return
                except Exception as exc:
                    QMessageBox.critical(
                        self, "Records", f"Could not create folder:\n{exc}"
                    )
                    return
                folder_id = str(folder.get("id") or "")
                if not folder_id:
                    QMessageBox.warning(
                        self,
                        "Records",
                        "Folder was not created — pick one in the tree or try again.",
                    )
                    return
                ref_key = str(folder.get("ref_key") or "")
                label = str(
                    folder.get("display_label") or folder.get("label") or typed
                )
                await self._reload_custom_and_select(folder)
            elif kind in {"item", "supplier"} and typed:
                row = self._resolve_row(kind, typed)
                if row:
                    ref_key = str(row.get("ref_key") or typed)
                    label = str(row.get("label") or ref_key)
                else:
                    ref_key = typed
                    label = typed
            elif kind == "separate":
                QMessageBox.information(
                    self,
                    "Upload destination",
                    "Select a separate expense in the tree (or jump by typing).",
                )
                return
            else:
                QMessageBox.information(
                    self,
                    "Upload destination",
                    f"Select a {_KIND_DEST_LABEL.get(kind, 'destination').lower()} "
                    "in the folder tree before uploading to Records.",
                )
                self._tree.setFocus()
                return

            if kind == "custom" and not folder_id:
                QMessageBox.information(
                    self,
                    "Upload destination",
                    "Select a custom folder in the tree, or create one with New…",
                )
                return
            if kind != "custom" and not ref_key:
                QMessageBox.information(
                    self,
                    "Upload destination",
                    "Select a destination in the folder tree.",
                )
                return

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
