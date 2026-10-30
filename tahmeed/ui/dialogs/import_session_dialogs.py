"""Dialogs for the bulk Import Daily page: map, mapping pack, finish."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.import_session_pack import (
    PACK_FILE_SUFFIX,
    PACK_MEMORY_FILE,
    MappingPack,
    find_pack_source,
    load_builtin_pack,
    read_mapping_pack,
    write_mapping_pack,
)
from tahmeed.services.import_session_validation import rank_truck_matches
from tahmeed.ui.accountant.import_session_grid import (
    GridModel,
    GridRow,
    GridView,
    light_theme_qss,
)
from tahmeed.ui.dialog_theme import show_info, show_question, show_warning
from tahmeed.ui.dialogs.not_in_list_dialog import (
    ask_not_in_list,
    run_add_item_dialog,
    run_add_payee_dialog,
)
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit

_SETTINGS_ORG = "Tahmeed"
_SETTINGS_APP = "tahmeed-expense"
_PACK_FOLDER_KEY = "import_session/pack_folder"

_BTN = (
    "QPushButton{{background:{bg};color:{fg};border:1px solid {bd};border-radius:5px;"
    "padding:5px 14px;font-family:'Segoe UI';font-size:12px;font-weight:600;}}"
    "QPushButton:hover{{background:{hover};}}"
    "QPushButton:disabled{{background:#E5E7EB;color:#9CA3AF;border-color:#E5E7EB;}}"
)


def dialog_button(text: str, *, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if primary:
        btn.setStyleSheet(_BTN.format(bg="#0077C5", fg="#FFFFFF", bd="#0077C5", hover="#005EA3"))
    else:
        btn.setStyleSheet(_BTN.format(bg="#FFFFFF", fg="#111827", bd="#D1D5DB", hover="#F3F4F6"))
    return btn


def _light(dialog: QDialog) -> None:
    dialog.setStyleSheet("QDialog{background:#FFFFFF;color:#111827;}" + light_theme_qss())


def _same_name(a: str, b: str) -> bool:
    return " ".join((a or "").upper().split()) == " ".join((b or "").upper().split())


def ask_new_payee(parent: QWidget, name: str) -> Optional[Tuple[str, str]]:
    """'Not in the Payee list — add it?' then the Add Payee form: ``(name, currency)``."""
    if not ask_not_in_list(parent, entity="Payee", name=name):
        return None
    return run_add_payee_dialog(parent, prefill=name)


def ask_new_item(parent: QWidget, name: str) -> Optional[dict]:
    """'Not in the Item list — add it?' then the Add New Item form (its fields)."""
    if not ask_not_in_list(parent, entity="Item", name=name):
        return None
    return run_add_item_dialog(parent, prefill_name=name)


@dataclass
class MappingChoice:
    descriptions: List[str]
    item: str
    payee: str
    # Create these first (Add New Item fields / (name, currency)).
    new_item: Optional[dict] = None
    new_payee: Optional[Tuple[str, str]] = None
    save_global: bool = False


class MapDescriptionsDialog(QDialog):
    """One Item + Payee for every ticked description; may add a new Item / Payee."""

    def __init__(
        self,
        descriptions: Sequence[str],
        *,
        item_names: Sequence[str],
        payee_names: Sequence[str],
        resolve_item: Callable[[str], Optional[str]],
        resolve_payee: Callable[[str], Optional[str]],
        initial_item: str = "",
        initial_payee: str = "",
        save_global: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _light(self)
        self._resolve_item = resolve_item
        self._resolve_payee = resolve_payee
        self._new_item: Optional[dict] = None
        self._new_payee: Optional[Tuple[str, str]] = None
        self.choice: Optional[MappingChoice] = None
        self.setWindowTitle("Map descriptions")
        self.setMinimumWidth(600)

        root = QVBoxLayout(self)
        intro = QLabel(
            f"<b>{len(descriptions):,} description(s)</b>. Pick a Payee and Item, or add "
            "new ones — new Items and Payees are created right away for everyone."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._list = QListWidget()
        for desc in descriptions:
            item = QListWidgetItem(desc)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)
        self._list.setMaximumHeight(220 if len(descriptions) > 1 else 60)
        root.addWidget(self._list)

        form = QFormLayout()
        self._payee = CompleterLineEdit(list(payee_names), ranked_contains=True)
        self._payee.setPlaceholderText("Type to search payees…")
        self._payee.setText(initial_payee)
        self._item = CompleterLineEdit(list(item_names), ranked_contains=True)
        self._item.setPlaceholderText("Type to search items (accounts)…")
        self._item.setText(initial_item)
        new_payee = dialog_button("New Payee…")
        new_payee.clicked.connect(self._on_new_payee)
        new_item = dialog_button("New Item…")
        new_item.clicked.connect(self._on_new_item)
        for label, field_, button in (
            ("PAYEE", self._payee, new_payee),
            ("ITEM", self._item, new_item),
        ):
            row = QHBoxLayout()
            row.addWidget(field_, 1)
            row.addWidget(button)
            form.addRow(label, row)
        root.addLayout(form)

        self._save_global = QCheckBox(
            "Also save to Description Maps now (permanent, used on every PC)"
        )
        self._save_global.setChecked(save_global)
        self._save_global.setToolTip(
            "Unticked: a temporary mapping for this import session only."
        )
        root.addWidget(self._save_global)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = dialog_button("Cancel")
        cancel.clicked.connect(self.reject)
        save = dialog_button("Save Mapping", primary=True)
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self._payee.setFocus()

    def _ticked(self) -> List[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        ]

    def _on_new_payee(self) -> None:
        created = run_add_payee_dialog(self, prefill=self._payee.text().strip())
        if created:
            self._new_payee = created
            self._payee.setText(created[0])

    def _on_new_item(self) -> None:
        data = run_add_item_dialog(self, prefill_name=self._item.text().strip())
        if data:
            self._new_item = data
            self._item.setText(str(data.get("name") or "").strip())

    def _on_save(self) -> None:
        descriptions = self._ticked()
        item_text = self._item.text().strip()
        payee_text = self._payee.text().strip()
        if not descriptions:
            show_warning(self, "Map descriptions", "Tick at least one description.")
            return
        if not item_text or not payee_text:
            show_warning(self, "Map descriptions", "Both PAYEE and ITEM are required.")
            return

        new_payee = self._new_payee if self._new_payee and _same_name(self._new_payee[0], payee_text) else None
        payee = None if new_payee else self._resolve_payee(payee_text)
        if payee is None and new_payee is None:
            new_payee = ask_new_payee(self, payee_text)
            if new_payee is None:
                return
            self._payee.setText(new_payee[0])

        new_item = (
            self._new_item
            if self._new_item and _same_name(str(self._new_item.get("name") or ""), item_text)
            else None
        )
        item = None if new_item else self._resolve_item(item_text)
        if item is None and new_item is None:
            new_item = ask_new_item(self, item_text)
            if new_item is None:
                return
            self._item.setText(str(new_item.get("name") or ""))

        self.choice = MappingChoice(
            descriptions,
            item=item or str(new_item.get("name") or "").strip(),
            payee=payee or new_payee[0],
            new_item=new_item,
            new_payee=new_payee,
            save_global=self._save_global.isChecked(),
        )
        self.accept()


@dataclass
class PackChoice:
    pack: MappingPack
    overwrite_manual: bool


class LoadPackDialog(QDialog):
    """Pick the built-in pack, the automation folder or a pack file to load."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        _light(self)
        self.choice: Optional[PackChoice] = None
        self._pack: Optional[MappingPack] = None
        self._builtin = load_builtin_pack()
        self.setWindowTitle("Load mapping pack")
        self.setMinimumWidth(660)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Load the QuickBooks expense automation's description mappings into this "
            "session as <b>temporary</b> mappings. Accounts become Items; nothing is "
            "saved to Description Maps.<br>Without a pack, rows are mapped with the "
            "<b>Description Maps</b> tab only. Trucks are never taken from a pack — "
            "fix each row's truck under Truck Issues."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._use_builtin = QRadioButton(self._builtin_label())
        self._use_builtin.setEnabled(self._builtin is not None)
        self._use_other = QRadioButton("Automation folder or pack file (.tmpack.json):")
        root.addWidget(self._use_builtin)
        root.addWidget(self._use_other)
        group = QButtonGroup(self)
        group.addButton(self._use_builtin)
        group.addButton(self._use_other)

        row = QHBoxLayout()
        row.setContentsMargins(22, 0, 0, 0)
        self._folder = QLineEdit()
        self._folder.setPlaceholderText("…\\expense automation   or   …\\pack.tmpack.json")
        self._folder.editingFinished.connect(self._refresh)
        browse = dialog_button("Folder…")
        browse.clicked.connect(self._browse_folder)
        browse_file = dialog_button("Pack File…")
        browse_file.clicked.connect(self._browse_file)
        row.addWidget(self._folder, 1)
        row.addWidget(browse)
        row.addWidget(browse_file)
        root.addLayout(row)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#6B7280;font-size:11px;")
        root.addWidget(self._status)

        self._count = QLabel()
        root.addWidget(self._count)
        self._overwrite = QCheckBox("Replace mappings I already typed in this session")
        root.addWidget(self._overwrite)

        buttons = QHBoxLayout()
        self._export_btn = dialog_button("Export Pack…")
        self._export_btn.setToolTip(
            "Save the chosen pack as one .tmpack.json file to copy to another PC"
        )
        self._export_btn.clicked.connect(self._on_export)
        buttons.addWidget(self._export_btn)
        buttons.addStretch()
        cancel = dialog_button("Cancel")
        cancel.clicked.connect(self.reject)
        self._load_btn = dialog_button("Load into Session", primary=True)
        self._load_btn.setDefault(True)
        self._load_btn.clicked.connect(self._on_load)
        buttons.addWidget(cancel)
        buttons.addWidget(self._load_btn)
        root.addLayout(buttons)

        saved = str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_PACK_FOLDER_KEY, "") or "")
        self._folder.setText(saved)
        if saved or self._builtin is None:
            self._use_other.setChecked(True)
        else:
            self._use_builtin.setChecked(True)
        group.buttonToggled.connect(lambda _b, _c: self._refresh())
        self._refresh()

    def _builtin_label(self) -> str:
        pack = self._builtin
        if pack is None:
            return "Built-in pack (not included in this build)"
        when = f", extracted {pack.created_at.strftime('%d/%m/%Y')}" if pack.created_at else ""
        return f"Built-in pack — {pack.name or 'Expense automation'}{when}"

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Expense automation folder", self._folder.text()
        )
        if folder:
            self._folder.setText(folder)
            self._use_other.setChecked(True)
            self._refresh()

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Mapping pack file",
            self._folder.text(),
            f"Mapping packs (*{PACK_FILE_SUFFIX} *.json);;All files (*)",
        )
        if path:
            self._folder.setText(path)
            self._use_other.setChecked(True)
            self._refresh()

    def _refresh(self) -> None:
        self._folder.setEnabled(self._use_other.isChecked())
        if self._use_builtin.isChecked():
            self._show_pack(self._builtin, "Built-in pack shipped with the app.")
        else:
            self._read(self._folder.text())

    def _read(self, text: str) -> None:
        source = find_pack_source(text.strip()) if text.strip() else None
        if source is None:
            self._show_pack(
                None,
                f"No pack file, and no {PACK_MEMORY_FILE} here."
                if text.strip()
                else "Choose the automation folder or a pack file.",
            )
            return
        self._show_pack(read_mapping_pack(source), f"Reading from {source}")

    def _show_pack(self, pack: Optional[MappingPack], note: str) -> None:
        self._pack = pack
        notes = [note]
        if pack is not None:
            if pack.missing_files:
                notes.append("Not found: " + ", ".join(pack.missing_files))
            if pack.errors:
                notes.append("Could not read: " + "; ".join(pack.errors))
        self._status.setText("<br>".join(notes))
        count = len(pack.mappings) if pack else 0
        self._count.setText(f"Description mappings (Payee + Account → Item): <b>{count:,}</b>")
        usable = pack is not None and not pack.is_empty
        self._load_btn.setEnabled(usable)
        self._export_btn.setEnabled(usable)

    def _on_export(self) -> None:
        pack = self._pack
        if pack is None or pack.is_empty:
            return
        stem = (pack.name or "mapping pack").strip()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export mapping pack",
            f"{stem}{PACK_FILE_SUFFIX}",
            f"Mapping packs (*{PACK_FILE_SUFFIX})",
        )
        if not path:
            return
        if not path.lower().endswith(PACK_FILE_SUFFIX):
            path = path.removesuffix(".json") + PACK_FILE_SUFFIX
        try:
            write_mapping_pack(pack, path, name=pack.name)
        except OSError as exc:
            show_warning(self, "Export mapping pack", f"Could not save the pack:\n\n{exc}")
            return
        show_info(
            self,
            "Export mapping pack",
            f"Saved {len(pack.mappings):,} mappings to\n{path}\n\n"
            "On the other PC: Import Daily → Load Mapping Pack… → Pack File…",
        )

    def _on_load(self) -> None:
        pack = self._pack
        if pack is None:
            return
        remembered = "" if self._use_builtin.isChecked() else self._folder.text().strip()
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_PACK_FOLDER_KEY, remembered)
        self.choice = PackChoice(pack=pack, overwrite_manual=self._overwrite.isChecked())
        self.accept()


@dataclass
class PromoteCandidate:
    key: str
    description: str
    payee: str
    item: str
    source: str
    rows: int
    current: str  # the Description Map in place today, if any
    ticked: bool = False


class FinishSessionDialog(QDialog):
    """End a session: optionally save some temporary mappings, then discard it."""

    def __init__(
        self,
        candidates: Sequence[PromoteCandidate],
        *,
        note: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _light(self)
        self._candidates = list(candidates)
        self.promote_keys: List[str] = []
        self.setWindowTitle("Finish import session")
        self.resize(980, 560)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Finishing discards this session: its staged rows, temporary mappings and truck "
            "fixes. Rows already pushed, new Items / Payees and mappings already saved to "
            "Description Maps all stay.<br>"
            "Tick temporary mappings to keep for good — they are saved to "
            "<b>Description Maps</b> first."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        if note:
            warn = QLabel(note)
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#B91C1C;font-weight:600;")
            root.addWidget(warn)

        bar = QHBoxLayout()
        self._used_only = QCheckBox("Only descriptions in this session's rows")
        self._used_only.setChecked(True)
        self._used_only.toggled.connect(lambda _c: self._fill())
        search = QLineEdit()
        search.setPlaceholderText("Search…")
        search.setClearButtonEnabled(True)
        search.setMaximumWidth(240)
        bar.addWidget(self._used_only)
        bar.addWidget(search)
        bar.addStretch()
        self._count = QLabel()
        bar.addWidget(self._count)
        root.addLayout(bar)

        self._model = GridModel(
            ["DESCRIPTION", "PAYEE", "ITEM", "FROM", "ROWS", "DESCRIPTION MAP NOW"],
            checkable=True,
            right_align=[4],
        )
        self._model.checks_changed.connect(self._update_count)
        self._view = GridView(self._model)
        search.textChanged.connect(self._view.set_filter_text)
        root.addWidget(self._view, 1)

        buttons = QHBoxLayout()
        cancel = dialog_button("Cancel")
        cancel.clicked.connect(self.reject)
        discard = dialog_button("Finish Without Saving")
        discard.clicked.connect(self._on_discard)
        self._save_btn = dialog_button("Save Ticked && Finish", primary=True)
        self._save_btn.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addStretch()
        buttons.addWidget(discard)
        buttons.addWidget(self._save_btn)
        root.addLayout(buttons)

        self._fill()
        self._model.set_checked([c.key for c in self._candidates if c.ticked], True)
        self._update_count()

    def _fill(self) -> None:
        ticked = set(self._model.checked_keys())
        shown = [c for c in self._candidates if c.rows or not self._used_only.isChecked()]
        self._model.set_rows(
            [
                GridRow(
                    key=c.key,
                    cells=[c.description, c.payee, c.item, c.source, c.rows, c.current],
                )
                for c in shown
            ]
        )
        self._model.set_checked([k for k in ticked if k in set(self._model.keys())], True)
        self._view.fit_columns(max_width=300)
        self._update_count()

    def _update_count(self) -> None:
        ticked = len(self._model.checked_keys())
        self._count.setText(f"{ticked:,} of {self._model.rowCount():,} ticked")
        self._save_btn.setEnabled(ticked > 0)

    def _on_discard(self) -> None:
        self.promote_keys = []
        self.accept()

    def _on_save(self) -> None:
        self.promote_keys = [str(k) for k in self._model.checked_keys()]
        self.accept()


@dataclass
class WrongTruck:
    """One wrong Excel truck value and the rows that carry it."""

    value: str
    row_ids: List[str]
    examples: List[str]


class TruckFixDialog(QDialog):
    """Fix wrong trucks one Excel value at a time (compact Correct Truck dialog).

    Apply & Next saves a truck or trailer from the list, Accept as Empty saves
    "no truck", Allow keeps a value that is not one of our trucks. Each choice
    goes on every row with that same Excel value; Skip leaves it for later.
    """

    def __init__(
        self,
        wrong: Sequence[WrongTruck],
        *,
        choices: Sequence[str],
        resolve: Callable[[str], Optional[str]],
        on_apply: Callable[[WrongTruck, str, bool], None],
        start_value: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _light(self)
        self.setWindowTitle("Correct Truck")
        self.resize(520, 470)
        self._remaining: List[WrongTruck] = list(wrong)
        self._choices = list(choices)
        self._resolve = resolve
        self._on_apply = on_apply
        self.applied_count = 0

        root = QVBoxLayout(self)
        root.setSpacing(3)
        intro = QLabel(
            "Fix one wrong truck at a time. The fix goes on every row with that same "
            "Excel value in this session only."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#6B7280;")
        root.addWidget(intro)

        def section(label: QLabel) -> QLabel:
            label.setStyleSheet("color:#0077C5;font-size:11px;font-weight:700;margin-top:6px;")
            root.addWidget(label)
            return label

        self._queue_label = section(QLabel())
        self._queue = QListWidget()
        self._queue.setMaximumHeight(110)
        self._queue.currentRowChanged.connect(self._load_current)
        root.addWidget(self._queue)
        self._rows_label = QLabel()
        self._rows_label.setStyleSheet("color:#6B7280;font-size:11px;")
        self._rows_label.setWordWrap(True)
        root.addWidget(self._rows_label)

        section(QLabel("CORRECT TRUCK"))
        self._correct = CompleterLineEdit(self._choices, matcher=rank_truck_matches)
        self._correct.setPlaceholderText("Type a truck, trailer or motorcycle — Tab picks it…")
        caps = self._correct.font()
        caps.setCapitalization(QFont.AllUppercase)
        self._correct.setFont(caps)
        self._correct.textChanged.connect(lambda _t: self._refresh_preview())
        root.addWidget(self._correct)

        self._preview_label = section(QLabel())
        self._preview = QListWidget()
        self._preview.itemClicked.connect(lambda item: self._correct.setText(item.text()))
        self._preview.itemDoubleClicked.connect(self._on_preview_double)
        root.addWidget(self._preview, 1)
        self._match_count = QLabel()
        self._match_count.setStyleSheet("color:#6B7280;font-size:11px;")
        root.addWidget(self._match_count)

        buttons = QHBoxLayout()
        buttons.addStretch()
        for text, slot, primary, tip in (
            ("Close", self.reject, False, "Close; fixes already applied stay."),
            ("Skip", self._skip, False, "Leave this one for later."),
            ("Accept as Empty", self._accept_empty, False, "These rows have no truck."),
            ("Allow", self._allow, False, "Keep this value although it is not one of our trucks."),
            ("Apply && Next", self._apply, True, "Save the truck typed above (Enter)."),
        ):
            btn = dialog_button(text, primary=primary)
            btn.setAutoDefault(False)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        btn.setDefault(True)
        root.addLayout(buttons)
        # Tab in CORRECT TRUCK takes the highlighted suggestion, then Enter applies.
        QWidget.setTabOrder(self._correct, btn)

        start = next(
            (i for i, w in enumerate(self._remaining) if _same_name(w.value, start_value)), 0
        )
        self._reload(start)

    # -- queue -------------------------------------------------------------

    def _reload(self, index: int = 0) -> None:
        self._queue.blockSignals(True)
        self._queue.clear()
        for wrong in self._remaining:
            self._queue.addItem(f"{wrong.value}  ({len(wrong.row_ids):,} row(s))")
        self._queue.blockSignals(False)
        self._queue_label.setText(
            f"WRONG TRUCKS — <b>{len(self._remaining):,}</b> left"
        )
        if not self._remaining:
            self._rows_label.clear()
            self._correct.clear()
            self._preview.clear()
            self._match_count.setText("All wrong trucks in this list are fixed.")
            return
        self._queue.setCurrentRow(min(max(index, 0), len(self._remaining) - 1))

    def _current(self) -> Optional[WrongTruck]:
        index = self._queue.currentRow()
        if 0 <= index < len(self._remaining):
            return self._remaining[index]
        return None

    def _load_current(self, _index: int = -1) -> None:
        wrong = self._current()
        if wrong is None:
            return
        more = len(wrong.row_ids) - len(wrong.examples)
        lines = list(wrong.examples) + ([f"… and {more:,} more"] if more > 0 else [])
        self._rows_label.setText("\n".join(lines))
        self._correct.clear()
        self._refresh_preview()
        self._correct.setFocus()

    # -- preview -----------------------------------------------------------

    def _refresh_preview(self) -> None:
        typed = self._correct.text().strip().upper()
        wrong = self._current()
        query = typed or (wrong.value if wrong else "")
        matches = rank_truck_matches(query, self._choices, limit=80) if query else []
        self._preview.clear()
        self._preview.addItems(matches)
        if typed:
            self._preview_label.setText("OUR FLEET (click to fill · double-click to apply)")
            self._match_count.setText(f"{len(matches):,} match(es) for '{typed}'")
        else:
            self._preview_label.setText(
                "CLOSEST IN OUR FLEET (click to fill · double-click to apply)"
            )
            self._match_count.setText(
                f"{len(matches):,} close to '{query}' — nothing is picked for you."
                if matches
                else "Nothing close in our list — type the truck, or Allow / Accept as Empty."
            )

    def _on_preview_double(self, item: QListWidgetItem) -> None:
        self._correct.setText(item.text())
        self._apply()

    # -- actions -----------------------------------------------------------

    def _apply(self) -> None:
        wrong = self._current()
        if wrong is None:
            self.accept()
            return
        text = " ".join(self._correct.text().upper().split())
        if not text:
            show_warning(self, "Correct Truck", "Type or pick the correct truck first.")
            return
        plate = self._resolve(text)
        if plate is not None:
            self._commit(wrong, plate, False)
            return
        answer = show_question(
            self,
            "Not one of our trucks",
            f"'{text}' is not in our Trucks, Trailers or Motorcycles & Cars.\n\n"
            f"Allow it anyway on {len(wrong.row_ids):,} row(s)?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._commit(wrong, text, True)

    def _allow(self) -> None:
        wrong = self._current()
        if wrong is None:
            return
        text = " ".join(self._correct.text().upper().split()) or wrong.value
        plate = self._resolve(text)
        if plate is not None:
            self._commit(wrong, plate, False)
            return
        answer = show_question(
            self,
            "Allow truck",
            f"Keep '{text}' on {len(wrong.row_ids):,} row(s)?\n\n"
            "It is not in our Trucks, Trailers or Motorcycles & Cars; it will be saved "
            "exactly as shown.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._commit(wrong, text, True)

    def _accept_empty(self) -> None:
        wrong = self._current()
        if wrong is None:
            return
        answer = show_question(
            self,
            "Accept as Empty",
            f"Save {len(wrong.row_ids):,} row(s) with '{wrong.value}' as no truck?\n\n"
            "Only for expenses that are not for any truck or trailer.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._commit(wrong, "", False)

    def _commit(self, wrong: WrongTruck, plate: str, allowed: bool) -> None:
        index = self._remaining.index(wrong)
        self._on_apply(wrong, plate, allowed)
        self.applied_count += 1
        self._remaining.remove(wrong)
        if not self._remaining:
            show_info(
                self, "Correct Truck", f"Done — applied {self.applied_count:,} fix(es)."
            )
            self.accept()
            return
        self._reload(index)

    def _skip(self) -> None:
        if self._remaining:
            self._queue.setCurrentRow((self._queue.currentRow() + 1) % len(self._remaining))
