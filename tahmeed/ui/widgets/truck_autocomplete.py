import asyncio
from typing import Callable, Awaitable, List

from PySide6.QtWidgets import QLineEdit, QCompleter
from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtGui import QKeyEvent


class TruckLineEdit(QLineEdit):
    """
    QLineEdit with async truck-number autocomplete.

    Behaviour:
    - User types a prefix (e.g. "t688") → queries MongoDB, shows a dropdown
      of all matching truck numbers (e.g. T688 EAF, T688 DZY, T688 EAZ).
    - Typing more characters (e.g. "t688 e") narrows the dropdown in real time.
    - Pressing Tab accepts the currently highlighted suggestion and closes the popup.
    - Minimum 2 characters before the first query fires; 150 ms debounce.

    Usage:
        from tahmeed.services.truck_service import search_trucks
        field = TruckLineEdit(fetch_fn=search_trucks)
    """

    def __init__(
        self,
        fetch_fn: Callable[[str], Awaitable[List[str]]],
        parent=None,
    ):
        super().__init__(parent)
        self._fetch_fn = fetch_fn

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._trigger_fetch)

        self.textEdited.connect(self._on_text_edited)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_text_edited(self, text: str) -> None:
        self._debounce.stop()
        if len(text.strip()) >= 2:
            self._debounce.start()
        else:
            self._model.setStringList([])

    def _trigger_fetch(self) -> None:
        asyncio.ensure_future(self._fetch_suggestions(self.text()))

    async def _fetch_suggestions(self, text: str) -> None:
        try:
            suggestions = await self._fetch_fn(text)
            self._model.setStringList(suggestions)
            if suggestions:
                self._completer.complete()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Key handling — Tab accepts top suggestion
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Tab and self._completer.popup().isVisible():
            completion = self._completer.currentCompletion()
            if completion:
                self.setText(completion)
                self._completer.popup().hide()
                event.accept()
                return
        super().keyPressEvent(event)
