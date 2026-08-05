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
    Pass ``sync_fn`` for other warm in-memory sources (e.g. description history).
    Otherwise pass ``fetch_fn``; a warm fleet cache is still preferred so typing
    during import/modals never nests asyncio tasks when no custom source is set.
    """

    def __init__(
        self,
        fetch_fn: Optional[Callable[[str], Awaitable[List[str]]]] = None,
        parent=None,
        *,
        local_numbers: Optional[Union[List[str], Callable[[], List[str]]]] = None,
        sync_fn: Optional[Callable[[str], Optional[List[str]]]] = None,
    ):
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._local_numbers = local_numbers
        self._sync_fn = sync_fn
        self._typed = ""
        self._suppress_preview = False
        self._preview_active = False
        # True after Tab / focus-out until the next keystroke — blocks late
        # debounce/async completions from reopening the popup.
        self._block_suggestions = False

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchStartsWith)
        self._completer.setMaxVisibleItems(8)
        self.setCompleter(self._completer)

        self._completer.highlighted[str].connect(self._on_highlighted)
        # Popup steals key events while open; re-route Tab/Enter to us.
        self._completer.popup().installEventFilter(self)
        # setCompleter() also installs a Qt filter on *this* widget that eats
        # Tab before keyPressEvent. Install ours last so we run first and can
        # accept the preview + hide the popup (same idea as the excel-grid
        # delegate filter).
        self.installEventFilter(self)

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

    def _in_item_view(self) -> bool:
        """True when this editor is inside a table/grid cell (register, etc.)."""
        p = self.parent()
        while p is not None:
            if getattr(p, "_grid_owner", None) is not None:
                return True
            # QTableWidget / QAbstractItemView viewport chain
            try:
                from PySide6.QtWidgets import QAbstractItemView
                if isinstance(p, QAbstractItemView):
                    return True
            except Exception:
                pass
            p = p.parent()
        return False

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Return, Qt.Key_Enter):
                if obj is self._completer.popup():
                    # Popup has the keyboard while open — re-route to the editor
                    # so the table delegate's Tab filter can accept + advance in
                    # one press (same pattern as CompleterLineEdit / Item column).
                    QApplication.sendEvent(
                        self, QKeyEvent(event.type(), event.key(), event.modifiers())
                    )
                    return True
                if obj is self:
                    if self._in_item_view():
                        # Beat Qt's completer filter: accept preview, then let the
                        # event continue to the table delegate (do not swallow).
                        self._debounce.stop()
                        self._accept_current_suggestion()
                        self._block_suggestions = True
                        self._suppress_preview = True
                        self._preview_active = False
                        return False
                    # Standalone (dialogs / tests): handle accept + focus move.
                    self.keyPressEvent(event)
                    return True
        return super().eventFilter(obj, event)

    def focusOutEvent(self, event) -> None:
        # Opening the completer popup temporarily moves focus with PopupFocusReason;
        # do not cancel suggestions in that case.
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._cancel_pending_suggestions(hide_popup=True)
        super().focusOutEvent(event)

    def _cancel_pending_suggestions(self, *, hide_popup: bool = False) -> None:
        """Stop debounce / deferred preview so they cannot reappear after Tab."""
        self._debounce.stop()
        self._block_suggestions = True
        self._suppress_preview = True
        self._preview_active = False
        if hide_popup:
            hide_completion_popup(self._completer)

    def _accept_current_suggestion(self) -> None:
        """Commit popup highlight or inline preview text, then hide the popup.

        Mirrors excel-grid behaviour: whatever is shown as the current preview
        becomes the field value when Tab/Enter is pressed.
        """
        if accept_completion(self, self._completer):
            return
        hide_completion_popup(self._completer)
        if self._preview_active or self.hasSelectedText():
            text = self.text()
            self.setText(text)
            self._typed = text
            self.setCursorPosition(len(text))
            return
        model = self._completer.completionModel()
        if model is not None and model.rowCount() > 0:
            suggestion = model.index(0, 0).data(Qt.DisplayRole)
            if suggestion:
                text = str(suggestion)
                self.setText(text)
                self._typed = text
                self.setCursorPosition(len(text))

    def _on_text_edited(self, text: str) -> None:
        self._typed = text
        self._suppress_preview = False
        self._preview_active = False
        self._block_suggestions = False
        self._debounce.stop()
        if len(text.strip()) >= 1:
            self._debounce.start()
        else:
            self._model.setStringList([])
            hide_completion_popup(self._completer)

    def _on_highlighted(self, suggestion: str) -> None:
        if self._block_suggestions:
            return
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
        if self._sync_fn is not None:
            try:
                return self._sync_fn(prefix)
            except Exception:
                return None
        # Do not fall through to the truck fleet cache when a custom async
        # fetch_fn is set (e.g. description / category editors) — a warm fleet
        # cache would return [] or truck numbers and block the real source.
        if self._fetch_fn is not None:
            return None
        try:
            from tahmeed.services.truck_service import search_fleet_sync
            return search_fleet_sync(prefix)
        except Exception:
            return None

    def _trigger_fetch(self) -> None:
        if self._block_suggestions:
            return
        typed = self._typed
        sync = self._sync_suggestions(typed)
        if sync is not None:
            self._apply_suggestions(sync)
            return
        if self._fetch_fn is None:
            return
        QTimer.singleShot(0, lambda t=typed: self._kick_async_fetch(t))

    def _kick_async_fetch(self, text: str, *, _retries: int = 0) -> None:
        if self._fetch_fn is None:
            return
        if self._block_suggestions:
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
                if _retries < 20:
                    QTimer.singleShot(
                        50,
                        lambda t=text, r=_retries + 1: self._kick_async_fetch(t, _retries=r),
                    )
                return
        except RuntimeError:
            pass
        try:
            asyncio.ensure_future(self._fetch_suggestions(text))
        except RuntimeError:
            if _retries < 20:
                QTimer.singleShot(
                    50,
                    lambda t=text, r=_retries + 1: self._kick_async_fetch(t, _retries=r),
                )

    def _apply_suggestions(self, suggestions: List[str]) -> None:
        self._model.setStringList(suggestions)
        if self._block_suggestions:
            hide_completion_popup(self._completer)
            return
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
            if self._block_suggestions:
                return
            self._apply_suggestions(suggestions)
        except Exception:
            pass

    def _apply_first_suggestion_preview(self) -> None:
        if self._suppress_preview or self._block_suggestions:
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
            self._debounce.stop()
            self._accept_current_suggestion()
            self._block_suggestions = True
            self._suppress_preview = True
            self._preview_active = False
            event.accept()
            return

        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            # Cancel pending debounce, commit preview (excel-style), hide popup.
            # In the register grid the delegate's Tab filter advances the cell;
            # focusNextPrevChild is only a fallback for standalone editors.
            self._debounce.stop()
            self._accept_current_suggestion()
            self._block_suggestions = True
            self._suppress_preview = True
            self._preview_active = False
            forward = key == Qt.Key_Tab and not bool(
                event.modifiers() & Qt.ShiftModifier
            )
            if not self._in_item_view():
                QTimer.singleShot(0, lambda f=forward: self.focusNextPrevChild(f))
            event.accept()
            return

        super().keyPressEvent(event)
