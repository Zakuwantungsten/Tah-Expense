"""Where should these files go? — Receipts park vs Records destinations.

Used from Receipts Main Upload (accountant). Customs created here are the same
folders as Paperwork → Records.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.api_client import ApiError
from tahmeed.services.receipt_records_service import (
    create_custom_folder,
    list_destinations,
)
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
    f"QLineEdit{{font:12px 'Segoe UI';color:{_TEXT};border:1px solid {_BORDER};"
    f"border-radius:4px;padding:4px 8px;background:#FFFFFF;min-height:30px;}}"
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
        self._destinations: dict[str, list[dict[str, Any]]] = {
            "custom": [],
            "item": [],
            "supplier": [],
            "separate": [],
        }
        self.setWindowTitle("Upload destination")
        self.setModal(True)
        self.setMinimumWidth(520)
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
                "Permanent company paper. The destination you pick is the folder."
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
        rec_lay.setSpacing(10)

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

        # Destination (dynamic label + typeahead) — this IS the folder
        dest_col = QVBoxLayout()
        dest_col.setSpacing(4)
        self._dest_lbl = QLabel("Item")
        self._dest_lbl.setStyleSheet(_FIELD_LBL_SS)
        self._dest_edit = CompleterLineEdit([], ranked_contains=True)
        self._dest_edit.setStyleSheet(_INPUT_SS)
        self._dest_edit.setPlaceholderText("Type to search…")
        self._dest_edit.setClearButtonEnabled(True)
        dest_col.addWidget(self._dest_lbl)
        dest_col.addWidget(self._dest_edit)
        self._dest_hint = QLabel("")
        self._dest_hint.setWordWrap(True)
        self._dest_hint.setStyleSheet(_HINT_SS)
        dest_col.addWidget(self._dest_hint)
        rec_lay.addLayout(dest_col)

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

        self._dest_status = QLabel("")
        self._dest_status.setStyleSheet(_HINT_SS)
        rec_lay.addWidget(self._dest_status)

        lay.addWidget(self._records_box)
        self._records_box.setEnabled(False)
        # Only show Records fields when that mode is active — avoids the trap of
        # filling Item/Supplier while Park is still selected.
        self._records_box.setVisible(False)

        self._park_radio.toggled.connect(self._sync_mode_ui)
        self._records_radio.toggled.connect(self._sync_mode_ui)

        root.addWidget(card)

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
        else:
            self._ok_btn.setText("Park")

    def _current_kind(self) -> str:
        return str(self._kind.currentData() or "item")

    def _on_kind_changed(self) -> None:
        kind = self._current_kind()
        label = _KIND_DEST_LABEL.get(kind, "Destination")
        self._dest_lbl.setText(label)
        self._dest_edit.clear()
        self._refresh_dest_suggestions(kind)

    async def _load_destinations(self) -> None:
        try:
            payload = await list_destinations()
            self._destinations = {
                "custom": list(payload.get("custom") or []),
                "item": list(payload.get("items") or []),
                "supplier": list(payload.get("suppliers") or []),
                "separate": list(payload.get("separate") or []),
            }
            self._refresh_dest_suggestions(self._current_kind())
        except Exception as exc:
            self._dest_status.setText(f"Could not load destinations: {exc}")

    def _resolve_row(self, kind: str, typed: str) -> dict[str, Any] | None:
        needle = " ".join((typed or "").split())
        if not needle:
            return None
        rows = list(self._destinations.get(kind) or [])
        lower = needle.lower()
        # Exact label / display_label / ref_key first
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
        # Contains: unique match only
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

    def _refresh_dest_suggestions(self, kind: str) -> None:
        rows = list(self._destinations.get(kind) or [])
        labels = []
        for r in rows:
            name = str(
                r.get("display_label") or r.get("label") or r.get("ref_key") or ""
            ).strip()
            if name:
                labels.append(name)
        # Prefer unique display labels; keep order
        seen: set[str] = set()
        unique: list[str] = []
        for name in labels:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(name)
        self._dest_edit.set_values(unique)
        n = len(unique)
        if kind == "custom":
            self._dest_edit.setPlaceholderText(
                "Existing folder, new name, or Parent/Child subfolder…"
            )
            self._dest_hint.setText(
                "Pick an existing custom folder, type a new name, or use "
                "Parent/Child (creates nested folders under Records → Custom)."
            )
            self._dest_status.setText(
                f"{n} custom folder{'s' if n != 1 else ''} available."
                if n
                else "No custom folders yet — type a name or Parent/Child path."
            )
        elif kind == "separate":
            self._dest_edit.setPlaceholderText("Type to search separate expenses…")
            self._dest_hint.setText(
                "The separate expense you pick is the Records folder."
            )
            self._dest_status.setText(f"{n} separate destination{'s' if n != 1 else ''}.")
        else:
            noun = "item" if kind == "item" else "supplier"
            self._dest_edit.setPlaceholderText(f"Type to search {noun}s…")
            self._dest_hint.setText(
                f"The {noun} you pick is the Records folder. Approved chase "
                f"receipts for the same {noun} also appear there."
            )
            self._dest_status.setText(
                f"{n} {noun}{'s' if n != 1 else ''} available."
                if n
                else f"No {noun}s in catalog — you can still type a folder name."
            )

    async def _accept_async(self) -> None:
        if self._accepting:
            return
        if not self._allow_records or self._park_radio.isChecked():
            self._choice = UploadDestinationChoice(mode="park")
            self.accept()
            return

        kind = self._current_kind()
        typed = " ".join((self._dest_edit.text() or "").split())
        if not typed:
            QMessageBox.information(
                self,
                "Upload destination",
                f"Enter or pick a {_KIND_DEST_LABEL.get(kind, 'destination').lower()} "
                "before uploading to Records.",
            )
            self._dest_edit.setFocus()
            return

        self._accepting = True
        self._ok_btn.setEnabled(False)
        try:
            row = self._resolve_row(kind, typed)
            folder_id = ""
            ref_key = ""
            label = typed

            if kind == "custom":
                if row and row.get("id") and "/" not in typed.replace("\\", "/"):
                    folder_id = str(row["id"])
                    ref_key = str(row.get("ref_key") or "")
                    label = str(
                        row.get("display_label") or row.get("label") or typed
                    )
                else:
                    try:
                        # Flat name or Parent/Child — API creates nested path.
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
                            "Folder was not created — try again or pick an existing one.",
                        )
                        return
                    ref_key = str(folder.get("ref_key") or "")
                    label = str(
                        folder.get("display_label") or folder.get("label") or typed
                    )
                    # Keep local cache warm for next open
                    self._destinations["custom"] = [
                        folder,
                        *[
                            r
                            for r in self._destinations.get("custom") or []
                            if str(r.get("id")) != folder_id
                        ],
                    ]
            else:
                if row:
                    ref_key = str(row.get("ref_key") or typed)
                    label = str(row.get("label") or ref_key)
                else:
                    if kind == "separate":
                        QMessageBox.information(
                            self,
                            "Upload destination",
                            "Pick a separate expense from the suggestions.",
                        )
                        return
                    # Item / supplier: allow typed folder name (backend accepts)
                    ref_key = typed
                    label = typed

            self._choice = UploadDestinationChoice(
                mode="records",
                kind=kind,
                ref_key=ref_key,
                folder_id=folder_id,
                label=label,
                note=self._note.text().strip(),
            )
            self.accept()
        finally:
            self._accepting = False
            self._ok_btn.setEnabled(True)
