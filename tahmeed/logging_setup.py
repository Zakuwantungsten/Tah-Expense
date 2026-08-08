"""Desktop structured logging to %%LOCALAPPDATA%%\\Tahmeed Expense\\logs."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tahmeed.config import APP_NAME

_configured = False


def logs_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "logs"


def setup_logging() -> Path:
    """Configure root tahmeed loggers once. Returns the log file path."""
    global _configured
    root = logs_root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "desktop.log"
    if _configured:
        return log_path

    handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    package = logging.getLogger("tahmeed")
    package.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in package.handlers):
        package.addHandler(handler)
    package.propagate = False
    _configured = True
    package.info("Desktop logging started → %s", log_path)
    return log_path
