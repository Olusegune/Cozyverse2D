"""Real Blender renders: orientation, leakage, projection matrices, separate invocations."""
import numpy as np
import pytest
from PIL import Image

import proxy_spec as ps
from conftest import write_spec
from cozyverse_worker import blender_adapter, qa

pytestmark = pytest.mark.blender

FACE = {"red": (224, 32, 32), "blue": (32, 64, 224), "green": (32, 192, 32),
        "yellow": (224, 208, 32), "magenta": (192, 32, 192), "orange": (255, 128, 0)}
# which colours may legitimately appear in each view of the calibration cube
ALLOWED = {"front": {"red", "orange"}, "back": {"blue", "orange"}, "left": {"green", "orange"},
           "right": {"yellow"}, "top": {"magenta", "orange"}}
RES = 384


def classify(arr):
    """Fraction of foreground pixels closest (within 40) to each named colour."""
    rgb = arr[..., :3].astype(int)
    fg = (rgb < 250).any(axis=-1)
    total = max(int(fg.sum()), 1)
    out = {}
    for name, c in FACE.items():
        d = np.abs(rgb - np.array(c)).max(axis=-1)
        out[name] = float(((d < 40) & fg).sum()) / total
    return out, fg


@pytest.fixture(scope="module")
def cube(blender, work):
    spec = write_spec(work / "cube.json", ps.calibration_cube_spec())
    metas = {}
    for v in ("front", "back", "left", "right", "perspective", "top"):
        metas[v] = blender_adapter.render_view(
            blender, spec, work / f"cube_{v}.png", work / f"cube_{v}.json", view=v, policy="none",
            engine="WORKBENCH", res=RES, flat=True)
    return metas


def img(work, v):
    return np.asarray(Image.open(work / f"cube_{v}.png").convert("RGB"))


@pytest.mark.parametrize("view", ["front", "back", "left", "right", "top"])
def test_each_elevation_shows_only_its_own_face(cube, work, view):
    fr, _ = classify(img(work, view))
    for name, frac in fr.items():
        if name in ALLOWED[view]:
            continue
        assert frac < 0.002, f"{view}: leaked {name} face ({frac:.4f})"
    primary = next(n for n in ALLOWED[view] if n != "orange")
    assert fr[primary] > 0.8, f"{view}: expected mostly {primary}, got {fr}"


def test_camera_matrices_read_from_blender(cube):
    for v in ("front", "back", "left", "right", "top"):
        cam = cube[v]["camera"]
        assert cam["type"] == "ORTHO"
        assert qa.check_camera(v, cam) == []
        assert cam["projection_matrix"][3] == pytest.approx([0, 0, 0, 1])   # parallel projection
    p = cube["perspective"]["camera"]
    assert p["type"] == "PERSP" and p["projection_matrix"][3][2] == pytest.approx(-1)
    assert qa.check_camera("perspective", p) == []


def test_ortho_scale_shared_by_the_four_elevations(cube):
    s = {cube[v]["camera"]["ortho_scale"] for v in ("front", "back", "left", "right")}
    assert len(s) == 1


def test_asymmetric_peg_side_matches_convention(cube, work):
    """Peg is on the object's LEFT (-X): image-left in FRONT, image-right in BACK."""
    def orange_cx(v):
        rgb = img(work, v).astype(int)
        m = np.abs(rgb - np.array(FACE["orange"])).max(axis=-1) < 40
        xs = np.where(m)[1]
        assert xs.size > 20, f"{v}: peg not visible"
        return xs.mean(), np.where((np.abs(rgb - np.array(FACE["red"])).max(axis=-1) < 40)
                                   | (np.abs(rgb - np.array(FACE["blue"])).max(axis=-1) < 40))[1].mean()
    fx, fbody = orange_cx("front")
    bx, bbody = orange_cx("back")
    assert fx < fbody
    assert bx > bbody


def test_right_elevation_hides_left_peg(cube, work):
    fr, _ = classify(img(work, "right"))
    assert fr["orange"] < 0.002


def test_perspective_shows_two_faces(cube, work):
    fr, _ = classify(img(work, "perspective"))
    assert fr["red"] > 0.05 and fr["green"] > 0.02 and fr["magenta"] > 0.02, fr   # front, left, top


def test_perspective_differs_from_every_elevation(cube, work):
    p = img(work, "perspective")
    for v in ("front", "back", "left", "right"):
        assert not np.array_equal(p, img(work, v))


def test_five_separate_invocations(cube, work):
    five = [cube[v] for v in ("front", "back", "left", "right", "perspective")]
    assert len({m["render_call_id"] for m in five}) == 5
    assert len({m["pid"] for m in five}) == 5           # one Blender process per view
    assert len({m["model_revision"] for m in five}) == 1   # same canonical model
    for v in ("front", "back", "left", "right", "perspective"):
        assert (work / f"cube_{v}.png").is_file()


def test_outputs_are_single_native_images(cube, work):
    for v in ("front", "back", "left", "right", "perspective"):
        with Image.open(work / f"cube_{v}.png") as im:
            assert im.size == (RES, RES)


def test_asymmetric_bar_elevations_differ(blender, work):
    spec = write_spec(work / "bar.json", ps.bar_spec())
    images = {}
    for v in ("front", "back", "left", "right"):
        blender_adapter.render_view(blender, spec, work / f"bar_{v}.png", work / f"bar_{v}.json",
                                    view=v, policy="building_foundation", engine="WORKBENCH", res=RES)
        images[v] = np.asarray(Image.open(work / f"bar_{v}.png").convert("RGB"))
    names = list(images)
    for i in range(4):
        for j in range(i + 1, 4):
            assert not np.array_equal(images[names[i]], images[names[j]]), (names[i], names[j])
    for v in names:
        assert qa.analyze_png(work / f"bar_{v}.png", RES)["findings"] == []
