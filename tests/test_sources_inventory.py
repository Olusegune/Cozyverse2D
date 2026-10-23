"""Source import (hostile inputs), approval gate, provenance, inventory CRUD, hierarchy, crops and masks."""
import hashlib
import io
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cozyverse_worker.app import create_app
from cozyverse_worker.config import Settings

TOKEN = "test-token-1234567890"
H = {"Authorization": f"Bearer {TOKEN}"}
SAMPLE = Path(__file__).parent / "fixtures" / "original_rain_market.jpg"
BAR_BOX = [0.36, 0.09, 0.14, 0.38]


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", token=TOKEN))
    with TestClient(app) as c:
        c.data_dir = tmp_path / "data"
        yield c


def png(w=200, h=120, color=(200, 30, 30), fmt="PNG") -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, fmt)
    return b.getvalue()


def new_project(c, **kw):
    r = c.post("/projects", headers=H, json={"name": "Market", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(c, pid, data):
    return c.post(f"/projects/{pid}/sources", headers=H, content=data,
                  )


def sample_project(c):
    pid = new_project(c)
    r = upload(c, pid, SAMPLE.read_bytes())
    assert r.status_code == 201, r.text
    return pid, r.json()


def test_import_real_photo_records_provenance(client):
    pid, src = sample_project(client)
    raw = SAMPLE.read_bytes()
    assert src["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (src["width"], src["height"], src["format"], src["origin"]) == (1360, 752, "JPEG", "IMPORTED")
    assert src["approved"] is False
    img = client.get(f"/sources/{src['id']}/image", headers=H)
    assert img.status_code == 200 and img.content == raw               # byte-identical, never re-encoded
    th = client.get(f"/sources/{src['id']}/image?thumb=true", headers=H)
    assert th.status_code == 200 and Image.open(io.BytesIO(th.content)).width <= 640
    on_disk = next((client.data_dir / "projects" / pid / "source").rglob("original.jpg"))
    assert not os.access(on_disk, os.W_OK) or not (on_disk.stat().st_mode & stat.S_IWRITE)   # read-only original


HOSTILE = [
    ("empty", b""),
    ("text", b"hello world, not an image"),
    ("svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
    ("truncated_png", png()[:60]),
    ("gif", png(fmt="GIF")),
    ("tiny", png(8, 8)),
    ("bomb_pixels", png(9000, 9000, fmt="PNG")),
    ("html_as_png", b"\x89PNG\r\n\x1a\n<html><script>"),
]


@pytest.mark.parametrize("name", [n for n, _ in HOSTILE])
def test_hostile_or_invalid_images_rejected(client, name):
    data = dict(HOSTILE)[name]
    pid = new_project(client)
    r = upload(client, pid, data)
    assert r.status_code == 400, (name, r.status_code, r.text)
    assert client.get(f"/projects/{pid}/sources", headers=H).json() == []      # nothing was stored


def test_oversize_upload_rejected_at_edge(client):
    pid = new_project(client)
    r = client.post(f"/projects/{pid}/sources", headers=H, content=b"x" * (41 * 1024 * 1024))
    assert r.status_code == 413


def test_client_filename_never_used_for_storage(client):
    pid = new_project(client)
    r = client.post(f"/projects/{pid}/sources", headers={**H, "Content-Disposition": 'attachment; filename="../../evil.png"'},
                    content=png())
    assert r.status_code == 201
    names = [p.name for p in (client.data_dir / "projects" / pid).rglob("*") if p.is_file()]
    assert "evil.png" not in names and not (client.data_dir / "evil.png").exists()


def test_inventory_requires_approved_source(client):
    pid, src = sample_project(client)
    body = {"name": "Neon BAR", "category": "building", "bbox": BAR_BOX}
    r = client.post(f"/projects/{pid}/inventory", headers=H, json=body)
    assert r.status_code == 409 and "Approve" in r.json()["detail"]
    ok = client.post(f"/projects/{pid}/sources/{src['id']}/approve", headers=H)
    assert ok.status_code == 200 and ok.json()["approved"] is True
    r = client.post(f"/projects/{pid}/inventory", headers=H, json=body)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["code"] == "BLD-001" and item["source_revision_id"] == src["id"] and item["bbox"] == BAR_BOX
    assert client.get(f"/projects/{pid}", headers=H).json()["approved_source_id"] == src["id"]


def approved(client):
    pid, src = sample_project(client)
    client.post(f"/projects/{pid}/sources/{src['id']}/approve", headers=H)
    return pid, src


def add(c, pid, name, category="prop", **kw):
    r = c.post(f"/projects/{pid}/inventory", headers=H, json={"name": name, "category": category, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_crop_and_polygon_mask_come_from_the_source(client):
    pid, src = approved(client)
    poly = [[0.36, 0.47], [0.43, 0.09], [0.50, 0.47]]                       # a triangle inside the box
    item = add(client, pid, "BAR", "building", bbox=BAR_BOX, polygon=poly)
    crop = Image.open(io.BytesIO(client.get(f"/inventory/{item['id']}/crop", headers=H).content))
    mask = Image.open(io.BytesIO(client.get(f"/inventory/{item['id']}/crop?kind=mask", headers=H).content))
    assert crop.mode == "RGBA" and crop.size == mask.size
    assert abs(crop.width - 0.14 * 1360) <= 2 and abs(crop.height - 0.38 * 752) <= 2
    assert mask.getpixel((1, 1)) == 0 and mask.getpixel((mask.width // 2, mask.height - 3)) == 255
    assert crop.getpixel((1, 1))[3] == 0                                    # outside the polygon is transparent


@pytest.mark.parametrize("bad", [
    {"bbox": [0, 0, 0, 0]}, {"bbox": [0.9, 0.9, 0.5, 0.5]}, {"bbox": [-0.1, 0, 0.5, 0.5]},
    {"bbox": [0.1, 0.1, 0.2]}, {"bbox": [0.1, 0.1, 0.2, 0.2], "polygon": [[0, 0], [1, 1]]},
    {"bbox": [0.1, 0.1, 0.2, 0.2], "polygon": [[0, 0], [2, 1], [0, 1]]},
])
def test_bad_geometry_rejected(client, bad):
    pid, _ = approved(client)
    r = client.post(f"/projects/{pid}/inventory", headers=H, json={"name": "x", "category": "prop", **bad})
    assert r.status_code in (400, 422), r.text


def test_hierarchy_ids_merge_history_and_tiny_props(client):
    pid, _ = approved(client)
    bar = add(client, pid, "BAR", "building", bbox=BAR_BOX)
    sign = add(client, pid, "Neon BAR sign", "sign", bbox=[0.40, 0.12, 0.02, 0.10], parent_id=bar["id"])
    bottle = add(client, pid, "Bottle", "prop", bbox=[0.45, 0.40, 0.004, 0.012])        # tiny prop is allowed
    bottle2 = add(client, pid, "Bottle", "prop", bbox=[0.46, 0.40, 0.004, 0.012])
    assert (bar["code"], sign["code"], bottle["code"], bottle2["code"]) == ("BLD-001", "SGN-001", "PRP-001", "PRP-002")

    tree = client.get(f"/projects/{pid}/inventory", headers=H).json()
    root = next(n for n in tree if n["id"] == bar["id"])
    assert [c["id"] for c in root["children"]] == [sign["id"]]

    # a parent cannot become its own descendant
    r = client.patch(f"/inventory/{bar['id']}", headers=H, json={"parent_id": sign["id"]})
    assert r.status_code == 400

    # merging accidental duplicates keeps one item, sums the count, keeps the other box as an instance
    m = client.post(f"/projects/{pid}/inventory/merge", headers=H, json={"ids": [bottle["id"], bottle2["id"]], "into": bottle["id"]})
    assert m.status_code == 200 and m.json()["count"] == 2
    assert len(client.get(f"/inventory/{bottle['id']}/instances", headers=H).json()) == 1

    # deleting a parent re-parents its children; the code is never reused
    assert client.delete(f"/inventory/{bar['id']}", headers=H).status_code == 204
    kids = client.get(f"/projects/{pid}/assets", headers=H).json()
    assert next(a for a in kids if a["id"] == sign["id"])["parent_id"] is None
    again = add(client, pid, "Another building", "building", bbox=BAR_BOX)
    assert again["code"] == "BLD-002"

    actions = [h["action"] for h in client.get(f"/projects/{pid}/inventory/history", headers=H).json()]
    assert {"create", "update", "merge", "delete"} <= set(actions) or {"create", "merge", "delete"} <= set(actions)


def test_edit_fields_and_provenance_follow_the_source_revision(client):
    pid, src = approved(client)
    item = add(client, pid, "Crate", "prop", bbox=[0.2, 0.6, 0.05, 0.06])
    r = client.patch(f"/inventory/{item['id']}", headers=H, json={
        "name": "Wooden crate", "visibility": "mostly_occluded", "status": "disputed", "count": 3,
        "goal": "2D_LAYER_EXPORT", "notes": "partly hidden by the stall"})
    assert r.status_code == 200
    out = r.json()
    assert (out["name"], out["visibility"], out["status"], out["count"], out["goal"]) == (
        "Wooden crate", "mostly_occluded", "disputed", 3, "2D_LAYER_EXPORT")
    assert out["source_revision_id"] == src["id"]
    assert client.patch(f"/inventory/{item['id']}", headers=H, json={"fixture": "bar"}).status_code == 422
    assert client.patch(f"/inventory/{item['id']}", headers=H, json={"count": 0}).status_code == 422


def test_mode_switch_keeps_sources_and_items(client):
    pid, src = approved(client)
    add(client, pid, "Crate", "prop", bbox=[0.2, 0.6, 0.05, 0.06])
    r = client.patch(f"/projects/{pid}", headers=H, json={"scene_mode": "REGULAR_IMAGE", "base_mode": "NONE"})
    assert r.status_code == 200 and r.json()["scene_mode"] == "REGULAR_IMAGE"
    assert len(client.get(f"/projects/{pid}/sources", headers=H).json()) == 1
    assert len(client.get(f"/projects/{pid}/assets", headers=H).json()) == 1
    assert client.patch(f"/projects/{pid}", headers=H, json={"scene_mode": "MINIATURE"}).status_code == 422


def test_regular_image_project_starts_without_a_base(client):
    pid = new_project(client, scene_mode="REGULAR_IMAGE", output_goal="2D_LAYER_EXPORT")
    p = client.get(f"/projects/{pid}", headers=H).json()
    assert p["base_mode"] == "NONE" and p["output_goal"] == "2D_LAYER_EXPORT"
    d = new_project(client, scene_mode="DIORAMA")
    assert client.get(f"/projects/{d}", headers=H).json()["base_mode"] == "MASTER_DIORAMA"


def test_source_file_tampering_blocks_approval(client):
    pid = new_project(client)
    src = upload(client, pid, png()).json()
    f = next((client.data_dir / "projects" / pid / "source").rglob("original.png"))
    os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
    f.write_bytes(png(color=(0, 0, 255)))
    r = client.post(f"/projects/{pid}/sources/{src['id']}/approve", headers=H)
    assert r.status_code == 400


def test_source_cannot_be_used_across_projects(client):
    p1, src = approved(client)
    p2 = new_project(client)
    r = client.post(f"/projects/{p2}/sources/{src['id']}/approve", headers=H)
    assert r.status_code == 400
    r = client.post(f"/projects/{p2}/inventory", headers=H,
                    json={"name": "x", "category": "prop", "bbox": [0.1, 0.1, 0.1, 0.1], "source_revision_id": src["id"]})
    assert r.status_code in (400, 409)
