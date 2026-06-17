import asyncio
from typing import Callable, Awaitable, List

from PySide6.QtWidgets import QLineEdit, QCompleter, QApplication
from PySide6.QtCore import Qt, QStringListModel, QTimer, QEvent
from PySide6.QtGui import QKeyEvent

from tahmeed.ui.widgets.completer_line_edit import (
    accept_completion, show_completion_preview,
)


class TruckLineEdit(QLineEdit):
    """
    QLineEdit with async truck-number autocomplete and inline preview.

    - User types a prefix → queries MongoDB after 150 ms debounce.
    - The first suggestion is highlighted in the popup AND shown inline in
      the field (the auto-completed portion is selected so the next keystroke
      replaces it naturally — e.g. typing "T6" shows "T6[88 EAF]").
    - Down / Up navigate the popup; Tab / Enter accept the highlighted item.
      Pressing Tab / Enter without navigating commits the typed text as-is
      (unless there is only one suggestion, which is auto-accepted).
    - Minimum 2 characters before the first query fires.
    """

    def __init__(
        self,
        fetch_fn: Callable[[str], Awaitable[List[str]]],
        parent=None,
    ):
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._typed = ""           # last text the user actually typed (not from the preview)
        self._suppress_preview = False  # True after Delete dismisses preview
        self._preview_active   = False  # True while a preview suggestion is displayed

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        # Connect after setCompleter() so our handler fires last and wins over any
        # Qt-internal highlighted→setText that lacks a selection.
        self._completer.highlighted[str].connect(self._on_highlighted)

        # While the popup is open it grabs the keyboard, so Tab/Enter never reach
        # the table delegate's "accept + move to next cell" filter installed on
        # this editor — the first press only closes the popup. Re-route those keys
        # back to the editor so a single Tab accepts the highlighted suggestion AND
        # advances the cell (Excel behaviour), instead of needing two presses.
        self._completer.popup().installEventFilter(self)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._trigger_fetch)

        self.textEdited.connect(self._on_text_edited)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._completer.popup() and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
                QApplication.sendEvent(
                    self, QKeyEvent(event.type(), event.key(), event.modifiers())
                )
                return True
        return super().eventFilter(obj, event)

    def _on_text_edited(self, text: str) -> None:
        self._typed = text
        self._suppress_preview = False  # new keystroke — re-enable auto-preview
        self._preview_active   = False
        self._debounce.stop()
        if len(text.strip()) >= 2:
            self._debounce.start()
        else:
            self._model.setStringList([])

    def _on_highlighted(self, suggestion: str) -> None:
        # Fired when user navigates the popup (Down/Up). Re-derive typed prefix
        # from field state in case the field is showing a previous preview.
        current = self.text()
        typed = current[:self.selectionStart()] if self.hasSelectedText() else current
        self._typed = typed
        show_completion_preview(self, typed, suggestion)
        self._preview_active = True

    def _trigger_fetch(self) -> None:
        asyncio.ensure_future(self._fetch_suggestions(self._typed))

    async def _fetch_suggestions(self, text: str) -> None:
        try:
            suggestions = await self._fetch_fn(text)
            self._model.setStringList(suggestions)
            if suggestions:
                self._completer.complete()
                if not self._suppress_preview:
                    QTimer.singleShot(0, self._apply_first_suggestion_preview)
        except Exception:
            pass

    def _apply_first_suggestion_preview(self) -> None:
        """Show the first matching suggestion as an inline preview.

        Same rationale as CompleterLineEdit._apply_first_suggestion_preview:
        bypasses the highlighted signal to avoid the 'already at row 0, no
        change, no signal' dead-end on every keystroke after the first.
        """
        if self._suppress_preview:
            return
        model = self._completer.completionModel()
        if model is None or model.rowCount() == 0:
            return
        suggestion = model.index(0, 0).data(Qt.DisplayRole)
        if not suggestion:
            return
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            popup.setCurrentIndex(model.index(0, 0))
        show_completion_preview(self, self._typed, suggestion)
        self._preview_active = True

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

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if accept_completion(self, self._completer):
                event.accept()
                return
        super().keyPressEvent(event)
