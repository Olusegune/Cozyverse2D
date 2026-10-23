"""Proxy wizard, GLB import + review gate, per-view re-render, regular-image base rule (real Blender)."""
import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import proxy_spec as ps
from conftest import write_spec
from cozyverse_worker import blender_adapter, qa
from cozyverse_worker.app import create_app
from cozyverse_worker.config import Settings
from cozyverse_worker.mesh_import import MeshRejected, validate_glb

pytestmark = pytest.mark.blender
TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
FAST = {"resolution": 256, "engine": "WORKBENCH"}
VIEWS = ["front", "back", "left", "right", "perspective"]
SAMPLE = Path(__file__).parent / "fixtures" / "original_rain_market.jpg"


@pytest.fixture()
def client(blender, tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN))
    with TestClient(app) as c:
        c.data_dir = tmp_path / "data"
        yield c


def wait(client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/jobs/{job_id}", headers=H).json()
        if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def assets_of(client, pid):
    return {a["code"]: a for a in client.get(f"/projects/{pid}/assets", headers=H).json()}


def project_with_source(client, **kw):
    pid = client.post("/projects", headers=H, json={"name": "P", **kw}).json()["id"]
    src = client.post(f"/projects/{pid}/sources", headers=H, content=SAMPLE.read_bytes()).json()
    client.post(f"/projects/{pid}/sources/{src['id']}/approve", headers=H)
    return pid


def add_item(client, pid, name, category, **kw):
    r = client.post(f"/projects/{pid}/inventory", headers=H,
                    json={"name": name, "category": category, "bbox": [0.36, 0.09, 0.14, 0.38], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def front_pixels(client, aid):
    return np.asarray(Image.open(__import__("io").BytesIO(client.get(f"/assets/{aid}/views/front/image", headers=H).content)).convert("RGB"))


# ------------------------------------------------------------------ pure validation
def make_glb(doc: dict, bin_chunk: bytes = b"") -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js
    if bin_chunk:
        bin_chunk += b"\0" * (-len(bin_chunk) % 4)
        body += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


GOOD = {"asset": {"version": "2.0"}, "meshes": [{"primitives": []}], "nodes": [{"mesh": 0}]}


def test_glb_validator_accepts_and_rejects():
    assert validate_glb(make_glb(GOOD))["meshes"] == 1
    bad = {
        "not glb": b"definitely not a glb file at all, just text",
        "truncated": make_glb(GOOD)[:20],
        "external buffer": make_glb({**GOOD, "buffers": [{"uri": "file:///C:/secrets.bin", "byteLength": 1}]}),
        "http image": make_glb({**GOOD, "images": [{"uri": "http://evil.example/x.png"}]}),
        "unknown extension": make_glb({**GOOD, "extensionsRequired": ["VENDOR_run_code"]}),
        "no meshes": make_glb({"asset": {"version": "2.0"}}),
        "wrong version": make_glb({**GOOD, "asset": {"version": "1.0"}}),
    }
    for name, data in bad.items():
        with pytest.raises(MeshRejected):
            validate_glb(data)
    ok = make_glb({**GOOD, "images": [{"uri": "data:image/png;base64,AAAA"}]})
    assert validate_glb(ok)["meshes"] == 1
    tampered = bytearray(make_glb(GOOD))
    tampered[8:12] = struct.pack("<I", len(tampered) + 5)                 # length header lies
    with pytest.raises(MeshRejected):
        validate_glb(bytes(tampered))


def test_parameter_validation():
    for cat, params in [("building", {"width": -1}), ("building", {"width": 9999}), ("building", {"roof": "dome"}),
                        ("building", {"color": "red"}), ("prop", {"unknown": 1}), ("building", {"tank": "yes"}),
                        ("vehicle", {"height": True})]:
        with pytest.raises(ValueError):
            ps.clean_params(cat, params)
    assert ps.clean_params("building", {"width": 6})["width"] == 6.0
    for p in (ps.clean_base_params, ):
        with pytest.raises(ValueError):
            p({"width": 0.1})
        with pytest.raises(ValueError):
            p({"material": "gold"})
        with pytest.raises(ValueError):
            p({"border": 6.0, "width": 10, "depth": 10})


# ------------------------------------------------------------------ proxy wizard
def test_every_category_renders_without_cropping(blender, work):
    for cat in ps.DEFAULTS:
        spec = write_spec(work / f"p_{cat}.json", ps.parametric_spec("TST-001", cat, cat))
        out = work / f"p_{cat}.png"
        meta = blender_adapter.render_view(blender, spec, out, work / f"p_{cat}.json.meta", view="front",
                                           policy="none", engine="WORKBENCH", res=192)
        an = qa.analyze_png(out, 192)
        assert an["findings"] == [], (cat, an["findings"])
        assert meta["included_objects"], cat


def test_wizard_parameters_change_the_model(client):
    pid = project_with_source(client)
    a = add_item(client, pid, "Shop", "building")
    j = wait(client, client.post(f"/assets/{a['id']}/build-proxy", headers=H).json()["job_id"])
    assert j["state"] == "SUCCEEDED", j
    rev_flat = j["result"]["model_revision"]
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["front"]}).json()["job_id"])["state"] == "SUCCEEDED"
    flat = front_pixels(client, a["id"])
    j = wait(client, client.post(f"/assets/{a['id']}/build-proxy", headers=H,
                                 json={"params": {"roof": "gable", "tank": True, "width": 7.0, "color": "#3366AA"}}).json()["job_id"])
    assert j["result"]["model_revision"] != rev_flat
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["front"]}).json()["job_id"])["state"] == "SUCCEEDED"
    gable = front_pixels(client, a["id"])
    assert not np.array_equal(flat, gable)
    r = client.post(f"/assets/{a['id']}/build-proxy", headers=H, json={"params": {"width": -3}})
    assert r.status_code in (202, 400)
    if r.status_code == 202:                                              # invalid params fail the job, never a render
        assert wait(client, r.json()["job_id"])["state"] == "FAILED"


# ------------------------------------------------------------------ mesh import
def build_canonical_glb(client):
    pid = client.post("/projects", headers=H, json={"name": "Src", "fixtures": ["bar"]}).json()["id"]
    bar = assets_of(client, pid)["BLD-003"]
    assert wait(client, client.post(f"/assets/{bar['id']}/build-proxy", headers=H).json()["job_id"])["state"] == "SUCCEEDED"
    glb = next((client.data_dir / "projects" / pid).rglob("canonical.glb")).read_bytes()
    assert glb[:4] == b"glTF"
    return glb


def test_mesh_import_requires_review_then_renders(client):
    glb = build_canonical_glb(client)
    pid = project_with_source(client)
    a = add_item(client, pid, "Imported bar", "prop")
    r = client.post(f"/assets/{a['id']}/mesh", headers=H, content=b"not a glb")
    assert r.status_code == 400
    r = client.post(f"/assets/{a['id']}/mesh", headers=H, content=glb)
    assert r.status_code == 201 and r.json()["mesh_status"] == "IMPORTED_PENDING_REVIEW"
    blocked = client.post(f"/assets/{a['id']}/render-five", headers=H, json=FAST)
    assert blocked.status_code == 409                                   # orientation/pivot must be reviewed first

    ok = client.post(f"/assets/{a['id']}/mesh/approve", headers=H)
    assert ok.status_code == 200 and ok.json()["mesh_status"] == "MESH_APPROVED"
    rev1 = ok.json()["model_revision"]
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["front", "right"]}).json()["job_id"])["state"] == "SUCCEEDED"
    front1 = front_pixels(client, a["id"])
    meta = json.loads(next((client.data_dir / "projects" / pid).rglob("front_meta.json")).read_text())
    assert meta["camera"]["type"] == "ORTHO" and meta["included_classes"] == ["asset"]
    z0 = meta["bounds"]["min"][2]
    assert abs(z0) < 1e-3                                              # pivot normalised to the ground

    # re-orienting invalidates the render and needs another approval
    r = client.put(f"/assets/{a['id']}/mesh/orientation", headers=H, json={"yaw_deg": 90, "target_height": 2.0})
    assert r.status_code == 200 and r.json()["mesh_status"] == "IMPORTED_PENDING_REVIEW"
    assert all(v["stale"] or v["status"] == "BLOCKED" for v in r.json()["views"].values() if v["path"])
    ok = client.post(f"/assets/{a['id']}/mesh/approve", headers=H).json()
    assert ok["model_revision"] != rev1
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["front"]}).json()["job_id"])["state"] == "SUCCEEDED"
    front2 = front_pixels(client, a["id"])
    meta2 = json.loads(next((client.data_dir / "projects" / pid).rglob("front_meta.json")).read_text())
    height = meta2["bounds"]["max"][2] - meta2["bounds"]["min"][2]
    assert height == pytest.approx(2.0, rel=0.02)                      # scaled to the requested height
    assert not np.array_equal(front1, front2)                          # yaw 90 shows a different face


# ------------------------------------------------------------------ per-view re-render
def test_rerender_one_view_leaves_the_rest_alone(client):
    pid = client.post("/projects", headers=H, json={"name": "P", "fixtures": ["bar"]}).json()["id"]
    aid = assets_of(client, pid)["BLD-003"]["id"]
    wait(client, client.post(f"/assets/{aid}/build-proxy", headers=H).json()["job_id"])
    assert wait(client, client.post(f"/assets/{aid}/render-five", headers=H, json=FAST).json()["job_id"])["state"] == "SUCCEEDED"
    before = assets_of(client, pid)["BLD-003"]["views"]
    for v in VIEWS:
        client.post(f"/assets/{aid}/views/{v}/review", headers=H, json={"status": "PASSED"})
    j = wait(client, client.post(f"/assets/{aid}/render-five", headers=H,
                                 json={**FAST, "views": ["left"], "engine": "WORKBENCH"}).json()["job_id"])
    assert j["state"] == "SUCCEEDED" and j["message"] == "1/1 views rendered"
    after = assets_of(client, pid)["BLD-003"]["views"]
    assert after["left"]["status"] in ("DRAFT", "NEEDS_REVIEW")           # re-rendered view needs review again
    for v in ("front", "back", "right", "perspective"):
        assert after[v]["status"] == "PASSED" and after[v]["sha256"] == before[v]["sha256"], v


# ------------------------------------------------------------------ regular image: no forced base
def test_regular_image_renders_without_any_base(client):
    pid = project_with_source(client, scene_mode="REGULAR_IMAGE", output_goal="3D_PROXY")
    a = add_item(client, pid, "Shop", "building")
    wait(client, client.post(f"/assets/{a['id']}/build-proxy", headers=H).json()["job_id"])
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["front", "perspective"]}).json()["job_id"])["state"] == "SUCCEEDED"
    for v in ("front", "perspective"):
        meta = json.loads(next((client.data_dir / "projects" / pid).rglob(f"{v}_meta.json")).read_text())
        assert meta["base_policy"] == "none" and meta["included_classes"] == ["asset"], v
    # opting in to a base is explicit
    client.patch(f"/projects/{pid}", headers=H, json={"base_mode": "LOCAL_PLINTH", "scene_mode": "DIORAMA"})
    wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json={**FAST, "views": ["perspective"]}).json()["job_id"])
    meta = json.loads(next((client.data_dir / "projects" / pid).rglob("perspective_meta.json")).read_text())
    assert meta["base_policy"] == "local_plinth"
