"""Pure-Python camera contract tests (no Blender needed)."""
import math

import pytest

import cv_camera as cc
import proxy_spec as ps

LO, HI = (-2.0, -1.5, 0.0), (2.0, 1.5, 7.7)  # tall, asymmetric box


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


@pytest.mark.parametrize("view,fwd", [("front", (0, 1, 0)), ("back", (0, -1, 0)),
                                      ("left", (1, 0, 0)), ("right", (-1, 0, 0))])
def test_elevation_forward_and_zero_tilt_roll(view, fwd):
    p = cc.elevation_pose(view, LO, HI)
    assert p["type"] == "ORTHO"
    assert all(abs(a - b) < 1e-12 for a, b in zip(p["forward"], fwd))
    right, up, back = p["rotation_matrix_columns"]
    assert up == pytest.approx((0, 0, 1), abs=1e-12)       # horizon level, no roll
    assert p["forward"][2] == 0                             # zero inclination
    assert abs(dot(right, up)) < 1e-12 and abs(dot(right, p["forward"])) < 1e-12
    # camera sits on the correct side of the target
    off = [p["location"][i] - p["target"][i] for i in range(3)]
    assert dot(off, p["forward"]) < 0


def test_elevations_have_equal_scale_and_margin():
    scales = {cc.elevation_pose(v, LO, HI)["ortho_scale"] for v in cc.ORTHO_VIEWS}
    assert len(scales) == 1
    s = scales.pop()
    for v in cc.ORTHO_VIEWS:
        pose = cc.elevation_pose(v, LO, HI)
        for c in cc.bounds_corners(LO, HI):
            nx, ny, _ = cc.project_point(pose, c)
            assert abs(nx) <= 1 - 2 * 0.08 + 1e-9 and abs(ny) <= 1 - 2 * 0.08 + 1e-9
    assert s == pytest.approx(max(4.0, 3.0, 7.7) / 0.84)


def test_orthographic_projection_is_parallel():
    pose = cc.elevation_pose("front", LO, HI)
    # two points at different depths but same x,z project identically in an elevation
    a = cc.project_point(pose, (1.0, -1.5, 3.0))
    b = cc.project_point(pose, (1.0, 1.5, 3.0))
    assert a[0] == pytest.approx(b[0]) and a[1] == pytest.approx(b[1])


def test_front_vs_back_mirror_x_in_image():
    f, b = cc.elevation_pose("front", LO, HI), cc.elevation_pose("back", LO, HI)
    p = (-1.9, 0.0, 3.0)  # near the left (-X) edge
    assert cc.project_point(f, p)[0] < 0 < cc.project_point(b, p)[0]


@pytest.mark.parametrize("quad", list(cc.PERSPECTIVE_QUADRANTS))
def test_perspective_frames_all_corners(quad):
    pose = cc.perspective_pose(LO, HI, quadrant=quad, elevation_deg=30, focal_mm=50)
    assert pose["type"] == "PERSP"
    for c in cc.bounds_corners(LO, HI):
        nx, ny, depth = cc.project_point(pose, c)
        assert depth > 0
        assert abs(nx) <= 0.84 + 1e-6 and abs(ny) <= 0.84 + 1e-6
    # distinct from every elevation, and elevated above the horizon
    assert pose["forward"][2] < 0
    for v in cc.ORTHO_VIEWS:
        assert pose["forward"] != cc.elevation_pose(v, LO, HI)["forward"]


def test_top_view_basis():
    p = cc.elevation_pose("top", (-6, -4, -0.35), (6, 4, 0.1))
    assert p["forward"] == (0.0, 0.0, -1.0) and p["type"] == "ORTHO"
    assert p["up"] == pytest.approx((0, 1, 0))


def test_policy_class_sets():
    assert ps.POLICY_CLASSES["none"] == ["asset"]
    assert "plinth" not in ps.POLICY_CLASSES["building_foundation"]
    assert "master_base" not in ps.POLICY_CLASSES["local_plinth"]
    assert "plinth" in ps.POLICY_CLASSES["local_plinth"]


def test_spec_hash_stable_and_sensitive():
    a, b = ps.bar_spec(), ps.bar_spec()
    assert ps.spec_hash(a) == ps.spec_hash(b)
    assert ps.spec_hash(a) != ps.spec_hash(ps.bar_spec(plinth_margin=0.9))


def test_bar_is_asymmetric():
    parts = {p["name"]: p for p in ps.bar_spec()["parts"]}
    assert parts["rear_door"]["center"][1] > 0                  # only on the back
    assert parts["vent_left"]["center"][0] < 0                  # left side only
    assert parts["ac_right"]["center"][0] > 0                   # right side only
    assert parts["tank"]["center"][0] > 0 and parts["tank"]["center"][1] > 0  # back-right corner
