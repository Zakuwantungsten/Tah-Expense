import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from tahmeed.version import APP_VERSION as APP_VERSION


def _app_root() -> Path:
    """Dev: project root. Packaged: folder containing the .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _load_env() -> None:
    # Bundled .env (inside PyInstaller extract dir for one-file builds).
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        load_dotenv(Path(sys._MEIPASS) / ".env")
        load_dotenv(Path(sys._MEIPASS) / ".env.build")
    # .env beside the exe (lets IT update connection without a full rebuild).
    load_dotenv(_app_root() / ".env", override=True)


_load_env()

MONGODB_URI: str = os.getenv("MONGODB_URI", "")
DB_NAME: str = os.getenv("DB_NAME", "tahmeed_expense")
API_BASE_URL: str = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
UPDATE_MANIFEST_URL: str = os.getenv("UPDATE_MANIFEST_URL", "")

APP_NAME = "Tahmeed Expense"
