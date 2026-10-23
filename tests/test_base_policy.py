"""Object-class mask renders prove which geometry each base policy includes."""
import numpy as np
import pytest
from PIL import Image

import proxy_spec as ps
from conftest import write_spec
from cozyverse_worker import blender_adapter, qa

pytestmark = pytest.mark.blender

MASK = {"asset": (255, 0, 0), "foundation": (0, 255, 0), "plinth": (0, 0, 255), "master_base": (255, 255, 0)}
RES = 384


def classes_present(path, min_px=12):
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(int)
    out = set()
    for name, c in MASK.items():
        if (np.abs(rgb - np.array(c)).max(axis=-1) == 0).sum() >= min_px:
            out.add(name)
    return out


@pytest.fixture(scope="module")
def full_spec(work):
    return write_spec(work / "bar_with_base.json", ps.with_master_base(ps.bar_spec()))


def mask(blender, work, spec, view, policy):
    out = work / f"mask_{view}_{policy}.png"
    meta = blender_adapter.render_view(blender, spec, out, work / f"mask_{view}_{policy}.json", view=view,
                                       policy=policy, engine="WORKBENCH", res=RES, mode="mask")
    return classes_present(out), meta


def test_none_excludes_everything_but_the_asset(blender, work, full_spec):
    got, meta = mask(blender, work, full_spec, "front", "none")
    assert got == {"asset"}
    assert meta["included_classes"] == ["asset"]


def test_building_foundation_adds_footing_only(blender, work, full_spec):
    for v in ("front", "left"):
        got, _ = mask(blender, work, full_spec, v, "building_foundation")
        assert got == {"asset", "foundation"}, (v, got)


def test_plinth_never_leaks_into_elevations_but_appears_in_perspective(blender, work, full_spec):
    front, _ = mask(blender, work, full_spec, "front", "building_foundation")
    persp, _ = mask(blender, work, full_spec, "perspective", "local_plinth")
    assert "plinth" not in front and "master_base" not in front
    assert "plinth" in persp and "master_base" not in persp


def test_master_base_only_when_requested(blender, work, full_spec):
    ctx, _ = mask(blender, work, full_spec, "perspective", "full_master_base")
    assert "master_base" in ctx and "plinth" not in ctx
    iso, _ = mask(blender, work, full_spec, "perspective", "building_foundation")
    assert "master_base" not in iso


def test_fit_includes_active_policy_without_cropping(blender, work, full_spec):
    for policy in ("building_foundation", "local_plinth", "full_master_base"):
        out = work / f"fit_{policy}.png"
        blender_adapter.render_view(blender, full_spec, out, work / f"fit_{policy}.json", view="front",
                                    policy=policy, engine="WORKBENCH", res=RES)
        an = qa.analyze_png(out, RES)
        assert not [f for f in an["findings"] if f["code"] in ("CROPPED", "EMPTY_RENDER")], (policy, an)


def test_master_base_has_its_own_top_view(blender, work):
    spec = write_spec(work / "base001.json", ps.master_base_spec())
    out = work / "base001_top.png"
    meta = blender_adapter.render_view(blender, spec, out, work / "base001_top.json", view="top",
                                       policy="none", engine="WORKBENCH", res=RES)
    cam = meta["camera"]
    assert cam["type"] == "ORTHO" and cam["forward"] == pytest.approx([0, 0, -1])
    assert meta["asset_id"] == "BASE-001"
    assert qa.analyze_png(out, RES)["findings"] == []
    # the slab is wider than deep, so the footprint must be wider than tall in the top view
    bbox = qa.analyze_png(out, RES)["bbox"]
    assert (bbox[2] - bbox[0]) > (bbox[3] - bbox[1])


def test_production_cycles_render_is_pure_white_and_uncropped(blender, work):
    spec = write_spec(work / "bar_prod.json", ps.bar_spec())
    out = work / "bar_prod_front.png"
    meta = blender_adapter.render_view(blender, spec, out, work / "bar_prod_front.json", view="front",
                                       policy="building_foundation", engine="CYCLES", res=256, samples=8)
    an = qa.analyze_png(out, 256)
    assert an["findings"] == [], an
    assert an["margin"] >= 0.07
    rgb = np.asarray(Image.open(out).convert("RGB"))
    assert (rgb[0] == 255).all() and (rgb[-1] == 255).all() and (rgb[:, 0] == 255).all()
    assert meta["engine"] == "CYCLES"


def test_neutral_chart_stays_neutral(blender, work):
    spec = write_spec(work / "chart.json", ps.chart_spec())
    out = work / "chart_front.png"
    blender_adapter.render_view(blender, spec, out, work / "chart_front.json", view="front", policy="none",
                                engine="CYCLES", res=384, samples=16)
    rgb = np.asarray(Image.open(out).convert("RGB")).astype(int)
    # sample the centre of each grey patch (x positions follow the chart layout)
    an = qa.analyze_png(out, 384)
    x0, y0, x1, y1 = an["bbox"]
    w = x1 - x0 + 1
    vals = []
    for i in range(6):
        cx = x0 + int((i + 0.5) / 6 * w)
        cy = y1 - int((y1 - y0) * 0.22)       # lower row = grey ramp
        px = rgb[cy - 3:cy + 3, cx - 3:cx + 3].reshape(-1, 3).mean(axis=0)
        vals.append(px)
    for px in vals:
        assert max(px) - min(px) <= 4, f"chart patch not neutral: {px}"
    lum = [p.mean() for p in vals]
    assert lum == sorted(lum, reverse=True), lum
