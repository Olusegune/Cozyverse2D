"""Canonical camera rig math for CozyVerse. Pure Python (no bpy) so tests and Blender share it.

World: right-handed, Z up. Object front faces -Y, back +Y, left -X, right +X.
Elevation cameras are built from an explicit rotation matrix, never look-at/track-to,
so roll and inclination are exactly zero.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

Vec = Tuple[float, float, float]

ORTHO_VIEWS = ("front", "back", "left", "right")
ALL_VIEWS = ORTHO_VIEWS + ("perspective",)

# Direction from target TO camera for each elevation.
CAMERA_OFFSET: Dict[str, Vec] = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
}

PERSPECTIVE_QUADRANTS: Dict[str, Tuple[float, float]] = {
    "front_left": (-1.0, -1.0),
    "front_right": (1.0, -1.0),
    "rear_left": (-1.0, 1.0),
    "rear_right": (1.0, 1.0),
}

MARGIN_PER_SIDE = 0.08  # >= 8% of frame on each side
SENSOR_MM = 36.0


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a))
    if n == 0:
        raise ValueError("zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def bounds_of(points: Iterable[Sequence[float]]) -> Tuple[Vec, Vec]:
    pts = [tuple(p) for p in points]
    if not pts:
        raise ValueError("no points")
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def bounds_center(lo: Vec, hi: Vec) -> Vec:
    return tuple((lo[i] + hi[i]) / 2.0 for i in range(3))  # type: ignore[return-value]


def bounds_corners(lo: Vec, hi: Vec) -> List[Vec]:
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def camera_basis(forward: Vec, up_hint: Vec = (0.0, 0.0, 1.0)) -> Tuple[Vec, Vec, Vec]:
    """Return (right, up, back) world vectors = camera local +X, +Y, +Z.

    Blender cameras look down local -Z, so back = -forward. up is derived from world Z
    so the horizon stays level (zero roll).
    """
    f = _norm(forward)
    right = _cross(f, up_hint)
    if _dot(right, right) < 1e-12:
        raise ValueError("forward is parallel to up_hint")
    right = _norm(right)
    up = _cross(right, f)
    return right, up, _mul(f, -1.0)


def elevation_extent(view: str, lo: Vec, hi: Vec) -> Tuple[float, float]:
    """(horizontal, vertical) world span visible in an elevation."""
    dx, dy, dz = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    if view in ("front", "back"):
        return dx, dz
    if view in ("left", "right"):
        return dy, dz
    if view == "top":
        return dx, dy
    raise ValueError(view)


def common_ortho_scale(lo: Vec, hi: Vec, margin: float = MARGIN_PER_SIDE) -> float:
    """One ortho_scale shared by all four elevations (square frame)."""
    span = 0.0
    for v in ORTHO_VIEWS:
        w, h = elevation_extent(v, lo, hi)
        span = max(span, w, h)
    return span / (1.0 - 2.0 * margin)


def elevation_pose(view: str, lo: Vec, hi: Vec, margin: float = MARGIN_PER_SIDE) -> dict:
    target = bounds_center(lo, hi)
    offset = _norm(CAMERA_OFFSET[view])
    forward = _mul(offset, -1.0)
    # Distance only needs to clear the object; ortho has no perspective foreshortening.
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
    distance = diag * 1.5 + 1.0
    location = _add(target, _mul(offset, distance))
    if view == "top":
        right, up, back = camera_basis(forward, up_hint=(0.0, 1.0, 0.0))
        scale = max(hi[0] - lo[0], hi[1] - lo[1]) / (1.0 - 2.0 * margin)
    else:
        right, up, back = camera_basis(forward)
        scale = common_ortho_scale(lo, hi, margin)
    return {
        "view": view,
        "type": "ORTHO",
        "target": target,
        "location": location,
        "forward": forward,
        "right": right,
        "up": up,
        "rotation_matrix_columns": (right, up, back),
        "ortho_scale": scale,
        "distance": distance,
        "clip_start": 0.01,
        "clip_end": distance + diag * 2.0 + 10.0,
    }


def perspective_pose(
    lo: Vec,
    hi: Vec,
    quadrant: str = "front_left",
    elevation_deg: float = 30.0,
    focal_mm: float = 50.0,
    margin: float = MARGIN_PER_SIDE,
) -> dict:
    if quadrant not in PERSPECTIVE_QUADRANTS:
        raise ValueError(quadrant)
    if not (0.0 <= elevation_deg < 90.0):
        raise ValueError("elevation must be in [0, 90)")
    target = bounds_center(lo, hi)
    hx, hy = PERSPECTIVE_QUADRANTS[quadrant]
    e = math.radians(elevation_deg)
    horiz = _norm((hx, hy, 0.0))
    offset = _norm((horiz[0] * math.cos(e), horiz[1] * math.cos(e), math.sin(e)))
    forward = _mul(offset, -1.0)
    corners = bounds_corners(lo, hi)
    right, up, back = camera_basis(forward)
    half_fov = math.atan((SENSOR_MM / 2.0) / focal_mm)
    # Smallest distance where every corner projects inside the frame with margin.
    limit = math.tan(half_fov) * (1.0 - 2.0 * margin)
    distance = 0.0
    for c in corners:
        rel = _sub(c, target)
        x, y, z = _dot(rel, right), _dot(rel, up), _dot(rel, back)  # z toward camera
        # camera at distance d along offset: depth = d - z ; need |x|/(d-z) <= limit/ ... (half extent)
        for coord in (abs(x), abs(y)):
            need = coord / limit + z
            distance = max(distance, need)
    distance *= 1.02
    location = _add(target, _mul(offset, distance))
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
    return {
        "view": "perspective",
        "type": "PERSP",
        "target": target,
        "location": location,
        "forward": forward,
        "right": right,
        "up": up,
        "rotation_matrix_columns": (right, up, back),
        "focal_mm": focal_mm,
        "sensor_mm": SENSOR_MM,
        "quadrant": quadrant,
        "elevation_deg": elevation_deg,
        "distance": distance,
        "clip_start": 0.01,
        "clip_end": distance + diag * 2.0 + 10.0,
    }


def project_point(pose: dict, p: Vec, aspect: float = 1.0) -> Tuple[float, float, float]:
    """Project world point to normalized image coords in [-1,1]; returns (nx, ny, depth)."""
    right, up, back = pose["rotation_matrix_columns"]
    rel = _sub(p, pose["location"])
    x, y, z = _dot(rel, right), _dot(rel, up), _dot(rel, back)
    depth = -z
    if pose["type"] == "ORTHO":
        half = pose["ortho_scale"] / 2.0
        return x / half, y / half, depth
    half_w = (SENSOR_MM / 2.0) / pose["focal_mm"]
    return (x / depth) / half_w, (y / depth) / half_w, depth


def pose_for(view: str, lo: Vec, hi: Vec, **persp) -> dict:
    if view in ORTHO_VIEWS or view == "top":
        return elevation_pose(view, lo, hi)
    if view == "perspective":
        return perspective_pose(lo, hi, **persp)
    raise ValueError(view)
