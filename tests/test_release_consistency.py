import re
from pathlib import Path

from tahmeed.config import APP_VERSION


ROOT = Path(__file__).resolve().parent.parent


def test_release_assets_use_authoritative_version_and_shared_mutex() -> None:
    version_source = (ROOT / "tahmeed" / "version.py").read_text("utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"$', version_source, re.MULTILINE)
    assert match is not None
    assert APP_VERSION == match.group(1)

    installer = (ROOT / "installer.iss").read_text("utf-8")
    build_script = (ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
    assert "AppVersion={#MyAppVersion}" in installer
    assert "TahmeedExpenseSetup-{#MyAppVersion}" in installer
    assert "AppMutex=TahmeedExpense.A3F1C2D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D" in installer
    assert 'Get-Content "tahmeed\\version.py" -Raw' in build_script
