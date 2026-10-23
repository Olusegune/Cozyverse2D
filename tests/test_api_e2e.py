"""Golden path through the HTTP API: project -> proxy -> five renders -> review -> ZIP -> reopen."""
import json
import subprocess
import time
import zipfile

import jsonschema
import pytest
from fastapi.testclient import TestClient

from cozyverse_worker.app import create_app
from cozyverse_worker.config import SCHEMA_DIR, Settings

pytestmark = pytest.mark.blender
TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
VIEWS = ["front", "back", "left", "right", "perspective"]
FAST = {"resolution": 256, "engine": "WORKBENCH"}


@pytest.fixture()
def client(blender, tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN))
    with TestClient(app) as c:
        yield c


def wait(client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/jobs/{job_id}", headers=H).json()
        if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def make_project(client, fixtures=("bar",)):
    r = client.post("/projects", headers=H, json={"name": "Rainy Night Market", "fixtures": list(fixtures)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assets = client.get(f"/projects/{pid}/assets", headers=H).json()
    return pid, {a["code"]: a for a in assets}


def blender_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq blender.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {line.split(",")[1].strip('"') for line in out.splitlines() if "blender.exe" in line.lower()}


def test_auth_required_everywhere(client):
    assert client.get("/health").status_code == 401
    assert client.get("/projects").status_code == 401
    assert client.get("/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/health", headers=H).status_code == 200


def test_input_validation(client):
    assert client.get("/jobs/..%2F..%2Fetc", headers=H).status_code in (400, 404)
    assert client.get("/assets/bad id/views/front/image", headers=H).status_code in (400, 404, 422)
    assert client.post("/projects", headers=H, json={"name": "x", "evil": 1}).status_code == 422
    assert client.post("/projects", headers=H, json={"name": "x" * 500}).status_code == 422
    big = {"name": "x", "pad": "y" * 2_000_000}
    assert client.post("/projects", headers=H, json=big).status_code == 413
    pid, a = make_project(client)
    aid = a["BLD-003"]["id"]
    assert client.post(f"/assets/{aid}/render-five", headers=H, json={"resolution": 99999}).status_code == 422
    assert client.post(f"/assets/{aid}/views/nope/review", headers=H,
                       json={"status": "PASSED"}).status_code == 422


def test_render_blocked_without_geometry(client):
    _, a = make_project(client)
    r = client.post(f"/assets/{a['BLD-003']['id']}/render-five", headers=H, json=FAST)
    assert r.status_code == 409
    assert "geometry" in r.json()["detail"].lower()


def test_golden_path(client):
    pid, a = make_project(client, ["bar", "master_base"])
    aid = a["BLD-003"]["id"]

    j = wait(client, client.post(f"/assets/{aid}/build-proxy", headers=H).json()["job_id"])
    assert j["state"] == "SUCCEEDED", j
    rev1 = j["result"]["model_revision"]

    j = wait(client, client.post(f"/assets/{aid}/render-five", headers=H, json=FAST).json()["job_id"])
    assert j["state"] == "SUCCEEDED", j

    asset = next(x for x in client.get(f"/projects/{pid}/assets", headers=H).json() if x["code"] == "BLD-003")
    for v in VIEWS:
        ve = asset["views"][v]
        assert ve["path"] == f"views/{v}.png" and ve["model_revision"] == rev1
        assert ve["status"] in ("DRAFT", "NEEDS_REVIEW")           # never auto-PASSED
        img = client.get(f"/assets/{aid}/views/{v}/image", headers=H)
        assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert asset["views"]["perspective"]["camera_type"] == "PERSP"
    assert all(asset["views"][v]["camera_type"] == "ORTHO" for v in VIEWS[:4])

    # export before review is PARTIAL, never COMPLETE
    j = wait(client, client.post(f"/projects/{pid}/export", headers=H).json()["job_id"])
    assert j["state"] == "SUCCEEDED" and j["result"]["status"] == "PARTIAL"

    # human review gate
    for v in VIEWS:
        r = client.post(f"/assets/{aid}/views/{v}/review", headers=H, json={"status": "PASSED"})
        assert r.status_code == 200, r.text

    # master base must be built + rendered + reviewed too for a COMPLETE project
    bid = a["BASE-001"]["id"]
    assert wait(client, client.post(f"/assets/{bid}/build-proxy", headers=H).json()["job_id"])["state"] == "SUCCEEDED"
    assert wait(client, client.post(f"/assets/{bid}/render-five", headers=H, json=FAST).json()["job_id"])["state"] == "SUCCEEDED"
    for v in VIEWS:
        assert client.post(f"/assets/{bid}/views/{v}/review", headers=H, json={"status": "PASSED"}).status_code == 200

    j = wait(client, client.post(f"/projects/{pid}/export", headers=H).json()["job_id"])
    assert j["result"]["status"] == "COMPLETE", j
    z = zipfile.ZipFile(j["result"]["path"])
    names = z.namelist()
    for v in VIEWS:
        assert f"assets/BLD-003/views/{v}.png" in names
        assert f"assets/BASE-001/views/{v}.png" in names
    assert "assets/BLD-003/mesh/canonical.glb" in names and "inventory/scene_manifest.json" in names
    assert not any(n.startswith("/") or ".." in n for n in names)
    manifest = json.loads(z.read("assets/BLD-003/asset.json"))
    jsonschema.Draft202012Validator(json.loads((SCHEMA_DIR / "asset.schema.json").read_text())).validate(manifest)
    assert manifest["qa"]["status"] == "PASSED"
    for v in VIEWS[:4]:
        assert manifest["views"][v]["camera"]["type"] == "ORTHO"
    assert manifest["views"]["perspective"]["camera"]["type"] == "PERSP"
    report = json.loads(z.read("reports/export_validation.json"))
    assert report["status"] == "COMPLETE" and report["unique_model_count"] == 2

    # editing the model invalidates every view and the export falls back to PARTIAL
    j = wait(client, client.post(f"/assets/{aid}/build-proxy", headers=H,
                                 json={"params": {"plinth_margin": 1.2, "plinth_height": 0.3}}).json()["job_id"])
    assert j["result"]["model_revision"] != rev1
    asset = next(x for x in client.get(f"/projects/{pid}/assets", headers=H).json() if x["code"] == "BLD-003")
    assert all(asset["views"][v]["stale"] and asset["views"][v]["status"] == "BLOCKED" for v in VIEWS)
    assert client.post(f"/assets/{aid}/views/front/review", headers=H, json={"status": "PASSED"}).status_code == 409
    j = wait(client, client.post(f"/projects/{pid}/export", headers=H).json()["job_id"])
    assert j["result"]["status"] == "PARTIAL"


def test_cancel_kills_blender_and_leaves_no_orphans(client):
    pid, a = make_project(client)
    aid = a["BLD-003"]["id"]
    wait(client, client.post(f"/assets/{aid}/build-proxy", headers=H).json()["job_id"])
    before = blender_pids()
    jid = client.post(f"/assets/{aid}/render-five", headers=H,
                      json={"resolution": 1024, "engine": "CYCLES", "samples": 256}).json()["job_id"]
    t0 = time.time()
    while time.time() - t0 < 60 and not (blender_pids() - before):
        time.sleep(0.1)
    assert blender_pids() - before, "render never started"
    assert client.delete(f"/jobs/{jid}", headers=H).status_code == 202
    j = wait(client, jid, timeout=60)
    assert j["state"] == "CANCELLED"
    time.sleep(1.0)
    assert not (blender_pids() - before), "orphaned Blender process after cancel"
    asset = client.get(f"/projects/{pid}/assets", headers=H).json()[0]
    assert all(asset["views"][v]["status"] == "BLOCKED" for v in VIEWS)   # nothing claims success


def test_reopen_project_keeps_state(blender, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", token=TOKEN)
    with TestClient(create_app(settings)) as c:
        pid, a = make_project(c)
        aid = a["BLD-003"]["id"]
        wait(c, c.post(f"/assets/{aid}/build-proxy", headers=H).json()["job_id"])
        wait(c, c.post(f"/assets/{aid}/render-five", headers=H, json=FAST).json()["job_id"])
    with TestClient(create_app(Settings(data_dir=tmp_path / "data", token=TOKEN))) as c2:
        assets = c2.get(f"/projects/{pid}/assets", headers=H).json()
        assert assets[0]["mesh_status"] == "PROXY_APPROVED"
        assert all(assets[0]["views"][v]["path"] for v in VIEWS)
        meta_cam = {v: assets[0]["views"][v] for v in VIEWS}
        assert all(m["sha256"] for m in meta_cam.values())
