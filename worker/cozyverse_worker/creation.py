"""Create/edit images through providers: estimates, consent, budgets, provenance. No silent fallback."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import uuid
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from .db import Database, dumps, now
from .providers import GenerateRequest, ImageProvider, ProviderError
from .providers.base import ModelInfo
from .secret_store import SecretStore
from .sources import SourceStore, validate_image
from .styles import StyleStore

ESTIMATE_TTL_S = 600
MAX_MASK_BYTES = 700_000
MAX_REFS = 8


class CreationError(ValueError):
    pass


class NeedsApproval(RuntimeError):
    pass


class Blocked(RuntimeError):
    """Capability, key or budget problem. The message lists every problem."""


def outbound_png(data: bytes) -> bytes:
    """Re-encode to PNG so no EXIF or other metadata leaves the device."""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()


class Providers:
    def __init__(self, db: Database, secrets: SecretStore, registry: dict[str, ImageProvider],
                 extra: Optional[dict[str, Any]] = None):
        self.db, self.secrets, self.registry = db, secrets, registry
        self.extra = extra if extra is not None else {}      # e.g. mesh providers; they share the key vault

    def provider(self, pid: str) -> Any:
        if pid in self.registry:
            return self.registry[pid]
        if pid in self.extra:
            return self.extra[pid]
        raise LookupError("provider")

    def configured_any(self, p: Any) -> bool:
        return (not p.needs_key) or bool(self.secrets.get(self.key_name(p.id)))

    @staticmethod
    def key_name(pid: str) -> str:
        return f"provider:{pid}"

    def configured(self, pid: str) -> bool:
        return self.configured_any(self.provider(pid))

    def set_key(self, pid: str, key: str) -> None:
        p = self.provider(pid)
        if not p.needs_key:
            raise CreationError("this provider does not use a key")
        key = key.strip()
        if not (8 <= len(key) <= 512) or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise CreationError("that does not look like an API key")
        self.secrets.set(self.key_name(pid), key)

    def delete_key(self, pid: str) -> None:
        self.provider(pid)
        self.secrets.delete(self.key_name(pid))

    def key(self, pid: str) -> Optional[str]:
        return self.secrets.get(self.key_name(pid)) if self.provider(pid).needs_key else None

    # ----- registry -----
    def overlay(self, m: ModelInfo) -> ModelInfo:
        row = self.db.one("SELECT * FROM model_registry WHERE provider=? AND model_id=?", (m.id.split(":")[0], m.id))
        if row:
            m.status, m.status_detail, m.last_checked = row["status"], row["status_detail"], row["last_checked"]
        return m

    def mark(self, m_id: str, status: str, detail: str = "") -> None:
        pid = m_id.split(":")[0]
        self.db.execute("INSERT INTO model_registry(provider,model_id,status,status_detail,last_checked) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(provider,model_id) DO UPDATE SET status=excluded.status, "
                        "status_detail=excluded.status_detail, last_checked=excluded.last_checked",
                        (pid, m_id, status, detail, now()))

    def model(self, model_id: str) -> tuple[ImageProvider, ModelInfo]:
        pid = model_id.split(":")[0]
        p = self.provider(pid)
        return p, self.overlay(p.model(model_id))

    def listing(self) -> list[dict]:
        out = []
        for p in self.registry.values():
            out.append({"id": p.id, "label": p.label, "needs_key": p.needs_key, "configured": self.configured(p.id),
                        "docs_url": p.docs_url, "models": [self.overlay(m).public() for m in p.models()]})
        return out

    def refresh(self, pid: str) -> dict:
        p = self.provider(pid)
        res = p.health_check(self.key(pid))
        for m in p.models():
            cur = self.overlay(m)
            if m.slug in res.get("visible", []) and cur.status in ("unknown", "not_entitled"):
                self.mark(m.id, "available", "Listed by the provider for your key.")
            elif res.get("visible") and m.slug not in res["visible"] and cur.status == "unknown":
                self.mark(m.id, "not_entitled", "Not listed for your key.")
        return {**res, "models": [self.overlay(m).public() for m in p.models()]}

    # ----- prices and budget -----
    def price(self, model_id: str) -> Optional[float]:
        v = self.db.get_setting("price:" + model_id)
        return float(v) if v else None

    def set_price(self, model_id: str, usd: Optional[float]) -> None:
        if usd is None:
            self.db.execute("DELETE FROM app_settings WHERE k=?", ("price:" + model_id,))
        else:
            if not 0 <= usd <= 1000:
                raise CreationError("price must be between 0 and 1000")
            self.db.set_setting("price:" + model_id, repr(float(usd)))

    def budget(self) -> dict:
        def val(k):
            v = self.db.get_setting(k)
            return float(v) if v else None
        import time
        t = time.time()
        day = self.db.one("SELECT COALESCE(SUM(est_usd),0) AS s FROM provider_usage WHERE at>?", (t - 86400,))["s"]
        month = self.db.one("SELECT COALESCE(SUM(est_usd),0) AS s FROM provider_usage WHERE at>?", (t - 30 * 86400,))["s"]
        return {"daily_cap_usd": val("budget:daily"), "monthly_cap_usd": val("budget:monthly"),
                "spent_day_usd": day, "spent_month_usd": month, "local_only": self.db.get_setting("local_only") == "1"}

    def set_budget(self, daily: Optional[float], monthly: Optional[float], local_only: Optional[bool]) -> dict:
        for k, v in (("budget:daily", daily), ("budget:monthly", monthly)):
            if v is None:
                self.db.execute("DELETE FROM app_settings WHERE k=?", (k,))
            elif 0 <= v <= 100000:
                self.db.set_setting(k, repr(float(v)))
            else:
                raise CreationError("budget out of range")
        if local_only is not None:
            self.db.set_setting("local_only", "1" if local_only else "0")
        return self.budget()

    def usage(self, limit: int = 100) -> list[dict]:
        return self.db.all("SELECT * FROM provider_usage ORDER BY at DESC LIMIT ?", (limit,))

    # ----- routing suggestion (never auto-applied) -----
    def recommend(self, goal: str) -> list[dict]:
        pref = {"quick_draft": ["lite", "flare", "mock"], "quality": ["pro", "sunburst", "2.1"],
                "text": ["sunburst", "pro", "2.1"], "character": ["pro", "2.1", "sunburst"], "budget": ["lite", "flare", "mock"]}
        keys = pref.get(goal, pref["quality"])
        scored = []
        for p in self.registry.values():
            for m in p.models():
                m = self.overlay(m)
                if m.status in ("disabled", "deprecated", "not_entitled") or not m.caps.generate:
                    continue
                if p.needs_key and not self.configured(p.id):
                    continue
                score = max((len(keys) - keys.index(k) for k in keys if k in m.slug), default=0)
                score += 2 if m.status == "verified" else 1 if m.status == "available" else 0
                scored.append((score, m))
        scored.sort(key=lambda t: -t[0])
        return [{"model_id": m.id, "label": m.label, "status": m.status,
                 "reason": f"{goal.replace('_', ' ')} preference; status {m.status}"} for _, m in scored]


class Creator:
    def __init__(self, db: Database, providers: Providers, styles: StyleStore, sources: SourceStore,
                 submit: Callable[..., str], get_project: Callable[[str], dict]):
        self.db, self.prov, self.styles, self.sources = db, providers, styles, sources
        self._submit_job, self.get_project = submit, get_project

    # ----- estimate -----
    def _request(self, project_id: str, b: dict) -> tuple[GenerateRequest, dict]:
        proj = self.get_project(project_id)
        mode = b.get("mode", "generate")
        if mode not in ("generate", "edit"):
            raise CreationError("bad mode")
        provider, model = self.prov.model(b["model_id"])
        refs_ids = list(b.get("reference_ids") or [])
        if len(refs_ids) > MAX_REFS:
            raise CreationError(f"at most {MAX_REFS} reference images")
        base_id = b.get("base_source_id")
        if mode == "edit" and not base_id:
            raise CreationError("edit needs a base image")
        order = ([base_id] if base_id else []) + refs_ids
        images, meta = [], []
        for sid in order:
            src = self.sources.get(sid)
            if src["project_id"] != project_id:
                raise CreationError("reference belongs to another project")
            data = outbound_png(self.sources.path(sid).read_bytes())
            images.append(data)
            meta.append({"source_id": sid, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        mask = None
        if b.get("mask_png_b64"):
            try:
                mask = base64.b64decode(b["mask_png_b64"], validate=True)
            except ValueError as e:
                raise CreationError("mask is not valid base64") from e
            if len(mask) > MAX_MASK_BYTES:
                raise CreationError("mask is too large")
            validate_image(mask)
        compiled = self.styles.compile(b.get("style_id"), b.get("prompt", ""), b.get("negative_prompt", ""),
                                       proj["scene_mode"], {"max_prompt_chars": model.caps.max_prompt_chars,
                                                            "negative_prompt": model.caps.negative_prompt},
                                       b.get("style_strength"))
        req = GenerateRequest(prompt=compiled.prompt, negative_prompt=compiled.negative_prompt, model_id=model.id,
                              aspect_ratio=b.get("aspect_ratio", "1:1"), resolution=b.get("resolution", "1K"),
                              n=int(b.get("n", 1)), seed=b.get("seed"), references=images, mask=mask, mode=mode,
                              transparent=bool(b.get("transparent")), scene_mode=proj["scene_mode"])
        info = {"compiled": compiled.as_dict(), "refs": meta, "provider": provider.id, "model": model.public(),
                "user_prompt": b.get("prompt", ""), "style_id": b.get("style_id"), "base_source_id": base_id,
                "mask_sha256": hashlib.sha256(mask).hexdigest() if mask else None}
        return req, info

    def estimate(self, project_id: str, b: dict) -> dict:
        req, info = self._request(project_id, b)
        provider, model = self.prov.model(req.model_id)
        problems = provider.validate_request(req)
        if provider.needs_key and not self.prov.configured(provider.id):
            problems.append(f"Connect your {provider.label} API key in Models and Costs first.")
        bud = self.prov.budget()
        if bud["local_only"] and provider.needs_key:
            problems.append("Local-only mode is on. Turn it off in Models and Costs to use online providers.")
        cost = provider.estimate(req, self.prov.price(model.id))
        if cost.usd_max is not None:
            if bud["daily_cap_usd"] is not None and bud["spent_day_usd"] + cost.usd_max > bud["daily_cap_usd"]:
                problems.append("This would pass your daily spending cap.")
            if bud["monthly_cap_usd"] is not None and bud["spent_month_usd"] + cost.usd_max > bud["monthly_cap_usd"]:
                problems.append("This would pass your monthly spending cap.")
        if problems:
            raise Blocked("; ".join(problems))
        canon = dumps({"p": req.prompt, "np": req.negative_prompt, "m": req.model_id, "ar": req.aspect_ratio,
                       "res": req.resolution, "n": req.n, "seed": req.seed, "mode": req.mode,
                       "refs": [r["sha256"] for r in info["refs"]], "mask": info["mask_sha256"], "t": req.transparent})
        rhash = hashlib.sha256(canon.encode()).hexdigest()
        eid = "est_" + uuid.uuid4().hex[:12]
        t = now()
        stored = dict(b)                                       # the mask (<=700 KB) stays local; a re-sent mask is hash-checked
        out = {"estimate_id": eid, "expires_at": t + ESTIMATE_TTL_S, "provider": provider.id, "provider_label": provider.label,
               "model": {"id": model.id, "label": model.label, "slug": model.slug, "status": model.status,
                         "status_detail": model.status_detail},
               "effective_prompt": req.prompt, "negative_prompt": req.negative_prompt,
               "warnings": info["compiled"]["warnings"], "truncated_fields": info["compiled"]["truncated_fields"],
               "cost": cost.public(), "requires_approval": not cost.free,
               "outbound": {"leaves_device": provider.needs_key, "prompt_chars": len(req.prompt),
                            "references": info["refs"], "mask_bytes": len(req.mask) if req.mask else 0},
               "settings": {"aspect_ratio": req.aspect_ratio, "resolution": req.resolution, "n": req.n, "seed": req.seed,
                            "mode": req.mode}}
        self.db.execute("INSERT INTO estimates(id,project_id,request_hash,request_json,estimate_json,created_at,expires_at) "
                        "VALUES(?,?,?,?,?,?,?)", (eid, project_id, rhash, dumps(stored), dumps(out), t, t + ESTIMATE_TTL_S))
        return out

    # ----- generate -----
    def generate(self, project_id: str, estimate_id: str, approved: bool, mask_png_b64: Optional[str] = None) -> str:
        row = self.db.one("SELECT * FROM estimates WHERE id=? AND project_id=?", (estimate_id, project_id))
        if not row:
            raise LookupError("estimate")
        if row["used"]:
            raise Blocked("This estimate was already used. Create a new estimate to run again.")
        if now() > row["expires_at"]:
            raise Blocked("This estimate expired. Create a new one.")
        est = json.loads(row["estimate_json"])
        if est["requires_approval"] and not approved:
            raise NeedsApproval("Approve the cost and the data that will be sent to run this.")
        body = json.loads(row["request_json"])
        if mask_png_b64:
            body["mask_png_b64"] = mask_png_b64
        req, info = self._request(project_id, body)
        # the request must be byte-for-byte what was estimated
        canon = dumps({"p": req.prompt, "np": req.negative_prompt, "m": req.model_id, "ar": req.aspect_ratio,
                       "res": req.resolution, "n": req.n, "seed": req.seed, "mode": req.mode,
                       "refs": [r["sha256"] for r in info["refs"]], "mask": info["mask_sha256"], "t": req.transparent})
        if hashlib.sha256(canon.encode()).hexdigest() != row["request_hash"]:
            raise Blocked("The request changed after it was estimated. Create a new estimate.")
        provider, model = self.prov.model(req.model_id)
        self.db.execute("UPDATE estimates SET used=1 WHERE id=?", (estimate_id,))   # one run per approval: no double charge

        def work(ev: threading.Event) -> dict:
            usage_id = "u_" + uuid.uuid4().hex[:10]
            est_usd = est["cost"]["usd_max"]
            self.db.execute("INSERT INTO provider_usage(id,provider,model_id,kind,est_usd,units,state,at) VALUES(?,?,?,?,?,?,?,?)",
                            (usage_id, provider.id, model.id, "image_" + req.mode, est_usd, est["cost"]["unit"], "started", now()))
            try:
                results = provider.generate(req, self.prov.key(provider.id), ev)
            except ProviderError as e:
                state = "orphaned_possible" if e.code in ("cancelled", "timeout") else "failed"
                self.db.execute("UPDATE provider_usage SET state=? WHERE id=?", (state, usage_id))
                if e.code in ("auth", "model_not_found"):
                    self.prov.mark(model.id, "disabled", f"Last attempt failed: {e}")
                raise RuntimeError(f"{provider.label}: {e}") from e
            sources = []
            for res in results:
                lineage_meta = {
                    "provider": provider.id, "model_id": model.id, "model_slug": model.slug,
                    "prompt": info["user_prompt"], "effective_prompt": req.prompt, "negative_prompt": req.negative_prompt,
                    "style": self.styles.snapshot(info["style_id"]) if info["style_id"] else None,
                    "aspect_ratio": req.aspect_ratio, "resolution": req.resolution, "seed": res.meta.get("seed", req.seed),
                    "scene_mode": req.scene_mode, "estimate_id": estimate_id, "est_usd": est_usd,
                    "references": [r["source_id"] for r in info["refs"]], "mask_sha256": info["mask_sha256"],
                    "provider_meta": {k: v for k, v in res.meta.items() if k not in ("layout",)},
                    "warnings": est["warnings"],
                }
                if "layout" in res.meta and req.mode == "generate":
                    lineage_meta["layout"] = res.meta["layout"]
                elif info["base_source_id"] and req.mode == "edit" and req.mask is None:
                    parent = self.sources.get(info["base_source_id"])["meta"]
                    if "layout" in parent:
                        lineage_meta["layout"] = parent["layout"]
                origin = "EDITED" if req.mode == "edit" else "GENERATED"
                sources.append(self.sources.add(project_id, res.data, origin, info["base_source_id"], lineage_meta)["id"])
            self.db.execute("UPDATE provider_usage SET state='completed' WHERE id=?", (usage_id,))
            if provider.needs_key:
                self.prov.mark(model.id, "verified", "A generation succeeded with this model.")
            return {"message": f"{len(sources)} image(s) created", "source_ids": sources}

        return self._submit_job("image_generate", project_id, work)

    # ----- free local edits (non-destructive: always a new revision) -----
    def edit_local(self, project_id: str, source_id: str, op: dict) -> dict:
        src = self.sources.get(source_id)
        if src["project_id"] != project_id:
            raise CreationError("source belongs to another project")
        with Image.open(self.sources.path(source_id)) as im:
            im = im.convert("RGB")
            W, H = im.size
            kind = op.get("op")
            if kind == "crop":
                x, y, w, h = (float(v) for v in op["box"])
                if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
                    raise CreationError("crop box must be normalized inside the image")
                im = im.crop((int(x * W), int(y * H), max(int(x * W) + 32, int((x + w) * W)), max(int(y * H) + 32, int((y + h) * H))))
                params = {"box": [x, y, w, h]}
            elif kind == "text_overlay":
                text = str(op.get("text", ""))[:80]
                if not text:
                    raise CreationError("text is required")
                x, y = float(op["x"]), float(op["y"])
                size = int(op.get("size", max(12, H // 20)))
                color = str(op.get("color", "#FFFFFF"))
                if not (len(color) == 7 and color[0] == "#") or not 8 <= size <= 400 or not (0 <= x <= 1 and 0 <= y <= 1):
                    raise CreationError("bad text overlay settings")
                d = ImageDraw.Draw(im)
                try:
                    font = ImageFont.load_default(size=size)
                except TypeError:
                    font = ImageFont.load_default()
                d.text((x * W, y * H), text, fill=color, font=font)
                params = {"text": text, "x": x, "y": y, "size": size, "color": color}
            elif kind == "flip":
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
                params = {}
            else:
                raise CreationError("unknown edit")
            b = io.BytesIO()
            im.save(b, "PNG")
        meta = {"local_edit": kind, "params": params, "provider": None}
        return self.sources.add(project_id, b.getvalue(), "EDITED", source_id, meta)
