from typing import List, Optional

from PySide6.QtWidgets import QLineEdit, QCompleter
from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtGui import QKeyEvent


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


def show_completion_preview(line_edit: QLineEdit, typed: str, suggestion: str) -> None:
    """Show ``suggestion`` inline with the auto-completed part selected.

    The selected portion is replaced by the next keystroke so the user can
    keep typing naturally. ``typed`` is what the user actually entered and is
    preserved across popup navigation steps.

    For a prefix match ("T6" → "T688 EAF"):   T6[88 EAF]
    For a contains match ("pend" → "Pending"): pend[ing]
    For a non-prefix contains ("ing" → "Pending"): [Pending]
    """
    if not suggestion:
        return
    if suggestion.lower().startswith(typed.lower()) and len(suggestion) > len(typed):
        # Preserve the user's typed casing; append the rest as a selection.
        preview = typed + suggestion[len(typed):]
        line_edit.setText(preview)
        line_edit.setSelection(len(typed), len(suggestion) - len(typed))
    elif typed and typed.lower() in suggestion.lower():
        # Contains match: show the full suggestion fully selected so typing replaces it.
        line_edit.setText(suggestion)
        line_edit.setSelection(0, len(suggestion))


def accept_completion(line_edit: QLineEdit, completer: QCompleter) -> bool:
    """Accept the highlighted popup item and write the canonical value into ``line_edit``.

    Accepts when:
    - the popup has an explicitly selected item (highlight_first or user navigation), OR
    - exactly one match remains (unambiguous).

    Returns False when the popup is not visible, or multiple candidates exist
    and none is selected — Tab/Enter then commit whatever is in the field.
    """
    popup = completer.popup()
    if popup is None or not popup.isVisible():
        return False
    idx = popup.currentIndex()
    if not idx.isValid():
        model = completer.completionModel()
        if model is None or model.rowCount() != 1:
            return False
        idx = model.index(0, 0)
    text = idx.data(Qt.DisplayRole)
    if text:
        line_edit.setText(text)
        popup.hide()
        return True
    return False


class CompleterLineEdit(QLineEdit):
    """
    QLineEdit with an in-memory autocomplete popup and inline preview.

    Behaviour:
    - As the user types, a dropdown shows all values that *contain* the typed
      text (case-insensitive). The first match is highlighted automatically.
    - The highlighted suggestion also appears inline in the field: the
      auto-completed part is shown as a selection, so the next keystroke
      replaces it and the user keeps typing naturally.
    - Down / Up navigate the popup; Tab / Enter accept the highlighted item
      (writing the canonical value). If multiple matches exist and none is
      selected, Tab / Enter commit the typed text as-is.
    """

    def __init__(self, values: List[str], parent: Optional[QLineEdit] = None) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._typed = ""  # last text the user actually typed (not from the preview)

        self._model = QStringListModel(self._values, self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        # Connect after setCompleter() so our handler fires last and wins over any
        # Qt-internal highlighted→setText that lacks a selection.
        self._completer.highlighted[str].connect(self._on_highlighted)

        self.textEdited.connect(self._on_text_edited)
        self.textEdited.connect(self._schedule_highlight)

    def canonical(self, text: str) -> Optional[str]:
        """Return the stored value matching *text* case-insensitively, else None."""
        t = (text or "").strip().lower()
        for v in self._values:
            if v.lower() == t:
                return v
        return None

    def _on_text_edited(self, text: str) -> None:
        self._typed = text

    def _on_highlighted(self, suggestion: str) -> None:
        show_completion_preview(self, self._typed, suggestion)

    def _schedule_highlight(self, _text: str) -> None:
        QTimer.singleShot(0, lambda: highlight_first(self._completer))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
            if accept_completion(self, self._completer):
                event.accept()
                return
        super().keyPressEvent(event)
