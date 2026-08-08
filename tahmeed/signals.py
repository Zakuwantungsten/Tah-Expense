"""App-wide signal bus — import app_signals anywhere to cross-connect modules."""

from PySide6.QtCore import QObject, Signal


class _AppSignals(QObject):
    transaction_saved = Signal()  # legacy; autosave removed
    # ConnectivityStatus from tahmeed.services.connectivity_service
    connectivity_changed = Signal(object)


app_signals = _AppSignals()
