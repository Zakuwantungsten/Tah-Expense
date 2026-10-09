import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

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


_TAILSCALE_CGNAT = re.compile(
    r"(?:^|[@/])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?::|/|\?|$)"
)


def _looks_like_remote_dev_target(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    if ".ts.net" in text or _TAILSCALE_CGNAT.search(text):
        return True
    return False


def _assert_local_dev_targets(*, mongodb_uri: str, api_base_url: str) -> None:
    """Refuse Tailscale/production hosts when running unpackaged (LOCAL_DEV.md)."""
    if getattr(sys, "frozen", False):
        return
    if os.getenv("TAHMEED_ALLOW_REMOTE_DB", "").strip() in {"1", "true", "yes"}:
        return
    offenders: list[str] = []
    if _looks_like_remote_dev_target(mongodb_uri):
        offenders.append("MONGODB_URI")
    if _looks_like_remote_dev_target(api_base_url):
        offenders.append("API_BASE_URL")
    parsed = urlparse(api_base_url or "")
    host = (parsed.hostname or "").lower()
    if host and host not in {"127.0.0.1", "localhost"} and not host.endswith(".ts.net"):
        # Non-loopback API in unpackaged mode is unusual; still allow only with override
        # when it is clearly not Tailscale (already caught). Keep quiet for empty.
        pass
    if not offenders:
        return
    raise RuntimeError(
        "Local development refuses Tailscale/production database or API hosts "
        f"({', '.join(offenders)}). Use 127.0.0.1 and tahmeed_expense_dev "
        "(see LOCAL_DEV.md), or set TAHMEED_ALLOW_REMOTE_DB=1 only in an emergency."
    )


_load_env()

MONGODB_URI: str = os.getenv("MONGODB_URI", "")
DB_NAME: str = os.getenv("DB_NAME", "tahmeed_expense")
API_BASE_URL: str = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
UPDATE_MANIFEST_URL: str = os.getenv("UPDATE_MANIFEST_URL", "")

_assert_local_dev_targets(mongodb_uri=MONGODB_URI, api_base_url=API_BASE_URL)

APP_NAME = "Tahmeed Expense"
