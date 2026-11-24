"""ExportProgressTracker — overall % never jumps backwards across phases."""

from __future__ import annotations

from tahmeed.ui.widgets.export_runner import ExportProgressTracker, _BAR_UNITS, _FETCH_SHARE


class _FakeOverlay:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, int | None]] = []
        self.progress_enabled = False

    def enable_progress(self, enabled: bool = True) -> None:
        self.progress_enabled = bool(enabled)

    def show_loading(self, message=None, *, value=None, maximum=None) -> None:
        self.calls.append((str(message or ""), value, maximum))


def test_tracker_fetch_then_write_advances_overall_percent() -> None:
    overlay = _FakeOverlay()
    tracker = ExportProgressTracker(overlay)  # type: ignore[arg-type]
    tracker.begin_fetch(10_000, phase="Loading records")
    tracker.on_fetch_batch(5_000)
    mid_fetch = tracker._overall_units()[0]
    tracker.finish_fetch(10_000)
    end_fetch = tracker._overall_units()[0]
    assert end_fetch > mid_fetch
    assert end_fetch == int(_FETCH_SHARE * _BAR_UNITS)

    tracker.begin_write(10_000, phase="Writing Excel")
    start_write = tracker._overall_units()[0]
    assert start_write >= end_fetch
    tracker.on_write_progress(5_000)
    mid_write = tracker._overall_units()[0]
    tracker.finish_write()
    done = tracker._overall_units()[0]
    assert mid_write > start_write
    assert done == _BAR_UNITS
    assert overlay.progress_enabled is True


def test_tracker_unknown_total_still_moves_forward() -> None:
    overlay = _FakeOverlay()
    tracker = ExportProgressTracker(overlay)  # type: ignore[arg-type]
    tracker.begin_fetch(0, phase="Loading")
    tracker.on_fetch_batch(2000)
    a = tracker._overall_units()[0]
    tracker.on_fetch_batch(4000)
    b = tracker._overall_units()[0]
    assert b >= a
