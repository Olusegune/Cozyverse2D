"""Domain service: projects, assets, jobs, five-view rendering, review, export."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

import jsonschema

from . import blender_adapter, qa
from .mesh_import import validate_glb
from .config import BLENDER_DIR, SCHEMA_DIR, Settings
from .db import Database, dumps, now
from .creation import Creator, Providers
from .inventory import Inventory
from .security import ASSET_CODE_RE, PathEscape, safe_join, slugify
from .providers import build_registry
from .secret_store import SecretStore, default_store
from .sources import SourceStore
from .styles import StyleStore

if str(BLENDER_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_DIR))
import cv_camera  # noqa: E402
import proxy_spec  # noqa: E402

VIEWS = cv_camera.ALL_VIEWS
MAX_QUEUE = 20

FIXTURES = {
    "bar": ("BLD-003", "Neon BAR", "building"),
    "master_base": ("BASE-001", "Master diorama base", "master_base"),
    "calibration_cube": ("CAL-001", "Calibration cube", "calibration"),
    "chart": ("CHT-001", "Neutral test chart", "calibration"),
}

APPROVED_MESH = ("PROXY_APPROVED", "MESH_APPROVED")

INFERRED = {
    "front": [], "back": ["unseen rear wall"], "left": ["unseen left wall"],
    "right": ["unseen right wall"], "perspective": [], "top": ["unseen roof"],
}


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


class Service:
    def __init__(self, settings: Settings, secret_store: Optional[SecretStore] = None, registry: Optional[dict] = None):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv-job")
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.sources = SourceStore(self.db, self.project_dir)
        self.inv = Inventory(self.db, self.project_dir)
        self.secrets = secret_store if secret_store is not None else default_store()
        self.providers = Providers(self.db, self.secrets, registry if registry is not None else build_registry())
        self.styles = StyleStore(self.db)
        self.creator = Creator(self.db, self.providers, self.styles, self.sources, self.submit_generic, self.get_project)
        self.db.execute("UPDATE jobs SET state='FAILED', message='Interrupted by restart; run again', "
                        "updated_at=? WHERE state IN ('RUNNING','QUEUED','NEW')", (now(),))

    # ---------- blender ----------
    def blender_info(self) -> dict:
        return blender_adapter.detect(self.settings.blender_path or self.db.get_setting("blender_path"))

    def set_blender_path(self, path: str) -> dict:
        info = blender_adapter.detect(path)
        if not info["found"] or Path(info["path"]) != Path(path):
            raise ValueError("not a working Blender executable")
        self.settings.blender_path = path
        self.db.set_setting("blender_path", path)
        return info

    def _blender(self) -> str:
        info = self.blender_info()
        if not info["found"]:
            raise Conflict("Blender not found. Set the Blender path in Settings.")
        return info["path"]

    # ---------- paths ----------
    def project_dir(self, project_id: str) -> Path:
        return safe_join(self.settings.projects_dir, project_id)

    def asset_dir(self, asset: dict) -> Path:
        return safe_join(self.project_dir(asset["project_id"]), "assets", asset["code"])

    # ---------- projects / assets ----------
    SCENE_MODES = ("DIORAMA", "REGULAR_IMAGE")
    OUTPUT_GOALS = ("2D_LAYER_EXPORT", "REFERENCE_ONLY", "3D_PROXY", "3D_RECONSTRUCTION", "SCENE_REASSEMBLY")
    BASE_MODES = ("NONE", "FOUNDATION", "LOCAL_PLINTH", "SCENE_CONTEXT", "MASTER_DIORAMA")

    def create_project(self, name: str, fixtures: list[str], scene_mode: str = "DIORAMA",
                       output_goal: str = "3D_RECONSTRUCTION") -> dict:
        if scene_mode not in self.SCENE_MODES or output_goal not in self.OUTPUT_GOALS:
            raise ValueError("bad scene mode or output goal")
        pid = "p_" + uuid.uuid4().hex[:12]
        slug = slugify(name)
        base_mode = "MASTER_DIORAMA" if scene_mode == "DIORAMA" else "NONE"   # regular images never get a forced base
        self.db.execute("INSERT INTO projects(id,name,slug,scene_mode,created_at,output_goal,base_mode) "
                        "VALUES(?,?,?,?,?,?,?)", (pid, name, slug, scene_mode, now(), output_goal, base_mode))
        self.project_dir(pid).mkdir(parents=True, exist_ok=True)
        for f in fixtures:
            self.add_asset(pid, f)
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict:
        row = self.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not row:
            raise NotFound("project")
        return row

    def update_project(self, pid: str, fields: dict) -> dict:
        """Mode/goal switches never delete revisions, masks or recipes (provenance is kept)."""
        self.get_project(pid)
        allowed = {"scene_mode": self.SCENE_MODES, "output_goal": self.OUTPUT_GOALS, "base_mode": self.BASE_MODES}
        for k, v in fields.items():
            if k == "name":
                if not (isinstance(v, str) and 1 <= len(v.strip()) <= 120):
                    raise ValueError("bad name")
                self.db.execute("UPDATE projects SET name=? WHERE id=?", (v.strip(), pid))
            elif k == "style_recipe_id":
                self.db.execute("UPDATE projects SET style_recipe_id=? WHERE id=?", (v, pid))
            elif k in allowed:
                if v not in allowed[k]:
                    raise ValueError(f"bad {k}")
                self.db.execute(f"UPDATE projects SET {k}=? WHERE id=?", (v, pid))
            else:
                raise ValueError(f"cannot edit {k}")
        return self.get_project(pid)

    def list_projects(self) -> list[dict]:
        return self.db.all("SELECT * FROM projects ORDER BY created_at DESC")

    def add_asset(self, pid: str, fixture: str) -> dict:
        self.get_project(pid)
        if fixture not in FIXTURES:
            raise ValueError("unknown fixture")
        code, name, cat = FIXTURES[fixture]
        if self.db.one("SELECT id FROM assets WHERE project_id=? AND code=?", (pid, code)):
            raise Conflict(f"{code} already exists in project")
        aid = "a_" + uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO assets(id,project_id,code,name,category,fixture,created_at) "
                        "VALUES(?,?,?,?,?,?,?)", (aid, pid, code, name, cat, fixture, now()))
        for v in VIEWS:
            self.db.execute("INSERT INTO view_renders(asset_id,view,status,updated_at) VALUES(?,?,?,?)",
                            (aid, v, "BLOCKED", now()))
        return self.get_asset(aid)

    def get_asset(self, aid: str) -> dict:
        row = self.db.one("SELECT * FROM assets WHERE id=?", (aid,))
        if not row:
            raise NotFound("asset")
        return row

    def list_assets(self, pid: str) -> list[dict]:
        self.get_project(pid)
        out = []
        for a in self.db.all("SELECT * FROM assets WHERE project_id=? ORDER BY code", (pid,)):
            out.append(self.asset_view(a))
        return out

    def asset_view(self, a: dict) -> dict:
        views = {}
        for r in self.db.all("SELECT * FROM view_renders WHERE asset_id=?", (a["id"],)):
            qa_json = json.loads(r["qa_json"]) if r["qa_json"] else None
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            cam = meta.get("camera") or {}
            summary = {
                "engine": meta.get("engine"), "resolution": meta.get("resolution"),
                "base_policy": meta.get("base_policy"), "forward": cam.get("forward"),
                "ortho_scale": cam.get("ortho_scale"), "lens_mm": cam.get("lens_mm"),
                "quadrant": cam.get("quadrant"), "elevation_deg": cam.get("elevation_deg"),
                "blender_version": meta.get("blender_version"), "seconds": meta.get("seconds"),
            } if meta else None
            views[r["view"]] = {
                "meta": summary,
                "status": r["status"], "path": r["path"], "sha256": r["sha256"],
                "model_revision": r["model_revision"], "qa": qa_json,
                "camera_type": "PERSP" if r["view"] == "perspective" else "ORTHO",
                "stale": bool(qa_json and qa_json.get("stale")),
            }
        return {"id": a["id"], "project_id": a["project_id"], "code": a["code"], "name": a["name"],
                "category": a["category"], "fixture": a["fixture"], "mesh_status": a["mesh_status"],
                "model_revision": a["model_revision"], "params": json.loads(a["params_json"]),
                "views": views, **self.inv.view(a)}

    # ---------- jobs ----------
    def _new_job(self, kind: str, subject: str, key: Optional[str] = None) -> str:
        if key:
            dup = self.db.one("SELECT id FROM jobs WHERE idempotency_key=? AND state IN ('QUEUED','RUNNING')",
                              (key,))
            if dup:
                return dup["id"]
        if self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('QUEUED','RUNNING')")["n"] >= MAX_QUEUE:
            raise Conflict("job queue is full")
        jid = "j_" + uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO jobs(id,kind,subject_id,state,progress,message,idempotency_key,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (jid, kind, subject, "QUEUED", 0.0, "Queued", key, now(), now()))
        self._cancel[jid] = threading.Event()
        return jid

    def _update_job(self, jid: str, **kw: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in kw) + ", updated_at=?"
        self.db.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*kw.values(), now(), jid))

    def _submit(self, jid: str, fn: Callable[[threading.Event], dict]) -> None:
        ev = self._cancel[jid]

        def run() -> None:
            if ev.is_set():
                self._update_job(jid, state="CANCELLED", message="Cancelled before start")
                return
            self._update_job(jid, state="RUNNING", message="Running")
            try:
                result = fn(ev)
                state = result.pop("_state", "SUCCEEDED")
                self._update_job(jid, state=state, progress=1.0 if state == "SUCCEEDED" else 0.0,
                                 message=result.get("message", state), result_json=dumps(result))
                if state != "SUCCEEDED":
                    row = self.db.one("SELECT progress FROM jobs WHERE id=?", (jid,))
                    self._update_job(jid, progress=row["progress"] if row else 0.0)
            except blender_adapter.Cancelled:
                self._update_job(jid, state="CANCELLED", message="Cancelled")
            except Conflict as e:
                self._update_job(jid, state="BLOCKED", message=str(e))
            except Exception as e:  # noqa: BLE001 - surface any failure on the job, never crash the pool
                self._update_job(jid, state="FAILED", message=f"{type(e).__name__}: {e}"[:500])

        self._pool.submit(run)

    def submit_generic(self, kind: str, subject: str, fn) -> str:
        """Queue any worker function as a job (one at a time, cancellable, failures surface on the job)."""
        jid = self._new_job(kind, subject)
        self._submit(jid, fn)
        return jid

    def get_job(self, jid: str) -> dict:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not row:
            raise NotFound("job")
        res = json.loads(row["result_json"]) if row["result_json"] else None
        return {"id": row["id"], "kind": row["kind"], "subject_id": row["subject_id"],
                "state": row["state"], "progress": row["progress"], "message": row["message"],
                "result": res}

    def cancel_job(self, jid: str) -> dict:
        job = self.get_job(jid)
        ev = self._cancel.get(jid)
        if ev and job["state"] in ("QUEUED", "RUNNING"):
            ev.set()
        return self.get_job(jid)

    def wait_job(self, jid: str, timeout: float = 600.0) -> dict:
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.get_job(jid)
            if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
                return j
            time.sleep(0.1)
        raise TimeoutError(jid)

    # ---------- proxy ----------
    def _spec_for(self, asset: dict) -> dict:
        params = json.loads(asset["params_json"])
        mesh = params.pop("_mesh", None)
        if asset["mesh_source"] == "imported" and mesh:
            path = self.asset_dir(asset) / "mesh" / "imported.glb"
            return proxy_spec.mesh_spec(asset["code"], asset["name"], str(path), mesh["sha256"],
                                        mesh.get("yaw_deg", 0), mesh.get("target_height"))
        if asset["fixture"] == "custom":
            return proxy_spec.parametric_spec(asset["code"], asset["name"], asset["category"], params)
        if asset["fixture"] == "master_base":
            return proxy_spec.master_base_spec(**{k: v for k, v in params.items() if k in proxy_spec.BASE_PARAMS})
        if asset["fixture"] == "bar":
            return proxy_spec.bar_spec(plinth_margin=params.get("plinth_margin", 0.5),
                                       plinth_height=params.get("plinth_height", 0.2))
        return proxy_spec.get_spec(asset["fixture"])

    def build_proxy(self, aid: str, params: Optional[dict] = None) -> str:
        asset = self.get_asset(aid)
        if params is not None:
            keep = json.loads(asset["params_json"]).get("_mesh")
            if asset["fixture"] == "custom":
                params = proxy_spec.clean_params(asset["category"], params)
            elif asset["fixture"] == "master_base":
                params = proxy_spec.clean_base_params(params)
            elif asset["fixture"] == "bar":
                ok = {"plinth_margin": (0.0, 5.0), "plinth_height": (0.02, 1.0)}
                for k, v in params.items():
                    if k not in ok or not isinstance(v, (int, float)) or isinstance(v, bool) or not ok[k][0] <= v <= ok[k][1]:
                        raise ValueError(f"bad parameter {k}")
            if keep:
                params = {**params, "_mesh": keep}
            self.db.execute("UPDATE assets SET params_json=? WHERE id=?", (dumps(params), aid))
        jid = self._new_job("mesh_proxy", aid)

        def work(ev: threading.Event) -> dict:
            self.db.execute("UPDATE assets SET mesh_source='proxy' WHERE id=?", (aid,))   # procedural source
            a = self.get_asset(aid)
            spec = self._spec_for(a)
            rev = proxy_spec.spec_hash_stable(spec)
            d = self.asset_dir(a)
            (d / "mesh").mkdir(parents=True, exist_ok=True)
            spec_path = d / "mesh" / "spec.json"
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf8")
            blender = self._blender()
            glb = d / "mesh" / "canonical.glb"
            blender_adapter.render_view(blender, spec_path, glb, d / "mesh" / "build_meta.json",
                                        view="front", policy="full_master_base" if a["category"] == "master_base" else "local_plinth",
                                        mode="glb", cancel=ev, include="asset,foundation,plinth")
            old = a["model_revision"]
            self.db.execute("UPDATE assets SET model_revision=?, mesh_status='PROXY_APPROVED', mesh_source='proxy' "
                            "WHERE id=?", (rev, aid))
            if old and old != rev:
                self._invalidate_views(aid)
            return {"message": "Proxy built", "model_revision": rev, "mesh": "mesh/canonical.glb"}

        self._submit(jid, work)
        return jid

    # ---------- sources and inventory ----------
    def add_source(self, pid: str, data: bytes, origin: str = "IMPORTED", parent_id: Optional[str] = None,
                   meta: Optional[dict] = None) -> dict:
        self.get_project(pid)
        return self.sources.add(pid, data, origin, parent_id, meta)

    def approve_source(self, pid: str, rid: str) -> dict:
        self.get_project(pid)
        return self.sources.approve(pid, rid)

    def create_inventory(self, pid: str, fields: dict) -> dict:
        proj = self.get_project(pid)
        fields = dict(fields)
        if fields.get("bbox") is not None:
            src = fields.get("source_revision_id") or proj["approved_source_id"]
            if not src:
                raise Conflict("Approve a source image before adding items to the inventory.")
            row = self.db.one("SELECT approved_at FROM source_revisions WHERE id=?", (src,))
            if not row or row["approved_at"] is None:
                raise Conflict("That source image is not approved yet.")
            fields["source_revision_id"] = src
        return self.asset_view(self.inv.create(pid, fields))

    def update_inventory(self, aid: str, fields: dict) -> dict:
        a = self.inv.update(aid, fields)
        if "category" in fields:
            self.db.execute("UPDATE assets SET params_json='{}' WHERE id=?", (aid,))   # old proxy params no longer fit
        return self.asset_view(a)

    def delete_inventory(self, aid: str) -> None:
        a = self.get_asset(aid)
        self.inv.delete(aid)
        shutil.rmtree(self.asset_dir(a), ignore_errors=True)

    def merge_inventory(self, pid: str, ids: list[str], into: str) -> dict:
        self.get_project(pid)
        a = self.inv.merge(pid, ids, into)
        return self.asset_view(a)

    def inventory_tree(self, pid: str) -> list[dict]:
        items = {x["id"]: {**x, "children": []} for x in self.list_assets(pid)}
        roots = []
        for it in items.values():
            parent = items.get(it["parent_id"]) if it.get("parent_id") else None
            (parent["children"] if parent else roots).append(it)
        return roots

    # ---------- imported meshes ----------
    def import_mesh(self, aid: str, data: bytes) -> dict:
        a = self.get_asset(aid)
        if a["fixture"] == "master_base":
            raise Conflict("the master base is built with the base designer")
        info = validate_glb(data)
        d = self.asset_dir(a) / "mesh"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "imported.glb.tmp"
        tmp.write_bytes(data)
        tmp.replace(d / "imported.glb")
        import hashlib as _h
        sha = _h.sha256(data).hexdigest()
        params = json.loads(a["params_json"])
        params["_mesh"] = {"sha256": sha, "yaw_deg": 0, "target_height": None, "summary": info}
        self.db.execute("UPDATE assets SET params_json=?, mesh_source='imported', "
                        "mesh_status='IMPORTED_PENDING_REVIEW' WHERE id=?", (dumps(params), aid))
        self._invalidate_views(aid)
        return self.asset_view(self.get_asset(aid))

    def set_mesh_orientation(self, aid: str, yaw_deg: int, target_height: Optional[float]) -> dict:
        a = self.get_asset(aid)
        params = json.loads(a["params_json"])
        if a["mesh_source"] != "imported" or "_mesh" not in params:
            raise Conflict("this asset has no imported mesh")
        proxy_spec.mesh_spec(a["code"], a["name"], "x", params["_mesh"]["sha256"], yaw_deg, target_height)  # validates
        params["_mesh"].update({"yaw_deg": yaw_deg, "target_height": target_height})
        self.db.execute("UPDATE assets SET params_json=?, mesh_status='IMPORTED_PENDING_REVIEW' WHERE id=?",
                        (dumps(params), aid))
        self._invalidate_views(aid)
        return self.asset_view(self.get_asset(aid))

    def approve_mesh(self, aid: str) -> dict:
        a = self.get_asset(aid)
        if a["mesh_source"] != "imported" or a["mesh_status"] != "IMPORTED_PENDING_REVIEW":
            raise Conflict("no imported mesh is waiting for review")
        spec = self._spec_for(a)
        rev = proxy_spec.spec_hash_stable(spec)
        d = self.asset_dir(a) / "mesh"
        shutil.copyfile(d / "imported.glb", d / "canonical.glb")
        (d / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf8")
        old = a["model_revision"]
        self.db.execute("UPDATE assets SET model_revision=?, mesh_status='MESH_APPROVED' WHERE id=?", (rev, aid))
        if old and old != rev:
            self._invalidate_views(aid)
        return self.asset_view(self.get_asset(aid))

    def _invalidate_views(self, aid: str) -> None:
        for r in self.db.all("SELECT * FROM view_renders WHERE asset_id=?", (aid,)):
            if r["path"]:
                self.db.execute("UPDATE view_renders SET status='BLOCKED', qa_json=?, updated_at=? "
                                "WHERE asset_id=? AND view=?",
                                (dumps({"stale": True, "findings": [{"code": "STALE_MODEL_REVISION",
                                        "severity": "critical", "detail": "model changed; re-render"}]}),
                                 now(), aid, r["view"]))

    # ---------- render five ----------
    def default_policies(self, asset: dict) -> dict:
        proj = self.get_project(asset["project_id"])
        if proj["scene_mode"] == "REGULAR_IMAGE" and proj["base_mode"] == "NONE":
            return {v: "none" for v in VIEWS}          # regular images do not get a forced base
        if asset["category"] == "building":
            return {v: "building_foundation" for v in cv_camera.ORTHO_VIEWS} | {"perspective": "local_plinth"}
        return {v: "none" for v in VIEWS}

    def render_five(self, aid: str, opts: dict) -> str:
        asset = self.get_asset(aid)
        if asset["mesh_status"] not in APPROVED_MESH or not asset["model_revision"]:
            raise Conflict("No approved geometry. Build a proxy or import a mesh first.")
        key = hashlib.sha256(dumps([aid, asset["model_revision"], opts]).encode()).hexdigest()
        jid = self._new_job("five_render", aid, key)
        self._submit(jid, lambda ev: self._do_render_five(jid, aid, opts, ev))
        return jid

    def _do_render_five(self, jid: str, aid: str, opts: dict, ev: threading.Event) -> dict:
        a = self.get_asset(aid)
        blender = self._blender()
        spec = self._spec_for(a)
        rev = proxy_spec.spec_hash_stable(spec)
        d = self.asset_dir(a)
        (d / "views").mkdir(parents=True, exist_ok=True)
        (d / "qa").mkdir(parents=True, exist_ok=True)
        spec_path = d / "mesh" / "spec.json"
        full = spec
        policies = self.default_policies(a) | (opts.get("base_policies") or {})
        if any(p in ("scene_context", "full_master_base") for p in policies.values()):
            full = proxy_spec.with_master_base(spec)
        render_spec_path = d / "qa" / "render_spec.json"
        render_spec_path.write_text(json.dumps(full), encoding="utf8")
        res = opts["resolution"]
        only = [v for v in (opts.get("views") or VIEWS) if v in VIEWS]
        done, failed = 0, []
        pending = dumps({"stale": True, "findings": [{"code": "RENDER_PENDING", "severity": "critical",
                                                       "detail": "re-render in progress"}]})
        for v in only:
            self.db.execute("UPDATE view_renders SET status='BLOCKED', qa_json=?, updated_at=? "
                            "WHERE asset_id=? AND view=?", (pending, now(), aid, v))
        for i, v in enumerate(only):
            if ev.is_set():
                raise blender_adapter.Cancelled()
            self._update_job(jid, message=f"Rendering {v} ({i + 1}/{len(only)})", progress=i / len(only))
            out = d / "views" / f"{v}.png"
            meta_p = d / "qa" / f"{v}_meta.json"
            try:
                meta = blender_adapter.render_view(
                    blender, render_spec_path, out, meta_p, view=v, policy=policies[v],
                    engine=opts["engine"], res=res, samples=opts["samples"], bg=opts["background"],
                    quadrant=opts["perspective_quadrant"], elevation=opts["perspective_elevation_degrees"],
                    focal=opts["perspective_focal_mm"], cancel=ev)
                analysis = qa.analyze_png(out, res, opts["background"])
                findings = analysis["findings"] + qa.check_camera(v, meta["camera"])
                critical = [f for f in findings if f["severity"] == "critical"]
                status = "FAILED" if critical else ("NEEDS_REVIEW" if findings else "DRAFT")
                report = {"analysis": analysis, "findings": findings, "stale": False}
                meta["output_sha256"] = qa.sha256_file(out)
                self.db.execute(
                    "UPDATE view_renders SET status=?, path=?, sha256=?, model_revision=?, meta_json=?, "
                    "qa_json=?, updated_at=? WHERE asset_id=? AND view=?",
                    (status, f"views/{v}.png", meta["output_sha256"], rev, dumps(meta), dumps(report),
                     now(), aid, v))
                if critical:
                    failed.append(v)
                else:
                    done += 1
            except blender_adapter.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                failed.append(v)
                self.db.execute("UPDATE view_renders SET status='FAILED', qa_json=?, updated_at=? "
                                "WHERE asset_id=? AND view=?",
                                (dumps({"findings": [{"code": "RENDER_ERROR", "severity": "critical",
                                                      "detail": str(e)[:300]}]}), now(), aid, v))
        self._write_asset_manifest(aid, opts, policies)
        if failed:
            return {"_state": "FAILED", "message": f"{done}/{len(only)} views rendered; failed: {', '.join(failed)}",
                    "failed": failed}
        return {"message": f"{len(only)}/{len(only)} views rendered", "views": only}

    # ---------- review ----------
    def review_view(self, aid: str, view: str, status: str, note: str = "") -> dict:
        if view not in VIEWS:
            raise ValueError("unknown view")
        r = self.db.one("SELECT * FROM view_renders WHERE asset_id=? AND view=?", (aid, view))
        if not r or not r["path"]:
            raise Conflict("view has not been rendered")
        qa_json = json.loads(r["qa_json"]) if r["qa_json"] else {}
        crit = [f for f in qa_json.get("findings", []) if f["severity"] == "critical"]
        if status == "PASSED" and (crit or qa_json.get("stale")):
            raise Conflict("view has critical findings or is stale; re-render before approving")
        qa_json["review_note"] = note
        self.db.execute("UPDATE view_renders SET status=?, qa_json=?, updated_at=? WHERE asset_id=? AND view=?",
                        (status, dumps(qa_json), now(), aid, view))
        a = self.get_asset(aid)
        self._write_asset_manifest(aid, {}, self.default_policies(a))
        return self.asset_view(self.get_asset(aid))

    def view_file(self, aid: str, view: str) -> Path:
        a = self.get_asset(aid)
        r = self.db.one("SELECT path FROM view_renders WHERE asset_id=? AND view=?", (aid, view))
        if not r or not r["path"]:
            raise NotFound("view image")
        p = safe_join(self.asset_dir(a), *r["path"].split("/"))
        if not p.is_file():
            raise NotFound("view image")
        return p

    # ---------- manifests ----------
    def _validator(self, name: str) -> jsonschema.Draft202012Validator:
        schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf8"))
        return jsonschema.Draft202012Validator(schema)

    def _asset_manifest(self, a: dict, policies: dict) -> dict:
        view_rows = {r["view"]: r for r in self.db.all("SELECT * FROM view_renders WHERE asset_id=?", (a["id"],))}
        views = {}
        for v in VIEWS:
            r = view_rows[v]
            qa_json = json.loads(r["qa_json"]) if r["qa_json"] else {}
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            entry = {"path": r["path"], "status": r["status"],
                     "camera_type": "PERSP" if v == "perspective" else "ORTHO",
                     "inferred_regions": INFERRED[v] if a["category"] != "calibration" else [],
                     "sha256": r["sha256"], "base_policy": policies.get(v, "none"),
                     "model_revision": r["model_revision"], "stale": bool(qa_json.get("stale"))}
            if meta.get("camera"):
                entry["camera"] = meta["camera"]
            views[v] = entry
        statuses = [x["status"] for x in views.values()]
        if all(s == "PASSED" for s in statuses):
            qa_status = "PASSED"
        elif any(s == "FAILED" for s in statuses):
            qa_status = "FAILED"
        elif all(s == "BLOCKED" for s in statuses):
            qa_status = "BLOCKED"
        elif any(s == "NEEDS_REVIEW" for s in statuses):
            qa_status = "NEEDS_REVIEW"
        else:
            qa_status = "DRAFT"
        issues = sorted({f["code"] for r in view_rows.values() if r["qa_json"]
                         for f in json.loads(r["qa_json"]).get("findings", [])})
        mesh = {"path": "mesh/canonical.glb" if a["mesh_status"] in APPROVED_MESH else None,
                "status": a["mesh_status"], "revision": a["model_revision"]}
        return {
            "asset_id": a["code"], "name": a["name"], "category": a["category"], "parent_id": None,
            "count": 1, "source_visibility": "unseen_inferred", "confidence": 1.0,
            "visible_features": [], "inferred_features": ["procedural proxy geometry"],
            "mesh": mesh, "views": views, "qa": {"status": qa_status, "issues": issues},
            "model_revision": a["model_revision"] or "none",
            "default_base_policy": policies.get("front", "none"),
        }

    def _write_asset_manifest(self, aid: str, opts: dict, policies: dict) -> dict:
        a = self.get_asset(aid)
        manifest = self._asset_manifest(a, policies)
        self._validator("asset.schema.json").validate(manifest)
        d = self.asset_dir(a)
        d.mkdir(parents=True, exist_ok=True)
        (d / "asset.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
        return manifest

    # ---------- export ----------
    def export_project(self, pid: str) -> str:
        self.get_project(pid)
        jid = self._new_job("export", pid)
        self._submit(jid, lambda ev: self._do_export(pid, ev))
        return jid

    def _do_export(self, pid: str, ev: threading.Event) -> dict:
        proj = self.get_project(pid)
        pdir = self.project_dir(pid)
        out_dir = pdir / "exports"
        out_dir.mkdir(exist_ok=True)
        assets = [self.get_asset(a["id"]) for a in self.db.all("SELECT id FROM assets WHERE project_id=?", (pid,))]
        entries: dict[str, bytes] = {}
        report: dict[str, Any] = {"assets": {}, "problems": []}
        complete, any_incomplete = [], False
        for a in assets:
            pol = self.default_policies(a)
            manifest = self._write_asset_manifest(a["id"], {}, pol)
            d = self.asset_dir(a)
            prefix = f"assets/{a['code']}/"
            entries[prefix + "asset.json"] = json.dumps(manifest, indent=2).encode()
            a_ok = manifest["qa"]["status"] == "PASSED" and a["mesh_status"] in APPROVED_MESH
            for v in VIEWS:
                ve = manifest["views"][v]
                if ve["path"] and not ve["stale"]:
                    f = safe_join(d, *ve["path"].split("/"))
                    if f.is_file() and qa.sha256_file(f) == ve["sha256"]:
                        entries[prefix + ve["path"]] = f.read_bytes()
                        entries[prefix + f"qa/{v}_camera.json"] = json.dumps(self._view_job_json(a, v, ve), indent=2).encode()
                        continue
                a_ok = False
            for rel in ("mesh/canonical.glb", "mesh/spec.json"):
                f = d / rel
                if f.is_file():
                    entries[prefix + rel] = f.read_bytes()
            entries[prefix + "qa/report.json"] = json.dumps(
                {"asset": a["code"], "qa": manifest["qa"], "views": {v: manifest["views"][v]["status"] for v in VIEWS}},
                indent=2).encode()
            report["assets"][a["code"]] = "COMPLETE" if a_ok else "INCOMPLETE"
            (complete if a_ok else []).append(a["code"])
            any_incomplete |= not a_ok
            if a["fixture"] == "master_base":
                base = proxy_spec.master_base_spec()["base"]
                bm = {"base_id": "BASE-001", "mode": base["mode"], "width_m": base["width_m"],
                      "depth_m": base["depth_m"], "thickness_m": base["thickness_m"],
                      "materials": {"substrate": "dark_stone", "surface": "wet_asphalt_and_pavers"},
                      "layers": ["TERRAIN", "ROAD-001", "SIDEWALK-001"],
                      "source_inferred_regions": ["underside"]}
                self._validator("master_base.schema.json").validate(bm)
                entries["base/master_base_manifest.json"] = json.dumps(bm, indent=2).encode()
        scene = {"scene_id": "CV-" + proj["slug"].upper()[:24], "name": proj["name"],
                 "source": {"path": "source/original.jpg"}, "style_dna": {},
                 "assets": [a["code"] for a in assets],
                 "export_completeness": "COMPLETE" if assets and not any_incomplete else "PARTIAL"}
        self._validator("scene.schema.json").validate(scene)
        entries["inventory/scene_manifest.json"] = json.dumps(scene, indent=2).encode()
        entries["project.json"] = json.dumps({"id": proj["id"], "name": proj["name"], "version": 1}, indent=2).encode()
        status = scene["export_completeness"]
        hashes = {k: hashlib.sha256(v).hexdigest() for k, v in entries.items()}
        report.update({"status": status, "unique_model_count": len(assets), "instance_count": len(assets),
                       "sha256": hashes})
        entries["reports/export_validation.json"] = json.dumps(report, indent=2).encode()
        eid = "e_" + uuid.uuid4().hex[:10]
        final = out_dir / f"{proj['slug']}_CozyVerse_0.1.0_{eid}.zip"
        tmp = final.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(entries):
                self._check_archive_name(name)
                z.writestr(name, entries[name])
        self._verify_zip(tmp, hashes)
        tmp.replace(final)
        self.db.execute("INSERT INTO export_runs(id,project_id,status,path,report_json,created_at) VALUES(?,?,?,?,?,?)",
                        (eid, pid, status, str(final), dumps(report), now()))
        return {"message": f"Export {status}", "export_id": eid, "status": status, "path": str(final),
                "folder": str(out_dir)}

    def _view_job_json(self, a: dict, v: str, ve: dict) -> dict:
        cam = ve.get("camera") or {}
        job = {"asset_id": a["code"], "model_revision": ve["model_revision"] or "none", "view": v,
               "projection": "PERSP" if v == "perspective" else "ORTHO", "base_policy": ve["base_policy"],
               "background": "white", "output_path": f"views/{v}.png",
               "qa_status": {"PASSED": "pass", "FAILED": "fail", "NEEDS_REVIEW": "needs_review"}.get(ve["status"], "pending")}
        if v == "perspective":
            job.update({"perspective_quadrant": cam.get("quadrant") or "front_left",
                        "perspective_elevation_degrees": cam.get("elevation_deg") or 30,
                        "perspective_focal_mm": cam.get("lens_mm") or 50})
        self._validator("render_job.schema.json").validate(job)
        return {**job, "camera": cam}

    @staticmethod
    def _check_archive_name(name: str) -> None:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
            raise PathEscape(name)

    @staticmethod
    def _verify_zip(path: Path, hashes: dict[str, str]) -> None:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise RuntimeError("corrupt zip")
            names = z.namelist()
            if len(names) != len(set(names)):
                raise RuntimeError("duplicate archive entries")
            for n in names:
                Service._check_archive_name(n)
                if n in hashes and hashlib.sha256(z.read(n)).hexdigest() != hashes[n]:
                    raise RuntimeError(f"hash mismatch for {n}")
            missing = [k for k in hashes if k not in names]
            if missing:
                raise RuntimeError(f"missing entries: {missing[:3]}")

    def shutdown(self) -> None:
        for ev in self._cancel.values():
            ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.db.close()
