"""pause_background_polls must stop connectivity probes even without a dashboard."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.ui import async_utils


def test_pause_background_polls_pauses_connectivity_without_dashboard(monkeypatch) -> None:
    _app = QApplication.instance() or QApplication([])
    paused = {"n": 0}
    resumed = {"n": 0}

    class _Mon:
        def pause(self) -> None:
            paused["n"] += 1

        def resume(self) -> None:
            resumed["n"] += 1

    import tahmeed.services.connectivity_service as cs

    monkeypatch.setattr(cs, "connectivity_monitor", _Mon())
    previous_depth = async_utils._poll_pause_depth
    async_utils._poll_pause_depth = 0
    try:
        w = QWidget()
        with async_utils.pause_background_polls(w):
            assert paused["n"] == 1
            assert resumed["n"] == 0
            with async_utils.pause_background_polls(w):
                assert paused["n"] == 1
            assert resumed["n"] == 0
        assert resumed["n"] == 1
    finally:
        async_utils._poll_pause_depth = previous_depth
        w.deleteLater()
        _app.processEvents()
