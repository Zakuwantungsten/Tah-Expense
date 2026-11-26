"""File dialogs that start at Documents / last-used — never the project cwd."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QFileDialog, QWidget

_ORG = "Tahmeed"
_APP = "tahmeed-expense"
_OPEN_KEY = "file_dialogs/last_open_dir"
_SAVE_KEY = "file_dialogs/last_save_dir"


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def _documents_dir() -> str:
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    if docs and Path(docs).is_dir():
        return str(Path(docs))
    home = QStandardPaths.writableLocation(QStandardPaths.HomeLocation)
    if home and Path(home).is_dir():
        return str(Path(home))
    # Never fall back to os.getcwd() (often the repo / run.py folder).
    return str(Path.home())


def preferred_open_directory() -> str:
    """Directory for Open / multi-file pickers."""
    saved = str(_settings().value(_OPEN_KEY, "") or "").strip()
    if saved and Path(saved).is_dir():
        return saved
    return _documents_dir()


def preferred_save_directory() -> str:
    """Directory for Save As / folder pickers."""
    saved = str(_settings().value(_SAVE_KEY, "") or "").strip()
    if saved and Path(saved).is_dir():
        return saved
    return preferred_open_directory()


def remember_open_path(path: str | Path | None) -> None:
    if not path:
        return
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if folder.is_dir():
        _settings().setValue(_OPEN_KEY, str(folder))


def remember_save_path(path: str | Path | None) -> None:
    if not path:
        return
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if folder.is_dir():
        _settings().setValue(_SAVE_KEY, str(folder))
        # Also seed open dir so next Upload lands near last save.
        _settings().setValue(_OPEN_KEY, str(folder))


def get_open_file_names(
    parent: Optional[QWidget],
    caption: str,
    filter: str = "All files (*.*)",
    *,
    directory: str | None = None,
) -> tuple[list[str], str]:
    start = directory or preferred_open_directory()
    paths, selected = QFileDialog.getOpenFileNames(parent, caption, start, filter)
    if paths:
        remember_open_path(paths[0])
    return list(paths or []), selected or ""


def get_existing_directory(
    parent: Optional[QWidget],
    caption: str,
    *,
    directory: str | None = None,
) -> str:
    start = directory or preferred_save_directory()
    dest = QFileDialog.getExistingDirectory(parent, caption, start)
    if dest:
        remember_save_path(dest)
    return dest or ""


def get_save_file_name(
    parent: Optional[QWidget],
    caption: str,
    default_name: str,
    filter: str = "All files (*.*)",
    *,
    directory: str | None = None,
) -> tuple[str, str]:
    start_dir = directory or preferred_save_directory()
    suggested = str(Path(start_dir) / Path(default_name).name)
    path, selected = QFileDialog.getSaveFileName(
        parent, caption, suggested, filter
    )
    if path:
        remember_save_path(path)
    return path or "", selected or ""
