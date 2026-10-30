"""Local bulk-import sessions: staged Excel rows + temporary mappings.

A session lives in ``%LOCALAPPDATA%\\meedexpe\\import_sessions\\{id}.json``.
Temporary mappings, truck corrections and ignored descriptions only apply to
the session that holds them; finishing the session discards them unless the
user promotes selected ones to the global Description Maps / fleet registry.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set

from tahmeed.config import APP_NAME
from tahmeed.services.daily_import_service import DailyImportCancelled
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.import_session_parser import (
    LoadedFile,
    ParsedWorkbook,
    SkippedExcelRow,
    StagedRow,
    normalize_import_description,
    parse_workbook,
)

SESSION_FORMAT_VERSION = 1

MAPPING_SOURCE_MANUAL = "manual"
MAPPING_SOURCE_PACK = "pack"
_DEFAULT_NAME = re.compile(r"Import \d{2}/\d{2}/\d{4} \d{2}:\d{2}")


@dataclass
class TempMapping:
    description: str
    item: str
    payee: str
    source: str = MAPPING_SOURCE_MANUAL


MODE_BOTH_MAPS_FIRST = "both_maps_first"
MODE_BOTH_BUILTIN_FIRST = "both_builtin_first"
MODE_BUILTIN_ONLY = "builtin_only"
MODE_MAPS_ONLY = "maps_only"
MAPPING_MODES = (MODE_BOTH_MAPS_FIRST, MODE_BOTH_BUILTIN_FIRST, MODE_BUILTIN_ONLY, MODE_MAPS_ONLY)


@dataclass
class SessionOptions:
    # Which of the built-in mappings and Description Maps apply, and which wins.
    # Mappings typed in this session always apply first.
    mapping_mode: str = MODE_BOTH_MAPS_FIRST

    @classmethod
    def from_dict(cls, data: dict) -> "SessionOptions":
        mode = data.get("mapping_mode")
        if mode not in MAPPING_MODES:
            # Older sessions had "use / prefer temporary mappings" switches.
            if data.get("use_temp_mappings") is False:
                mode = MODE_MAPS_ONLY
            elif data.get("prefer_temp_mappings", True):
                mode = MODE_BOTH_BUILTIN_FIRST
            else:
                mode = MODE_BOTH_MAPS_FIRST
        return cls(mapping_mode=mode)


@dataclass
class PushedDay:
    pushed_at: datetime
    row_count: int
    upload_ids: List[str] = field(default_factory=list)


def truck_key(value: str) -> str:
    return " ".join((value or "").upper().split())


def _compact(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


@dataclass
class ImportSession:
    session_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    options: SessionOptions = field(default_factory=SessionOptions)
    files: Dict[str, LoadedFile] = field(default_factory=dict)
    rows: List[StagedRow] = field(default_factory=list)
    skipped: List[SkippedExcelRow] = field(default_factory=list)
    temp_mappings: Dict[str, TempMapping] = field(default_factory=dict)  # typed here
    # A pack loaded for this session; replaces the built-in mappings while set.
    pack_mappings: Dict[str, TempMapping] = field(default_factory=dict)
    pack_name: str = ""
    # Descriptions saved to Description Maps from this session: they still
    # apply in "built-in only" mode, like any mapping typed here.
    typed_to_maps: Set[str] = field(default_factory=set)
    # row_id -> correct plate for that one row; "" means "no truck". Only lives
    # in this session: never saved to packs or Description Maps.
    truck_fixes: Dict[str, str] = field(default_factory=dict)
    # Rows whose fix is kept as typed although it is not one of our trucks.
    truck_allowed: Set[str] = field(default_factory=set)
    ignored: Dict[str, str] = field(default_factory=dict)  # key -> display
    pushed_days: Dict[date, PushedDay] = field(default_factory=dict)
    # Not named by the user: the name follows the dates of the loaded files.
    auto_named: bool = False

    @classmethod
    def new(cls, name: str = "") -> "ImportSession":
        now = datetime.now()
        session = cls(
            session_id=uuid.uuid4().hex,
            name=name.strip(),
            created_at=now,
            updated_at=now,
            auto_named=not name.strip(),
        )
        session.refresh_auto_name()
        return session

    def touch(self) -> None:
        self.updated_at = datetime.now()

    def rename(self, name: str) -> None:
        self.name = name.strip()
        self.auto_named = False
        self.touch()

    def refresh_auto_name(self) -> None:
        """'MATUMIZI 01/10/2026 – 19/10/2026' from the rows' dates (auto-named only)."""
        if not self.auto_named:
            return
        dates = sorted({r.batch_date for r in self.rows if r.batch_date})
        if not dates:
            self.name = f"Import {self.created_at.strftime('%d/%m/%Y %H:%M')}"
        elif dates[0] == dates[-1]:
            self.name = f"MATUMIZI {dates[0]:%d/%m/%Y}"
        else:
            self.name = f"MATUMIZI {dates[0]:%d/%m/%Y} – {dates[-1]:%d/%m/%Y}"

    # -- files ---------------------------------------------------------------

    def add_parsed(self, parsed: ParsedWorkbook) -> None:
        """Add (or replace) one workbook's rows."""
        key = parsed.file.file_key
        self.rows = [r for r in self.rows if r.file_key != key] + list(parsed.rows)
        self.skipped = [s for s in self.skipped if s.file_key != key] + list(parsed.skipped)
        self.files[key] = parsed.file
        self.refresh_auto_name()
        self.touch()

    def remove_file(self, file_key: str) -> bool:
        if file_key not in self.files:
            return False
        del self.files[file_key]
        for row in self.rows:
            if row.file_key == file_key:
                self.truck_fixes.pop(row.row_id, None)
                self.truck_allowed.discard(row.row_id)
        self.rows = [r for r in self.rows if r.file_key != file_key]
        self.skipped = [s for s in self.skipped if s.file_key != file_key]
        self.refresh_auto_name()
        self.touch()
        return True

    # -- grouping ------------------------------------------------------------

    def days(self) -> Dict[date, List[StagedRow]]:
        grouped: Dict[date, List[StagedRow]] = defaultdict(list)
        for row in self.rows:
            grouped[row.batch_date].append(row)
        return {d: grouped[d] for d in sorted(grouped)}

    def rows_for_day(self, batch_date: date) -> List[StagedRow]:
        return [r for r in self.rows if r.batch_date == batch_date]

    # -- temporary description mappings --------------------------------------

    def set_mapping(
        self,
        description: str,
        item: str,
        payee: str,
        *,
        source: str = MAPPING_SOURCE_MANUAL,
    ) -> None:
        display = normalize_import_description(description)
        key = normalize_description(display)
        if not key:
            return
        self.temp_mappings[key] = TempMapping(
            description=display,
            item=" ".join((item or "").strip().upper().split()),
            payee=" ".join((payee or "").strip().upper().split()),
            source=source,
        )
        self.touch()

    def set_mappings(
        self,
        descriptions: Iterable[str],
        item: str,
        payee: str,
        *,
        source: str = MAPPING_SOURCE_MANUAL,
    ) -> int:
        count = 0
        for description in descriptions:
            if not normalize_import_description(description):
                continue
            self.set_mapping(description, item, payee, source=source)
            count += 1
        return count

    def remove_mapping(self, description: str) -> bool:
        key = normalize_description(normalize_import_description(description))
        if self.temp_mappings.pop(key, None) is None:
            return False
        self.touch()
        return True

    def get_mapping(self, description: str) -> Optional[TempMapping]:
        return self.temp_mappings.get(
            normalize_description(normalize_import_description(description))
        )

    def note_saved_to_maps(self, descriptions: Iterable[str]) -> None:
        for description in descriptions:
            key = normalize_description(normalize_import_description(description))
            if key:
                self.typed_to_maps.add(key)
        self.touch()

    # -- loaded mapping pack -------------------------------------------------

    def set_pack_mappings(self, mappings: Dict[str, TempMapping], name: str = "") -> None:
        self.pack_mappings = dict(mappings)
        self.pack_name = name if mappings else ""
        self.touch()

    def use_builtin_mappings(self) -> None:
        self.set_pack_mappings({})

    # -- ignored descriptions ------------------------------------------------

    def ignore(self, descriptions: Iterable[str]) -> int:
        added = 0
        for description in descriptions:
            display = normalize_import_description(description)
            key = normalize_description(display)
            if key and key not in self.ignored:
                self.ignored[key] = display
                added += 1
        if added:
            self.touch()
        return added

    def unignore(self, descriptions: Iterable[str]) -> int:
        removed = 0
        for description in descriptions:
            key = normalize_description(normalize_import_description(description))
            if self.ignored.pop(key, None) is not None:
                removed += 1
        if removed:
            self.touch()
        return removed

    def is_ignored(self, description: str) -> bool:
        return normalize_description(normalize_import_description(description)) in self.ignored

    # -- truck fixes (one row each) ------------------------------------------

    def set_truck_fix(self, row_id: str, plate: str, *, allowed: bool = False) -> None:
        """Correct truck for one row; ``""`` means the row has no truck.

        ``allowed`` keeps a value that is not one of our trucks / trailers.
        """
        if not row_id:
            return
        self.truck_fixes[row_id] = truck_key(plate)
        if allowed and plate.strip():
            self.truck_allowed.add(row_id)
        else:
            self.truck_allowed.discard(row_id)
        self.touch()

    def remove_truck_fix(self, row_id: str) -> bool:
        self.truck_allowed.discard(row_id)
        if self.truck_fixes.pop(row_id, None) is None:
            return False
        self.touch()
        return True

    def truck_fix_for(self, row_id: str) -> Optional[str]:
        return self.truck_fixes.get(row_id)

    def truck_fix_allowed(self, row_id: str) -> bool:
        return row_id in self.truck_allowed

    # -- push bookkeeping ----------------------------------------------------

    def mark_day_pushed(
        self,
        batch_date: date,
        *,
        row_count: int,
        upload_ids: Sequence[str] = (),
    ) -> None:
        self.pushed_days[batch_date] = PushedDay(
            pushed_at=datetime.now(),
            row_count=int(row_count),
            upload_ids=list(upload_ids),
        )
        self.touch()

    def unmark_day_pushed(self, batch_date: date) -> bool:
        if self.pushed_days.pop(batch_date, None) is None:
            return False
        self.touch()
        return True

    def is_day_pushed(self, batch_date: date) -> bool:
        return batch_date in self.pushed_days

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": SESSION_FORMAT_VERSION,
            "session_id": self.session_id,
            "name": self.name,
            "auto_named": self.auto_named,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "options": asdict(self.options),
            "files": [asdict(f) for f in self.files.values()],
            "rows": [_row_to_json(r) for r in self.rows],
            "skipped": [_skipped_to_json(s) for s in self.skipped],
            "temp_mappings": [asdict(m) for m in self.temp_mappings.values()],
            "pack_mappings": [asdict(m) for m in self.pack_mappings.values()],
            "pack_name": self.pack_name,
            "typed_to_maps": sorted(self.typed_to_maps),
            "truck_fixes": dict(self.truck_fixes),
            "truck_allowed": sorted(self.truck_allowed),
            "ignored": list(self.ignored.values()),
            "pushed_days": {
                d.isoformat(): {
                    "pushed_at": p.pushed_at.isoformat(),
                    "row_count": p.row_count,
                    "upload_ids": list(p.upload_ids),
                }
                for d, p in self.pushed_days.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImportSession":
        session = cls(
            session_id=str(data["session_id"]),
            name=str(data.get("name") or ""),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            options=SessionOptions.from_dict(data.get("options") or {}),
        )
        auto = data.get("auto_named")
        # Older sessions: only the default "Import dd/mm/yyyy hh:mm" name follows the dates.
        session.auto_named = (
            bool(auto) if auto is not None else bool(_DEFAULT_NAME.fullmatch(session.name))
        )
        for raw in data.get("files") or []:
            loaded = LoadedFile(**raw)
            session.files[loaded.file_key] = loaded
        session.rows = [_row_from_json(r) for r in data.get("rows") or []]
        session.skipped = [_skipped_from_json(s) for s in data.get("skipped") or []]
        for raw in data.get("temp_mappings") or []:
            mapping = TempMapping(**raw)
            key = normalize_description(mapping.description)
            # Older sessions copied a loaded pack in among the typed mappings.
            if mapping.source == MAPPING_SOURCE_PACK:
                session.pack_mappings[key] = mapping
            else:
                session.temp_mappings[key] = mapping
        for raw in data.get("pack_mappings") or []:
            mapping = TempMapping(**raw)
            session.pack_mappings[normalize_description(mapping.description)] = mapping
        session.pack_name = str(data.get("pack_name") or "")
        if session.pack_mappings and not session.pack_name:
            session.pack_name = "Loaded earlier"
        session.typed_to_maps = {str(k) for k in data.get("typed_to_maps") or []}
        # Older sessions kept value-wide "truck_corrections"; those are dropped.
        session.truck_fixes = {
            str(k): truck_key(v) for k, v in (data.get("truck_fixes") or {}).items()
        }
        session.truck_allowed = {
            str(k) for k in data.get("truck_allowed") or [] if session.truck_fixes.get(str(k))
        }
        for display in data.get("ignored") or []:
            session.ignored[normalize_description(display)] = display
        for iso, raw in (data.get("pushed_days") or {}).items():
            session.pushed_days[date.fromisoformat(iso)] = PushedDay(
                pushed_at=datetime.fromisoformat(raw["pushed_at"]),
                row_count=int(raw.get("row_count") or 0),
                upload_ids=list(raw.get("upload_ids") or []),
            )
        session.refresh_auto_name()
        return session


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _from_iso(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _row_to_json(row: StagedRow) -> dict:
    out = asdict(row)
    out["batch_date"] = row.batch_date.isoformat()
    out["excel_date"] = _iso(row.excel_date)
    return out


def _row_from_json(raw: dict) -> StagedRow:
    data = dict(raw)
    data["batch_date"] = date.fromisoformat(data["batch_date"])
    data["excel_date"] = _from_iso(data.get("excel_date"))
    return StagedRow(**data)


def _skipped_to_json(row: SkippedExcelRow) -> dict:
    out = asdict(row)
    out["batch_date"] = _iso(row.batch_date)
    return out


def _skipped_from_json(raw: dict) -> SkippedExcelRow:
    data = dict(raw)
    data["batch_date"] = _from_iso(data.get("batch_date"))
    return SkippedExcelRow(**data)


# -- storage -------------------------------------------------------------------


def sessions_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "import_sessions"


def session_path(session_id: str) -> Path:
    return sessions_root() / f"{session_id}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def save_session(session: ImportSession) -> Path:
    path = session_path(session.session_id)
    _atomic_write_json(path, session.to_dict())
    return path


def load_session(session_id: str) -> ImportSession:
    with session_path(session_id).open("r", encoding="utf-8") as handle:
        return ImportSession.from_dict(json.load(handle))


def delete_session(session_id: str) -> bool:
    try:
        session_path(session_id).unlink()
    except FileNotFoundError:
        return False
    return True


@dataclass
class SessionSummary:
    session_id: str
    name: str
    updated_at: datetime
    file_count: int
    row_count: int
    day_count: int
    pushed_day_count: int


def list_sessions() -> List[SessionSummary]:
    """Saved sessions, most recently updated first. Unreadable files are skipped."""
    root = sessions_root()
    if not root.is_dir():
        return []
    out: List[SessionSummary] = []
    for path in root.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            rows = data.get("rows") or []
            out.append(
                SessionSummary(
                    session_id=str(data["session_id"]),
                    name=str(data.get("name") or ""),
                    updated_at=datetime.fromisoformat(data["updated_at"]),
                    file_count=len(data.get("files") or []),
                    row_count=len(rows),
                    day_count=len({r.get("batch_date") for r in rows}),
                    pushed_day_count=len(data.get("pushed_days") or {}),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    out.sort(key=lambda s: s.updated_at, reverse=True)
    return out


# -- loading files -------------------------------------------------------------


@dataclass
class FileLoadError:
    path: str
    message: str


async def add_files_to_session(
    session: ImportSession,
    paths: Sequence[str | Path],
    *,
    sheet_name: Optional[str] = None,
    on_file: Optional[Callable[[int, int, str], object]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[FileLoadError]:
    """Parse each workbook off the UI thread and add it to the session.

    A file that fails to parse is reported and skipped; the others still load.
    The session is saved once at the end.
    """
    import inspect

    errors: List[FileLoadError] = []
    total = len(paths)
    for index, path in enumerate(paths):
        if on_file is not None:
            update = on_file(index + 1, total, Path(path).name)
            if inspect.isawaitable(update):
                await update
        try:
            parsed = await asyncio.to_thread(
                parse_workbook,
                path,
                sheet_name=sheet_name,
                should_cancel=should_cancel,
            )
        except DailyImportCancelled:
            raise
        except Exception as exc:  # openpyxl raises assorted errors on bad files
            errors.append(FileLoadError(path=str(path), message=str(exc)))
            continue
        session.add_parsed(parsed)
    save_session(session)
    return errors
