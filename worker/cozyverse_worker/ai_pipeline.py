"""Optional AI steps that feed the manual pipeline: detection proposals and image-to-3D meshes."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import uuid
from typing import Callable, Optional

from PIL import Image

from . import blender_adapter
from .creation import Blocked, CreationError, NeedsApproval, Providers, outbound_png
from .db import Database, dumps, now
from .mesh_providers import MeshProvider, MeshRequest
from .providers.base import ProviderError
from .sources import SourceStore
from .vision import (Detection, VisionProvider, iou, merge_detections, tile_regions, to_full)

TTL = 600
MAX_EDGE = 2048
POLL = (2, 3, 3, 5, 5, 8, 8, 10, 15)
MAX_WAIT_S = 900


def _consent(db: Database, project_id: str, kind: str, request: dict, rhash: str, out: dict) -> dict:
    eid = "est_" + uuid.uuid4().hex[:12]
    t = now()
    out = {**out, "estimate_id": eid, "expires_at": t + TTL}
    db.execute("INSERT INTO estimates(id,project_id,request_hash,request_json,estimate_json,created_at,expires_at) "
               "VALUES(?,?,?,?,?,?,?)", (eid, project_id, rhash, dumps({"kind": kind, **request}), dumps(out), t, t + TTL))
    return out


def _take(db: Database, project_id: str, estimate_id: str, kind: str, approved: bool) -> tuple[dict, dict]:
    row = db.one("SELECT * FROM estimates WHERE id=? AND project_id=?", (estimate_id, project_id))
    if not row or json.loads(row["request_json"]).get("kind") != kind:
        raise LookupError("estimate")
    if row["used"]:
        raise Blocked("This estimate was already used. Create a new one to run again.")
    if now() > row["expires_at"]:
        raise Blocked("This estimate expired. Create a new one.")
    est = json.loads(row["estimate_json"])
    if est["requires_approval"] and not approved:
        raise NeedsApproval("Approve the cost and the data that will be sent to run this.")
    return json.loads(row["request_json"]), est


class Detector:
    def __init__(self, db: Database, prov: Providers, registry: dict[str, VisionProvider], sources: SourceStore,
                 get_project: Callable, create_item: Callable, list_items: Callable, submit: Callable):
        self.db, self.prov, self.registry, self.sources = db, prov, registry, sources
        self.get_project, self.create_item, self.list_items, self.submit = get_project, create_item, list_items, submit

    def providers(self) -> list[dict]:
        return [{"id": p.id, "label": p.label, "needs_key": p.needs_key, "needs_model": p.needs_model,
                 "configured": (not p.needs_key) or bool(self.prov.secrets.get(self.prov.key_name("google"))),
                 "docs_url": p.docs_url} for p in self.registry.values()]

    def _vkey(self, p: VisionProvider) -> Optional[str]:
        return self.prov.secrets.get(self.prov.key_name("google")) if p.needs_key else None

    def estimate(self, pid: str, body: dict) -> dict:
        proj = self.get_project(pid)
        if not proj["approved_source_id"]:
            raise Blocked("Approve a source image before detecting objects.")
        p = self.registry.get(body["provider"])
        if not p:
            raise LookupError("detector")
        problems = []
        if p.needs_key and not self._vkey(p):
            problems.append("Connect your Google API key in Models and Costs first.")
        if p.needs_model and not body.get("model"):
            problems.append("Enter the Gemini model slug to use for detection.")
        if p.needs_key and self.prov.budget()["local_only"]:
            problems.append("Local-only mode is on. Turn it off in Models and Costs to use online providers.")
        tiles = int(body.get("tiles", 0))
        if tiles not in (0, 2, 3):
            raise CreationError("tiles must be 0, 2 or 3")
        calls = 1 + len(tile_regions(tiles))
        cost = p.estimate(self.prov.price("vision:" + p.id), calls)
        bud = self.prov.budget()
        if cost.usd_max is not None:
            if bud["daily_cap_usd"] is not None and bud["spent_day_usd"] + cost.usd_max > bud["daily_cap_usd"]:
                problems.append("This would pass your daily spending cap.")
        if problems:
            raise Blocked("; ".join(problems))
        png = self._image(proj["approved_source_id"])
        sha = hashlib.sha256(png).hexdigest()
        req = {"provider": p.id, "model": body.get("model"), "tiles": tiles, "image_sha": sha}
        return _consent(self.db, pid, "detect", req, sha, {
            "provider": p.id, "provider_label": p.label, "model": body.get("model"), "calls": calls, "cost": cost.public(),
            "requires_approval": not cost.free if hasattr(cost, "free") else p.needs_key,
            "outbound": {"leaves_device": p.needs_key, "image": {"source_id": proj["approved_source_id"], "sha256": sha,
                                                                   "bytes": len(png)}, "prompt_chars": 1100 if p.needs_key else 0},
            "warnings": ["Proposals are not facts. Every detected item starts as unconfirmed and needs your review."]})

    def _image(self, source_id: str) -> bytes:
        data = outbound_png(self.sources.path(source_id).read_bytes())
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) > MAX_EDGE:
                im = im.convert("RGB")
                im.thumbnail((MAX_EDGE, MAX_EDGE))
                b = io.BytesIO()
                im.save(b, "PNG")
                return b.getvalue()
        return data

    def run(self, pid: str, estimate_id: str, approved: bool) -> str:
        req, est = _take(self.db, pid, estimate_id, "detect", approved)
        proj = self.get_project(pid)
        p = self.registry[req["provider"]]
        src_id = proj["approved_source_id"]
        png = self._image(src_id)
        if hashlib.sha256(png).hexdigest() != req["image_sha"]:
            raise Blocked("The source changed after the estimate. Create a new estimate.")
        self.db.execute("UPDATE estimates SET used=1 WHERE id=?", (estimate_id,))
        layout = self.sources.get(src_id)["meta"].get("layout")

        def work(ev: threading.Event) -> dict:
            usage = "u_" + uuid.uuid4().hex[:10]
            self.db.execute("INSERT INTO provider_usage(id,provider,model_id,kind,est_usd,units,state,at) VALUES(?,?,?,?,?,?,?,?)",
                            (usage, p.id, req.get("model") or p.id, "detect", est["cost"]["usd_max"], est["cost"]["unit"], "started", now()))
            groups: list[list[Detection]] = []
            try:
                groups.append(p.detect(png, self._vkey(p), req.get("model"), None, {"layout": layout}))
                with Image.open(io.BytesIO(png)) as full:
                    W, H = full.size
                    for region in tile_regions(req["tiles"]):
                        if ev.is_set():
                            raise blender_adapter.Cancelled()
                        crop = full.crop((int(region[0] * W), int(region[1] * H), int((region[0] + region[2]) * W),
                                          int((region[1] + region[3]) * H)))
                        b = io.BytesIO()
                        crop.save(b, "PNG")
                        groups.append([to_full(d, region) for d in p.detect(b.getvalue(), self._vkey(p), req.get("model"), region, {"layout": layout})])
            except ProviderError as e:
                self.db.execute("UPDATE provider_usage SET state='failed' WHERE id=?", (usage,))
                raise RuntimeError(f"{p.label}: {e}") from e
            merged = merge_detections(groups)
            existing = [i for i in self.list_items(pid) if i["bbox"]]
            created, skipped, ids = [], 0, {}
            for d in merged:
                if any(e["category"] == d.category and iou(e["bbox"], d.bbox) >= 0.6 for e in existing):
                    skipped += 1
                    continue
                item = self.create_item(pid, {"name": d.name, "category": d.category, "bbox": d.bbox, "confidence": d.confidence,
                                              "visibility": d.visibility, "status": "unconfirmed", "source_revision_id": src_id,
                                              "notes": f"Proposed by {p.label}. Needs review."})
                created.append(item["id"])
                ids[d.name] = item["id"]
            self.db.execute("UPDATE provider_usage SET state='completed' WHERE id=?", (usage,))
            return {"message": f"{len(created)} proposals added, {skipped} duplicates skipped", "created": created,
                    "skipped_duplicates": skipped, "passes": len(groups)}

        return self.submit("detect", pid, work)


class MeshGen:
    def __init__(self, db: Database, prov: Providers, registry: dict[str, MeshProvider], get_asset: Callable,
                 crop_path: Callable, import_mesh: Callable, submit: Callable, set_provenance: Callable):
        self.db, self.prov, self.registry = db, prov, registry
        self.get_asset, self.crop_path, self.import_mesh, self.submit = get_asset, crop_path, import_mesh, submit
        self.set_provenance = set_provenance

    def providers(self) -> list[dict]:
        out = []
        for p in self.registry.values():
            ms = []
            for m in p.models():
                row = self.db.one("SELECT * FROM model_registry WHERE provider=? AND model_id=?", (p.id, m.id))
                if row and m.status != "disabled":
                    m.status, m.status_detail, m.last_checked = row["status"], row["status_detail"], row["last_checked"]
                ms.append({**m.public(), "price_usd": self.prov.price(m.id)})
            out.append({"id": p.id, "label": p.label, "needs_key": p.needs_key, "configured": self.prov.configured_any(p),
                        "docs_url": p.docs_url, "models": ms})
        return out

    def estimate(self, aid: str, body: dict) -> dict:
        a = self.get_asset(aid)
        p = self.registry.get(body["provider_id"])
        if not p:
            raise LookupError("provider")
        m = p.model(body["model_id"])
        crop = self.crop_path(a, "crop.png")
        problems = []
        if not crop.is_file():
            problems.append("This asset has no source crop. Draw or detect a box around it first.")
        if m.status in ("disabled", "deprecated"):
            problems.append(f"{m.label} is {m.status}: {m.status_detail}")
        if p.needs_key and not self.prov.secrets.get(self.prov.key_name(p.id)):
            problems.append(f"Connect your {p.label} API key in Models and Costs first.")
        if p.needs_key and self.prov.budget()["local_only"]:
            problems.append("Local-only mode is on. Turn it off in Models and Costs to use online providers.")
        opts = body.get("options") or {}
        req = None
        if not problems:
            png = outbound_png(crop.read_bytes())
            req = MeshRequest(png, opts)
        cost = p.estimate(req or MeshRequest(b"", opts), self.prov.price(m.id)) if req else None
        bud = self.prov.budget()
        if cost and cost.usd_max is not None:
            if bud["daily_cap_usd"] is not None and bud["spent_day_usd"] + cost.usd_max > bud["daily_cap_usd"]:
                problems.append("This would pass your daily spending cap.")
        if problems:
            raise Blocked("; ".join(problems))
        sha = hashlib.sha256(req.image_png).hexdigest()
        rh = hashlib.sha256(dumps({"a": aid, "m": m.id, "o": opts, "sha": sha}).encode()).hexdigest()
        return _consent(self.db, a["project_id"], "mesh", {"asset_id": aid, "provider_id": p.id, "model_id": m.id,
                                                            "options": opts, "image_sha": sha}, rh, {
            "provider": p.id, "provider_label": p.label, "model": {"id": m.id, "label": m.label, "status": m.status},
            "cost": cost.public(), "requires_approval": not cost.free,
            "outbound": {"leaves_device": p.needs_key, "image": {"asset": a["code"], "sha256": sha, "bytes": len(req.image_png)},
                         "options": opts},
            "warnings": ["The back and sides of the model are guessed by the provider. Review the mesh before approving it.",
                         "Cancelling does not refund credits once a task has started."]})

    def run(self, aid: str, estimate_id: str, approved: bool) -> str:
        a = self.get_asset(aid)
        req, est = _take(self.db, a["project_id"], estimate_id, "mesh", approved)
        if req["asset_id"] != aid:
            raise Blocked("This estimate belongs to another asset.")
        p = self.registry[req["provider_id"]]
        png = outbound_png(self.crop_path(a, "crop.png").read_bytes())
        if hashlib.sha256(png).hexdigest() != req["image_sha"]:
            raise Blocked("The source crop changed after the estimate. Create a new estimate.")
        self.db.execute("UPDATE estimates SET used=1 WHERE id=?", (estimate_id,))

        def work(ev: threading.Event) -> dict:
            key = self.prov.secrets.get(self.prov.key_name(p.id)) if p.needs_key else None
            usage = "u_" + uuid.uuid4().hex[:10]
            self.db.execute("INSERT INTO provider_usage(id,provider,model_id,kind,est_usd,units,state,at) VALUES(?,?,?,?,?,?,?,?)",
                            (usage, p.id, req["model_id"], "mesh", est["cost"]["usd_max"], est["cost"]["unit"], "started", now()))
            try:
                tid = p.submit(MeshRequest(png, req["options"]), key)
                self.db.execute("UPDATE provider_usage SET remote_task_id=? WHERE id=?", (tid, usage))
                waited, i = 0.0, 0
                while True:
                    if ev.is_set():
                        note = p.cancel(tid, key)
                        self.db.execute("UPDATE provider_usage SET state=? WHERE id=?",
                                        ("cancelled" if note.supported else "orphaned_possible", usage))
                        raise blender_adapter.Cancelled(note.note)
                    t = p.poll(tid, key)
                    if t.state == "succeeded":
                        break
                    if t.state in ("failed", "cancelled"):
                        raise ProviderError("generation_failed", t.error or f"the task {t.state}")
                    if waited > MAX_WAIT_S:
                        raise ProviderError("timeout", f"task {tid} did not finish in time and may still be billed")
                    delay = POLL[min(i, len(POLL) - 1)]
                    p._sleep(delay)
                    waited += delay
                    i += 1
                data = p.download(t)
                summary = p.validate_mesh(data)
            except ProviderError as e:
                self.db.execute("UPDATE provider_usage SET state=? WHERE id=?",
                                ("orphaned_possible" if e.code == "timeout" else "failed", usage))
                if e.code in ("auth", "model_not_found"):
                    self.prov.mark(req["model_id"], "disabled", f"Last attempt failed: {e}")
                raise RuntimeError(f"{p.label}: {e}") from e
            self.import_mesh(aid, data)                                   # lands as IMPORTED_PENDING_REVIEW
            self.set_provenance(aid, {"provider": p.id, "model": req["model_id"], "task_id": tid, "options": req["options"],
                                      "credits": t.credits, "image_sha": req["image_sha"], "estimate_id": estimate_id,
                                      "mesh_summary": summary, "at": now()})
            self.db.execute("UPDATE provider_usage SET state='completed', actual_usd=NULL WHERE id=?", (usage,))
            if p.needs_key:
                self.prov.mark(req["model_id"], "verified", "A task succeeded with this model.")
            return {"message": "Mesh downloaded. Review its orientation before approving.", "task_id": tid,
                    "credits": t.credits, "mesh_summary": summary}

        return self.submit("mesh_generate", aid, work)
