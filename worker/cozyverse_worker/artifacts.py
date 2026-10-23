"""Artifact QA service: analyze, preview repairs, accept (explicit), revert. Originals are never lost."""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

from . import artifact_qa as aq
from . import qa
from .db import Database, dumps, now
from .security import safe_join

REMEDIATION = {
    "COLOR_CAST": ["Re-render with neutral studio lighting (preferred)", "Preview a masked colour correction"],
    "NOISE": ["Re-render with more samples or a denoiser pass (preferred)", "Preview a flat-area noise reduction"],
    "WAXY_SKIN_SUSPECT": ["Review the character material (roughness, subsurface) and re-render. No automatic fix."],
    "INCONSISTENT_HEIGHT": ["Re-render all five views from the same model revision"],
}
KINDS = ("cast", "noise")


class RepairError(RuntimeError):
    pass


class Artifacts:
    def __init__(self, db: Database, get_asset: Callable[[str], dict], asset_dir: Callable[[dict], Path],
                 write_manifest: Callable[[str], None]):
        self.db, self.get_asset, self.asset_dir, self.write_manifest = db, get_asset, asset_dir, write_manifest

    # ----- helpers -----
    def _view_row(self, aid: str, view: str) -> dict:
        r = self.db.one("SELECT * FROM view_renders WHERE asset_id=? AND view=?", (aid, view))
        if not r or not r["path"]:
            raise RepairError("this view has not been rendered")
        return r

    def _file(self, a: dict, view: str) -> Path:
        return self.asset_dir(a) / "views" / f"{view}.png"

    def _load(self, p: Path) -> tuple[np.ndarray, Optional[np.ndarray]]:
        with Image.open(p) as im:
            arr = np.asarray(im.convert("RGBA"))
        return arr[..., :3].copy(), arr[..., 3].copy()

    # ----- analyze -----
    def analyze_view(self, aid: str, view: str, settings: Optional[dict] = None) -> dict:
        a = self.get_asset(aid)
        row = self._view_row(aid, view)
        s = aq.ArtifactSettings.from_dict(settings)
        p = self._file(a, view)
        sha = qa.sha256_file(p)
        meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
        white = meta.get("background", "white") == "white"
        rep = aq.analyze(Image.open(p), s, category=a["category"], white_bg=white)
        mdir = self.asset_dir(a) / "qa" / "artifacts" / view
        mdir.mkdir(parents=True, exist_ok=True)
        saved = []
        for name, m in rep["masks"].items():
            aq.mask_image(m.astype(np.float64)).save(mdir / f"{name}.png")
            saved.append(name)
        pub = aq.public_report(rep)
        for f in pub["findings"]:
            f["remediation"] = REMEDIATION.get(f["code"], [])
        pub["masks"] = saved
        pub["view"], pub["sha256"] = view, sha
        self.db.execute("INSERT INTO artifact_reports(asset_id,view,report_json,sha256,at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(asset_id,view) DO UPDATE SET report_json=excluded.report_json, "
                        "sha256=excluded.sha256, at=excluded.at", (aid, view, dumps(pub), sha, now()))
        return {**pub, "stale": False}

    def analyze_asset(self, aid: str, settings: Optional[dict] = None) -> dict:
        a = self.get_asset(aid)
        views, boxes = {}, {}
        for v in ("front", "back", "left", "right", "perspective"):
            r = self.db.one("SELECT path, meta_json FROM view_renders WHERE asset_id=? AND view=?", (aid, v))
            if not r or not r["path"]:
                continue
            views[v] = self.analyze_view(aid, v, settings)
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            res = (meta.get("resolution") or [0])[0]
            an = qa.analyze_png(self._file(a, v), res or Image.open(self._file(a, v)).width,
                                meta.get("background", "white"))
            boxes[v] = an["bbox"]
        res = max((json.loads(r["meta_json"]).get("resolution", [0])[0] for r in self.db.all(
            "SELECT meta_json FROM view_renders WHERE asset_id=? AND meta_json IS NOT NULL", (aid,))), default=0)
        cons = aq.consistency_findings(boxes, res or 1) if aq.ArtifactSettings.from_dict(settings).check_consistency else []
        return {"views": views, "consistency": cons}

    def latest(self, aid: str, view: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM artifact_reports WHERE asset_id=? AND view=?", (aid, view))
        if not r:
            return None
        a = self.get_asset(aid)
        p = self._file(a, view)
        stale = (not p.is_file()) or qa.sha256_file(p) != r["sha256"]
        return {**json.loads(r["report_json"]), "stale": stale, "analyzed_at": r["at"]}

    def blocking(self, aid: str) -> list[str]:
        """Critical findings on the CURRENT files that nobody has resolved. Stale reports never block."""
        out = []
        for v in ("front", "back", "left", "right", "perspective"):
            rep = self.latest(aid, v)
            if rep and not rep["stale"]:
                out += [f"{v}:{f['code']}" for f in rep["findings"] if f["severity"] == "critical"]
        return out

    # ----- repairs -----
    def _rdir(self, a: dict, rid: str) -> Path:
        return safe_join(self.asset_dir(a), "qa", "repairs", rid)

    def preview(self, aid: str, view: str, kind: str, strength: float = 1.0) -> dict:
        if kind not in KINDS:
            raise RepairError("only colour-cast and noise repairs exist. Skin and shape issues are fixed by re-rendering.")
        if not 0.05 <= strength <= 1.0:
            raise RepairError("strength must be between 0.05 and 1")
        a = self.get_asset(aid)
        row = self._view_row(aid, view)
        rep = self.latest(aid, view)
        if not rep or rep["stale"]:
            raise RepairError("analyze this view first so the repair uses its current masks")
        p = self._file(a, view)
        rgb, alpha = self._load(p)
        white = (json.loads(row["meta_json"]) if row["meta_json"] else {}).get("background", "white") == "white"
        fg = aq.foreground(rgb, alpha, white)
        before_sha = qa.sha256_file(p)
        if kind == "cast":
            c = rep["metrics"].get("cast", {})
            if not c.get("ok") or c["magnitude"] < aq.CAST_WARN:
                raise RepairError("no colour cast was measured on this view")
            out, w = aq.neutralize_cast(rgb, (c["a"], c["b"]), fg, strength, curve=c.get("curve"))
            params = {"cast_a": c["a"], "cast_b": c["b"], "curve": c.get("curve"), "strength": strength, "max_de": 14.0}
            mask = w
        else:
            n = rep["metrics"].get("noise", {})
            if not n.get("ok") or n["sigma"] < aq.NOISE_WARN:
                raise RepairError("no noise was measured in the flat areas of this view")
            flat = np.asarray(Image.open(self.asset_dir(a) / "qa" / "artifacts" / view / "flat.png")) > 127
            out, w = aq.reduce_noise(rgb, flat, strength)
            params = {"strength": strength, "sigma_before": n["sigma"]}
            mask = w
        res_png = aq.to_png(out, alpha if (alpha < 255).any() else None)
        after_rep = aq.analyze(Image.open(io.BytesIO(res_png)), aq.ArtifactSettings(), category=a["category"], white_bg=white)
        lab = aq.srgb_to_lab(rgb)
        protected = fg & (np.hypot(lab[..., 1], lab[..., 2]) > aq.PROTECT_CHROMA)
        metrics = {"before": {"cast": rep["metrics"].get("cast"), "noise": rep["metrics"].get("noise")},
                   "after": {"cast": {k: v for k, v in after_rep["metrics"].get("cast", {}).items() if k != "mask"},
                             "noise": {k: v for k, v in after_rep["metrics"].get("noise", {}).items() if k != "mask"}},
                   "changed_pixels": int((np.abs(out.astype(int) - rgb.astype(int)).max(axis=-1) > 0).sum()),
                   "protected_pixels": int(protected.sum()),
                   "protected_untouched": bool(np.array_equal(out[protected], rgb[protected])) if kind == "cast" else True}
        rid = "r_" + uuid.uuid4().hex[:10]
        d = self._rdir(a, rid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "before.png").write_bytes(p.read_bytes())
        (d / "after.png").write_bytes(res_png)
        aq.diff_heatmap(rgb, out).save(d / "diff.png")
        aq.mask_image(mask).save(d / "mask.png")
        self.db.execute("INSERT INTO view_repairs(id,asset_id,view,kind,params_json,metrics_json,original_sha,result_sha,"
                        "rel_dir,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (rid, aid, view, kind, dumps(params), dumps(metrics), before_sha,
                         hashlib.sha256(res_png).hexdigest(), f"qa/repairs/{rid}", now()))
        return {"id": rid, "kind": kind, "view": view, "params": params, "metrics": metrics, "accepted": False,
                "files": ["before", "after", "diff", "mask"]}

    def _repair(self, aid: str, rid: str) -> dict:
        r = self.db.one("SELECT * FROM view_repairs WHERE id=? AND asset_id=?", (rid, aid))
        if not r:
            raise LookupError("repair")
        return r

    def repair_file(self, aid: str, rid: str, which: str) -> Path:
        if which not in ("before", "after", "diff", "mask"):
            raise RepairError("unknown file")
        r = self._repair(aid, rid)
        p = self._rdir(self.get_asset(aid), rid) / f"{which}.png"
        if not p.is_file():
            raise LookupError("repair file")
        return p

    def accept(self, aid: str, rid: str, note: str = "") -> dict:
        a = self.get_asset(aid)
        r = self._repair(aid, rid)
        if r["accepted_at"]:
            raise RepairError("this repair was already accepted")
        view = r["view"]
        p = self._file(a, view)
        if not p.is_file() or qa.sha256_file(p) != r["original_sha"]:
            raise RepairError("the view changed after this preview. Analyze it again and make a new preview.")
        d = self._rdir(a, rid)
        odir = self.asset_dir(a) / "qa" / "originals"
        odir.mkdir(parents=True, exist_ok=True)
        keep = odir / f"{view}_{r['original_sha'][:12]}.png"
        if not keep.exists():
            keep.write_bytes(p.read_bytes())                       # the original is never lost
        tmp = p.with_suffix(".png.tmp")
        tmp.write_bytes((d / "after.png").read_bytes())
        os.replace(tmp, p)
        row = self.db.one("SELECT qa_json FROM view_renders WHERE asset_id=? AND view=?", (aid, view))
        qa_json = json.loads(row["qa_json"]) if row and row["qa_json"] else {}
        log = qa_json.setdefault("repairs", [])
        log.append({"id": rid, "kind": r["kind"], "params": json.loads(r["params_json"]),
                    "original_sha": r["original_sha"], "result_sha": r["result_sha"], "accepted_at": now(), "note": note[:500]})
        self.db.execute("UPDATE view_renders SET sha256=?, status='NEEDS_REVIEW', qa_json=?, updated_at=? "
                        "WHERE asset_id=? AND view=?", (r["result_sha"], dumps(qa_json), now(), aid, view))
        self.db.execute("UPDATE view_repairs SET accepted_at=?, note=? WHERE id=?", (now(), note[:500], rid))
        self.write_manifest(aid)
        return self.analyze_view(aid, view)

    def reject(self, aid: str, rid: str) -> None:
        r = self._repair(aid, rid)
        if r["accepted_at"]:
            raise RepairError("accepted repairs are reverted, not rejected")
        import shutil
        shutil.rmtree(self._rdir(self.get_asset(aid), rid), ignore_errors=True)
        self.db.execute("DELETE FROM view_repairs WHERE id=?", (rid,))

    def revert(self, aid: str, view: str) -> dict:
        a = self.get_asset(aid)
        reps = self.db.all("SELECT * FROM view_repairs WHERE asset_id=? AND view=? AND accepted_at IS NOT NULL "
                           "ORDER BY accepted_at", (aid, view))
        if not reps:
            raise RepairError("this view has no accepted repairs")
        orig = reps[0]["original_sha"]
        keep = self.asset_dir(a) / "qa" / "originals" / f"{view}_{orig[:12]}.png"
        if not keep.is_file():
            raise RepairError("the saved original is missing")
        p = self._file(a, view)
        tmp = p.with_suffix(".png.tmp")
        tmp.write_bytes(keep.read_bytes())
        os.replace(tmp, p)
        row = self.db.one("SELECT qa_json FROM view_renders WHERE asset_id=? AND view=?", (aid, view))
        qa_json = json.loads(row["qa_json"]) if row and row["qa_json"] else {}
        qa_json.setdefault("repairs", []).append({"reverted_to": orig, "at": now()})
        self.db.execute("UPDATE view_renders SET sha256=?, status='NEEDS_REVIEW', qa_json=?, updated_at=? "
                        "WHERE asset_id=? AND view=?", (orig, dumps(qa_json), now(), aid, view))
        self.db.execute("DELETE FROM view_repairs WHERE asset_id=? AND view=? AND accepted_at IS NOT NULL", (aid, view))
        self.write_manifest(aid)
        return self.analyze_view(aid, view)

    def repairs(self, aid: str, view: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM view_repairs WHERE asset_id=?", [aid]
        if view:
            q += " AND view=?"; args.append(view)
        return [{"id": r["id"], "view": r["view"], "kind": r["kind"], "params": json.loads(r["params_json"]),
                 "metrics": json.loads(r["metrics_json"]), "accepted": r["accepted_at"] is not None, "note": r["note"]}
                for r in self.db.all(q + " ORDER BY created_at", tuple(args))]
