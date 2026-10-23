"""Export: provenance, 2D layers, unverified 2D, hierarchy counts, overrides, base designer outputs, contact sheet."""
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cozyverse_worker.app import create_app
from cozyverse_worker.config import SCHEMA_DIR, Settings
from cozyverse_worker.secret_store import MemoryStore

TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
SAMPLE = Path(__file__).parent / "fixtures" / "original_rain_market.jpg"
VIEWS = ["front", "back", "left", "right", "perspective"]
FAST = {"resolution": 256, "engine": "WORKBENCH"}


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN), MemoryStore())
    with TestClient(app) as c:
        c.data_dir = tmp_path / "data"
        yield c


def wait(c, jid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/jobs/{jid}", headers=H).json()
        if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
            return j
        time.sleep(0.1)
    raise TimeoutError


def setup(c, **kw):
    pid = c.post("/projects", headers=H, json={"name": "Rainy Night Market", **kw}).json()["id"]
    src = c.post(f"/projects/{pid}/sources", headers=H, content=SAMPLE.read_bytes()).json()
    c.post(f"/projects/{pid}/sources/{src['id']}/approve", headers=H)
    return pid, src


def add(c, pid, name, cat, box, **kw):
    r = c.post(f"/projects/{pid}/inventory", headers=H, json={"name": name, "category": cat, "bbox": box, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def export(c, pid, **body):
    r = c.post(f"/projects/{pid}/export", headers=H, json=body)
    assert r.status_code == 202, r.text
    return wait(c, r.json()["job_id"])


def test_regular_image_two_d_export_is_complete_without_any_3d(client):
    pid, src = setup(client, scene_mode="REGULAR_IMAGE", output_goal="2D_LAYER_EXPORT")
    poly = [[0.40, 0.45], [0.50, 0.12], [0.55, 0.45]]
    a = add(client, pid, "Neon BAR", "building", [0.36, 0.09, 0.19, 0.38], polygon=poly, goal="2D_LAYER_EXPORT")
    b = add(client, pid, "Taxi", "vehicle", [0.62, 0.55, 0.12, 0.08], goal="REFERENCE_ONLY", count=2)
    j = export(client, pid)
    assert j["state"] == "SUCCEEDED" and j["result"]["status"] == "COMPLETE", j
    z = zipfile.ZipFile(j["result"]["path"])
    names = set(z.namelist())
    raw = SAMPLE.read_bytes()
    assert z.read("source/original.jpg") == raw and hashlib.sha256(raw).hexdigest() == src["sha256"]
    assert "source/lineage.json" in names and "layers/manifest.json" in names
    layer = Image.open(io.BytesIO(z.read(f"layers/{a['code']}.png")))
    assert layer.mode == "RGBA" and layer.getpixel((0, 0))[3] == 0              # polygon mask became real transparency
    assert f"reference/{b['code']}.png" in names
    man = json.loads(z.read("layers/manifest.json"))["layers"]
    assert {m["asset_id"]: m["unverified_2d"] for m in man} == {a["code"]: False, b["code"]: True}
    am = json.loads(z.read(f"assets/{b['code']}/asset.json"))
    assert am["output_class"] == "UNVERIFIED_2D" and am["views"]["front"]["status"] == "BLOCKED" and am["views"]["front"]["path"] is None
    assert not any(n.startswith(f"assets/{b['code']}/views/") for n in names)   # no fake angle images
    sch = json.loads((SCHEMA_DIR / "scene.schema.json").read_text())
    scene = json.loads(z.read("inventory/scene_manifest.json"))
    jsonschema.Draft202012Validator(sch).validate(scene)
    assert scene["source"]["sha256"] == src["sha256"] and scene["export_completeness"] == "COMPLETE"
    rep = json.loads(z.read("reports/export_validation.json"))
    assert rep["unique_model_count"] == 2 and rep["instance_count"] == 3
    proj = json.loads(z.read("project.json"))
    assert proj["scene_mode"] == "REGULAR_IMAGE" and proj["approved_source_revision_id"] == src["id"]


def test_three_d_goal_without_model_makes_export_partial_with_reasons(client):
    pid, _ = setup(client)
    add(client, pid, "Shop", "building", [0.1, 0.1, 0.2, 0.3])                    # default goal: 3D_RECONSTRUCTION
    add(client, pid, "Crate", "prop", [0.5, 0.6, 0.05, 0.05], goal="2D_LAYER_EXPORT")
    j = export(client, pid)
    assert j["result"]["status"] == "PARTIAL"
    reasons = j["result"]["incomplete"]["BLD-001"]
    assert any("no approved 3D model" in r for r in reasons) and any("front view missing" in r for r in reasons)
    assert "PRP-001" not in j["result"]["incomplete"]


def test_hierarchy_is_exported(client):
    pid, _ = setup(client, scene_mode="REGULAR_IMAGE", output_goal="2D_LAYER_EXPORT")
    bar = add(client, pid, "BAR", "building", [0.36, 0.09, 0.19, 0.38], goal="2D_LAYER_EXPORT")
    sign = add(client, pid, "BAR sign", "sign", [0.40, 0.12, 0.02, 0.1], parent_id=bar["id"], goal="2D_LAYER_EXPORT")
    z = zipfile.ZipFile(export(client, pid)["result"]["path"])
    scene = json.loads(z.read("inventory/scene_manifest.json"))
    assert scene["scene_relationships"] == [{"parent": bar["id"], "child": sign["id"]}]
    sm = json.loads(z.read(f"assets/{sign['code']}/asset.json"))
    assert sm["parent_id"] == bar["code"] and sm["bbox_normalized_xywh"] == [0.4, 0.12, 0.02, 0.1]
    inv = json.loads(z.read("inventory/inventory.json"))
    assert inv[0]["children"][0]["code"] == sign["code"]


def test_override_of_critical_findings_requires_a_written_reason(client):
    pid, _ = setup(client)
    r = client.post(f"/projects/{pid}/export", headers=H, json={"override_critical": True, "override_reason": "ok"})
    assert r.status_code == 409 and "written reason" in r.json()["detail"]
    j = export(client, pid, override_critical=True, override_reason="Reviewed manually with the client on Friday.")
    z = zipfile.ZipFile(j["result"]["path"])
    assert json.loads(z.read("reports/export_validation.json"))["override"]["reason"].startswith("Reviewed manually")


# ------------------------------------------------------------------ Blender-backed
@pytest.mark.blender
def test_base_designer_params_reach_the_exported_manifest_and_top_view(blender, client):
    pid = client.post("/projects", headers=H, json={"name": "P", "fixtures": ["master_base"]}).json()["id"]
    base = client.get(f"/projects/{pid}/assets", headers=H).json()[0]
    bad = client.post(f"/assets/{base['id']}/build-proxy", headers=H, json={"params": {"width": 0.1}})
    assert bad.status_code in (202, 400)
    if bad.status_code == 202:
        assert wait(client, bad.json()["job_id"])["state"] == "FAILED"
    params = {"width": 18.0, "depth": 9.0, "thickness": 0.5, "bevel": 0.1, "border": 0.2, "lanes": 3, "material": "black_lacquer_wood"}
    j = wait(client, client.post(f"/assets/{base['id']}/build-proxy", headers=H, json={"params": params}).json()["job_id"])
    assert j["state"] == "SUCCEEDED", j
    assert wait(client, client.post(f"/assets/{base['id']}/render-five", headers=H, json=FAST).json()["job_id"])["state"] == "SUCCEEDED"
    for v in VIEWS:
        assert client.post(f"/assets/{base['id']}/views/{v}/review", headers=H, json={"status": "PASSED"}).status_code == 200
    ex = export(client, pid)
    assert ex["result"]["status"] == "COMPLETE", ex
    z = zipfile.ZipFile(ex["result"]["path"])
    bm = json.loads(z.read("base/master_base_manifest.json"))
    assert (bm["width_m"], bm["depth_m"], bm["thickness_m"], bm["bevel_m"], bm["materials"]["substrate"]) == (18.0, 9.0, 0.5, 0.1, "black_lacquer_wood")
    assert "base/master_base.glb" in z.namelist() and "base/top.png" in z.namelist()
    top = Image.open(io.BytesIO(z.read("base/top.png")))
    assert top.size == (256, 256)
    for v in VIEWS:
        assert f"assets/BASE-001/views/{v}.png" in z.namelist()


@pytest.mark.blender
def test_contact_sheet_is_supplementary_and_never_replaces_the_five_files(blender, client):
    pid = client.post("/projects", headers=H, json={"name": "P", "fixtures": ["bar"]}).json()["id"]
    a = client.get(f"/projects/{pid}/assets", headers=H).json()[0]
    wait(client, client.post(f"/assets/{a['id']}/build-proxy", headers=H).json()["job_id"])
    assert wait(client, client.post(f"/assets/{a['id']}/render-five", headers=H, json=FAST).json()["job_id"])["state"] == "SUCCEEDED"
    for v in VIEWS:
        client.post(f"/assets/{a['id']}/views/{v}/review", headers=H, json={"status": "PASSED"})
    z = zipfile.ZipFile(export(client, pid, include_contact_sheet=True)["result"]["path"])
    names = z.namelist()
    assert "assets/BLD-003/extras/contact_sheet.png" in names
    assert all(f"assets/BLD-003/views/{v}.png" in names for v in VIEWS)          # the five stay individual files
    sheet = Image.open(io.BytesIO(z.read("assets/BLD-003/extras/contact_sheet.png")))
    assert sheet.width == 320 * 5
    plain = zipfile.ZipFile(export(client, pid)["result"]["path"])
    assert "assets/BLD-003/extras/contact_sheet.png" not in plain.namelist()      # off by default
