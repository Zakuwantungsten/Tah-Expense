"""Receipt upload ingress hardening — allowlist, magic bytes, sanitize, rate limit."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any

from .attachment_ops import ALLOWED_ATTACHMENT_EXTENSIONS, MAX_ATTACHMENT_BYTES

# Intermediate suffixes that must never appear in a receipt filename chain.
_DANGEROUS_EXT = frozenset(
    {
        ".exe",
        ".dll",
        ".scr",
        ".bat",
        ".cmd",
        ".com",
        ".msi",
        ".js",
        ".jse",
        ".vbs",
        ".vbe",
        ".wsf",
        ".wsh",
        ".ps1",
        ".jar",
        ".hta",
        ".cpl",
        ".pif",
        ".lnk",
        ".iso",
        ".img",
        ".dmg",
        ".apk",
        ".sh",
        ".bash",
        ".php",
        ".asp",
        ".aspx",
        ".cgi",
        ".py",
        ".rb",
        ".pl",
    }
)

_MZ = b"MZ"
_PDF = b"%PDF"
_PNG = b"\x89PNG\r\n\x1a\n"
_GIF87 = b"GIF87a"
_GIF89 = b"GIF89a"
_JPEG = b"\xff\xd8\xff"
_WEBP_RIFF = b"RIFF"
_WEBP_WEBP = b"WEBP"
_BMP = b"BM"
_TIFF_LE = b"II*\x00"
_TIFF_BE = b"MM\x00*"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
_ZIP_EMPTY = b"PK\x05\x06"

# ftyp brands for HEIC/HEIF
_HEIF_BRANDS = (
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"mif1",
    b"msf1",
    b"heim",
    b"heis",
)

_RATE_LOCK = Lock()
_RATE_HITS: dict[str, deque[float]] = defaultdict(deque)


class UploadSecurityError(ValueError):
    """Fail-closed upload rejection with a desk-facing reason."""

    def __init__(self, message: str, *, code: str = "upload_rejected") -> None:
        super().__init__(message)
        self.code = code


def sanitize_upload_filename(filename: str) -> str:
    """Basename only; strip path tricks and control chars."""
    name = Path(str(filename or "file").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip(" .") or "file"
    # Collapse runs of dots that enable double-ext confusion displays
    while ".." in name:
        name = name.replace("..", ".")
    return name[:180]


def extension_chain(filename: str) -> list[str]:
    name = Path(sanitize_upload_filename(filename)).name.lower()
    if "." not in name:
        return []
    return [f".{part}" for part in name.split(".")[1:] if part]


def primary_extension(filename: str) -> str:
    chain = extension_chain(filename)
    return chain[-1] if chain else ""


def allowed_receipt_extension(filename: str) -> bool:
    ext = primary_extension(filename)
    return bool(ext) and ext in ALLOWED_ATTACHMENT_EXTENSIONS


def has_dangerous_extension_chain(filename: str) -> bool:
    return any(ext in _DANGEROUS_EXT for ext in extension_chain(filename))


def _is_heif(content: bytes) -> bool:
    if len(content) < 12:
        return False
    if content[4:8] != b"ftyp":
        return False
    brand = content[8:12]
    return brand in _HEIF_BRANDS or any(b in content[8:24] for b in _HEIF_BRANDS)


def _looks_like_text(content: bytes) -> bool:
    if not content:
        return False
    sample = content[:8192]
    if b"\x00" in sample:
        return False
    if sample.startswith(_MZ) or sample.startswith(_OLE) or sample.startswith(_ZIP):
        return False
    # UTF-8 / ASCII printable-ish
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = sample.decode("latin-1")
        except UnicodeDecodeError:
            return False
    bad = sum(1 for ch in text if ord(ch) < 9 or (13 < ord(ch) < 32))
    return bad / max(len(text), 1) < 0.05


def sniff_content_type(content: bytes, filename: str) -> str | None:
    """Return canonical MIME from magic bytes when they match the allowlist ext."""
    if not content:
        return None
    ext = primary_extension(filename)
    head = content[:64]

    if head.startswith(_MZ):
        return None  # executable — never allowed
    if head.startswith(_PDF) and ext == ".pdf":
        return "application/pdf"
    if head.startswith(_JPEG) and ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if head.startswith(_PNG) and ext == ".png":
        return "image/png"
    if (head.startswith(_GIF87) or head.startswith(_GIF89)) and ext == ".gif":
        return "image/gif"
    if (
        len(content) >= 12
        and content.startswith(_WEBP_RIFF)
        and content[8:12] == _WEBP_WEBP
        and ext == ".webp"
    ):
        return "image/webp"
    if head.startswith(_BMP) and ext == ".bmp":
        return "image/bmp"
    if (head.startswith(_TIFF_LE) or head.startswith(_TIFF_BE)) and ext in {
        ".tif",
        ".tiff",
    }:
        return "image/tiff"
    if _is_heif(content) and ext in {".heic", ".heif"}:
        return "image/heic"
    if head.startswith(_OLE) and ext in {".doc", ".xls"}:
        return (
            "application/msword"
            if ext == ".doc"
            else "application/vnd.ms-excel"
        )
    if (head.startswith(_ZIP) or head.startswith(_ZIP_EMPTY)) and ext in {
        ".docx",
        ".xlsx",
    }:
        # OOXML is a zip; require declared office ext (already allowlisted).
        if ext == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext in {".csv", ".txt"} and _looks_like_text(content):
        return "text/csv" if ext == ".csv" else "text/plain"
    return None


def strip_jpeg_exif(content: bytes) -> bytes:
    """Remove APP1 (EXIF) segments from JPEG; leave other formats unchanged."""
    if not content.startswith(_JPEG) or len(content) < 4:
        return content
    out = bytearray(content[:2])  # SOI
    i = 2
    n = len(content)
    while i + 3 < n:
        if content[i] != 0xFF:
            out.extend(content[i:])
            break
        marker = content[i + 1]
        if marker == 0xDA:  # SOS — copy rest
            out.extend(content[i:])
            break
        if marker == 0xD9:  # EOI
            out.extend(content[i : i + 2])
            break
        if marker in {0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01, 0xD8}:
            out.extend(content[i : i + 2])
            i += 2
            continue
        if i + 3 >= n:
            break
        seglen = int.from_bytes(content[i + 2 : i + 4], "big")
        if seglen < 2 or i + 2 + seglen > n:
            out.extend(content[i:])
            break
        if marker == 0xE1:  # APP1 EXIF — skip
            i += 2 + seglen
            continue
        out.extend(content[i : i + 2 + seglen])
        i += 2 + seglen
    return bytes(out) if len(out) > 2 else content


def sanitize_upload_bytes(content: bytes, content_type: str) -> bytes:
    if content_type == "image/jpeg":
        return strip_jpeg_exif(content)
    return content


def validate_receipt_upload(
    filename: str,
    content: bytes,
    *,
    claimed_content_type: str | None = None,
) -> dict[str, Any]:
    """Fail closed. Never trusts client MIME. Returns sanitized name + sniffed type."""
    del claimed_content_type  # explicitly ignored
    name = sanitize_upload_filename(filename)
    if not content:
        raise UploadSecurityError("Empty file rejected", code="empty_file")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise UploadSecurityError(
            f"File exceeds maximum size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.",
            code="too_large",
        )
    if has_dangerous_extension_chain(name):
        raise UploadSecurityError(
            f"Dangerous filename '{name}' rejected",
            code="dangerous_extension",
        )
    if not allowed_receipt_extension(name):
        raise UploadSecurityError(
            f"Unsupported file type '{primary_extension(name) or '(none)'}'. "
            "Allowed: PDF, images (incl. HEIC), Office docs, CSV, TXT.",
            code="bad_extension",
        )
    sniffed = sniff_content_type(content, name)
    if not sniffed:
        raise UploadSecurityError(
            f"File content does not match type '{primary_extension(name)}' "
            "(magic-byte check failed).",
            code="magic_mismatch",
        )
    cleaned = sanitize_upload_bytes(content, sniffed)
    return {
        "filename": name,
        "content": cleaned,
        "content_type": sniffed,
        "size": len(cleaned),
        "extension": primary_extension(name),
    }


def check_mobile_upload_rate(
    user_key: str,
    *,
    limit: int,
    window_seconds: int = 3600,
    file_count: int = 1,
) -> None:
    """In-process sliding window. Raises UploadSecurityError when exceeded."""
    if limit <= 0:
        return
    key = str(user_key or "anon")
    now = time.monotonic()
    with _RATE_LOCK:
        hits = _RATE_HITS[key]
        cutoff = now - max(1, int(window_seconds))
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) + max(1, int(file_count)) > limit:
            raise UploadSecurityError(
                f"Upload rate limit exceeded ({limit} files / hour). Try again later.",
                code="rate_limited",
            )
        for _ in range(max(1, int(file_count))):
            hits.append(now)


def reset_rate_limits_for_tests() -> None:
    with _RATE_LOCK:
        _RATE_HITS.clear()
