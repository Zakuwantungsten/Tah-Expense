# PyInstaller spec — run via:  scripts/build_windows.ps1

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
root = Path(SPECPATH)

datas = [(".env.build", ".")]
datas += collect_data_files("qtawesome")

hiddenimports = collect_submodules("motor") + collect_submodules("pymongo") + [
    "bcrypt",
    "qasync",
    "openpyxl",
    "pyqtgraph",
    "qtawesome",
    "qtawesome.iconic_font",
    "bson",
    "dns",
    "dns.resolver",
]

a = Analysis(
    ["run.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tahmeed Expense",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Tahmeed Expense",
)
