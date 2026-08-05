"""Dialog to correct truck/trailer numbers after paste or failed validation.

All flagged trucks appear in one combined list. As each row is fixed,
accepted as a place label (YARD/GARAGE), or cleared, it disappears from
the list. When the list is empty the dialog closes automatically.

Applying a correction can also fix every other open row with the same
pasted value (e.g. many ``T760 DN`` → ``T760 HDN``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
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
FleetKind = Literal["truck", "trailer"]

_CTRL_H = 34
_BORDER = "#E5E7EB"
_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_ORANGE = "#E85D04"
_GREEN = "#16A34A"
_RED = "#B91C1C"

_INPUT_SS = (
    f"QLineEdit {{ background: {_WHITE}; border: 1px solid #d1d5db; border-radius: 5px;"
    f" padding: 0 10px; font-size: 13px; color: {_T1};"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
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


def _norm_key(value: str) -> str:
    """Compare pasted originals case/space-insensitively."""
    return " ".join((value or "").upper().split())


@dataclass
class TruckIssue:
    row: int                 # 0-based grid / import row
    original: str
    kind: IssueKind
    corrected: str = ""      # filled when resolved
    skip: bool = False       # clear the cell (cashier) or empty
    is_place_label: bool = False
    omit_row: bool = False   # import mode: park row for later (Skipped tab)
    allow_anyway: bool = False  # import mode: save even if not in fleet


@dataclass
class _RowWidgets:
    issue: TruckIssue
    card: QFrame
    edit: QLineEdit
    kind_label: QLabel
    status_badge: QLabel
    add_btn: Optional[QPushButton] = None
    accept_label_btn: Optional[QPushButton] = None
    allow_btn: Optional[QPushButton] = None
    skip_row_btn: Optional[QPushButton] = None


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
        import_mode: bool = False,
        fleet_kinds: Optional[Dict[str, str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Correct truck numbers")
        self.setMinimumWidth(760 if import_mode else 680)
        self.setMinimumHeight(380)
        self.setModal(True)
        self._fleet = set(fleet)
        self._fleet_kinds: Dict[str, str] = dict(fleet_kinds or {})
        self._can_add = can_add
        self._import_mode = import_mode
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
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
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

        if self._import_mode:
            intro_text = (
                "These truck numbers are not in your fleet (or need a format fix). "
                "Apply a correction, allow a row anyway, or skip it for follow-up — "
                "skipped rows go to the Skipped tab and can rejoin this upload later. "
                "When you Apply one fix, you can also fix every similar pasted value. "
                "Matching rows continue importing."
            )
            if self._can_add:
                intro_text += " You can also add a missing truck/trailer to the registry."
        else:
            intro_text = (
                "All flagged trucks are listed here. Fix one, accept a place label "
                "(YARD / GARAGE), or clear it — it will leave the list. "
                "Applying a fix can also update every other row with the same pasted value. "
                "When nothing remains, this window closes."
            )
            if self._can_add:
                intro_text += " You can also add a missing truck/trailer to the registry."
        intro = QLabel(intro_text)
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

        if self._import_mode:
            bulk = QHBoxLayout()
            bulk.setSpacing(8)
            skip_all = QPushButton("Skip all remaining")
            skip_all.setCursor(Qt.PointingHandCursor)
            skip_all.setStyleSheet(_BTN_SECONDARY)
            skip_all.clicked.connect(self._skip_all_remaining)
            bulk.addWidget(skip_all)
            allow_all = QPushButton("Allow all remaining")
            allow_all.setCursor(Qt.PointingHandCursor)
            allow_all.setStyleSheet(_BTN_ORANGE)
            allow_all.clicked.connect(self._allow_all_remaining)
            bulk.addWidget(allow_all)
            bulk.addStretch()
            root.addLayout(bulk)

        buttons = QDialogButtonBox()
        done_btn = buttons.addButton("Done", QDialogButtonBox.AcceptRole)
        cancel_btn = buttons.addButton(
            "Skip remaining" if self._import_mode else "Cancel remaining",
            QDialogButtonBox.RejectRole,
        )
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

    def _lookup_kind(self, number: str) -> Optional[str]:
        if not number:
            return None
        key = number.strip().upper()
        kind = self._fleet_kinds.get(key)
        if kind:
            return kind
        matched = try_match_fleet(key, self._fleet)
        if matched:
            return self._fleet_kinds.get(matched)
        # Fall back to sync cache if kinds were not passed in
        try:
            from tahmeed.services.truck_service import lookup_fleet_kind_sync
            return lookup_fleet_kind_sync(matched or key)
        except Exception:
            return None

    def _status_for_text(self, text: str) -> tuple[str, str]:
        """Return (label, color) for the registry status badge."""
        raw = (text or "").strip()
        if not raw:
            return ("—", _T2)
        if is_allowed_place_label(raw, self._allowed_labels):
            return ("Place label", _BLUE)
        if is_place_label_candidate(raw):
            return ("Place label?", _BLUE)
        norm = normalize_truck_number(raw, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            return ("Place label", _BLUE)
        if norm.status == "empty":
            return ("—", _T2)
        if norm.status == "invalid":
            return ("Invalid format", "#B45309")
        matched = try_match_fleet(norm.value, self._fleet)
        if matched is None:
            return ("Not in registry", _RED)
        kind = self._lookup_kind(matched)
        if kind == "trailer":
            return ("Trailer ✓", _GREEN)
        if kind == "truck":
            return ("Truck ✓", _GREEN)
        return ("In registry ✓", _GREEN)

    def _refresh_status_badge(self, rw: _RowWidgets) -> None:
        label, color = self._status_for_text(rw.edit.text())
        rw.status_badge.setText(label)
        rw.status_badge.setStyleSheet(
            f"QLabel {{ color: {color}; background: {_BG}; border: 1px solid {_BORDER};"
            f" border-radius: 5px; padding: 0 10px; font-size: 12px; font-weight: 700;"
            f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px;"
            f" min-width: 110px; }}"
        )
        rw.status_badge.setAlignment(Qt.AlignCenter)

    # ── Similar-row helpers ───────────────────────────────────────────────

    def _similar_open_rows(self, rw: _RowWidgets) -> List[_RowWidgets]:
        key = _norm_key(rw.issue.original)
        if not key:
            return []
        return [
            other for other in self._rows
            if other is not rw and _norm_key(other.issue.original) == key
        ]

    def _confirm_apply_similar(
        self,
        rw: _RowWidgets,
        corrected: str,
        *,
        action: str = "apply",
    ) -> List[_RowWidgets]:
        """Ask whether to fix other open rows with the same pasted value."""
        similar = self._similar_open_rows(rw)
        if not similar:
            return []
        original = rw.issue.original
        verb = {
            "apply": "Apply",
            "allow": "Allow",
            "add": "Add / apply",
        }.get(action, "Apply")
        reply = QMessageBox.question(
            self,
            "Similar trucks",
            f'{verb} "{corrected}" to this row and {len(similar)} other '
            f'row(s) with the same pasted value "{original}"?\n\n'
            "Choose No to fix only this row.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return similar if reply == QMessageBox.Yes else []

    def _resolve_issue(
        self,
        issue: TruckIssue,
        *,
        corrected: str,
        skip: bool = False,
        omit_row: bool = False,
        allow_anyway: bool = False,
        is_place_label: bool = False,
    ) -> TruckIssue:
        issue.corrected = corrected
        issue.skip = skip
        issue.omit_row = omit_row
        issue.allow_anyway = allow_anyway
        issue.is_place_label = is_place_label
        return issue

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
                pasted = rw.card.findChild(QLabel, "pastedLbl")
                if pasted is not None:
                    pasted.setText(f'Pasted: "{issue.original}"')
                self._refresh_kind_label(rw)
                self._refresh_status_badge(rw)
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

        # Status badge (Truck / Trailer / Not in registry) — not a chooser
        status_badge = QLabel("—")
        status_badge.setFixedHeight(_CTRL_H)
        status_badge.setMinimumWidth(110)
        status_badge.setToolTip(
            "Shows whether the entered number is a Truck or Trailer in your registry"
        )
        edit_row.addWidget(status_badge)

        add_btn = None
        if self._can_add:
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
        apply_btn.setToolTip(
            "Apply this value (and optionally all similar pasted values) if valid"
        )
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setFixedHeight(_CTRL_H)
        apply_btn.setStyleSheet(_BTN_PRIMARY)
        edit_row.addWidget(apply_btn)

        allow_btn = None
        skip_row_btn = None
        if self._import_mode:
            allow_btn = QPushButton("Allow anyway")
            allow_btn.setToolTip("Import this row even if the truck is not in the fleet")
            allow_btn.setCursor(Qt.PointingHandCursor)
            allow_btn.setFixedHeight(_CTRL_H)
            allow_btn.setStyleSheet(_BTN_ORANGE)
            edit_row.addWidget(allow_btn)

            skip_row_btn = QPushButton("Skip row")
            skip_row_btn.setToolTip("Park this row in Skipped — other rows still import")
            skip_row_btn.setCursor(Qt.PointingHandCursor)
            skip_row_btn.setFixedHeight(_CTRL_H)
            skip_row_btn.setStyleSheet(_BTN_SECONDARY)
            edit_row.addWidget(skip_row_btn)
        else:
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
            status_badge=status_badge,
            add_btn=add_btn,
            accept_label_btn=accept_btn,
            allow_btn=allow_btn,
            skip_row_btn=skip_row_btn,
        )
        self._refresh_kind_label(rw)
        self._refresh_status_badge(rw)
        accept_btn.setVisible(
            is_place_label_candidate(edit.text())
            or is_allowed_place_label(edit.text(), self._allowed_labels)
        )

        edit.textChanged.connect(lambda _t, r=rw: self._on_edit_changed(r))
        apply_btn.clicked.connect(lambda _=False, r=rw: self._apply_row(r))
        accept_btn.clicked.connect(lambda _=False, r=rw: self._accept_label_row(r))
        if allow_btn is not None:
            allow_btn.clicked.connect(lambda _=False, r=rw: self._allow_anyway_row(r))
        if skip_row_btn is not None:
            skip_row_btn.clicked.connect(lambda _=False, r=rw: self._omit_row(r))
        else:
            clear_btn.clicked.connect(lambda _=False, r=rw: self._clear_row(r))
        if add_btn is not None:
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
        self._refresh_status_badge(rw)

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

    def _finish_resolved(
        self,
        rw: _RowWidgets,
        *,
        corrected: str,
        skip: bool = False,
        omit_row: bool = False,
        allow_anyway: bool = False,
        is_place_label: bool = False,
        also: Optional[List[_RowWidgets]] = None,
    ) -> None:
        """Resolve ``rw`` and optionally the similar rows in ``also``."""
        self._remove_row(
            rw,
            self._resolve_issue(
                rw.issue,
                corrected=corrected,
                skip=skip,
                omit_row=omit_row,
                allow_anyway=allow_anyway,
                is_place_label=is_place_label,
            ),
        )
        for other in list(also or []):
            if other not in self._rows:
                continue
            self._remove_row(
                other,
                self._resolve_issue(
                    other.issue,
                    corrected=corrected,
                    skip=skip,
                    omit_row=omit_row,
                    allow_anyway=allow_anyway,
                    is_place_label=is_place_label,
                ),
            )

    def _clear_row(self, rw: _RowWidgets) -> None:
        issue = rw.issue
        issue.corrected = ""
        issue.skip = True
        issue.omit_row = False
        issue.allow_anyway = False
        issue.is_place_label = False
        self._remove_row(rw, issue)

    def _omit_row(self, rw: _RowWidgets) -> None:
        """Import mode: park this row for the Skipped tab; do not import it now."""
        issue = rw.issue
        text = rw.edit.text().strip()
        issue.corrected = text or issue.original
        issue.skip = False
        issue.omit_row = True
        issue.allow_anyway = False
        issue.is_place_label = False
        self._remove_row(rw, issue)

    def _allow_anyway_row(self, rw: _RowWidgets, *, ask_similar: bool = True) -> None:
        """Import mode: accept truck even when not in fleet / odd format.

        Always re-checks trucks + trailers first. If the corrected value is in
        the registry, resolve it as a normal match (not an override). If it is
        still missing, warn the user before allowing.
        """
        text = rw.edit.text().strip() or rw.issue.original
        if not text:
            QMessageBox.warning(self, "Allow anyway", "Enter a truck value first.")
            return

        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        is_place = False
        if norm.status in ("ok", "normalized"):
            value = norm.value
        elif norm.status == "place_label":
            value = norm.value
            is_place = True
        else:
            value = " ".join(text.upper().split())

        rw.edit.setText(value)
        self._refresh_status_badge(rw)

        # Place labels are not fleet vehicles — allow without registry check
        if is_place or is_allowed_place_label(value, self._allowed_labels):
            also = (
                self._confirm_apply_similar(rw, value, action="allow")
                if ask_similar
                else []
            )
            self._finish_resolved(
                rw,
                corrected=normalize_place_label(value),
                is_place_label=True,
                also=also,
            )
            return

        matched = try_match_fleet(value, self._fleet)
        if matched is not None:
            rw.edit.setText(matched)
            self._refresh_status_badge(rw)
            also = (
                self._confirm_apply_similar(rw, matched, action="apply")
                if ask_similar
                else []
            )
            # In registry — treat as a normal apply, not an override
            self._finish_resolved(rw, corrected=matched, also=also)
            return

        # Not in trucks or trailers — flag and confirm before allowing
        if ask_similar:
            reply = QMessageBox.warning(
                self,
                "Not in vehicle registry",
                f'Your allowed truck "{value}" is not in the vehicle registry '
                "(trucks or trailers).\n\n"
                "Allow it anyway for this import?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                rw.edit.setFocus()
                return

        also = (
            self._confirm_apply_similar(rw, value, action="allow")
            if ask_similar
            else []
        )
        self._finish_resolved(
            rw,
            corrected=value,
            allow_anyway=True,
            also=also,
        )

    def _skip_all_remaining(self) -> None:
        for rw in list(self._rows):
            self._omit_row(rw)

    def _allow_all_remaining(self) -> None:
        """Allow every open row; warn once if any are still outside the registry."""
        pending: list[_RowWidgets] = []
        for rw in list(self._rows):
            if rw not in self._rows:
                continue
            text = rw.edit.text().strip() or rw.issue.original
            norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
            if norm.status in ("ok", "normalized"):
                value = norm.value
            elif norm.status == "place_label" or is_allowed_place_label(
                text, self._allowed_labels
            ):
                # Resolve place labels quietly
                self._allow_anyway_row(rw, ask_similar=False)
                continue
            else:
                value = " ".join(text.upper().split())
            matched = try_match_fleet(value, self._fleet)
            if matched is not None:
                self._allow_anyway_row(rw, ask_similar=False)
            else:
                pending.append(rw)

        if not pending:
            return

        samples = []
        for rw in pending[:5]:
            samples.append(
                (rw.edit.text().strip() or rw.issue.original or "").strip() or "—"
            )
        extra = f"\n…and {len(pending) - 5} more" if len(pending) > 5 else ""
        reply = QMessageBox.warning(
            self,
            "Not in vehicle registry",
            f"{len(pending)} allowed truck(s) are not in the vehicle registry "
            "(trucks or trailers):\n\n"
            + "\n".join(f"  • {s}" for s in samples)
            + extra
            + "\n\nAllow them anyway for this import?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for rw in list(pending):
            if rw in self._rows:
                self._allow_anyway_row(rw, ask_similar=False)

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
        also = self._confirm_apply_similar(rw, text, action="apply")
        self._finish_resolved(
            rw,
            corrected=text,
            is_place_label=True,
            also=also,
        )

    def _apply_row(self, rw: _RowWidgets) -> None:
        text = rw.edit.text().strip()
        if not text:
            if self._import_mode:
                self._omit_row(rw)
            else:
                self._clear_row(rw)
            return

        if is_allowed_place_label(text, self._allowed_labels):
            value = normalize_place_label(text)
            also = self._confirm_apply_similar(rw, value, action="apply")
            self._finish_resolved(
                rw, corrected=value, is_place_label=True, also=also,
            )
            return

        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            also = self._confirm_apply_similar(rw, norm.value, action="apply")
            self._finish_resolved(
                rw, corrected=norm.value, is_place_label=True, also=also,
            )
            return

        if norm.status == "invalid":
            if self._import_mode:
                QMessageBox.warning(
                    self,
                    "Invalid format",
                    f'"{text}" is not a recognized truck format yet.\n\n'
                    "Use “Allow anyway” to import as-is, “Skip row” to park it, "
                    "or enter a T + digits + suffix number (e.g. T688 EAF).",
                )
            else:
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
        self._refresh_status_badge(rw)
        matched = try_match_fleet(number, self._fleet)
        if matched is None:
            QMessageBox.warning(
                self,
                "Not in registry",
                f'"{number}" is not in the truck/trailer registry.\n\n'
                + (
                    "Use “Add to registry”, “Allow anyway”, or “Skip row”."
                    if self._import_mode and self._can_add
                    else (
                        "Use “Allow anyway” or “Skip row”, or enter a registered number."
                        if self._import_mode
                        else (
                            "Use “Add to registry”, or enter a registered number."
                            if self._can_add
                            else "Enter a registered number, or ask an admin/accountant to add it."
                        )
                    )
                ),
            )
            rw.edit.setFocus()
            return

        also = self._confirm_apply_similar(rw, matched, action="apply")
        self._finish_resolved(rw, corrected=matched, also=also)

    def _ask_registry_kind(self, number: str) -> Optional[str]:
        """Ask whether to add as Truck or Trailer. Returns ``trucks``/``trailers``."""
        box = QMessageBox(self)
        box.setWindowTitle("Add to registry")
        box.setIcon(QMessageBox.Question)
        box.setText(f'Add "{number}" to the fleet registry as:')
        truck_btn = box.addButton("Truck", QMessageBox.AcceptRole)
        trailer_btn = box.addButton("Trailer", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is truck_btn:
            return "trucks"
        if clicked is trailer_btn:
            return "trailers"
        return None

    def _add_to_registry_row(self, rw: _RowWidgets) -> None:
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
        kind = self._ask_registry_kind(number)
        if kind is None:
            return
        # Persist after dialog closes — no nested asyncio inside import modals.
        self.pending_registry_adds.append((kind, number))
        self._fleet.add(number)
        self._fleet_kinds[number] = "truck" if kind == "trucks" else "trailer"
        self._refresh_fleet_completers()
        self._refresh_status_badge(rw)
        also = self._confirm_apply_similar(rw, number, action="add")
        self._finish_resolved(rw, corrected=number, also=also)

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
            tip = (
                "Skip row / Allow anyway, or fix with Apply."
                if self._import_mode
                else "Resolve them in the list, or Cancel remaining."
            )
            QMessageBox.information(
                self,
                "Still need attention",
                f"{len(self._rows)} truck(s) still need attention.\n{tip}",
            )
            self._rows[0].edit.setFocus()
            return
        # Empty list already called accept() via _remove_row; if we got here with
        # zero rows for another reason, close cleanly.
        if not self._rows:
            self.accept()

    def _on_cancel(self) -> None:
        """Skip/clear every still-open issue and close."""
        for rw in list(self._rows):
            if self._import_mode:
                self._omit_row(rw)
            else:
                issue = rw.issue
                issue.corrected = ""
                issue.skip = True
                issue.omit_row = False
                issue.allow_anyway = False
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
        if self._import_mode:
            self.accept()
        else:
            self.reject()
