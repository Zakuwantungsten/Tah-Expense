"""App-wide signal bus — import app_signals anywhere to cross-connect modules."""

from PySide6.QtCore import QObject, Signal


class _AppSignals(QObject):
    transaction_saved = Signal()  # emitted after any single row is auto-saved


app_signals = _AppSignals()
