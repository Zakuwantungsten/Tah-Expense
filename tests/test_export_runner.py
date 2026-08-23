"""export_runner helpers."""

from __future__ import annotations

from pathlib import Path

from tahmeed.ui.widgets.export_runner import export_file_ready, normalize_xlsx_path


def test_normalize_xlsx_path_resolves() -> None:
    p = normalize_xlsx_path("report")
    assert p.endswith(".xlsx")
    assert Path(p).is_absolute()


def test_export_file_ready_false_for_missing(tmp_path) -> None:
    assert export_file_ready(str(tmp_path / "nope.xlsx")) is False


def test_export_file_ready_true_for_nonempty_file(tmp_path) -> None:
    f = tmp_path / "ok.xlsx"
    f.write_bytes(b"x")
    assert export_file_ready(str(f)) is True
