"""Style engine, provider adapters (mocked transports), consent/budget gates, key hygiene, SSRF guard."""
import base64
import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cozyverse_worker.app import create_app
from cozyverse_worker.config import Settings
from cozyverse_worker.providers import GoogleProvider, KieProvider, MockProvider, OpenAIProvider
from cozyverse_worker.safe_fetch import FetchRefused, fetch_bytes
from cozyverse_worker.secret_store import MemoryStore

TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-test-SECRET-1234567890"


def png_bytes(w=256, h=160, color=(10, 120, 200), exif=False) -> bytes:
    b = io.BytesIO()
    im = Image.new("RGB", (w, h), color)
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SECRET-CAMERA-MAKER"
        im.save(b, "PNG", exif=ex)
    else:
        im.save(b, "PNG")
    return b.getvalue()


class Recorder:
    """httpx.MockTransport handler that records requests and plays back queued responses."""

    def __init__(self, *responses):
        self.requests: list[httpx.Request] = []
        self.queue = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return r(request) if callable(r) else r

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self), timeout=5)


def make_registry(rec, sleeps=None, kie_fetch=None):
    sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    kw = {"client_factory": rec.client, "sleep": sleep}
    return {"mock": MockProvider(**kw), "google": GoogleProvider(**kw), "openai": OpenAIProvider(**kw),
            "kie": KieProvider(fetcher=kie_fetch or (lambda u: (png_bytes(), "image/png")), **kw)}


@pytest.fixture()
def env(tmp_path):
    store = MemoryStore()
    rec = Recorder(httpx.Response(500))
    sleeps: list[float] = []
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN), store, make_registry(rec, sleeps))
    with TestClient(app) as c:
        c.store, c.rec, c.sleeps, c.data_dir, c.app_ = store, rec, sleeps, tmp_path / "data", app
        yield c


def swap_registry(c, rec, **kw):
    c.rec = rec
    c.app_.state.service.providers.registry = make_registry(rec, c.sleeps, **kw)


def project(c, mode="DIORAMA"):
    return c.post("/projects", headers=H, json={"name": "P", "scene_mode": mode}).json()["id"]


def wait(c, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/jobs/{jid}", headers=H).json()
        if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def est_body(model="mock:scene-v1", **kw):
    return {"prompt": "rainy night market with neon signs", "model_id": model, "aspect_ratio": "16:9", **kw}


def run_generate(c, pid, body, approved=True):
    e = c.post(f"/projects/{pid}/generate/estimate", headers=H, json=body)
    assert e.status_code == 200, e.text
    g = c.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e.json()["estimate_id"], "approved": approved})
    assert g.status_code == 202, g.text
    return e.json(), wait(c, g.json()["job_id"])


# --------------------------------------------------------------------------- style engine
def test_catalog_breadth_and_no_artist_names(env):
    styles = env.get("/styles", headers=H).json()
    app_styles = [s for s in styles if s["owner"] == "app"]
    assert len(app_styles) >= 50
    assert {s["dimensionality"] for s in app_styles} == {"2D", "2.5D", "3D", "photo"}
    assert {"anime_cel", "watercolor", "claymation", "photoreal_cgi", "documentary", "pixel_art", "paper_cutout"} <= {s["id"] for s in app_styles}
    banned = ("ghibli", "pixar", "disney", "dreamworks", "miyazaki", "van gogh", "banksy", "rembrandt")
    for s in app_styles:
        assert s["traits"] and not any(b in s["traits"].lower() for b in banned), s["id"]


def test_user_recipes_versioning_blend_export_import(env):
    r = env.post("/styles", headers=H, json={"name": "Paper clay", "dimensionality": "2.5D", "palette": "warm ochres",
                                              "strength": 0.8, "blend": [{"style_id": "paper_cutout", "weight": 0.6},
                                                                         {"style_id": "claymation", "weight": 0.4}],
                                              "negative_constraints": ["text artifacts"]})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["version"] == 1 and r.json()["owner"] == "user"
    u = env.put(f"/styles/{sid}", headers=H, json={"name": "Paper clay v2", "dimensionality": "2.5D", "strength": 0.5})
    assert u.json()["version"] == 2 and u.json()["label"] == "Paper clay v2"
    assert env.post(f"/styles/{sid}/favorite", headers=H, json={"on": True}).status_code == 204
    assert next(s for s in env.get("/styles", headers=H).json() if s["id"] == sid)["favorite"] is True
    bad = env.post("/styles", headers=H, json={"name": "x", "blend": [{"style_id": "nope", "weight": 0.5}]})
    assert bad.status_code == 400
    assert env.post("/styles", headers=H, json={"name": "x", "strength": 3}).status_code == 422
    doc = env.get("/styles/export", headers=H).json()
    assert doc["format"] == "cozyverse-styles" and len(doc["recipes"]) == 1
    assert env.post("/styles/import", headers=H, json=doc).status_code == 201
    assert env.post("/styles/import", headers=H, json={"format": "other"}).status_code == 400
    assert env.delete(f"/styles/{sid}", headers=H).status_code == 204


def test_compile_keeps_user_words_and_shows_overlay_by_mode(env):
    body = {"style_id": "watercolor", "prompt": "A crowded street food market in the rain", "scene_mode": "DIORAMA"}
    d = env.post("/styles/compile", headers=H, json=body).json()
    assert d["prompt"].startswith("A crowded street food market in the rain")
    assert "watercolour" in d["prompt"] or "watercolor" in d["prompt"].lower()
    assert "miniature diorama" in d["prompt"]
    r = env.post("/styles/compile", headers=H, json={**body, "scene_mode": "REGULAR_IMAGE"}).json()
    assert "diorama" not in r["prompt"]                                        # style is independent of mode
    lo = env.post("/styles/compile", headers=H, json={**body, "strength": 0.1}).json()["prompt"]
    hi = env.post("/styles/compile", headers=H, json={**body, "strength": 0.95}).json()["prompt"]
    assert "subtle influence" in lo and "strongly" in hi


def test_negative_prompt_folded_with_warning_and_truncation_never_touches_user_text(env):
    b = {"style_id": "oil_painting", "prompt": "Harbour at dawn", "negative_prompt": "blurry, extra fingers",
         "scene_mode": "REGULAR_IMAGE", "model_id": "google:gemini-nano-banana-2.1"}
    d = env.post("/styles/compile", headers=H, json=b).json()
    assert "Avoid: blurry, extra fingers" in d["prompt"] and d["negative_prompt"] == ""
    assert any("negative-prompt" in w for w in d["warnings"])
    m = env.post("/styles/compile", headers=H, json={**b, "model_id": "mock:scene-v1"}).json()
    assert m["negative_prompt"] == "blurry, extra fingers" and "Avoid" not in m["prompt"]
    from cozyverse_worker.styles import StyleStore
    s = StyleStore(env.app_.state.service.db)
    out = s.compile("oil_painting", "X" * 60, "", "REGULAR_IMAGE", {"max_prompt_chars": 100})
    assert out.prompt.startswith("X" * 60) and len(out.prompt) <= 100 and out.truncated_fields == ["style"]


# --------------------------------------------------------------------------- mock generation (both modes)
@pytest.mark.parametrize("mode,expect_base", [("DIORAMA", True), ("REGULAR_IMAGE", False)])
def test_mock_generation_is_free_deterministic_and_records_provenance(env, mode, expect_base):
    pid = project(env, mode)
    est, job = run_generate(env, pid, est_body(n=2, seed=7, style_id="claymation"), approved=False)
    assert est["cost"]["free"] and est["requires_approval"] is False and est["outbound"]["leaves_device"] is False
    assert job["state"] == "SUCCEEDED", job
    ids = job["result"]["source_ids"]
    assert len(ids) == 2
    s0 = env.get(f"/sources/{ids[0]}", headers=H).json()
    m = s0["meta"]
    assert s0["origin"] == "GENERATED" and m["provider"] == "mock" and m["model_id"] == "mock:scene-v1"
    assert m["prompt"] == "rainy night market with neon signs" and "claymation" in m["effective_prompt"].lower()
    assert m["style"]["id"] == "claymation" and m["seed"] == 7 and m["scene_mode"] == mode
    raw = env.get(f"/sources/{ids[0]}/image", headers=H).content
    assert Image.open(io.BytesIO(raw)).size == (s0["width"], s0["height"])    # native dimensions, no fake upscaling
    cats = {o["category"] for o in m["layout"]}
    assert ("master_base" in cats) is expect_base
    assert env.get(f"/sources/{ids[0]}/image", headers=H).content != env.get(f"/sources/{ids[1]}/image", headers=H).content
    _, again = run_generate(env, pid, est_body(n=1, seed=7, style_id="claymation"))
    a2 = env.get(f"/sources/{again['result']['source_ids'][0]}/image", headers=H).content
    assert a2 == raw                                                          # same inputs, same pixels


def test_generation_never_auto_decomposes_or_approves(env):
    pid = project(env)
    _, job = run_generate(env, pid, est_body())
    assert env.get(f"/projects/{pid}", headers=H).json()["approved_source_id"] is None
    assert env.get(f"/projects/{pid}/assets", headers=H).json() == []


def test_ai_edit_with_and_without_mask_keeps_lineage(env):
    pid = project(env)
    _, job = run_generate(env, pid, est_body(seed=3))
    base = job["result"]["source_ids"][0]
    base_sha = env.get(f"/sources/{base}", headers=H).json()["sha256"]
    est, ej = run_generate(env, pid, {**est_body(), "prompt": "make the sky purple", "mode": "edit", "base_source_id": base})
    assert ej["state"] == "SUCCEEDED"
    child = env.get(f"/sources/{ej['result']['source_ids'][0]}", headers=H).json()
    assert child["origin"] == "EDITED" and child["parent_id"] == base
    assert [x["id"] for x in env.get(f"/sources/{child['id']}/lineage", headers=H).json()] == [child["id"], base]
    assert env.get(f"/sources/{base}", headers=H).json()["sha256"] == base_sha            # parent untouched
    mask = io.BytesIO()
    Image.new("L", (64, 64), 255).save(mask, "PNG")
    mb = base64.b64encode(mask.getvalue()).decode()
    e2 = env.post(f"/projects/{pid}/generate/estimate", headers=H,
                  json={**est_body(), "prompt": "add lanterns", "mode": "edit", "base_source_id": base, "mask_png_b64": mb})
    assert e2.status_code == 200 and e2.json()["outbound"]["mask_bytes"] > 0
    # the mask must be re-sent and identical at approval time; a different one is refused
    other = io.BytesIO()
    Image.new("L", (64, 64), 0).save(other, "PNG")
    bad = env.post(f"/projects/{pid}/generate", headers=H,
                   json={"estimate_id": e2.json()["estimate_id"], "approved": True,
                         "mask_png_b64": base64.b64encode(other.getvalue()).decode()})
    assert bad.status_code == 409


def test_local_edits_are_new_revisions(env):
    pid = project(env)
    _, job = run_generate(env, pid, est_body())
    sid = job["result"]["source_ids"][0]
    before = env.get(f"/sources/{sid}/image", headers=H).content
    for op in ({"op": "crop", "box": [0.1, 0.1, 0.5, 0.5]}, {"op": "text_overlay", "text": "MAMA'S KITCHEN", "x": 0.1, "y": 0.1},
               {"op": "flip"}):
        r = env.post(f"/projects/{pid}/sources/{sid}/edit-local", headers=H, json=op)
        assert r.status_code == 201, r.text
        assert r.json()["origin"] == "EDITED" and r.json()["parent_id"] == sid and r.json()["meta"]["local_edit"] == op["op"]
    assert env.get(f"/sources/{sid}/image", headers=H).content == before
    assert env.post(f"/projects/{pid}/sources/{sid}/edit-local", headers=H, json={"op": "crop", "box": [0.9, 0.9, 0.5, 0.5]}).status_code == 400


# --------------------------------------------------------------------------- providers: keys, gating, consent
def test_keys_never_returned_and_stored_only_in_vault(env):
    assert env.put("/providers/google/key", headers=H, json={"key": KEY}).status_code == 204
    assert env.put("/providers/google/key", headers=H, json={"key": "has space in it"}).status_code in (400, 422)
    assert env.put("/providers/mock/key", headers=H, json={"key": KEY}).status_code == 400
    listing = env.get("/providers", headers=H)
    assert KEY not in listing.text
    g = next(p for p in listing.json() if p["id"] == "google")
    assert g["configured"] is True and "key" not in g
    assert env.store.get("provider:google") == KEY
    for f in env.data_dir.rglob("*"):
        if f.is_file():
            assert KEY.encode() not in f.read_bytes(), f
    assert env.delete("/providers/google/key", headers=H).status_code == 204
    assert env.store.get("provider:google") is None


def test_without_keys_online_models_are_blocked_but_mock_works(env):
    pid = project(env)
    r = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=est_body("openai:gpt-image-2.5-flare"))
    assert r.status_code == 409 and "Connect your OpenAI" in r.json()["detail"]
    assert env.post(f"/projects/{pid}/generate/estimate", headers=H, json=est_body()).status_code == 200
    rec = env.get("/models/recommend?goal=quick_draft", headers=H).json()
    assert [m["model_id"] for m in rec] == ["mock:scene-v1"]               # unconfigured providers are not suggested


@pytest.mark.parametrize("model,patch,fragment", [
    ("google:gemini-3.1-flash-lite-image", {"resolution": "4K"}, "resolution 4K"),
    ("google:gemini-nano-banana-2.1", {"n": 4}, "between 1 and 1"),
    ("google:gemini-nano-banana-2.1", {"seed": 5}, "seed"),
    ("google:gemini-nano-banana-2.1", {"transparent": True}, "transparent"),
    ("openai:gpt-image-2", {"mode": "edit"}, "cannot edit"),
    ("openai:gpt-image-2.5-flare", {"aspect_ratio": "5:4"}, "aspect ratio"),
    ("kie:google/nano-banana-edit", {}, "disabled"),
    ("kie:google/nano-banana", {"n": 2}, "between 1 and 1"),
    ("google:gemini-2.5-flash-image", {}, "deprecated"),
])
def test_capability_gating_disables_unsupported_requests(env, model, patch, fragment):
    pid = project(env)
    pname = model.split(":")[0]
    env.put(f"/providers/{pname}/key", headers=H, json={"key": KEY})
    if patch.get("mode") == "edit":
        _, job = run_generate(env, pid, est_body())
        patch = {**patch, "base_source_id": job["result"]["source_ids"][0]}
    r = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=est_body(model, **patch))
    assert r.status_code == 409 and fragment in r.json()["detail"], r.text
    assert env.rec.requests == []                                            # nothing was sent to any provider


def test_mask_edit_blocked_on_models_without_mask_support(env):
    pid = project(env)
    env.put("/providers/google/key", headers=H, json={"key": KEY})
    _, job = run_generate(env, pid, est_body())
    mask = io.BytesIO(); Image.new("L", (64, 64), 255).save(mask, "PNG")
    r = env.post(f"/projects/{pid}/generate/estimate", headers=H, json={
        **est_body("google:gemini-nano-banana-2.1"), "mode": "edit", "base_source_id": job["result"]["source_ids"][0],
        "mask_png_b64": base64.b64encode(mask.getvalue()).decode()})
    assert r.status_code == 409 and "mask" in r.json()["detail"]


GEMINI_OK = lambda: httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
    {"type": "image", "data": base64.b64encode(png_bytes(320, 180)).decode()}]}]})


def test_paid_flow_requires_approval_shows_what_leaves_and_is_one_shot(env):
    pid = project(env)
    env.put("/providers/google/key", headers=H, json={"key": KEY})
    swap_registry(env, Recorder(GEMINI_OK()))
    src = env.post(f"/projects/{pid}/sources", headers=H, content=png_bytes(exif=True)).json()
    body = est_body("google:gemini-nano-banana-2.1", reference_ids=[src["id"]], resolution="2K")
    e = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=body).json()
    assert e["requires_approval"] is True and e["cost"]["unit"] == "unknown" and e["cost"]["usd_max"] is None
    assert "will bill your account" in e["cost"]["note"]
    assert e["outbound"]["leaves_device"] is True and e["outbound"]["references"][0]["source_id"] == src["id"]
    assert e["effective_prompt"].startswith("rainy night market")             # the exact prompt, never hidden
    no = env.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e["estimate_id"], "approved": False})
    assert no.status_code == 428 and env.rec.requests == []
    ok = env.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e["estimate_id"], "approved": True})
    job = wait(env, ok.json()["job_id"])
    assert job["state"] == "SUCCEEDED", job
    again = env.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e["estimate_id"], "approved": True})
    assert again.status_code == 409 and len(env.rec.requests) == 1             # no double charge on retry
    # the outbound reference is a re-encoded PNG without EXIF metadata
    sent = json.loads(env.rec.requests[0].content)
    ref = next(b for b in sent["input"] if b["type"] == "image")
    img = Image.open(io.BytesIO(base64.b64decode(ref["data"])))
    assert not img.getexif() and b"SECRET-CAMERA-MAKER" not in base64.b64decode(ref["data"])
    m = env.get(f"/sources/{job['result']['source_ids'][0]}", headers=H).json()["meta"]
    assert m["provider"] == "google" and m["model_slug"] == "gemini-nano-banana-2.1" and m["references"] == [src["id"]]
    assert next(x for x in env.get("/providers", headers=H).json() if x["id"] == "google")["models"][0]["status"] == "verified"


def test_estimate_expiry_and_budget_caps_and_local_only(env):
    pid = project(env)
    env.put("/providers/openai/key", headers=H, json={"key": KEY})
    swap_registry(env, Recorder(httpx.Response(200, json={"data": []})))
    body = est_body("openai:gpt-image-2.5-flare", aspect_ratio="16:9", n=2)
    env.put("/settings/prices", headers=H, json={"model_id": "openai:gpt-image-2.5-flare", "usd": 0.5})
    e = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=body).json()
    assert e["cost"]["usd_min"] == pytest.approx(1.0) and e["cost"]["exact"] is False
    env.app_.state.service.db.execute("UPDATE estimates SET expires_at=? WHERE id=?", (time.time() - 1, e["estimate_id"]))
    assert env.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e["estimate_id"], "approved": True}).status_code == 409
    env.put("/settings/budget", headers=H, json={"daily_cap_usd": 0.75})
    r = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=body)
    assert r.status_code == 409 and "daily spending cap" in r.json()["detail"]
    env.put("/settings/budget", headers=H, json={"local_only": True})
    r = env.post(f"/projects/{pid}/generate/estimate", headers=H, json={**body, "n": 1})
    assert r.status_code == 409 and "Local-only" in r.json()["detail"]
    assert env.post(f"/projects/{pid}/generate/estimate", headers=H, json=est_body()).status_code == 200   # mock still fine


# --------------------------------------------------------------------------- adapter contracts
def test_google_request_shape_errors_and_no_silent_fallback(env):
    pid = project(env)
    env.put("/providers/google/key", headers=H, json={"key": KEY})
    swap_registry(env, Recorder(httpx.Response(401, json={"error": {"message": "API key not valid"}})))
    e, job = run_generate(env, pid, est_body("google:gemini-3-pro-image"))
    assert job["state"] == "FAILED" and "rejected the API key" in job["message"] and KEY not in job["message"]
    assert len(env.rec.requests) == 1                                         # no retry, no other model, no other provider
    req = env.rec.requests[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["model"] == "gemini-3-pro-image" and body["response_format"]["aspect_ratio"] == "16:9"
    assert body["response_format"]["image_size"] == "1K" and body["response_format"]["type"] == "image"
    m = next(x for x in env.get("/providers", headers=H).json() if x["id"] == "google")["models"]
    assert next(x for x in m if x["slug"] == "gemini-3-pro-image")["status"] == "disabled"     # smoke failure is shown


def test_retry_with_backoff_on_429_then_success(env):
    pid = project(env)
    env.put("/providers/google/key", headers=H, json={"key": KEY})
    swap_registry(env, Recorder(httpx.Response(429), httpx.Response(503), GEMINI_OK()))
    _, job = run_generate(env, pid, est_body("google:gemini-nano-banana-2.1"))
    assert job["state"] == "SUCCEEDED" and len(env.rec.requests) == 3
    assert len(env.sleeps) == 2 and env.sleeps[0] < env.sleeps[1]            # exponential


def test_openai_generation_size_header_and_edit_multipart(env):
    pid = project(env)
    env.put("/providers/openai/key", headers=H, json={"key": KEY})
    img = base64.b64encode(png_bytes(512, 288)).decode()
    swap_registry(env, Recorder(httpx.Response(200, json={"data": [{"b64_json": img}, {"b64_json": img}]})))
    _, job = run_generate(env, pid, est_body("openai:gpt-image-2.5-sunburst", n=2, aspect_ratio="16:9"))
    assert job["state"] == "SUCCEEDED" and len(job["result"]["source_ids"]) == 2
    req = env.rec.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/images/generations" and req.headers["authorization"] == f"Bearer {KEY}"
    b = json.loads(req.content)
    assert b["model"] == "gpt-image-2.5-sunburst" and b["size"] == "1536x864" and b["n"] == 2 and b["output_format"] == "png"
    base = job["result"]["source_ids"][0]
    mask = io.BytesIO(); Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(mask, "PNG")
    swap_registry(env, Recorder(httpx.Response(200, json={"data": [{"b64_json": img}]})))
    _, ej = run_generate(env, pid, {**est_body("openai:gpt-image-2.5-sunburst"), "mode": "edit", "base_source_id": base,
                                    "mask_png_b64": base64.b64encode(mask.getvalue()).decode()})
    assert ej["state"] == "SUCCEEDED"
    er = env.rec.requests[0]
    assert str(er.url).endswith("/v1/images/edits") and er.headers["content-type"].startswith("multipart/form-data")
    assert b'name="image[]"' in er.content and b'name="mask"' in er.content and b'name="model"' in er.content


def test_kie_task_polling_ssrf_safe_download_and_cancel(env):
    pid = project(env)
    env.put("/providers/kie/key", headers=H, json={"key": KEY})
    created = httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "task_1"}})
    waiting = httpx.Response(200, json={"code": 200, "data": {"state": "generating"}})
    done = httpx.Response(200, json={"code": 200, "data": {"state": "success", "resultJson": json.dumps(
        {"resultUrls": ["https://cdn.example.com/out.png"]})}})
    fetched = []
    swap_registry(env, Recorder(created, waiting, waiting, done), kie_fetch=lambda u: (fetched.append(u) or png_bytes(), "image/png"))
    _, job = run_generate(env, pid, est_body("kie:google/nano-banana", aspect_ratio="3:2"))
    assert job["state"] == "SUCCEEDED", job
    first = env.rec.requests[0]
    assert str(first.url) == "https://api.kie.ai/api/v1/jobs/createTask" and first.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(first.content) == {"model": "google/nano-banana", "input": {"prompt": job_prompt(env, job), "output_format": "png", "aspect_ratio": "3:2"}}
    assert "taskId=task_1" in str(env.rec.requests[1].url) and fetched == ["https://cdn.example.com/out.png"]
    assert env.sleeps[:2] == [2, 3]                                          # polling backs off

    # Kie answers HTTP 200 even for errors: the body code decides
    swap_registry(env, Recorder(httpx.Response(200, json={"code": 401, "msg": "bad key"})))
    _, bad = run_generate(env, pid, est_body("kie:google/nano-banana"))
    assert bad["state"] == "FAILED" and "rejected the API key" in bad["message"]
    # a hostile result URL is refused by the SSRF guard (real fetcher, private address)
    swap_registry(env, Recorder(created, httpx.Response(200, json={"code": 200, "data": {"state": "success", "resultJson": json.dumps(
        {"resultUrls": ["https://metadata.internal/latest"]})}})))
    env.app_.state.service.providers.registry["kie"]._fetch = lambda u: fetch_bytes(u, resolver=lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    _, ssrf = run_generate(env, pid, est_body("kie:google/nano-banana"))
    assert ssrf["state"] == "FAILED" and "refused" in ssrf["message"]


def job_prompt(c, job):
    sid = job["result"]["source_ids"][0]
    return c.get(f"/sources/{sid}", headers=H).json()["meta"]["effective_prompt"]


def test_kie_cancel_marks_possible_orphan_and_never_claims_refund(env):
    pid = project(env)
    env.put("/providers/kie/key", headers=H, json={"key": KEY})
    created = httpx.Response(200, json={"code": 200, "data": {"taskId": "task_9"}})
    waiting = httpx.Response(200, json={"code": 200, "data": {"state": "generating"}})
    swap_registry(env, Recorder(created, waiting))
    e = env.post(f"/projects/{pid}/generate/estimate", headers=H, json=est_body("kie:google/nano-banana")).json()
    jid = env.post(f"/projects/{pid}/generate", headers=H, json={"estimate_id": e["estimate_id"], "approved": True}).json()["job_id"]
    t0 = time.time()
    while len(env.rec.requests) < 2 and time.time() - t0 < 5:
        time.sleep(0.01)
    env.delete(f"/jobs/{jid}", headers=H)
    # sleeping is stubbed, so release the poll loop by letting it observe the cancel flag
    j = wait(env, jid)
    assert j["state"] in ("CANCELLED", "FAILED")
    usage = env.get("/settings/usage", headers=H).json()
    assert usage and usage[0]["state"] in ("orphaned_possible", "failed", "completed")
    if j["state"] == "FAILED":
        assert "may still" in j["message"]


def test_health_check_marks_listed_models_available_not_verified(env):
    env.put("/providers/google/key", headers=H, json={"key": KEY})
    swap_registry(env, Recorder(httpx.Response(200, json={"models": [{"name": "models/gemini-nano-banana-2.1"}, {"name": "models/other"}]})))
    r = env.post("/providers/google/refresh", headers=H)
    assert r.status_code == 200
    st = {m["slug"]: m["status"] for m in r.json()["models"]}
    assert st["gemini-nano-banana-2.1"] == "available" and st["gemini-3-pro-image"] == "not_entitled"
    assert st["gemini-2.5-flash-image"] == "deprecated"
    assert env.rec.requests[0].headers["x-goog-api-key"] == KEY and "/models" in str(env.rec.requests[0].url)


# --------------------------------------------------------------------------- SSRF guard
def pub(ip):
    return lambda host, port, **k: [(2, 1, 6, "", (ip, port))]


@pytest.mark.parametrize("url", ["http://cdn.example.com/a.png", "https://1.2.3.4/a.png", "https://user:pw@cdn.example.com/a.png",
                                 "https://cdn.example.com:8443/a.png", "ftp://cdn.example.com/a.png", "file:///etc/passwd",
                                 "https://[::1]/a.png"])
def test_fetch_refuses_bad_urls(url):
    with pytest.raises(FetchRefused):
        fetch_bytes(url, resolver=pub("93.184.216.34"))


@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "172.16.5.4", "::1",
                                "fd00:ec2::254", "::ffff:10.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_fetch_refuses_non_public_resolution(ip):
    with pytest.raises(FetchRefused):
        fetch_bytes("https://rebind.example.com/a.png", resolver=pub(ip))


def test_fetch_pins_ip_refuses_redirects_size_and_type():
    seen = {}

    def ok(request):
        seen["url"], seen["host"] = str(request.url), request.headers["host"]
        return httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"})
    data, ctype = fetch_bytes("https://cdn.example.com/a/b.png?x=1", resolver=pub("93.184.216.34"), transport=httpx.MockTransport(ok))
    assert data == b"PNGDATA" and ctype == "image/png"
    assert seen["url"] == "https://93.184.216.34/a/b.png?x=1" and seen["host"] == "cdn.example.com"   # connection pinned to the checked IP
    with pytest.raises(FetchRefused):
        fetch_bytes("https://cdn.example.com/a", resolver=pub("93.184.216.34"),
                    transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://127.0.0.1/"})))
    with pytest.raises(FetchRefused):
        fetch_bytes("https://cdn.example.com/a", resolver=pub("93.184.216.34"), max_bytes=10,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 100, headers={"content-type": "image/png"})))
    with pytest.raises(FetchRefused):
        fetch_bytes("https://cdn.example.com/a", resolver=pub("93.184.216.34"),
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})))
