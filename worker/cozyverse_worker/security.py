"""Input validation and path safety helpers."""
from __future__ import annotations

import hmac
import re
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ASSET_CODE_RE = re.compile(r"^[A-Z]{2,5}-[0-9]{3,5}$")


class PathEscape(ValueError):
    pass


def check_id(value: str) -> str:
    if not ID_RE.match(value or ""):
        raise ValueError("invalid identifier")
    return value


def safe_join(root: Path, *parts: str) -> Path:
    """Join parts under root; raise if the result escapes root (after resolving symlinks)."""
    root_r = root.resolve()
    for p in parts:
        if p in ("", ".") or "\x00" in p or Path(p).is_absolute() or ".." in Path(p).parts:
            raise PathEscape(f"unsafe path component: {p!r}")
    candidate = root_r.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root_r):
        raise PathEscape("path escapes root")
    return candidate


def slugify(name: str, fallback: str = "project") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:48]
    return s or fallback


def token_ok(supplied: str | None, expected: str) -> bool:
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode("utf8"), expected.encode("utf8"))
