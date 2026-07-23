"""Dialog to correct truck/trailer numbers after paste or failed validation.

All flagged trucks appear in one combined list. As each row is fixed,
accepted as a place label (YARD/GARAGE), or cleared, it disappears from
the list. When the list is empty the dialog closes automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from tahmeed.services.truck_format import (
    is_allowed_place_label,
    is_place_label_candidate,
    normalize_place_label,
    normalize_truck_number,
    try_match_fleet,
)
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

IssueKind = Literal["invalid_format", "not_in_registry"]

_CTRL_H = 34
_BORDER = "#E5E7EB"
_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_ORANGE = "#E85D04"

_INPUT_SS = (
    f"QLineEdit {{ background: {_WHITE}; border: 1px solid #d1d5db; border-radius: 5px;"
    f" padding: 0 10px; font-size: 13px; color: {_T1};"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
)
_COMBO_SS = (
    f"QComboBox {{ background: {_WHITE}; border: 1px solid #d1d5db; border-radius: 5px;"
    f" padding: 0 8px; font-size: 12px; color: {_T1};"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QComboBox:focus {{ border-color: {_BLUE}; }}"
)
_BTN_SECONDARY = (
    f"QPushButton {{ background: {_WHITE}; color: #374151; border: 1px solid #d1d5db;"
    f" border-radius: 5px; padding: 0 12px; font-size: 12px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QPushButton:hover {{ background: {_BG}; }}"
)
_BTN_ORANGE = (
    f"QPushButton {{ background: {_ORANGE}; color: #fff; border: none;"
    f" border-radius: 5px; padding: 0 12px; font-size: 12px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    "QPushButton:hover { background: #F48C06; }"
)
_BTN_BLUE = (
    f"QPushButton {{ background: {_WHITE}; color: {_BLUE}; border: 1px solid {_BLUE};"
    f" border-radius: 5px; padding: 0 12px; font-size: 12px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QPushButton:hover {{ background: #E8F4FD; }}"
)
_BTN_PRIMARY = (
    f"QPushButton {{ background: {_BLUE}; color: #fff; border: none;"
    f" border-radius: 5px; padding: 0 12px; font-size: 12px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    "QPushButton:hover { background: #005EA3; }"
)


@dataclass
class TruckIssue:
    row: int                 # 0-based grid row
    original: str
    kind: IssueKind
    corrected: str = ""      # filled when resolved
    skip: bool = False       # clear the cell
    is_place_label: bool = False


@dataclass
class _RowWidgets:
    issue: TruckIssue
    card: QFrame
    edit: QLineEdit
    kind_label: QLabel
    add_btn: Optional[QPushButton] = None
    kind_choice: Optional[QComboBox] = None
    accept_label_btn: Optional[QPushButton] = None


class TruckCorrectionDialog(QDialog):
    """One combined list of truck issues; resolved rows disappear immediately."""

    def __init__(
        self,
        issues: List[TruckIssue],
        fleet: set[str],
        *,
        can_add: bool = False,
        allowed_labels: Optional[Set[str]] = None,
        on_resolved=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Correct truck numbers")
        self.setMinimumWidth(640)
        self.setMinimumHeight(380)
        self.setModal(True)
        self._fleet = set(fleet)
        self._can_add = can_add
        self._on_resolved = on_resolved  # optional callback(TruckIssue) per resolved row
        self._allowed_labels: Set[str] = {
            normalize_place_label(x) for x in (allowed_labels or set()) if x
        }
        self.new_labels: List[str] = []
        # (kind, number) queued for the caller to persist after the dialog closes —
        # avoids nested asyncio during import modals.
        self.pending_registry_adds: List[tuple[str, str]] = []
        self.issues: List[TruckIssue] = []  # resolved results (updated as rows clear)
        self._rows: List[_RowWidgets] = []
        self._body_lay: Optional[QVBoxLayout] = None
        self._count_label: Optional[QLabel] = None
        self._build_ui()
        self.add_issues(issues)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; }}"
            "QLabel { border: none; background: transparent; }"
            "QFrame { border: none; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Correct truck numbers")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        intro = QLabel(
            "All flagged trucks are listed here. Fix one, accept a place label "
            "(YARD / GARAGE), or clear it — it will leave the list. "
            "When nothing remains, this window closes."
            + (
                " You can also add a missing truck/trailer to the registry."
                if self._can_add
                else ""
            )
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color: {_T2}; font-size: 12px; border: none; background: transparent;"
        )
        root.addWidget(intro)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {_T1}; font-size: 12px; font-weight: 600;"
            " border: none; background: transparent;"
        )
        root.addWidget(self._count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")

        body = QWidget()
        body.setStyleSheet("background:transparent; border:none;")
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setSpacing(10)
        self._body_lay.setContentsMargins(0, 0, 2, 0)
        self._body_lay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox()
        done_btn = buttons.addButton("Done", QDialogButtonBox.AcceptRole)
        cancel_btn = buttons.addButton("Cancel remaining", QDialogButtonBox.RejectRole)
        done_btn.setDefault(True)
        done_btn.setStyleSheet(_BTN_PRIMARY)
        cancel_btn.setStyleSheet(_BTN_SECONDARY)
        buttons.accepted.connect(self._on_done)
        buttons.rejected.connect(self._on_cancel)
        root.addWidget(buttons)

    def _update_count(self) -> None:
        n = len(self._rows)
        if self._count_label is not None:
            self._count_label.setText(
                f"{n} truck{'s' if n != 1 else ''} still need attention"
                if n
                else "All trucks resolved"
            )

    def _fleet_suggestions(self) -> List[str]:
        """Sorted registered numbers for inline autocomplete (same UX as table)."""
        return sorted(self._fleet)

    def _refresh_fleet_completers(self) -> None:
        for rw in self._rows:
            if isinstance(rw.edit, TruckLineEdit):
                rw.edit.set_local_numbers(self._fleet_suggestions)

    # ── Add / remove rows ─────────────────────────────────────────────────

    def add_issues(self, issues: List[TruckIssue]) -> None:
        """Append issues (dedupe by grid row — latest wins)."""
        if not issues or self._body_lay is None:
            return
        existing = {rw.issue.row: rw for rw in self._rows}
        for issue in issues:
            if issue.row in existing:
                # Refresh the open card instead of stacking duplicates
                rw = existing[issue.row]
                rw.issue.original = issue.original
                rw.issue.kind = issue.kind
                norm = normalize_truck_number(
                    issue.original, allowed_labels=self._allowed_labels
                )
                if norm.status in ("ok", "normalized", "place_label"):
                    rw.edit.setText(norm.value)
                else:
                    rw.edit.setText(norm.value or issue.original.upper())
                self._refresh_kind_label(rw)
                continue
            card = self._make_card(issue)
            # Insert before the trailing stretch
            self._body_lay.insertWidget(self._body_lay.count() - 1, card)
        self._update_count()

    def _make_card(self, issue: TruckIssue) -> QFrame:
        card = QFrame()
        card.setObjectName("truckIssueCard")
        card.setStyleSheet(
            f"QFrame#truckIssueCard {{"
            f" background-color: {_WHITE}; border: 1px solid {_BORDER};"
            " border-radius: 12px; }"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel(f"Row {issue.row + 1}")
        title.setStyleSheet(
            f"font-weight: 700; color: {_T1}; font-size: 12px;"
            " border: none; background: transparent;"
        )
        header.addWidget(title)

        kind_label = QLabel("")
        kind_label.setWordWrap(True)
        header.addWidget(kind_label, 1)
        lay.addLayout(header)

        pasted = QLabel(f'Pasted: "{issue.original}"')
        pasted.setObjectName("pastedLbl")
        pasted.setStyleSheet(
            f"color: {_T2}; font-size: 11px; border: none; background: transparent;"
        )
        lay.addWidget(pasted)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(8)

        # Sync fleet filter — modal dialogs cannot safely nest asyncio fetches.
        edit = TruckLineEdit(local_numbers=self._fleet_suggestions)
        edit.setFixedHeight(_CTRL_H)
        edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        norm = normalize_truck_number(issue.original, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label"):
            edit.setText(norm.value)
        else:
            edit.setText(norm.value or issue.original.upper())
        edit.setPlaceholderText("T688 EAF  or  YARD")
        edit.setStyleSheet(_INPUT_SS)
        edit.editingFinished.connect(lambda e=edit: self._autonorm_edit(e))
        edit_row.addWidget(edit, 1)

        add_btn = None
        kind_choice = None
        if self._can_add:
            kind_choice = QComboBox()
            kind_choice.addItems(["Truck", "Trailer"])
            kind_choice.setFixedWidth(96)
            kind_choice.setFixedHeight(_CTRL_H)
            kind_choice.setStyleSheet(_COMBO_SS)
            edit_row.addWidget(kind_choice)
            add_btn = QPushButton("Add to registry")
            add_btn.setCursor(Qt.PointingHandCursor)
            add_btn.setFixedHeight(_CTRL_H)
            add_btn.setStyleSheet(_BTN_ORANGE)
            edit_row.addWidget(add_btn)

        accept_btn = QPushButton("Accept label")
        accept_btn.setToolTip("Accept as place label (YARD / GARAGE) and remove from list")
        accept_btn.setCursor(Qt.PointingHandCursor)
        accept_btn.setFixedHeight(_CTRL_H)
        accept_btn.setStyleSheet(_BTN_BLUE)
        edit_row.addWidget(accept_btn)

        apply_btn = QPushButton("Apply")
        apply_btn.setToolTip("Apply this value and remove from list if valid")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setFixedHeight(_CTRL_H)
        apply_btn.setStyleSheet(_BTN_PRIMARY)
        edit_row.addWidget(apply_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setFixedHeight(_CTRL_H)
        clear_btn.setStyleSheet(_BTN_SECONDARY)
        edit_row.addWidget(clear_btn)
        lay.addLayout(edit_row)

        rw = _RowWidgets(
            issue=issue,
            card=card,
            edit=edit,
            kind_label=kind_label,
            add_btn=add_btn,
            kind_choice=kind_choice,
            accept_label_btn=accept_btn,
        )
        self._refresh_kind_label(rw)
        accept_btn.setVisible(
            is_place_label_candidate(edit.text())
            or is_allowed_place_label(edit.text(), self._allowed_labels)
        )

        edit.textChanged.connect(lambda _t, r=rw: self._on_edit_changed(r))
        apply_btn.clicked.connect(lambda _=False, r=rw: self._apply_row(r))
        accept_btn.clicked.connect(lambda _=False, r=rw: self._accept_label_row(r))
        clear_btn.clicked.connect(lambda _=False, r=rw: self._clear_row(r))
        if add_btn is not None and kind_choice is not None:
            add_btn.clicked.connect(
                lambda _=False, r=rw: self._add_to_registry_row(r)
            )

        self._rows.append(rw)
        return card

    def _refresh_kind_label(self, rw: _RowWidgets) -> None:
        if rw.issue.kind == "invalid_format":
            text = "Invalid format — use T + digits + space + suffix (e.g. T688 EAF)"
            color = "#b45309"
        else:
            text = "Not in truck/trailer registry"
            color = "#b91c1c"
        rw.kind_label.setText(text)
        rw.kind_label.setStyleSheet(
            f"color: {color}; font-size: 11px; border: none; background: transparent;"
        )

    def _on_edit_changed(self, rw: _RowWidgets) -> None:
        text = rw.edit.text().strip()
        if rw.accept_label_btn is not None:
            rw.accept_label_btn.setVisible(
                is_place_label_candidate(text)
                or is_allowed_place_label(text, self._allowed_labels)
            )

    def _autonorm_edit(self, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if not text:
            return
        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label") and norm.value != text:
            edit.blockSignals(True)
            edit.setText(norm.value)
            edit.blockSignals(False)

    # ── Resolve one row → remove from list ────────────────────────────────

    def _remove_row(self, rw: _RowWidgets, resolved: TruckIssue) -> None:
        self.issues.append(resolved)
        if rw in self._rows:
            self._rows.remove(rw)
        rw.card.setParent(None)
        rw.card.deleteLater()
        self._update_count()
        if callable(self._on_resolved):
            try:
                self._on_resolved(resolved)
            except Exception:
                pass
        if not self._rows:
            self.accept()

    def _clear_row(self, rw: _RowWidgets) -> None:
        issue = rw.issue
        issue.corrected = ""
        issue.skip = True
        issue.is_place_label = False
        self._remove_row(rw, issue)

    def _accept_label_row(self, rw: _RowWidgets) -> None:
        text = normalize_place_label(rw.edit.text())
        if not text:
            QMessageBox.warning(self, "Label", "Enter a place label first (e.g. YARD).")
            return
        if not is_place_label_candidate(text) and not is_allowed_place_label(
            text, self._allowed_labels
        ):
            QMessageBox.warning(
                self,
                "Label",
                "Place labels should be words like YARD or GARAGE "
                "(not a truck number).",
            )
            return
        if text not in self._allowed_labels:
            self._allowed_labels.add(text)
            self.new_labels.append(text)
        issue = rw.issue
        issue.corrected = text
        issue.skip = False
        issue.is_place_label = True
        self._remove_row(rw, issue)

    def _apply_row(self, rw: _RowWidgets) -> None:
        text = rw.edit.text().strip()
        if not text:
            self._clear_row(rw)
            return

        if is_allowed_place_label(text, self._allowed_labels):
            issue = rw.issue
            issue.corrected = normalize_place_label(text)
            issue.skip = False
            issue.is_place_label = True
            self._remove_row(rw, issue)
            return

        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            issue = rw.issue
            issue.corrected = norm.value
            issue.skip = False
            issue.is_place_label = True
            self._remove_row(rw, issue)
            return

        if norm.status == "invalid":
            QMessageBox.warning(
                self,
                "Invalid format",
                f'"{text}" is not a valid truck number.\n\n'
                "Use T + digits + space + suffix, e.g. T688 EAF,\n"
                "or click “Accept label” for YARD / GARAGE.",
            )
            rw.edit.setFocus()
            return

        number = norm.value
        rw.edit.setText(number)
        matched = try_match_fleet(number, self._fleet)
        if matched is None:
            QMessageBox.warning(
                self,
                "Not in registry",
                f'"{number}" is not in the truck/trailer registry.\n\n'
                + (
                    "Use “Add to registry”, or enter a registered number."
                    if self._can_add
                    else "Enter a registered number, or ask an admin/accountant to add it."
                ),
            )
            rw.edit.setFocus()
            return

        issue = rw.issue
        issue.corrected = matched
        issue.skip = False
        issue.is_place_label = False
        self._remove_row(rw, issue)

    def _add_to_registry_row(self, rw: _RowWidgets) -> None:
        if rw.kind_choice is None:
            return
        text = rw.edit.text().strip()
        norm = normalize_truck_number(text, allowed_labels=())
        if norm.status not in ("ok", "normalized"):
            QMessageBox.warning(
                self, "Format",
                "Enter a valid truck number first (e.g. T688 EAF).",
            )
            return
        number = norm.value
        rw.edit.setText(number)
        kind = "trucks" if rw.kind_choice.currentText() == "Truck" else "trailers"
        # Persist after dialog closes — no nested asyncio inside import modals.
        self.pending_registry_adds.append((kind, number))
        self._fleet.add(number)
        self._refresh_fleet_completers()
        issue = rw.issue
        issue.corrected = number
        issue.skip = False
        issue.is_place_label = False
        self._remove_row(rw, issue)

    # ── Dialog close ──────────────────────────────────────────────────────

    def _on_done(self) -> None:
        """Auto-apply every remaining row that can be resolved; warn if any remain."""
        for rw in list(self._rows):
            text = rw.edit.text().strip()
            if not text:
                self._clear_row(rw)
                continue
            if is_allowed_place_label(text, self._allowed_labels) or is_place_label_candidate(text):
                # Only auto-accept if already remembered; candidates need explicit click
                if is_allowed_place_label(text, self._allowed_labels):
                    issue = rw.issue
                    issue.corrected = normalize_place_label(text)
                    issue.skip = False
                    issue.is_place_label = True
                    self._remove_row(rw, issue)
                continue
            norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
            if norm.status == "place_label":
                issue = rw.issue
                issue.corrected = norm.value
                issue.skip = False
                issue.is_place_label = True
                self._remove_row(rw, issue)
                continue
            if norm.status in ("ok", "normalized"):
                matched = try_match_fleet(norm.value, self._fleet)
                if matched is not None:
                    rw.edit.setText(matched)
                    issue = rw.issue
                    issue.corrected = matched
                    issue.skip = False
                    issue.is_place_label = False
                    self._remove_row(rw, issue)
                    continue
        if self._rows:
            QMessageBox.information(
                self,
                "Still need attention",
                f"{len(self._rows)} truck(s) still need a fix, Accept label, or Clear.\n"
                "Resolve them in the list, or Cancel remaining.",
            )
            self._rows[0].edit.setFocus()
            return
        # Empty list already called accept() via _remove_row; if we got here with
        # zero rows for another reason, close cleanly.
        if not self._rows:
            self.accept()

    def _on_cancel(self) -> None:
        """Clear every still-open issue and close."""
        for rw in list(self._rows):
            issue = rw.issue
            issue.corrected = ""
            issue.skip = True
            issue.is_place_label = False
            self.issues.append(issue)
            if rw in self._rows:
                self._rows.remove(rw)
            rw.card.setParent(None)
            rw.card.deleteLater()
            if callable(self._on_resolved):
                try:
                    self._on_resolved(issue)
                except Exception:
                    pass
        self._update_count()
        self.reject()
