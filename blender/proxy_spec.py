"""Editable proxy specs as explicit part lists (pure Python, no bpy).

A spec is {"asset_id", "name", "kind", "units": "m", "parts": [...]}.
Each part: {"name", "class", "shape": "box"|"cylinder", "center": [x,y,z], "size": [x,y,z],
            "color": "#RRGGBB", "emission": float, "face_colors": {"-y": "#..", ...}}
For cylinders, size = [diameter_x, diameter_y, height].
Classes: asset | foundation | plinth | master_base.
Origin is bottom-centre at ground (z = 0). Front faces -Y.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List

CLASSES = ("asset", "foundation", "plinth", "master_base")

POLICY_CLASSES: Dict[str, List[str]] = {
    "none": ["asset"],
    "building_foundation": ["asset", "foundation"],
    "local_plinth": ["asset", "foundation", "plinth"],
    "scene_context": ["asset", "foundation", "master_base"],
    "full_master_base": ["asset", "foundation", "master_base"],
}

CLASS_MASK_COLORS = {
    "asset": (255, 0, 0),
    "foundation": (0, 255, 0),
    "plinth": (0, 0, 255),
    "master_base": (255, 255, 0),
}


def _box(name, cls, center, size, color, emission=0.0, face_colors=None):
    p = {"name": name, "class": cls, "shape": "box", "center": list(center),
         "size": list(size), "color": color, "emission": emission}
    if face_colors:
        p["face_colors"] = face_colors
    return p


def _cyl(name, cls, center, size, color, emission=0.0):
    return {"name": name, "class": cls, "shape": "cylinder", "center": list(center),
            "size": list(size), "color": color, "emission": emission}


def calibration_cube_spec() -> dict:
    """Asymmetric orientation fixture. Each face carries a unique flat colour."""
    faces = {"-y": "#E02020", "+y": "#2040E0", "-x": "#20C020", "+x": "#E0D020",
             "+z": "#C020C0", "-z": "#20C0C0"}
    parts = [
        _box("body", "asset", (0, 0, 0.8), (2.0, 1.4, 1.6), "#808080", face_colors=faces),
        # peg on the LEFT face (-X), clearly off-centre
        _cyl("peg", "asset", (-1.35, 0.3, 1.2), (0.3, 0.3, 0.7), "#FF8000"),
    ]
    # cylinder is created along Z; renderer rotates parts with "rotate_y_deg"
    parts[-1]["rotate_y_deg"] = 90
    return {"asset_id": "CAL-001", "name": "Calibration cube", "kind": "calibration_cube",
            "units": "m", "parts": parts}


def bar_spec(plinth_margin=0.5, plinth_height=0.2) -> dict:
    W, D, H = 4.0, 3.0, 5.6
    parts = [
        _box("body", "asset", (0, 0, H / 2), (W, D, H), "#4A3072"),
        _box("roof_cap", "asset", (0, 0, H + 0.1), (W + 0.2, D + 0.2, 0.2), "#5A5A60"),
        # front (-Y)
        _box("sign_BAR", "asset", (-1.0, -D / 2 - 0.1, 3.6), (0.5, 0.2, 2.4), "#1445B0", emission=4.0),
        _box("sign_OPEN", "asset", (0.9, -D / 2 - 0.05, 2.6), (0.9, 0.1, 0.4), "#EE5FBC", emission=4.0),
        _box("window_front", "asset", (0.9, -D / 2 - 0.02, 4.3), (1.2, 0.04, 1.0), "#E6A52D", emission=2.5),
        _box("awning", "asset", (0, -D / 2 - 0.45, 2.3), (W + 0.2, 0.9, 0.15), "#8A4B2A"),
        # back (+Y): door only on the rear
        _box("rear_door", "asset", (-0.8, D / 2 + 0.04, 1.0), (1.0, 0.08, 2.0), "#2F7A4B"),
        # left (-X): red vent
        _box("vent_left", "asset", (-W / 2 - 0.04, 0.4, 3.5), (0.08, 1.2, 0.8), "#D02B2B"),
        # right (+X): blue AC unit
        _box("ac_right", "asset", (W / 2 + 0.25, -0.3, 3.2), (0.5, 1.0, 0.6), "#2A62D8"),
        # roof tank, back-right corner
        _box("tank_legs", "asset", (1.4, 0.9, H + 0.2 + 0.25), (1.0, 1.0, 0.5), "#3A3A3A"),
        _cyl("tank", "asset", (1.4, 0.9, H + 0.2 + 0.5 + 0.7), (1.1, 1.1, 1.4), "#2B2F36"),
        # foundation: footing ledge + one entry step
        _box("footing", "foundation", (0, 0, 0.075), (W + 0.3, D + 0.3, 0.15), "#6B6B6B"),
        _box("entry_step", "foundation", (0.8, -D / 2 - 0.5, 0.05), (1.6, 0.7, 0.1), "#7A7A7A"),
        # local presentation plinth (below ground)
        _box("plinth", "plinth", (0, -0.2, -plinth_height / 2),
             (W + 2 * plinth_margin + 0.3, D + 2 * plinth_margin + 1.0, plinth_height), "#7A4E2D"),
    ]
    return {"asset_id": "BLD-003", "name": "Neon BAR", "kind": "proxy_building",
            "units": "m", "parts": parts}


def master_base_spec(width=12.0, depth=8.0, thickness=0.35, as_class="asset") -> dict:
    parts = [
        _box("slab", as_class, (0, 0, -thickness / 2), (width, depth, thickness), "#1A1D22"),
        _box("road", as_class, (0, 1.6, 0.01), (width - 0.6, 2.4, 0.02), "#2A2C30"),
        _box("lane_line", as_class, (0, 1.6, 0.03), (width - 1.2, 0.08, 0.02), "#F2F2F2"),
        _box("sidewalk", as_class, (0, -2.6, 0.04), (width - 0.6, 2.2, 0.08), "#7C7C80"),
    ]
    return {"asset_id": "BASE-001", "name": "Master diorama base", "kind": "master_base",
            "units": "m", "parts": parts,
            "base": {"mode": "procedural_rectangle", "width_m": width, "depth_m": depth,
                     "thickness_m": thickness}}


def chart_spec() -> dict:
    """Neutral-colour test chart: grey ramp plus RGB patches on a flat panel (QA fixture)."""
    parts = []
    greys = ["#F2F2F2", "#C8C8C8", "#969696", "#646464", "#323232", "#0A0A0A"]
    for i, g in enumerate(greys):
        parts.append(_box(f"grey_{i}", "asset", (-1.25 + i * 0.5, 0, 0.5), (0.48, 0.04, 0.9), g))
    for i, c in enumerate(["#FF0000", "#00FF00", "#0000FF"]):
        parts.append(_box(f"rgb_{i}", "asset", (-0.5 + i * 0.5, 0, 1.45), (0.48, 0.04, 0.9), c))
    return {"asset_id": "CHT-001", "name": "Neutral test chart", "kind": "chart",
            "units": "m", "parts": parts}


def with_master_base(spec: dict, base: dict | None = None) -> dict:
    base = base or master_base_spec(as_class="master_base")
    out = dict(spec)
    out["parts"] = list(spec["parts"]) + [dict(p, **{"class": "master_base"}) for p in base["parts"]]
    return out


def spec_hash(spec: dict) -> str:
    """Stable mesh/model revision hash of the canonical spec."""
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(blob).hexdigest()


def get_spec(kind: str) -> dict:
    return {"calibration_cube": calibration_cube_spec, "bar": bar_spec,
            "master_base": master_base_spec, "chart": chart_spec}[kind]()
