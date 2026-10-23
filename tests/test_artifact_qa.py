"""Artifact QA workflow on real renders: analysis, masked repairs with preview/accept/revert, export gate."""
import hashlib
import io
import json
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cozyverse_worker import artifact_qa as aq
from cozyverse_worker.app import create_app
from cozyverse_worker.config import Settings
from cozyverse_worker.secret_store import MemoryStore

pytestmark = pytest.mark.blender
TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
VIEWS = ["front", "back", "left", "right", "perspective"]
FAST = {"resolution": 384, "engine": "WORKBENCH"}


@pytest.fixture()
def client(blender, tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN), MemoryStore())
    with TestClient(app) as c:
        c.data_dir, c.svc = tmp_path / "data", app.state.service
        yield c


def wait(c, jid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/jobs/{jid}", headers=H).json()
        if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
            return j
        time.sleep(0.1)
    raise TimeoutError


def chart(c, views=("front",)):
    pid = c.post("/projects", headers=H, json={"name": "P", "fixtures": ["chart"]}).json()["id"]
    a = c.get(f"/projects/{pid}/assets", headers=H).json()[0]
    assert wait(c, c.post(f"/assets/{a['id']}/build-proxy", headers=H).json()["job_id"])["state"] == "SUCCEEDED"
    body = {**FAST, "views": list(views)}
    assert wait(c, c.post(f"/assets/{a['id']}/render-five", headers=H, json=body).json()["job_id"])["state"] == "SUCCEEDED"
    return pid, a["id"]


def view_path(c, aid, view="front"):
    return c.svc.asset_dir(c.svc.get_asset(aid)) / "views" / f"{view}.png"


def tint(c, aid, view="front", factors=(1.0, 1.0, 0.8)):
    """Simulate a render that came out with a global yellow cast on neutral surfaces (saturated patches keep theirs)."""
    p = view_path(c, aid, view)
    img = np.asarray(Image.open(p).convert("RGB")).copy()
    lab = aq.srgb_to_lab(img)
    fg = ~(img >= 253).all(axis=-1)
    neutral = fg & (np.hypot(lab[..., 1], lab[..., 2]) < 20)
    out = img.astype(np.float64)
    out[neutral] *= np.array(factors)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(p)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    c.svc.db.execute("UPDATE view_renders SET sha256=? WHERE asset_id=? AND view=?", (sha, aid, view))
    return sha


def test_clean_render_has_no_cast_or_noise_and_masks_are_saved(client):
    _, aid = chart(client)
    r = client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H)
    assert r.status_code == 200, r.text
    rep = r.json()
    codes = {f["code"] for f in rep["findings"]}
    assert "COLOR_CAST" not in codes and "NOISE" not in codes, rep["findings"]
    assert rep["settings"]["neutralize_cast"] and rep["settings"]["mode"] == "studio"
    assert "protected" in rep["masks"]
    assert client.get(f"/assets/{aid}/views/front/artifacts/mask/protected", headers=H).status_code == 200
    assert "limits" in rep and "fallible" in rep["limits"]
    again = client.get(f"/assets/{aid}/views/front/artifacts", headers=H).json()
    assert again["stale"] is False and again["sha256"] == rep["sha256"]


def test_settings_are_validated_and_scene_grade_mode_respects_deliberate_warmth(client):
    _, aid = chart(client)
    assert client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H, json={"reduce_noise": "yes"}).status_code == 422
    assert client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H, json={"bogus": True}).status_code == 422
    tint(client, aid)
    studio = client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H).json()
    grade = client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H, json={"mode": "scene_grade"}).json()
    assert any(f["code"] == "COLOR_CAST" for f in studio["findings"])
    assert not any(f["code"] == "COLOR_CAST" for f in grade["findings"])        # deliberate warm grade is left alone
    assert any(f["code"] == "CAST_SCENE_GRADE" for f in grade["findings"])


def test_cast_repair_preview_accept_revert_keeps_original_and_protects_colours(client):
    _, aid = chart(client)
    clean_sha = hashlib.sha256(view_path(client, aid).read_bytes()).hexdigest()
    clean = np.asarray(Image.open(view_path(client, aid)).convert("RGB")).copy()
    tinted_sha = tint(client, aid)
    rep = client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H).json()
    cast = next(f for f in rep["findings"] if f["code"] == "COLOR_CAST")
    assert cast["remediation"][0].startswith("Re-render")                       # re-rendering is the preferred path

    p = client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "cast", "strength": 1.0})
    assert p.status_code == 201, p.text
    pv = p.json()
    assert pv["accepted"] is False and pv["metrics"]["protected_untouched"] is True and pv["metrics"]["protected_pixels"] > 0
    assert pv["metrics"]["after"]["cast"]["magnitude"] < pv["metrics"]["before"]["cast"]["magnitude"]
    for which in ("before", "after", "diff", "mask"):
        assert client.get(f"/assets/{aid}/repairs/{pv['id']}/image?which={which}", headers=H).status_code == 200
    assert hashlib.sha256(view_path(client, aid).read_bytes()).hexdigest() == tinted_sha     # nothing applied yet

    ok = client.post(f"/assets/{aid}/repairs/{pv['id']}/accept", headers=H, json={"note": "client approved neutral look"})
    assert ok.status_code == 200, ok.text
    after_img = np.asarray(Image.open(view_path(client, aid)).convert("RGB"))
    row = client.svc.db.one("SELECT * FROM view_renders WHERE asset_id=? AND view='front'", (aid,))
    assert row["status"] == "NEEDS_REVIEW" and row["sha256"] == hashlib.sha256(view_path(client, aid).read_bytes()).hexdigest()
    originals = list((client.svc.asset_dir(client.svc.get_asset(aid)) / "qa" / "originals").glob("front_*.png"))
    assert len(originals) == 1 and hashlib.sha256(originals[0].read_bytes()).hexdigest() == tinted_sha
    log = json.loads(row["qa_json"])["repairs"][0]
    assert log["kind"] == "cast" and log["note"] == "client approved neutral look" and log["original_sha"] == tinted_sha
    assert ok.json()["metrics"]["cast"]["magnitude"] < 3.0
    grey = (clean.astype(int).std(axis=-1) < 3) & ~(clean >= 253).all(axis=-1)
    err = np.abs(after_img.astype(int) - clean.astype(int)).max(axis=-1)[grey]
    assert np.median(err) < 8                                                   # grey patches are back near the clean render
    bg = (clean >= 253).all(axis=-1)
    assert np.array_equal(after_img[bg], clean[bg])                             # background pixels are not touched
    assert (after_img[0] == 255).all() and (after_img[:, 0] == 255).all()       # border stays pure white
    assert row["sha256"] != clean_sha

    rv = client.post(f"/assets/{aid}/views/front/repairs/revert", headers=H)
    assert rv.status_code == 200
    assert hashlib.sha256(view_path(client, aid).read_bytes()).hexdigest() == tinted_sha     # exactly what the user had before


def test_a_preview_goes_stale_when_the_view_is_rendered_again(client):
    _, aid = chart(client)
    tint(client, aid)
    client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H)
    pv = client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "cast"}).json()
    assert wait(client, client.post(f"/assets/{aid}/render-five", headers=H, json={**FAST, "views": ["front"]}).json()["job_id"])["state"] == "SUCCEEDED"
    r = client.post(f"/assets/{aid}/repairs/{pv['id']}/accept", headers=H, json={})
    assert r.status_code == 409 and "changed" in r.json()["detail"]
    assert client.get(f"/assets/{aid}/views/front/artifacts", headers=H).json()["stale"] is True


def test_repair_kinds_are_limited_and_need_a_fresh_analysis(client):
    _, aid = chart(client)
    assert client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "skin"}).status_code == 422
    r = client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "cast"})
    assert r.status_code == 409 and "analyze" in r.json()["detail"].lower()
    client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H)
    r = client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "cast"})
    assert r.status_code == 409 and "no colour cast" in r.json()["detail"]      # clean view: nothing to repair


def approve_all(c, aid):
    for v in VIEWS:
        assert c.post(f"/assets/{aid}/views/{v}/review", headers=H, json={"status": "PASSED"}).status_code == 200


def export(c, pid, **body):
    return wait(c, c.post(f"/projects/{pid}/export", headers=H, json=body).json()["job_id"])


def test_export_gate_blocks_unresolved_critical_findings_until_repaired_or_overridden(client):
    pid, aid = chart(client, VIEWS)
    approve_all(client, aid)
    cons = client.post(f"/assets/{aid}/artifacts/analyze", headers=H).json()
    assert cons["consistency"] == []                                            # five real renders agree on height
    assert export(client, pid)["result"]["status"] == "COMPLETE"

    tint(client, aid, factors=(1.0, 1.0, 0.62))                                 # severe cast on one view
    rep = client.post(f"/assets/{aid}/views/front/artifacts/analyze", headers=H).json()
    assert any(f["code"] == "COLOR_CAST" and f["severity"] == "critical" for f in rep["findings"])
    blocked = export(client, pid)
    assert blocked["result"]["status"] == "PARTIAL"
    assert any("critical artifact" in r for r in blocked["result"]["incomplete"]["CHT-001"])

    # a written override is allowed and recorded
    z = zipfile.ZipFile(export(client, pid, override_critical=True, override_reason="Intentional sodium-lamp look, approved.")["result"]["path"])
    rep_json = json.loads(z.read("reports/export_validation.json"))
    assert rep_json["status"] == "COMPLETE" and rep_json["override"]["reason"].startswith("Intentional sodium-lamp")

    # or the defect is repaired, which clears the gate (the view needs human re-approval afterwards)
    pv = client.post(f"/assets/{aid}/views/front/repairs/preview", headers=H, json={"kind": "cast"}).json()
    assert client.post(f"/assets/{aid}/repairs/{pv['id']}/accept", headers=H, json={"note": "fixed"}).status_code == 200
    assert export(client, pid)["result"]["status"] == "PARTIAL"                 # front is NEEDS_REVIEW again
    assert client.post(f"/assets/{aid}/views/front/review", headers=H, json={"status": "PASSED"}).status_code == 200
    final = export(client, pid)
    assert final["result"]["status"] == "COMPLETE", final["result"]
    man = json.loads(zipfile.ZipFile(final["result"]["path"]).read("assets/CHT-001/asset.json"))
    assert man["views"]["front"]["status"] == "PASSED"
