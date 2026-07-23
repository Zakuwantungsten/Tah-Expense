import asyncio
from typing import Callable, Awaitable, List, Optional, Union

from PySide6.QtWidgets import QLineEdit, QCompleter, QApplication
from PySide6.QtCore import Qt, QStringListModel, QTimer, QEvent
from PySide6.QtGui import QKeyEvent

from tahmeed.ui.widgets.completer_line_edit import (
    accept_completion, hide_completion_popup, show_completion_preview,
)


class TruckLineEdit(QLineEdit):
    """
    QLineEdit with truck-number autocomplete and inline preview.

    - User types a prefix → suggestions after a short debounce.
    - First suggestion is highlighted in the popup AND shown inline
      (e.g. typing "T6" shows "T6[88 EAF]").
    - Down / Up navigate; Tab / Enter accept the highlighted item.

    Pass ``local_numbers`` (list or callable) to filter synchronously — required
    inside modal dialogs where nested asyncio.ensure_future is unsafe.
    Otherwise pass ``fetch_fn``; a warm fleet cache is still preferred so typing
    during import/modals never nests asyncio tasks.
    """

    def __init__(
        self,
        fetch_fn: Optional[Callable[[str], Awaitable[List[str]]]] = None,
        parent=None,
        *,
        local_numbers: Optional[Union[List[str], Callable[[], List[str]]]] = None,
    ):
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._local_numbers = local_numbers
        self._typed = ""
        self._suppress_preview = False
        self._preview_active = False

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchStartsWith)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        self._completer.highlighted[str].connect(self._on_highlighted)
        self._completer.popup().installEventFilter(self)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(40)
        self._debounce.timeout.connect(self._trigger_fetch)

        self.textEdited.connect(self._on_text_edited)

    def set_local_numbers(
        self, numbers: Optional[Union[List[str], Callable[[], List[str]]]]
    ) -> None:
        """Update the sync suggestion source (e.g. after Add to registry)."""
        self._local_numbers = numbers

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
        self._suppress_preview = False
        self._preview_active = False
        self._debounce.stop()
        if len(text.strip()) >= 1:
            self._debounce.start()
        else:
            self._model.setStringList([])

    def _on_highlighted(self, suggestion: str) -> None:
        current = self.text()
        typed = current[:self.selectionStart()] if self.hasSelectedText() else current
        self._typed = typed
        show_completion_preview(self, typed, suggestion)
        self._preview_active = True

    def _local_list(self) -> List[str]:
        src = self._local_numbers
        if src is None:
            return []
        if callable(src):
            try:
                return list(src() or [])
            except Exception:
                return []
        return list(src)

    def _filter_local(self, prefix: str, limit: int = 10) -> List[str]:
        value = prefix.strip().upper()
        if not value:
            return []
        out: List[str] = []
        for number in self._local_list():
            n = str(number or "").upper()
            if n.startswith(value):
                out.append(n)
            if len(out) >= limit:
                break
        return out

    def _sync_suggestions(self, prefix: str) -> Optional[List[str]]:
        """Return suggestions without awaiting. Prefer explicit local_numbers."""
        if self._local_numbers is not None:
            return self._filter_local(prefix)
        try:
            from tahmeed.services.truck_service import search_fleet_sync
            return search_fleet_sync(prefix)
        except Exception:
            return None

    def _trigger_fetch(self) -> None:
        typed = self._typed
        sync = self._sync_suggestions(typed)
        if sync is not None:
            self._apply_suggestions(sync)
            return
        if self._fetch_fn is None:
            return
        QTimer.singleShot(0, lambda t=typed: self._kick_async_fetch(t))

    def _kick_async_fetch(self, text: str) -> None:
        if self._fetch_fn is None:
            return
        # Prefer warm cache again in case it filled between debounce and kick.
        sync = self._sync_suggestions(text)
        if sync is not None:
            self._apply_suggestions(sync)
            return
        # Python 3.14 + qasync: cannot start a Task while another (e.g. import)
        # is the current task and a modal dialog is pumping the Qt loop.
        try:
            if asyncio.current_task() is not None:
                return
        except RuntimeError:
            pass
        try:
            asyncio.ensure_future(self._fetch_suggestions(text))
        except RuntimeError:
            pass

    def _apply_suggestions(self, suggestions: List[str]) -> None:
        self._model.setStringList(suggestions)
        if suggestions:
            self._completer.complete()
            if not self._suppress_preview:
                QTimer.singleShot(0, self._apply_first_suggestion_preview)
        else:
            hide_completion_popup(self._completer)

    async def _fetch_suggestions(self, text: str) -> None:
        try:
            suggestions = await self._fetch_fn(text)
            if text != self._typed:
                return
            self._apply_suggestions(suggestions)
        except Exception:
            pass

    def _apply_first_suggestion_preview(self) -> None:
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

        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self._preview_active:
            self._suppress_preview = True
            self._preview_active = False
            self.setText(self._typed)
            self.setCursorPosition(len(self._typed))
            event.accept()
            return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if accept_completion(self, self._completer):
                self._preview_active = False
                event.accept()
                return
            hide_completion_popup(self._completer)
            self._preview_active = False

        if key == Qt.Key_Tab:
            if not accept_completion(self, self._completer):
                hide_completion_popup(self._completer)
                if self._preview_active:
                    self.deselect()
                    self.setCursorPosition(len(self.text()))
            self._preview_active = False
            self.focusNextPrevChild(not bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return

        super().keyPressEvent(event)
