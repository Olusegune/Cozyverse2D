"""Immutable source images with lineage. Untrusted bytes: decode-validated, size capped, re-encoded thumbnails."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

from .db import Database, dumps, now
from .security import safe_join

MAX_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 60_000_000          # decompression-bomb guard (about 7750 x 7750)
MIN_SIDE = 32
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
ORIGINS = ("IMPORTED", "GENERATED", "EDITED")
THUMB = 640

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageRejected(ValueError):
    pass


def validate_image(data: bytes) -> tuple[str, int, int]:
    """Content-sniff by decoding. Returns (format, width, height) or raises ImageRejected."""
    if not data or len(data) > MAX_BYTES:
        raise ImageRejected("file is empty or larger than 40 MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format or ""
            w, h = im.size
            if fmt not in FORMATS:
                raise ImageRejected("only PNG, JPEG and WebP images are supported")
            if w * h > MAX_PIXELS:
                raise ImageRejected("image has too many pixels")
            if w < MIN_SIDE or h < MIN_SIDE:
                raise ImageRejected("image is too small")
            if getattr(im, "n_frames", 1) > 1:
                raise ImageRejected("animated images are not supported")
            im.load()          # full decode proves the file is really an image
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError) as e:
        raise ImageRejected("file is not a valid image") from e
    return fmt, w, h


class SourceStore:
    def __init__(self, db: Database, project_dir):
        self.db = db
        self.project_dir = project_dir     # callable(project_id) -> Path

    def _row(self, rid: str) -> dict:
        r = self.db.one("SELECT * FROM source_revisions WHERE id=?", (rid,))
        if not r:
            raise LookupError("source")
        return r

    def public(self, r: dict) -> dict:
        return {"id": r["id"], "project_id": r["project_id"], "parent_id": r["parent_id"], "origin": r["origin"],
                "sha256": r["sha256"], "width": r["width"], "height": r["height"], "format": r["fmt"],
                "bytes": r["bytes"], "meta": json.loads(r["meta_json"]), "created_at": r["created_at"],
                "approved": r["approved_at"] is not None}

    def add(self, project_id: str, data: bytes, origin: str, parent_id: Optional[str] = None,
            meta: Optional[dict] = None) -> dict:
        if origin not in ORIGINS:
            raise ValueError("bad origin")
        fmt, w, h = validate_image(data)
        if parent_id:
            if self._row(parent_id)["project_id"] != project_id:
                raise ValueError("parent belongs to another project")
        rid = "s_" + uuid.uuid4().hex[:12]
        rel = f"source/{rid}/original{FORMATS[fmt]}"
        dest = safe_join(self.project_dir(project_id), *rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        os.chmod(dest, stat.S_IREAD)       # originals are immutable
        self._thumb(data, dest.parent / "thumb.webp")
        sha = hashlib.sha256(data).hexdigest()
        self.db.execute("INSERT INTO source_revisions(id,project_id,parent_id,origin,rel_path,sha256,width,height,"
                        "fmt,bytes,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, project_id, parent_id, origin, rel, sha, w, h, fmt, len(data), dumps(meta or {}), now()))
        return self.public(self._row(rid))

    @staticmethod
    def _thumb(data: bytes, out: Path) -> None:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB))
            im.save(out, "WEBP", quality=82)

    def list(self, project_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM source_revisions WHERE project_id=? ORDER BY created_at", (project_id,))
        return [self.public(r) for r in rows]

    def get(self, rid: str) -> dict:
        return self.public(self._row(rid))

    def path(self, rid: str, thumb: bool = False) -> Path:
        r = self._row(rid)
        base = safe_join(self.project_dir(r["project_id"]), *r["rel_path"].split("/"))
        p = base.parent / "thumb.webp" if thumb else base
        if not p.is_file():
            raise LookupError("source file")
        return p

    def verify(self, rid: str) -> bool:
        r = self._row(rid)
        return hashlib.sha256(self.path(rid).read_bytes()).hexdigest() == r["sha256"]

    def approve(self, project_id: str, rid: str) -> dict:
        r = self._row(rid)
        if r["project_id"] != project_id:
            raise ValueError("source belongs to another project")
        if not self.verify(rid):
            raise ValueError("source file changed on disk")
        self.db.execute("UPDATE source_revisions SET approved_at=? WHERE id=?", (now(), rid))
        self.db.execute("UPDATE projects SET approved_source_id=? WHERE id=?", (rid, project_id))
        return self.get(rid)

    def lineage(self, rid: str) -> list[dict]:
        out, seen = [], set()
        cur: Optional[str] = rid
        while cur and cur not in seen:
            seen.add(cur)
            r = self._row(cur)
            out.append(self.public(r))
            cur = r["parent_id"]
        return out
