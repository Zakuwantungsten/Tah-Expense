import asyncio
from typing import Callable, Awaitable, List

from PySide6.QtWidgets import QLineEdit, QCompleter
from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtGui import QKeyEvent

from tahmeed.ui.widgets.completer_line_edit import (
    accept_completion, highlight_first, show_completion_preview,
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
        self._typed = ""  # last text the user actually typed (not from the preview)

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        # Connect after setCompleter() so our handler fires last and wins over any
        # Qt-internal highlighted→setText that lacks a selection.
        self._completer.highlighted[str].connect(self._on_highlighted)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._trigger_fetch)

        self.textEdited.connect(self._on_text_edited)

    def _on_text_edited(self, text: str) -> None:
        self._typed = text
        self._debounce.stop()
        if len(text.strip()) >= 2:
            self._debounce.start()
        else:
            self._model.setStringList([])

    def _on_highlighted(self, suggestion: str) -> None:
        show_completion_preview(self, self._typed, suggestion)

    def _trigger_fetch(self) -> None:
        # Use _typed (not self.text()) so preview text doesn't affect the query.
        asyncio.ensure_future(self._fetch_suggestions(self._typed))

    async def _fetch_suggestions(self, text: str) -> None:
        try:
            suggestions = await self._fetch_fn(text)
            self._model.setStringList(suggestions)
            if suggestions:
                self._completer.complete()
                QTimer.singleShot(0, lambda: highlight_first(self._completer))
        except Exception:
            pass

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
            if accept_completion(self, self._completer):
                event.accept()
                return
        super().keyPressEvent(event)
