"""The QuickBooks expense automation's description memory as Import Daily mappings.

Only the automation's ``data/description_memory.json``
(``{description: {payee, account, source}}``) is used.

Its ``truck_corrections.json`` is not used: every truck is unique, so a wrong
plate is fixed row by row, never mapped. Its ``ignored_descriptions.json`` is
not used either: every expense is tracked in this app.

Accounts are QuickBooks account names (``Direct Costs:Spares``); they become
Items through :class:`ItemIndex`. Nothing is saved to Description Maps.

A *pack file* holds the same memory in one JSON so it can be copied to
another PC; one ships with the app as the built-in pack, used automatically.
Loading another pack replaces the built-in mappings for that session only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.import_session_parser import normalize_import_description
from tahmeed.services.import_session_service import (
    MAPPING_SOURCE_PACK,
    ImportSession,
    TempMapping,
)
from tahmeed.services.import_session_validation import ImportReferences

PACK_MEMORY_FILE = "description_memory.json"

PACK_FILE_FORMAT = "tahmeed-mapping-pack"
PACK_FILE_VERSION = 1
PACK_FILE_SUFFIX = ".tmpack.json"
BUILTIN_PACK_NAME = "expense_automation.tmpack.json"


@dataclass
class PackMapping:
    description: str
    payee: str
    account: str
    source: str = ""


@dataclass
class MappingPack:
    folder: str
    mappings: List[PackMapping] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    name: str = ""
    created_at: Optional[datetime] = None

    @property
    def is_empty(self) -> bool:
        return not self.mappings


def find_pack_folder(path: str | Path) -> Optional[Path]:
    """The folder holding ``description_memory.json``: ``path`` itself or its ``data`` child."""
    base = Path(path)
    for candidate in (base, base / "data"):
        if (candidate / PACK_MEMORY_FILE).is_file():
            return candidate
    return None


def find_pack_source(path: str | Path) -> Optional[Path]:
    """A pack file, or the automation folder / ``data`` folder holding its memory."""
    base = Path(path)
    if base.is_file():
        if base.name == PACK_MEMORY_FILE:
            return base.parent
        return base if base.suffix.lower() == ".json" else None
    return find_pack_folder(base)


def _load_json_file(path: Path, pack: MappingPack) -> object:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        pack.errors.append(f"{path.name}: {exc}")
        return None


def _fill_pack(pack: MappingPack, memory: object) -> None:
    """Parse the automation's description memory into ``pack``."""
    if not isinstance(memory, dict):
        return
    for description, entry in memory.items():
        if not isinstance(entry, dict):
            continue
        payee = str(entry.get("payee") or "").strip()
        account = str(entry.get("account") or "").strip()
        if normalize_import_description(description) and (payee or account):
            pack.mappings.append(
                PackMapping(str(description), payee, account, str(entry.get("source") or ""))
            )


def _read_pack_file(path: Path) -> MappingPack:
    pack = MappingPack(folder=str(path))
    data = _load_json_file(path, pack)
    if data is None:
        return pack
    if not isinstance(data, dict) or data.get("format") != PACK_FILE_FORMAT:
        pack.errors.append(f"{path.name}: not a mapping pack file")
        return pack
    pack.name = str(data.get("name") or "")
    try:
        pack.created_at = datetime.fromisoformat(str(data.get("created_at") or ""))
    except ValueError:
        pack.created_at = None
    _fill_pack(pack, data.get(PACK_MEMORY_FILE.removesuffix(".json")))
    return pack


def read_mapping_pack(path: str | Path) -> MappingPack:
    """Read a pack file, or the automation's ``data`` folder."""
    root = Path(path)
    if root.is_file():
        return _read_pack_file(root)
    pack = MappingPack(folder=str(root), name=root.parent.name if root.name == "data" else root.name)
    memory_path = root / PACK_MEMORY_FILE
    if memory_path.is_file():
        _fill_pack(pack, _load_json_file(memory_path, pack))
    else:
        pack.missing_files.append(PACK_MEMORY_FILE)
    return pack


def pack_to_dict(pack: MappingPack, *, name: str = "") -> dict:
    """Single-file form; the inner object keeps the automation's format."""
    memory: Dict[str, dict] = {}
    for m in pack.mappings:
        entry = {"payee": m.payee, "account": m.account}
        if m.source:
            entry["source"] = m.source
        memory[m.description] = entry
    return {
        "format": PACK_FILE_FORMAT,
        "version": PACK_FILE_VERSION,
        "name": name or pack.name,
        "created_at": (pack.created_at or datetime.now()).isoformat(timespec="seconds"),
        "description_memory": memory,
    }


def write_mapping_pack(pack: MappingPack, path: str | Path, *, name: str = "") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(pack_to_dict(pack, name=name), handle, ensure_ascii=False, indent=1)
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target


def builtin_pack_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        root = Path(sys._MEIPASS) / "tahmeed"
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "assets" / "mapping_packs" / BUILTIN_PACK_NAME


def load_builtin_pack() -> Optional[MappingPack]:
    path = builtin_pack_path()
    if not path.is_file():
        return None
    pack = read_mapping_pack(path)
    return None if pack.is_empty else pack


def _upper(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


def pack_to_mappings(pack: MappingPack) -> Dict[str, TempMapping]:
    """Description key -> mapping; the Account stays as the Item name to resolve."""
    out: Dict[str, TempMapping] = {}
    for entry in pack.mappings:
        display = normalize_import_description(entry.description)
        key = normalize_description(display)
        if key:
            out[key] = TempMapping(
                display, _upper(entry.account), _upper(entry.payee), MAPPING_SOURCE_PACK
            )
    return out


_builtin_cache: Optional[Dict[str, TempMapping]] = None


def builtin_mappings() -> Dict[str, TempMapping]:
    """The built-in pack's mappings (read once; empty when the pack is missing)."""
    global _builtin_cache
    if _builtin_cache is None:
        pack = load_builtin_pack()
        _builtin_cache = pack_to_mappings(pack) if pack is not None else {}
    return _builtin_cache


@dataclass
class PackApplyResult:
    mappings_added: int = 0
    mappings_in_session: int = 0
    unresolved_items: Counter = field(default_factory=Counter)
    unresolved_payees: Counter = field(default_factory=Counter)


def apply_mapping_pack(
    session: ImportSession, pack: MappingPack, refs: ImportReferences
) -> PackApplyResult:
    """Use ``pack`` instead of the built-in mappings for this session.

    Mappings typed in this session are not touched; they still win.
    """
    mappings = pack_to_mappings(pack)
    result = PackApplyResult(mappings_added=len(mappings))
    session_keys = {normalize_description(r.description) for r in session.rows}
    for key, mapping in mappings.items():
        if refs.items.resolve(mapping.item) is None:
            result.unresolved_items[mapping.item or "(blank)"] += 1
        if refs.payees.resolve(mapping.payee) is None:
            result.unresolved_payees[mapping.payee or "(blank)"] += 1
        if key in session_keys:
            result.mappings_in_session += 1
    session.set_pack_mappings(mappings, pack.name or Path(pack.folder).name)
    return result
