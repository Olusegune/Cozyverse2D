"""Inventory of assets detected or drawn on an approved source: stable IDs, hierarchy, history, crops, masks."""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw

from .db import Database, dumps, now
from .security import safe_join

CATEGORIES = {
    "building": "BLD", "vehicle": "VEH", "character": "CHR", "animal": "ANM", "prop": "PRP", "sign": "SGN",
    "light": "LGT", "vegetation": "VEG", "furniture": "FRN", "structure": "STR", "background": "BGD",
    "effect": "VFX", "other": "OBJ", "master_base": "BASE", "calibration": "CAL",
}
VISIBILITY = ("fully_visible", "partially_visible", "front_and_partial_side", "mostly_occluded", "unseen_inferred")
STATUSES = ("confirmed", "unconfirmed", "disputed")
GOALS = ("2D_LAYER_EXPORT", "REFERENCE_ONLY", "3D_PROXY", "3D_RECONSTRUCTION")
KINDS = ("modelable", "background", "vfx")
EDITABLE = {"name", "category", "parent_id", "count", "visibility", "confidence", "scene_zone", "notes",
            "status", "goal", "kind", "bbox", "polygon"}


class InvalidItem(ValueError):
    pass


def check_bbox(b: Any) -> list[float]:
    if not (isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)):
        raise InvalidItem("bbox must be [x, y, w, h]")
    x, y, w, h = (float(v) for v in b)
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        raise InvalidItem("bbox must be normalized inside the image")
    return [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]


def check_polygon(p: Any) -> Optional[list[list[float]]]:
    if p is None:
        return None
    if not (isinstance(p, list) and 3 <= len(p) <= 500):
        raise InvalidItem("polygon needs 3 to 500 points")
    out = []
    for pt in p:
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in pt)):
            raise InvalidItem("polygon points must be normalized [x, y]")
        out.append([round(float(pt[0]), 5), round(float(pt[1]), 5)])
    return out


class Inventory:
    def __init__(self, db: Database, project_dir):
        self.db = db
        self.project_dir = project_dir

    # ----- helpers -----
    def _asset(self, aid: str) -> dict:
        r = self.db.one("SELECT * FROM assets WHERE id=?", (aid,))
        if not r:
            raise LookupError("asset")
        return r

    def snapshot(self, a: dict) -> dict:
        return {k: a[k] for k in ("id", "code", "name", "category", "parent_id", "count", "visibility", "confidence",
                                  "scene_zone", "notes", "status", "goal", "kind", "bbox_json", "polygon_json",
                                  "source_revision_id")}

    def _log(self, pid: str, aid: Optional[str], action: str, before: Optional[dict], after: Optional[dict]) -> None:
        self.db.execute("INSERT INTO inventory_history(project_id,asset_id,action,before_json,after_json,at) "
                        "VALUES(?,?,?,?,?,?)", (pid, aid, action, dumps(before) if before else None,
                                                dumps(after) if after else None, now()))

    def next_code(self, pid: str, category: str) -> str:
        """Next free code for the category. Codes of deleted items are never reused (stable IDs)."""
        prefix = CATEGORIES[category]
        pat = prefix + "-%"
        used = {r["code"] for r in self.db.all("SELECT code FROM assets WHERE project_id=? AND code LIKE ?", (pid, pat))}
        for col in ("before_json", "after_json"):
            for r in self.db.all(f"SELECT json_extract({col}, '$.code') AS c FROM inventory_history "
                                 f"WHERE project_id=? AND c LIKE ?", (pid, pat)):
                used.add(r["c"])
        nums = [int(c.split("-")[1]) for c in used if c and c.split("-")[1].isdigit()]
        return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"

    def _check_parent(self, pid: str, aid: Optional[str], parent_id: Optional[str]) -> None:
        if not parent_id:
            return
        p = self.db.one("SELECT id, parent_id, project_id FROM assets WHERE id=?", (parent_id,))
        if not p or p["project_id"] != pid:
            raise InvalidItem("parent not found in this project")
        cur, hops = p, 0
        while cur and hops < 64:
            if aid and cur["id"] == aid:
                raise InvalidItem("a parent cannot be its own descendant")
            cur = self.db.one("SELECT id, parent_id FROM assets WHERE id=?", (cur["parent_id"],)) if cur["parent_id"] else None
            hops += 1

    # ----- CRUD -----
    def create(self, pid: str, fields: dict) -> dict:
        name = str(fields.get("name", "")).strip()
        category = fields.get("category", "other")
        if not name or len(name) > 120:
            raise InvalidItem("name is required (max 120 characters)")
        if category not in CATEGORIES:
            raise InvalidItem("unknown category")
        bbox = check_bbox(fields["bbox"]) if fields.get("bbox") is not None else None
        poly = check_polygon(fields.get("polygon"))
        self._check_parent(pid, None, fields.get("parent_id"))
        src = fields.get("source_revision_id")
        if src:
            s = self.db.one("SELECT project_id FROM source_revisions WHERE id=?", (src,))
            if not s or s["project_id"] != pid:
                raise InvalidItem("source revision not found in this project")
        aid = "a_" + uuid.uuid4().hex[:12]
        code = self.next_code(pid, category)
        vis = fields.get("visibility", "partially_visible")
        status = fields.get("status", "confirmed")
        goal = fields.get("goal", "3D_RECONSTRUCTION")
        kind = fields.get("kind", "modelable")
        if vis not in VISIBILITY or status not in STATUSES or goal not in GOALS or kind not in KINDS:
            raise InvalidItem("invalid visibility, status, goal or kind")
        count = int(fields.get("count", 1))
        conf = float(fields.get("confidence", 1.0))
        if not (1 <= count <= 9999 and 0 <= conf <= 1):
            raise InvalidItem("count or confidence out of range")
        self.db.execute(
            "INSERT INTO assets(id,project_id,code,name,category,fixture,created_at,parent_id,source_revision_id,"
            "bbox_json,polygon_json,count,visibility,confidence,scene_zone,notes,status,goal,kind) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, pid, code, name, category, "custom", now(), fields.get("parent_id"), src,
             dumps(bbox) if bbox else None, dumps(poly) if poly else None, count, vis, conf,
             str(fields.get("scene_zone", ""))[:80], str(fields.get("notes", ""))[:2000], status, goal, kind))
        for v in ("front", "back", "left", "right", "perspective"):
            self.db.execute("INSERT INTO view_renders(asset_id,view,status,updated_at) VALUES(?,?,?,?)",
                            (aid, v, "BLOCKED", now()))
        a = self._asset(aid)
        self._log(pid, aid, "create", None, self.snapshot(a))
        if bbox and src:
            self.make_crop(aid)
        return a

    def update(self, aid: str, fields: dict) -> dict:
        a = self._asset(aid)
        bad = set(fields) - EDITABLE
        if bad:
            raise InvalidItem(f"cannot edit: {', '.join(sorted(bad))}")
        before = self.snapshot(a)
        sets: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                v = str(v).strip()
                if not v or len(v) > 120:
                    raise InvalidItem("name is required (max 120 characters)")
            elif k == "category":
                if v not in CATEGORIES:
                    raise InvalidItem("unknown category")
                if a["fixture"] != "custom":
                    raise InvalidItem("fixture assets keep their category")
            elif k == "parent_id":
                self._check_parent(a["project_id"], aid, v)
            elif k == "count":
                v = int(v)
                if not 1 <= v <= 9999:
                    raise InvalidItem("count out of range")
            elif k == "confidence":
                v = float(v)
                if not 0 <= v <= 1:
                    raise InvalidItem("confidence out of range")
            elif k == "visibility" and v not in VISIBILITY:
                raise InvalidItem("invalid visibility")
            elif k == "status" and v not in STATUSES:
                raise InvalidItem("invalid status")
            elif k == "goal" and v not in GOALS:
                raise InvalidItem("invalid goal")
            elif k == "kind" and v not in KINDS:
                raise InvalidItem("invalid kind")
            elif k == "bbox":
                v = dumps(check_bbox(v)); k = "bbox_json"
            elif k == "polygon":
                p = check_polygon(v); v = dumps(p) if p else None; k = "polygon_json"
            elif k in ("scene_zone", "notes"):
                v = str(v)[: 80 if k == "scene_zone" else 2000]
            sets[k] = v
        if sets:
            cols = ", ".join(f"{k}=?" for k in sets)
            self.db.execute(f"UPDATE assets SET {cols} WHERE id=?", (*sets.values(), aid))
        a2 = self._asset(aid)
        self._log(a["project_id"], aid, "update", before, self.snapshot(a2))
        if ("bbox_json" in sets or "polygon_json" in sets) and a2["source_revision_id"]:
            self.make_crop(aid)
        return a2

    def delete(self, aid: str) -> None:
        a = self._asset(aid)
        if a["fixture"] != "custom":
            raise InvalidItem("fixture assets cannot be deleted")
        before = self.snapshot(a)
        # children move up one level instead of disappearing
        self.db.execute("UPDATE assets SET parent_id=? WHERE parent_id=?", (a["parent_id"], aid))
        self.db.execute("DELETE FROM assets WHERE id=?", (aid,))
        self._log(a["project_id"], aid, "delete", before, None)

    def merge(self, pid: str, ids: list[str], into: str) -> dict:
        if into not in ids or len(set(ids)) < 2:
            raise InvalidItem("merge needs at least two items including the target")
        rows = [self._asset(i) for i in set(ids)]
        if any(r["project_id"] != pid or r["fixture"] != "custom" for r in rows):
            raise InvalidItem("only inventory items of this project can be merged")
        target = self._asset(into)
        before = [self.snapshot(r) for r in rows]
        total = 0
        for r in rows:
            total += r["count"]
            if r["id"] != into:
                if r["bbox_json"]:
                    self.db.execute("INSERT INTO asset_instances(id,asset_id,bbox_json,polygon_json,created_at) "
                                    "VALUES(?,?,?,?,?)", ("i_" + uuid.uuid4().hex[:10], into, r["bbox_json"],
                                                          r["polygon_json"], now()))
                self.db.execute("UPDATE assets SET parent_id=? WHERE parent_id=?", (target["parent_id"], r["id"]))
                self.db.execute("DELETE FROM assets WHERE id=?", (r["id"],))
        self.db.execute("UPDATE assets SET count=? WHERE id=?", (min(total, 9999), into))
        a = self._asset(into)
        self._log(pid, into, "merge", {"items": before}, self.snapshot(a))
        return a

    def add_instance(self, aid: str, bbox: Any, polygon: Any = None) -> dict:
        a = self._asset(aid)
        bb, poly = check_bbox(bbox), check_polygon(polygon)
        iid = "i_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO asset_instances(id,asset_id,bbox_json,polygon_json,created_at) VALUES(?,?,?,?,?)",
                        (iid, aid, dumps(bb), dumps(poly) if poly else None, now()))
        n = self.db.one("SELECT COUNT(*) AS n FROM asset_instances WHERE asset_id=?", (aid,))["n"]
        self.db.execute("UPDATE assets SET count=MAX(count, ?) WHERE id=?", (n + 1, aid))
        self._log(a["project_id"], aid, "add_instance", None, {"instance": iid, "bbox": bb})
        return {"id": iid, "bbox": bb, "polygon": poly}

    def instances(self, aid: str) -> list[dict]:
        return [{"id": r["id"], "bbox": json.loads(r["bbox_json"]),
                 "polygon": json.loads(r["polygon_json"]) if r["polygon_json"] else None}
                for r in self.db.all("SELECT * FROM asset_instances WHERE asset_id=? ORDER BY created_at", (aid,))]

    def history(self, pid: str, limit: int = 200) -> list[dict]:
        rows = self.db.all("SELECT * FROM inventory_history WHERE project_id=? ORDER BY id DESC LIMIT ?", (pid, limit))
        return [{"id": r["id"], "asset_id": r["asset_id"], "action": r["action"], "at": r["at"],
                 "before": json.loads(r["before_json"]) if r["before_json"] else None,
                 "after": json.loads(r["after_json"]) if r["after_json"] else None} for r in rows]

    def view(self, a: dict) -> dict:
        return {
            "parent_id": a["parent_id"], "source_revision_id": a["source_revision_id"],
            "bbox": json.loads(a["bbox_json"]) if a["bbox_json"] else None,
            "polygon": json.loads(a["polygon_json"]) if a["polygon_json"] else None,
            "count": a["count"], "visibility": a["visibility"], "confidence": a["confidence"],
            "scene_zone": a["scene_zone"], "notes": a["notes"], "status": a["status"], "goal": a["goal"],
            "kind": a["kind"], "has_crop": self.crop_path(a, "crop.png").is_file(),
        }

    # ----- crops and masks -----
    def asset_source_dir(self, a: dict) -> Path:
        return safe_join(self.project_dir(a["project_id"]), "assets", a["code"], "source")

    def crop_path(self, a: dict, name: str) -> Path:
        return self.asset_source_dir(a) / name

    def make_crop(self, aid: str, source_path: Optional[Path] = None) -> None:
        """Write source/crop.png (RGBA, polygon-masked if a polygon exists) and source/mask.png (L)."""
        a = self._asset(aid)
        if not a["bbox_json"] or not a["source_revision_id"]:
            return
        r = self.db.one("SELECT rel_path, project_id FROM source_revisions WHERE id=?", (a["source_revision_id"],))
        if not r:
            return
        src = source_path or safe_join(self.project_dir(r["project_id"]), *r["rel_path"].split("/"))
        x, y, w, h = json.loads(a["bbox_json"])
        with Image.open(src) as im:
            im = im.convert("RGBA")
            W, H = im.size
            box = (int(x * W), int(y * H), max(int(x * W) + 1, int((x + w) * W)), max(int(y * H) + 1, int((y + h) * H)))
            crop = im.crop(box)
        mask = Image.new("L", crop.size, 255)
        if a["polygon_json"]:
            mask = Image.new("L", crop.size, 0)
            pts = [((px - x) / w * crop.width, (py - y) / h * crop.height) for px, py in json.loads(a["polygon_json"])]
            ImageDraw.Draw(mask).polygon(pts, fill=255)
        out = crop.copy()
        out.putalpha(mask)
        d = self.asset_source_dir(a)
        d.mkdir(parents=True, exist_ok=True)
        out.save(d / "crop.png")
        mask.save(d / "mask.png")
