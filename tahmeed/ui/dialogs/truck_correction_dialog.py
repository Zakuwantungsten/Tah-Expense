"""Dialog to correct fleet numbers after paste or failed validation.

All flagged trucks appear in one combined list. As each row is fixed,
accepted as a place label (YARD/GARAGE), or cleared, it disappears from
the list. When the list is empty the dialog closes automatically.

Applying a correction can also fix every other open row with the same
pasted value (e.g. many ``T760 DN`` → ``T760 HDN``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel,
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
from tahmeed.ui.dialog_theme import (
    show_info,
    show_question,
    show_warning,
    style_message_box,
)
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

IssueKind = Literal["invalid_format", "not_in_registry"]
FleetKind = Literal["truck", "trailer", "motor_vehicle"]

_CTRL_H = 34
_TRUCK_EDIT_W = 168  # plate-sized; do not stretch across the row
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
    row_label: str = ""      # optional UI title (e.g. "File row 2442")
    combo_parts: List[str] = field(default_factory=list)  # unused when combo_suffix is set
    combo_suffix: str = ""  # ``/T691ELK`` kept while the user edits the truck only


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
    part_edits: List[QLineEdit] = field(default_factory=list)
    part_badges: List[QLabel] = field(default_factory=list)


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
        heading: str = "",
        intro: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(heading or "Correct truck numbers")
        self.setMinimumWidth(820 if import_mode else 680)
        self.setMinimumHeight(380)
        self.setModal(True)
        self._fleet = set(fleet)
        self._fleet_kinds: Dict[str, str] = dict(fleet_kinds or {})
        self._can_add = can_add
        self._import_mode = import_mode
        self._heading = heading or "Correct truck numbers"
        self._intro = intro
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
            "QToolTip {"
            f" background-color: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 4px;"
            " padding: 6px 10px; font-size: 12px; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(self._heading)
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        if self._intro:
            intro_text = self._intro
        elif self._import_mode:
            intro_text = (
                "These truck numbers are not in your fleet (or need a format fix). "
                "Apply a correction, allow a row anyway, or skip it for follow-up — "
                "skipped rows go to the Skipped tab and can rejoin this upload later. "
                "When you Apply one fix, you can also fix every similar pasted value. "
                "Matching rows continue importing."
            )
            if self._can_add:
                intro_text += " You can also add a missing vehicle to the fleet registry."
        else:
            intro_text = (
                "All flagged trucks are listed here. Fix one, accept a place label "
                "(YARD / GARAGE), allow a partner truck that is not in your fleet, "
                "or clear it — it will leave the list. "
                "Applying a fix can also update every other row with the same pasted value. "
                "When nothing remains, this window closes."
            )
            if self._can_add:
                intro_text += " You can also add a missing vehicle to the fleet registry."
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

        footer = QHBoxLayout()
        footer.setSpacing(8)
        if self._import_mode:
            skip_all = QPushButton("Skip all remaining")
            skip_all.setCursor(Qt.PointingHandCursor)
            skip_all.setFixedHeight(_CTRL_H)
            skip_all.setStyleSheet(_BTN_SECONDARY)
            skip_all.clicked.connect(self._skip_all_remaining)
            footer.addWidget(skip_all)
        allow_all = QPushButton("Allow all remaining")
        allow_all.setCursor(Qt.PointingHandCursor)
        allow_all.setFixedHeight(_CTRL_H)
        allow_all.setStyleSheet(_BTN_ORANGE)
        allow_all.clicked.connect(self._allow_all_remaining)
        footer.addWidget(allow_all)
        footer.addStretch()
        done_btn = QPushButton("Done")
        done_btn.setDefault(True)
        done_btn.setCursor(Qt.PointingHandCursor)
        done_btn.setFixedHeight(_CTRL_H)
        done_btn.setStyleSheet(_BTN_PRIMARY)
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)
        cancel_btn = QPushButton(
            "Skip remaining" if self._import_mode else "Cancel remaining"
        )
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedHeight(_CTRL_H)
        cancel_btn.setStyleSheet(_BTN_SECONDARY)
        cancel_btn.clicked.connect(self._on_cancel)
        footer.addWidget(cancel_btn)
        root.addLayout(footer)

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

    def _combo_part_labels(self, count: int) -> List[str]:
        if count <= 1:
            return ["Truck"]
        if count == 2:
            return ["Truck", "Trailer"]
        return ["Truck"] + [f"Trailer {i}" for i in range(1, count)]

    def _row_value(self, rw: _RowWidgets) -> str:
        """Merged truck/trailer value, or the single edit box."""
        if rw.part_edits:
            parts: List[str] = []
            for edit in rw.part_edits:
                text = edit.text().strip()
                if not text:
                    continue
                norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
                if norm.status in ("ok", "normalized", "place_label"):
                    parts.append(norm.value)
                else:
                    parts.append(" ".join(text.upper().split()))
            return "/".join(parts)
        return rw.edit.text().strip()

    def _truck_edit_value(self, rw: _RowWidgets) -> str:
        """Current truck field, falling back to the leading plate of the pasted cell."""
        return self._row_value(rw) or self._edit_source_for_issue(rw.issue)

    def _edit_source_for_issue(self, issue: TruckIssue) -> str:
        """Single-field text: leading truck when the trailer suffix is kept aside."""
        raw = issue.original or ""
        suffix = issue.combo_suffix or ""
        if suffix and raw.endswith(suffix):
            return raw[: -len(suffix)].strip()
        if suffix:
            cut = raw.find(suffix)
            if cut > 0:
                return raw[:cut].strip()
        return raw

    def _fill_truck_edit(self, edit: QLineEdit, source: str) -> None:
        norm = normalize_truck_number(source, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label"):
            edit.setText(norm.value)
        else:
            edit.setText(norm.value or str(source).upper())

    def _with_combo_suffix(
        self,
        issue: TruckIssue,
        truck: str,
        *,
        is_place_label: bool = False,
    ) -> str:
        truck = (truck or "").strip()
        if is_place_label or not issue.combo_suffix:
            return truck
        return f"{truck}{issue.combo_suffix}"

    def _is_two_trailer_issue(self, issue: TruckIssue) -> bool:
        """True when the pasted cell has truck + two (or more) trailers."""
        if len(issue.combo_parts or []) >= 3:
            return True
        if (issue.combo_suffix or "").count("/") >= 2:
            return True
        try:
            from tahmeed.services.import_truck_check import split_truck_combo_cell
            parts = split_truck_combo_cell(issue.original or "")
        except Exception:
            return False
        return bool(parts) and len(parts) >= 3

    def _refresh_fleet_completers(self) -> None:
        numbers = self._fleet_suggestions
        for rw in self._rows:
            edits = rw.part_edits or ([rw.edit] if isinstance(rw.edit, TruckLineEdit) else [])
            for edit in edits:
                if isinstance(edit, TruckLineEdit):
                    edit.set_local_numbers(numbers)

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
            # Free-form motorcycle/car plates can still be in the fleet.
            matched_free = try_match_fleet(raw, self._fleet)
            if matched_free is not None:
                kind = self._lookup_kind(matched_free)
                if kind == "motor_vehicle":
                    return ("Bike/Car ✓", _GREEN)
                if kind == "trailer":
                    return ("Trailer ✓", _GREEN)
                if kind == "truck":
                    return ("Truck ✓", _GREEN)
                return ("In registry ✓", _GREEN)
            return ("Invalid format", "#B45309")
        matched = try_match_fleet(norm.value, self._fleet)
        if matched is None:
            return ("Not in registry", _RED)
        kind = self._lookup_kind(matched)
        if kind == "trailer":
            return ("Trailer ✓", _GREEN)
        if kind == "truck":
            return ("Truck ✓", _GREEN)
        if kind == "motor_vehicle":
            return ("Bike/Car ✓", _GREEN)
        return ("In registry ✓", _GREEN)

    # ── Similar-row helpers ───────────────────────────────────────────────

    def _similar_open_rows(self, rw: _RowWidgets) -> List[_RowWidgets]:
        key = _norm_key(self._edit_source_for_issue(rw.issue))
        if not key:
            return []
        return [
            other for other in self._rows
            if other is not rw and _norm_key(self._edit_source_for_issue(other.issue)) == key
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
        reply = show_question(
            self,
            "Similar trucks",
            f'{verb} "{corrected}" to this row and {len(similar)} other '
            f'row(s) with the same pasted value "{original}"?\n\n'
            "Choose No to fix only this row.",
            default_no=False,
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
                if issue.combo_suffix:
                    rw.issue.combo_suffix = issue.combo_suffix
                if issue.combo_parts:
                    rw.issue.combo_parts = list(issue.combo_parts)
                if rw.part_edits and rw.issue.combo_parts:
                    for edit, part in zip(rw.part_edits, rw.issue.combo_parts):
                        self._fill_truck_edit(edit, part)
                else:
                    self._fill_truck_edit(rw.edit, self._edit_source_for_issue(issue))
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
        title = QLabel(issue.row_label or f"Row {issue.row + 1}")
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

        # Import combos keep trailers on ``combo_suffix`` and edit the truck only.
        combo = [] if issue.combo_suffix else list(issue.combo_parts or [])
        part_edits: List[QLineEdit] = []
        part_badges: List[QLabel] = []
        add_btn = None
        accept_btn = None
        rw_holder: Optional[_RowWidgets] = None

        apply_btn = QPushButton("Apply")
        apply_btn.setToolTip(
            "Apply this value (and optionally all similar pasted values) if valid"
        )
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setFixedHeight(_CTRL_H)
        apply_btn.setStyleSheet(_BTN_PRIMARY)

        allow_btn = QPushButton("Allow anyway")
        allow_btn.setToolTip(
            "Keep this truck on the row even though it is not in your fleet "
            "(e.g. a partner vehicle)"
        )
        allow_btn.setCursor(Qt.PointingHandCursor)
        allow_btn.setFixedHeight(_CTRL_H)
        allow_btn.setStyleSheet(_BTN_ORANGE)
        skip_row_btn = None
        clear_btn = None
        if self._import_mode:
            skip_row_btn = QPushButton("Skip row")
            skip_row_btn.setToolTip("Park this row in Skipped — other rows still import")
            skip_row_btn.setCursor(Qt.PointingHandCursor)
            skip_row_btn.setFixedHeight(_CTRL_H)
            skip_row_btn.setStyleSheet(_BTN_SECONDARY)
        else:
            clear_btn = QPushButton("Clear")
            clear_btn.setToolTip("Clear the truck cell and remove this row from the list")
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setFixedHeight(_CTRL_H)
            clear_btn.setStyleSheet(_BTN_SECONDARY)

        def _add_row_actions(row: QHBoxLayout) -> None:
            row.addWidget(apply_btn)
            if allow_btn is not None:
                row.addWidget(allow_btn)
            if skip_row_btn is not None:
                row.addWidget(skip_row_btn)
            if clear_btn is not None:
                row.addWidget(clear_btn)

        if len(combo) >= 2:
            labels = self._combo_part_labels(len(combo))
            for idx, part in enumerate(combo):
                prow = QHBoxLayout()
                prow.setSpacing(8)
                name = QLabel(labels[idx])
                name.setFixedWidth(80)
                name.setStyleSheet(
                    f"color: {_T1}; font-size: 12px; font-weight: 600;"
                    " border: none; background: transparent;"
                )
                prow.addWidget(name)
                edit = TruckLineEdit(local_numbers=self._fleet_suggestions)
                edit.setFixedHeight(_CTRL_H)
                edit.setMinimumWidth(120)
                edit.setMaximumWidth(_TRUCK_EDIT_W)
                edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                norm = normalize_truck_number(part, allowed_labels=self._allowed_labels)
                if norm.status in ("ok", "normalized", "place_label"):
                    edit.setText(norm.value)
                else:
                    edit.setText(norm.value or str(part).upper())
                edit.setPlaceholderText("T688 EAF")
                edit.setStyleSheet(_INPUT_SS)
                edit.editingFinished.connect(lambda e=edit: self._autonorm_edit(e))
                prow.addWidget(edit)
                badge = QLabel("—")
                badge.setFixedHeight(_CTRL_H)
                badge.setMinimumWidth(110)
                prow.addWidget(badge)
                if self._can_add:
                    part_add = QPushButton("Add")
                    part_add.setToolTip(f"Add this {labels[idx].lower()} to the fleet registry")
                    part_add.setCursor(Qt.PointingHandCursor)
                    part_add.setFixedHeight(_CTRL_H)
                    part_add.setStyleSheet(_BTN_ORANGE)
                    prow.addWidget(part_add)
                    part_add.clicked.connect(
                        lambda _=False, i=idx: self._add_combo_part(rw_holder, i)
                    )
                if idx == 0:
                    _add_row_actions(prow)
                    prow.addStretch()
                lay.addLayout(prow)
                part_edits.append(edit)
                part_badges.append(badge)
            primary_edit = part_edits[0]
            status_badge = part_badges[0]
        else:
            edit_row = QHBoxLayout()
            edit_row.setSpacing(8)
            edit = TruckLineEdit(local_numbers=self._fleet_suggestions)
            edit.setFixedHeight(_CTRL_H)
            edit.setMinimumWidth(120)
            edit.setMaximumWidth(_TRUCK_EDIT_W)
            edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self._fill_truck_edit(edit, self._edit_source_for_issue(issue))
            edit.setPlaceholderText("T688 EAF  or  YARD")
            edit.setStyleSheet(_INPUT_SS)
            edit.editingFinished.connect(lambda e=edit: self._autonorm_edit(e))
            edit_row.addWidget(edit)
            status_badge = QLabel("—")
            status_badge.setFixedHeight(_CTRL_H)
            status_badge.setMinimumWidth(110)
            status_badge.setToolTip(
                "Shows whether the entered number is a Truck, Trailer, or "
                "Motorcycle/Car in your registry"
            )
            edit_row.addWidget(status_badge)
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
            _add_row_actions(edit_row)
            edit_row.addStretch()
            lay.addLayout(edit_row)
            primary_edit = edit

        rw = _RowWidgets(
            issue=issue,
            card=card,
            edit=primary_edit,
            kind_label=kind_label,
            status_badge=status_badge,
            add_btn=add_btn,
            accept_label_btn=accept_btn,
            allow_btn=allow_btn,
            skip_row_btn=skip_row_btn,
            part_edits=part_edits,
            part_badges=part_badges,
        )
        rw_holder = rw
        self._refresh_kind_label(rw)
        self._refresh_status_badge(rw)
        if accept_btn is not None:
            accept_btn.setVisible(
                is_place_label_candidate(primary_edit.text())
                or is_allowed_place_label(primary_edit.text(), self._allowed_labels)
            )

        for edit in (part_edits or [primary_edit]):
            edit.textChanged.connect(lambda _t, r=rw: self._on_edit_changed(r))
        apply_btn.clicked.connect(lambda _=False, r=rw: self._apply_row(r))
        if accept_btn is not None:
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
        if rw.part_edits:
            labels = self._combo_part_labels(len(rw.part_edits))
            bad: List[str] = []
            for i, edit in enumerate(rw.part_edits):
                status, _ = self._status_for_text(edit.text())
                if "✓" not in status and status not in ("Place label", "Place label?"):
                    bad.append(labels[i])
            if not bad:
                text, color = "Ready to apply", _GREEN
            elif len(bad) == 1:
                text, color = f"{bad[0]} needs attention", "#b91c1c"
            else:
                text, color = "Truck and trailer need attention", "#b91c1c"
        elif self._is_two_trailer_issue(rw.issue):
            status, _ = self._status_for_text(rw.edit.text())
            if "✓" in status or status in ("Place label", "Place label?"):
                text, color = "Two trailers — Allow anyway or Skip", "#b45309"
            else:
                text, color = "Two trailers — truck needs attention", "#b91c1c"
        elif rw.issue.kind == "invalid_format":
            text = "Invalid format — use T + digits + space + suffix (e.g. T688 EAF)"
            color = "#b45309"
        else:
            text = "Not in fleet registry"
            color = "#b91c1c"
        rw.kind_label.setText(text)
        rw.kind_label.setStyleSheet(
            f"color: {color}; font-size: 11px; border: none; background: transparent;"
        )

    def _refresh_status_badge(self, rw: _RowWidgets) -> None:
        if rw.part_edits and rw.part_badges:
            for edit, badge in zip(rw.part_edits, rw.part_badges):
                label, color = self._status_for_text(edit.text())
                badge.setText(label)
                badge.setStyleSheet(
                    f"QLabel {{ color: {color}; background: {_BG}; border: 1px solid {_BORDER};"
                    f" border-radius: 5px; padding: 0 10px; font-size: 12px; font-weight: 700;"
                    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px;"
                    f" min-width: 110px; }}"
                )
                badge.setAlignment(Qt.AlignCenter)
            return
        label, color = self._status_for_text(rw.edit.text())
        rw.status_badge.setText(label)
        rw.status_badge.setStyleSheet(
            f"QLabel {{ color: {color}; background: {_BG}; border: 1px solid {_BORDER};"
            f" border-radius: 5px; padding: 0 10px; font-size: 12px; font-weight: 700;"
            f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px;"
            f" min-width: 110px; }}"
        )
        rw.status_badge.setAlignment(Qt.AlignCenter)

    def _on_edit_changed(self, rw: _RowWidgets) -> None:
        if rw.accept_label_btn is not None and not rw.part_edits:
            text = rw.edit.text().strip()
            rw.accept_label_btn.setVisible(
                is_place_label_candidate(text)
                or is_allowed_place_label(text, self._allowed_labels)
            )
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)

    def _autonorm_edit(self, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if not text:
            return
        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label") and norm.value != text:
            edit.blockSignals(True)
            edit.setText(norm.value)
            edit.blockSignals(False)

    def _part_match_value(self, text: str) -> tuple[Optional[str], str]:
        """Canonical plate for one combo field, plus ``ok`` / ``place`` / error kind."""
        raw = (text or "").strip()
        if not raw:
            return None, "empty"
        if is_allowed_place_label(raw, self._allowed_labels):
            return normalize_place_label(raw), "place"
        norm = normalize_truck_number(raw, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            return norm.value, "place"
        if norm.status in ("ok", "normalized"):
            matched = try_match_fleet(norm.value, self._fleet)
            if matched is not None:
                return matched, "ok"
            return None, "missing"
        matched = try_match_fleet(raw, self._fleet)
        if matched is not None:
            return matched, "ok"
        if norm.status == "invalid":
            return None, "invalid"
        return None, "missing"

    def _combo_resolved_parts(
        self, rw: _RowWidgets
    ) -> tuple[Optional[List[str]], Optional[str]]:
        """Return canonical truck/trailer parts, or ``(None, warning)``."""
        labels = self._combo_part_labels(len(rw.part_edits))
        values: List[str] = []
        for i, edit in enumerate(rw.part_edits):
            value, kind = self._part_match_value(edit.text())
            name = labels[i]
            raw = edit.text().strip()
            if kind == "empty":
                return None, f"Enter a {name.lower()} number."
            if kind == "invalid":
                return None, (
                    f'"{raw}" is not a recognized {name.lower()} format.\n\n'
                    "Use T + digits + suffix (e.g. T688 EAF), or Add / Allow anyway."
                )
            if kind == "missing":
                return None, (
                    f'"{raw}" is not in the fleet registry.\n\n'
                    "Use “Add” next to that field, “Allow anyway”, or “Skip row”."
                )
            assert value is not None
            values.append(value)
            edit.setText(value)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        return values, None

    def _add_combo_part(self, rw: Optional[_RowWidgets], idx: int) -> None:
        """Add one truck or trailer from a split combo card to the registry."""
        if rw is None or idx < 0 or idx >= len(rw.part_edits):
            return
        edit = rw.part_edits[idx]
        number = self._commit_registry_add(edit.text().strip())
        if not number:
            return
        edit.setText(number)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        parts, err = self._combo_resolved_parts(rw)
        if err is not None:
            return
        merged = "/".join(parts or [])
        also = self._confirm_apply_similar(rw, merged, action="add")
        self._finish_resolved(rw, corrected=merged, also=also)

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
                corrected=self._with_combo_suffix(
                    rw.issue, corrected, is_place_label=is_place_label
                ),
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
                    corrected=self._with_combo_suffix(
                        other.issue, corrected, is_place_label=is_place_label
                    ),
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
        text = self._truck_edit_value(rw)
        issue.corrected = self._with_combo_suffix(issue, text or issue.original)
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
        text = self._truck_edit_value(rw)
        if not text:
            show_warning(self, "Allow anyway", "Enter a truck value first.")
            return

        if rw.part_edits:
            also = (
                self._confirm_apply_similar(rw, text, action="allow")
                if ask_similar
                else []
            )
            unmatched = [
                edit.text().strip()
                for edit in rw.part_edits
                if edit.text().strip()
                and try_match_fleet(edit.text().strip(), self._fleet) is None
            ]
            if unmatched and ask_similar:
                reply = show_question(
                    self,
                    "Not in vehicle registry",
                    "Allow this truck/trailer row anyway even though "
                    f"{len(unmatched)} part(s) are not in the registry?",
                )
                if reply != QMessageBox.Yes:
                    return
            self._finish_resolved(
                rw,
                corrected=text,
                allow_anyway=bool(unmatched),
                also=also,
            )
            return

        text = rw.edit.text().strip() or rw.issue.original

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
        if matched is not None and not self._is_two_trailer_issue(rw.issue):
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

        if matched is not None:
            rw.edit.setText(matched)
            value = matched
            self._refresh_status_badge(rw)

        # Not in fleet registry, or two-trailer irregularity — confirm first
        if ask_similar:
            if self._is_two_trailer_issue(rw.issue):
                reply = show_question(
                    self,
                    "Two trailers",
                    f'This row has two trailers:\n"{rw.issue.original}"\n\n'
                    + (
                        "Allow it anyway for this import?"
                        if self._import_mode
                        else "Allow it anyway on this row?"
                    ),
                )
            else:
                reply = show_question(
                    self,
                    "Not in vehicle registry",
                    f'"{value}" is not in your fleet registry '
                    "(trucks, trailers, or motorcycles & cars).\n\n"
                    + (
                        "Allow it anyway for this import?"
                        if self._import_mode
                        else "Allow this partner / non-fleet truck anyway?"
                    ),
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
            if rw.part_edits:
                parts, err = self._combo_resolved_parts(rw)
                if err is None and parts:
                    self._allow_anyway_row(rw, ask_similar=False)
                else:
                    pending.append(rw)
                continue
            if self._is_two_trailer_issue(rw.issue):
                pending.append(rw)
                continue
            text = self._truck_edit_value(rw)
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
                (self._truck_edit_value(rw) or "").strip() or "—"
            )
        extra = f"\n…and {len(pending) - 5} more" if len(pending) > 5 else ""
        reply = show_question(
            self,
            "Not in vehicle registry",
            f"{len(pending)} row(s) are not a normal truck match "
            "(not in the vehicle registry, or two trailers):\n\n"
            + "\n".join(f"  • {s}" for s in samples)
            + extra
            + "\n\nAllow them anyway for this import?",
        )
        if reply != QMessageBox.Yes:
            return
        for rw in list(pending):
            if rw in self._rows:
                self._allow_anyway_row(rw, ask_similar=False)

    def _accept_label_row(self, rw: _RowWidgets) -> None:
        text = normalize_place_label(rw.edit.text())
        if not text:
            show_warning(self, "Label", "Enter a place label first (e.g. YARD).")
            return
        if not is_place_label_candidate(text) and not is_allowed_place_label(
            text, self._allowed_labels
        ):
            show_warning(
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

    def _apply_combo_row(self, rw: _RowWidgets) -> None:
        parts, err = self._combo_resolved_parts(rw)
        if err is not None:
            if self._import_mode and not any(e.text().strip() for e in rw.part_edits):
                self._omit_row(rw)
                return
            show_warning(self, "Truck / trailer", err)
            return
        merged = "/".join(parts or [])
        also = self._confirm_apply_similar(rw, merged, action="apply")
        self._finish_resolved(rw, corrected=merged, also=also)

    def _apply_row(self, rw: _RowWidgets) -> None:
        if rw.part_edits:
            self._apply_combo_row(rw)
            return
        text = rw.edit.text().strip()
        if not text:
            if self._import_mode:
                self._omit_row(rw)
            else:
                self._clear_row(rw)
            return

        if self._is_two_trailer_issue(rw.issue):
            show_warning(
                self,
                "Two trailers",
                "This cell has two trailers and cannot be applied as a "
                "normal truck match.\n\n"
                "Use “Allow anyway” to import as-is, or “Skip row”.",
            )
            rw.edit.setFocus()
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
                show_warning(
                    self,
                    "Invalid format",
                    f'"{text}" is not a recognized truck format yet.\n\n'
                    "Use “Allow anyway” to import as-is, “Skip row” to park it, "
                    "or enter a T + digits + suffix number (e.g. T688 EAF).",
                )
            else:
                show_warning(
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
            show_warning(
                self,
                "Not in registry",
                f'"{number}" is not in the fleet registry.\n\n'
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
        """Ask which fleet collection to use. Returns collection path or None."""
        box = QMessageBox(self)
        box.setWindowTitle("Add to registry")
        box.setIcon(QMessageBox.Question)
        box.setText(f'Add "{number}" to which registry?')
        box.setInformativeText(
            "Truck / Trailer require plate format T688 EAF.\n"
            "Motorcycle/Car accepts other registration styles."
        )
        truck_btn = box.addButton("Truck", QMessageBox.AcceptRole)
        trailer_btn = box.addButton("Trailer", QMessageBox.AcceptRole)
        motor_btn = box.addButton("Motorcycle/Car", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        style_message_box(box)
        box.exec()
        clicked = box.clickedButton()
        if clicked is truck_btn:
            return "trucks"
        if clicked is trailer_btn:
            return "trailers"
        if clicked is motor_btn:
            return "motor_vehicles"
        return None

    def _commit_registry_add(self, text: str) -> Optional[str]:
        """Ask for a registry kind and queue the plate. Returns canonical number."""
        if not text:
            show_warning(
                self, "Registry", "Enter a registration number first."
            )
            return None
        if is_allowed_place_label(text, self._allowed_labels) or (
            is_place_label_candidate(text) and not any(ch.isdigit() for ch in text)
        ):
            show_warning(
                self,
                "Registry",
                f'"{text}" looks like a place label, not a vehicle.\n'
                "Use “Remember as place label” instead.",
            )
            return None

        kind = self._ask_registry_kind(text)
        if kind is None:
            return None

        if kind in ("trucks", "trailers"):
            norm = normalize_truck_number(text, allowed_labels=())
            if norm.status not in ("ok", "normalized"):
                show_warning(
                    self,
                    "Format",
                    f'Truck/Trailer numbers must look like T688 EAF.\n\n'
                    f'"{text}" is not a valid plate format.\n'
                    "Choose Motorcycle/Car for other registration styles.",
                )
                return None
            number = norm.value
        else:
            number = " ".join(text.upper().split())
            if len(number) < 2:
                show_warning(
                    self, "Registry", "Enter a registration number first."
                )
                return None

        from tahmeed.services.truck_service import collection_to_kind

        self.pending_registry_adds.append((kind, number))
        self._fleet.add(number)
        self._fleet_kinds[number] = collection_to_kind(kind)
        self._refresh_fleet_completers()
        return number

    def _add_to_registry_row(self, rw: _RowWidgets) -> None:
        text = rw.edit.text().strip()
        number = self._commit_registry_add(text)
        if not number:
            return

        rw.edit.setText(number)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        if self._is_two_trailer_issue(rw.issue):
            return
        also = self._confirm_apply_similar(rw, number, action="add")
        self._finish_resolved(rw, corrected=number, also=also)

    # ── Dialog close ──────────────────────────────────────────────────────

    def _on_done(self) -> None:
        """Auto-apply every remaining row that can be resolved; warn if any remain."""
        for rw in list(self._rows):
            if rw.part_edits:
                parts, err = self._combo_resolved_parts(rw)
                if err is None and parts:
                    issue = rw.issue
                    issue.corrected = "/".join(parts)
                    issue.skip = False
                    issue.is_place_label = False
                    self._remove_row(rw, issue)
                continue
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
                if self._is_two_trailer_issue(rw.issue):
                    continue
                matched = try_match_fleet(norm.value, self._fleet)
                if matched is not None:
                    rw.edit.setText(matched)
                    issue = rw.issue
                    issue.corrected = self._with_combo_suffix(issue, matched)
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
            show_info(
                self,
                "Still need attention",
                f"{len(self._rows)} truck(s) still need attention.\n{tip}",
            )
            focus = self._rows[0]
            (focus.part_edits[0] if focus.part_edits else focus.edit).setFocus()
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
