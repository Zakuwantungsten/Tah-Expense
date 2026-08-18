from typing import List, Optional

from PySide6.QtWidgets import QLineEdit, QCompleter, QApplication
from PySide6.QtCore import Qt, QStringListModel, QTimer, QEvent
from PySide6.QtGui import QKeyEvent


def rank_completion_matches(query: str, values: List[str]) -> List[str]:
    """Return values that contain ``query``, ranked QuickBooks-style.

    Order within matches:
      1. exact (case-insensitive)
      2. starts-with
      3. word-boundary contains (match after a non-alnum, e.g. space)
      4. mid-string contains

    Non-matching values are omitted. Empty/whitespace query returns ``values`` unchanged.
    """
    q = (query or "").strip()
    if not q:
        return list(values)
    q_lower = q.lower()
    exact: List[str] = []
    prefix: List[str] = []
    word: List[str] = []
    mid: List[str] = []
    for v in values:
        vl = v.lower()
        if q_lower not in vl:
            continue
        if vl == q_lower:
            exact.append(v)
        elif vl.startswith(q_lower):
            prefix.append(v)
        else:
            idx = vl.find(q_lower)
            if idx > 0 and not vl[idx - 1].isalnum():
                word.append(v)
            else:
                mid.append(v)
    return exact + prefix + word + mid


def highlight_first(completer: QCompleter) -> None:
    """Select the first row of the popup so the user can see what Tab will pick."""
    popup = completer.popup()
    if popup is None or not popup.isVisible():
        return
    model = completer.completionModel()
    if model is None or model.rowCount() == 0:
        return
    if not popup.currentIndex().isValid():
        popup.setCurrentIndex(model.index(0, 0))


def show_completion_preview(
    line_edit: QLineEdit,
    typed: str,
    suggestion: str,
    *,
    replace_on_contains: bool = False,
) -> bool:
    """Show ``suggestion`` inline with the auto-completed part selected.

    The selected portion is replaced by the next keystroke so the user can
    keep typing naturally. ``typed`` is what the user actually entered and is
    preserved across popup navigation steps.

    For a prefix match ("T6" → "T688 EAF"):   T6[88 EAF]
    For a contains match ("pend" → "Pending") when ``replace_on_contains``:
      pend[ing] if prefix of suggestion, else [Pending] fully selected.

    Mid-string auto-preview is off by default so typing ``l`` does not replace
    the field with ``Diesel CSH`` ahead of a better prefix match like ``LATRA``.
    Returns True when the field text was updated.
    """
    if not suggestion:
        return False
    if suggestion.lower().startswith(typed.lower()) and len(suggestion) > len(typed):
        # Preserve the user's typed casing; append the rest as a selection.
        preview = typed + suggestion[len(typed):]
        line_edit.setText(preview)
        line_edit.setSelection(len(typed), len(suggestion) - len(typed))
        return True
    if replace_on_contains and typed and typed.lower() in suggestion.lower():
        # Contains match: show the full suggestion fully selected so typing replaces it.
        line_edit.setText(suggestion)
        line_edit.setSelection(0, len(suggestion))
        return True
    return False


def accept_completion(line_edit: QLineEdit, completer: QCompleter) -> bool:
    """Accept the highlighted popup item and write the canonical value into ``line_edit``.

    Accepts when:
    - the popup has an explicitly selected item (highlight_first or user navigation), OR
    - exactly one match remains (unambiguous).

    Returns False when the popup is not visible, or multiple candidates exist
    and none is selected — Tab/Enter then commit whatever is in the field
    (including an active inline preview).
    Always hides the popup when it was visible so focus can move cleanly.
    """
    popup = completer.popup()
    if popup is None or not popup.isVisible():
        return False
    idx = popup.currentIndex()
    if not idx.isValid():
        model = completer.completionModel()
        if model is None or model.rowCount() != 1:
            popup.hide()
            return False
        idx = model.index(0, 0)
    text = idx.data(Qt.DisplayRole)
    popup.hide()
    if text:
        committing = getattr(line_edit, "_committing", None)
        if committing is not None:
            line_edit._committing = True  # type: ignore[attr-defined]
        try:
            line_edit.setText(text)
            if hasattr(line_edit, "_typed"):
                line_edit._typed = text  # type: ignore[attr-defined]
            line_edit.setCursorPosition(len(text))
        finally:
            if committing is not None:
                line_edit._committing = False  # type: ignore[attr-defined]
        return True
    return False


def hide_completion_popup(completer: QCompleter) -> None:
    """Hide the completer popup if it is still open."""
    popup = completer.popup()
    if popup is not None and popup.isVisible():
        popup.hide()


class CompleterLineEdit(QLineEdit):
    """
    QLineEdit with an in-memory autocomplete popup and inline preview.

    Behaviour (QuickBooks-style):
    - As the user types, a dropdown shows all values that *contain* the typed
      text (case-insensitive). The first match is highlighted automatically.
    - With ``ranked_contains=True`` (Item column), matches are ordered exact →
      prefix → word-boundary → mid-string. The typed text is never rewritten
      until Tab / Enter / click accepts the highlighted row — so a short prefix
      cannot lock in a different item name.
    - Without ``ranked_contains``, prefix matches get an inline preview: the
      auto-completed part is shown as a selection, so the next keystroke
      replaces it and the user keeps typing naturally.
    - Down / Up navigate the popup; Tab / Enter accept the highlighted item
      (writing the canonical value). If the popup is hidden but a preview is
      showing, Tab keeps the preview text. Otherwise Tab commits typed text.
    - Delete / Backspace while a preview is showing dismisses the preview
      (restores the typed text) without accepting or re-triggering it.
    """

    def __init__(
        self,
        values: List[str],
        parent: Optional[QLineEdit] = None,
        *,
        ranked_contains: bool = False,
    ) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._ranked_contains = ranked_contains
        self._typed = ""          # last text the user actually typed (not from the preview)
        self._suppress_preview = False  # True after Delete dismisses preview
        self._preview_active   = False  # True while a preview suggestion is displayed
        self._committing = False  # True while Tab/click writes a chosen suggestion
        self._ignore_highlight = False  # True while we auto-select popup row 0

        self._model = QStringListModel(self._values, self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)
        # QLineEdit+PopupCompletion inserts the highlighted row via
        # _q_completionHighlighted → setText. That locks in a full item name
        # on the first letter and refills it after Backspace. Drop that slot
        # and handle highlight ourselves.
        self._disconnect_qt_highlight_insert()
        self._completer.highlighted[str].connect(self._on_highlighted)
        self._completer.activated[str].connect(self._on_activated)

        # While the popup is open it grabs the keyboard, so Tab/Enter never reach
        # the table delegate's "accept + move to next cell" filter installed on
        # this editor — the first press only closes the popup. Re-route those keys
        # back to the editor so a single Tab accepts the highlighted suggestion AND
        # advances the cell (Excel behaviour), instead of needing two presses.
        self._completer.popup().installEventFilter(self)

        self.textEdited.connect(self._on_text_edited)
        self.textEdited.connect(self._schedule_highlight)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._completer.popup() and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
                QApplication.sendEvent(
                    self, QKeyEvent(event.type(), event.key(), event.modifiers())
                )
                return True
        return super().eventFilter(obj, event)

    def _disconnect_qt_highlight_insert(self) -> None:
        """Best-effort: drop Python highlighted→setText if Qt exposed it.

        The C++ QLineEdit slot often cannot be disconnected from Python;
        ``_keep_typed_text`` still undoes that auto-insert.
        """
        signal = self._completer.highlighted[str]
        for slot in (self.setText, "_q_completionHighlighted"):
            try:
                signal.disconnect(self, slot)
            except (TypeError, RuntimeError):
                pass

    def _keep_typed_text(self) -> None:
        """Put back what the user typed if Qt/completer replaced the field."""
        if self._committing or not self._ranked_contains:
            return
        if self.text() != self._typed:
            self.setText(self._typed)
            self.setCursorPosition(len(self._typed))

    def _on_activated(self, text: str) -> None:
        """Click / Enter on a popup row — this is an explicit accept."""
        if not text:
            return
        self._committing = True
        try:
            self.setText(text)
            self._typed = text
            self.setCursorPosition(len(text))
        finally:
            QTimer.singleShot(0, self._end_commit)

    def _end_commit(self) -> None:
        self._committing = False

    def canonical(self, text: str) -> Optional[str]:
        """Return the stored value matching *text* case-insensitively, else None."""
        t = (text or "").strip().lower()
        for v in self._values:
            if v.lower() == t:
                return v
        return None

    def _reorder_model_for_query(self, text: str) -> None:
        """Put best contains-matches first so row 0 is the QuickBooks-style pick."""
        ranked = rank_completion_matches(text, self._values)
        if not ranked:
            self._model.setStringList(self._values)
            return
        ranked_set = set(ranked)
        rest = [v for v in self._values if v not in ranked_set]
        self._model.setStringList(ranked + rest)

    def _on_text_edited(self, text: str) -> None:
        if self._committing:
            return
        self._typed = text
        self._suppress_preview = False  # new keystroke — re-enable auto-preview
        self._preview_active   = False
        if self._ranked_contains:
            self._reorder_model_for_query(text)
            # setStringList can leave QCompleter's filter stale; reset the prefix
            # so the popup re-filters and shows matches as you type.
            self._completer.setCompletionPrefix("")
            if (text or "").strip():
                self._completer.setCompletionPrefix(text)

    def _on_highlighted(self, suggestion: str) -> None:
        # Fired when user navigates the popup (Down/Up). Re-derive the typed
        # prefix from field state because the field may currently be showing a
        # previous preview (the suffix is a text selection beyond the real cursor).
        if self._committing:
            return
        if self._ranked_contains:
            # Item column: list highlight only. Undo Qt's PopupCompletion setText
            # (it runs even when we skip our own preview).
            self._keep_typed_text()
            QTimer.singleShot(0, self._keep_typed_text)
            return
        if self._ignore_highlight:
            return
        current = self.text()
        typed = current[:self.selectionStart()] if self.hasSelectedText() else current
        # Prefer the last real keystroke when a prefix preview is already showing.
        if self._typed and current.lower().startswith(self._typed.lower()):
            typed = self._typed
        # User explicitly moved selection — allow full contains replace.
        shown = show_completion_preview(
            self, typed, suggestion, replace_on_contains=True
        )
        self._preview_active = shown

    def _schedule_highlight(self, _text: str) -> None:
        if self._suppress_preview or self._committing:
            return
        QTimer.singleShot(0, self._apply_first_suggestion_preview)

    def _apply_first_suggestion_preview(self) -> None:
        """Show the first matching suggestion as an inline preview.

        Called via QTimer so _typed is already up-to-date with what the user
        typed.  Reads the suggestion directly from the completion model instead
        of relying on the highlighted signal, which only fires when the popup's
        current index *changes* — meaning it silently does nothing on the second
        and subsequent keystrokes when the same suggestion stays at row 0.

        Auto-preview only applies for prefix matches. Mid-string hits stay in the
        popup (highlighted) without rewriting the field.
        """
        if self._suppress_preview or self._committing:
            return
        query = self._typed
        if not (query or "").strip():
            return
        # Ensure the completion model is filtered for the typed text (Qt may
        # still have an empty/stale prefix after a model rewrite).
        if self._completer.completionPrefix() != query:
            self._completer.setCompletionPrefix(query)
        model = self._completer.completionModel()
        if model is None or model.rowCount() == 0:
            return
        suggestion = model.index(0, 0).data(Qt.DisplayRole)
        if not suggestion:
            return
        popup = self._completer.popup()
        if popup is not None:
            if not popup.isVisible() and self._completer.completionCount() > 0:
                self._completer.complete()
            if popup.isVisible():
                self._ignore_highlight = True
                try:
                    popup.setCurrentIndex(model.index(0, 0))
                finally:
                    self._ignore_highlight = False
        if self._ranked_contains:
            # Keep the user's keystrokes; Tab/Enter/click accept the highlight.
            self._keep_typed_text()
            QTimer.singleShot(0, self._keep_typed_text)
            return
        # Prefix-only auto-preview (QuickBooks): typing "lat" → LAT[RA].
        shown = show_completion_preview(
            self,
            query,
            suggestion,
            replace_on_contains=False,
        )
        self._preview_active = shown

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        # Delete/Backspace while a preview suggestion is displayed: dismiss the
        # preview (restore typed text) instead of deleting it and re-triggering
        # the autocomplete cycle.
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self._preview_active:
            self._suppress_preview = True
            self._preview_active   = False
            self.setText(self._typed)
            self.setCursorPosition(len(self._typed))
            event.accept()
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self._ranked_contains:
            # If Qt already inserted a full item name, put the real keystrokes
            # back first so this Backspace can actually erase.
            self._keep_typed_text()
            self._suppress_preview = True

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if accept_completion(self, self._completer):
                self._preview_active = False
                event.accept()
                return
            hide_completion_popup(self._completer)
            if self._preview_active:
                self.deselect()
                self.setCursorPosition(len(self.text()))
            self._preview_active = False

        if key == Qt.Key_Tab:
            # Accept highlighted suggestion, or keep the inline preview text.
            if not accept_completion(self, self._completer):
                hide_completion_popup(self._completer)
                if self._preview_active:
                    self.deselect()
                    self.setCursorPosition(len(self.text()))
            self._preview_active = False
            # Defer focus move; when embedded in the grid the delegate's Tab
            # filter usually handles this first and calls _tab_forward instead.
            self.focusNextPrevChild(
                not bool(event.modifiers() & Qt.ShiftModifier)
            )
            event.accept()
            return

        super().keyPressEvent(event)
