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


BASE_PARAMS = ("width", "depth", "thickness", "bevel", "border", "lanes", "material")
BASE_LIMITS = {"width": (1.0, 60.0), "depth": (1.0, 60.0), "thickness": (0.05, 3.0), "bevel": (0.0, 0.5),
               "border": (0.0, 1.0), "lanes": (0, 6)}
BASE_MATERIALS = {   # name -> (substrate, trim)
    "dark_stone": ("#1A1D22", "#0E1014"), "black_lacquer_wood": ("#16110F", "#0B0807"),
    "concrete": ("#6E6E70", "#4F4F52"), "aged_stone": ("#5B5750", "#3C3934"),
    "dark_metal": ("#2A2D33", "#15171B"), "wood_tabletop": ("#5A3B22", "#3A2414"),
}


def clean_base_params(params: dict | None) -> dict:
    base = {"width": 12.0, "depth": 8.0, "thickness": 0.35, "bevel": 0.04, "border": 0.08, "lanes": 2,
            "material": "dark_stone"}
    for k, v in (params or {}).items():
        if k not in BASE_PARAMS:
            raise ValueError(f"unknown parameter {k}")
        base[k] = v
    for k, (lo, hi) in BASE_LIMITS.items():
        if not isinstance(base[k], (int, float)) or isinstance(base[k], bool) or not lo <= base[k] <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
        base[k] = int(base[k]) if k == "lanes" else float(base[k])
    if base["material"] not in BASE_MATERIALS:
        raise ValueError("unknown material")
    if base["border"] * 2 >= min(base["width"], base["depth"]):
        raise ValueError("border is wider than the base")
    return base


def master_base_spec(width=12.0, depth=8.0, thickness=0.35, bevel=0.04, border=0.08, lanes=2,
                     material="dark_stone", as_class="asset") -> dict:
    p = clean_base_params({"width": width, "depth": depth, "thickness": thickness, "bevel": bevel,
                           "border": border, "lanes": lanes, "material": material})
    W, D, T = p["width"], p["depth"], p["thickness"]
    sub, trim = BASE_MATERIALS[p["material"]]
    parts = [_box("slab", as_class, (0, 0, -T / 2), (W, D, T), sub)]
    if p["bevel"] > 0:
        parts.append(_box("bevel_step", as_class, (0, 0, 0.005), (W - 2 * p["bevel"], D - 2 * p["bevel"], 0.01), sub))
    if p["border"] > 0:
        b = p["border"]
        parts += [_box("trim_n", as_class, (0, D / 2 - b / 2, 0.03), (W, b, 0.06), trim),
                  _box("trim_s", as_class, (0, -D / 2 + b / 2, 0.03), (W, b, 0.06), trim),
                  _box("trim_w", as_class, (-W / 2 + b / 2, 0, 0.03), (b, D - 2 * b, 0.06), trim),
                  _box("trim_e", as_class, (W / 2 - b / 2, 0, 0.03), (b, D - 2 * b, 0.06), trim)]
    inner_w = W - 2 * p["border"] - 0.1
    if p["lanes"] > 0:
        road_d = min(D * 0.45, p["lanes"] * 1.3)
        cy = D * 0.2
        parts.append(_box("road", as_class, (0, cy, 0.012), (inner_w, road_d, 0.02), "#2A2C30"))
        for i in range(1, p["lanes"]):
            y = cy - road_d / 2 + i * road_d / p["lanes"]
            parts.append(_box(f"lane_line_{i}", as_class, (0, y, 0.03), (inner_w * 0.95, 0.07, 0.02), "#F2F2F2"))
        side_d = max(0.4, (D - 2 * p["border"] - road_d) / 2 - 0.1)
        parts.append(_box("sidewalk", as_class, (0, cy - road_d / 2 - side_d / 2 - 0.05, 0.035), (inner_w, side_d, 0.07), "#7C7C80"))
    return {"asset_id": "BASE-001", "name": "Master diorama base", "kind": "master_base", "units": "m",
            "parts": parts, "base": {"mode": "procedural_rectangle", "width_m": W, "depth_m": D, "thickness_m": T,
                                     "bevel_m": p["bevel"], "border_width_m": p["border"], "lanes": p["lanes"],
                                     "material": p["material"]}}


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


# ---------------------------------------------------------------- parametric proxies
PARAM_LIMITS = {"width": (0.1, 40.0), "depth": (0.1, 40.0), "height": (0.1, 40.0), "roof_pitch_deg": (0.0, 60.0)}
DEFAULTS = {
    "building": {"width": 4.0, "depth": 3.0, "height": 5.6, "roof": "flat", "roof_pitch_deg": 25.0,
                 "awning": True, "tank": False, "color": "#8A7F72"},
    "vehicle": {"width": 1.9, "depth": 4.4, "height": 1.5, "color": "#E6A52D"},
    "character": {"width": 0.6, "depth": 0.35, "height": 1.75, "color": "#C9A27E"},
    "animal": {"width": 0.4, "depth": 0.9, "height": 0.7, "color": "#8B6B4A"},
    "prop": {"width": 0.8, "depth": 0.8, "height": 0.8, "color": "#9A7B4F"},
    "furniture": {"width": 1.0, "depth": 0.6, "height": 0.9, "color": "#8A5A3A"},
    "sign": {"width": 0.9, "depth": 0.1, "height": 2.6, "color": "#1445B0"},
    "light": {"width": 0.3, "depth": 0.3, "height": 4.5, "color": "#3A3A3A"},
    "vegetation": {"width": 1.6, "depth": 1.6, "height": 3.0, "color": "#3E7A3A"},
    "structure": {"width": 3.0, "depth": 2.0, "height": 2.5, "color": "#7A7A7A"},
    "background": {"width": 6.0, "depth": 0.2, "height": 4.0, "color": "#808080"},
    "effect": {"width": 1.0, "depth": 1.0, "height": 1.0, "color": "#808080"},
    "other": {"width": 1.0, "depth": 1.0, "height": 1.0, "color": "#808080"},
}


def clean_params(category: str, params: dict | None) -> dict:
    """Validate and complete proxy parameters. Raises ValueError on bad input."""
    base = dict(DEFAULTS.get(category, DEFAULTS["other"]))
    for k, v in (params or {}).items():
        if k not in base:
            raise ValueError(f"unknown parameter {k}")
        base[k] = v
    for k, (lo, hi) in PARAM_LIMITS.items():
        if k in base:
            if not isinstance(base[k], (int, float)) or isinstance(base[k], bool) or not lo <= base[k] <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            base[k] = float(base[k])
    col = base.get("color")
    if not (isinstance(col, str) and len(col) == 7 and col[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in col[1:])):
        raise ValueError("color must be #RRGGBB")
    if base.get("roof", "flat") not in ("flat", "gable", "shed"):
        raise ValueError("roof must be flat, gable or shed")
    for flag in ("awning", "tank"):
        if flag in base and not isinstance(base[flag], bool):
            raise ValueError(f"{flag} must be true or false")
    return base


def _shade(hexcol: str, f: float) -> str:
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(c * f))) for c in (r, g, b))


def _tan(deg: float) -> float:
    import math
    return math.tan(math.radians(deg))


def _shape(name, shape, center, size, color, emission=0.0, rotate_deg=None):
    p = {"name": name, "class": "asset", "shape": shape, "center": list(center), "size": list(size),
         "color": color, "emission": emission}
    if rotate_deg:
        p["rotate_deg"] = list(rotate_deg)
    return p


def parametric_spec(code: str, name: str, category: str, params: dict | None = None) -> dict:
    p = clean_params(category, params)
    W, D, H, C = p["width"], p["depth"], p["height"], p["color"]
    parts: list = []
    if category == "building":
        parts.append(_box("body", "asset", (0, 0, H / 2), (W, D, H), C))
        parts.append(_box("footing", "foundation", (0, 0, 0.06), (W + 0.2, D + 0.2, 0.12), "#6B6B6B"))
        parts.append(_box("plinth", "plinth", (0, -0.2, -0.1), (W + 1.0, D + 1.5, 0.2), "#7A4E2D"))
        parts.append(_box("entry_step", "foundation", (0, -D / 2 - 0.3, 0.04), (min(1.4, W * 0.4), 0.5, 0.08), "#7A7A7A"))
        roof = p.get("roof", "flat")
        if roof == "gable":
            rise = (W / 2) * _tan(p["roof_pitch_deg"])
            parts.append(_shape("roof", "prism", (0, 0, H + rise / 2), (W + 0.3, D + 0.3, rise), _shade(C, 0.55)))
            top = H + rise
        elif roof == "shed":
            drop = W * _tan(p["roof_pitch_deg"])
            parts.append(_shape("roof", "box", (0, 0, H + drop / 2 + 0.05), (W + 0.3, D + 0.3, 0.12), _shade(C, 0.55),
                                rotate_deg=(0, -p["roof_pitch_deg"], 0)))
            top = H + drop + 0.1
        else:
            parts.append(_box("roof_cap", "asset", (0, 0, H + 0.1), (W + 0.2, D + 0.2, 0.2), _shade(C, 0.6)))
            top = H + 0.2
        door_h = min(2.1, H * 0.45)
        parts.append(_box("door", "asset", (-W * 0.2, -D / 2 - 0.02, door_h / 2), (0.9, 0.05, door_h), "#4B3A2A"))
        for i in range(max(1, int(H // 2.8))):
            z = 2.8 * (i + 1) + 0.4 if H > 3.2 else H * 0.6
            if z + 0.5 < H:
                parts.append(_box(f"window_{i}", "asset", (W * 0.2, -D / 2 - 0.02, z), (1.0, 0.05, 0.9), "#E6A52D", emission=2.0))
        if p.get("awning"):
            parts.append(_box("awning", "asset", (0, -D / 2 - 0.4, door_h + 0.3), (W + 0.1, 0.8, 0.12), "#A54B3C"))
        if p.get("tank"):
            tx = 0.0 if roof == "gable" else W * 0.25      # on a gable the ridge is the only place it can sit
            base_z = top if roof != "shed" else H          # shed roofs get tall legs reaching from the wall top
            leg_h = 0.5 if roof != "shed" else max(0.5, top - H + 0.3)
            parts.append(_box("tank_legs", "asset", (tx, D * 0.25, base_z + leg_h / 2), (0.9, 0.9, leg_h), "#3A3A3A"))
            parts.append(_cyl("tank", "asset", (tx, D * 0.25, base_z + leg_h + 0.7), (1.0, 1.0, 1.4), "#2B2F36"))
    elif category == "vehicle":
        wr = H * 0.22
        parts += [_box("body", "asset", (0, 0, wr + H * 0.22), (W, D, H * 0.44), C),
                  _box("cabin", "asset", (0, D * 0.05, wr + H * 0.61), (W * 0.9, D * 0.5, H * 0.34), _shade(C, 0.85)),
                  _box("windshield", "asset", (0, -D * 0.2, wr + H * 0.62), (W * 0.8, 0.04, H * 0.22), "#20252B")]
        for sx in (-1, 1):
            for sy in (-1, 1):
                parts.append(_shape(f"wheel_{sx}_{sy}", "cylinder", (sx * (W / 2 - 0.1), sy * D * 0.32, wr),
                                    (wr * 2, wr * 2, 0.25), "#1A1A1A", rotate_deg=(0, 90, 0)))
        parts.append(_box("headlight_l", "asset", (-W * 0.3, -D / 2 - 0.01, wr + H * 0.28), (0.25, 0.04, 0.15), "#FFF2C0", emission=3.0))
        parts.append(_box("headlight_r", "asset", (W * 0.3, -D / 2 - 0.01, wr + H * 0.28), (0.25, 0.04, 0.15), "#FFF2C0", emission=3.0))
    elif category in ("character", "animal"):
        leg_h = H * (0.48 if category == "character" else 0.45)
        torso = H * (0.34 if category == "character" else 0.3)
        parts += [_box("legs", "asset", (0, 0, leg_h / 2), (W * 0.55, D * 0.9, leg_h), "#2E3A52"),
                  _box("torso", "asset", (0, 0, leg_h + torso / 2), (W, D, torso), C if category == "animal" else "#B5533C"),
                  _shape("head", "sphere", (0, -D * 0.05, leg_h + torso + H * 0.09), (H * 0.17, H * 0.17, H * 0.19), C)]
        if category == "character":
            parts += [_box("arm_l", "asset", (-W * 0.62, 0, leg_h + torso * 0.55), (W * 0.2, D * 0.6, torso * 0.9), C),
                      _box("arm_r", "asset", (W * 0.62, 0, leg_h + torso * 0.55), (W * 0.2, D * 0.6, torso * 0.9), C)]
    elif category == "light":
        parts += [_cyl("pole", "asset", (0, 0, H / 2), (W * 0.45, W * 0.45, H), C),
                  _shape("lamp", "sphere", (0, 0, H + 0.1), (W * 1.4, W * 1.4, W * 1.4), "#FFB347", emission=2.2)]
    elif category == "vegetation":
        parts += [_cyl("trunk", "asset", (0, 0, H * 0.25), (W * 0.18, W * 0.18, H * 0.5), "#5A3E2B"),
                  _shape("canopy", "sphere", (0, 0, H * 0.7), (W, W, H * 0.6), C)]
    elif category == "sign":
        parts += [_cyl("post", "asset", (0, 0, H * 0.35), (0.08, 0.08, H * 0.7), "#3A3A3A"),
                  _box("board", "asset", (0, 0, H * 0.78), (W, D, H * 0.4), C, emission=2.5)]
    else:
        parts.append(_box("body", "asset", (0, 0, H / 2), (W, D, H), C))
    return {"asset_id": code, "name": name, "kind": "parametric", "category": category, "units": "m",
            "parts": parts, "params": p}


def mesh_spec(code: str, name: str, mesh_path: str, file_sha256: str, yaw_deg: int = 0,
              target_height: float | None = None) -> dict:
    """Spec for an imported mesh. The revision hash covers the file hash and orientation/scale choices."""
    if yaw_deg not in (0, 90, 180, 270):
        raise ValueError("yaw must be 0, 90, 180 or 270")
    if target_height is not None and not (0.05 <= target_height <= 100):
        raise ValueError("target height must be 0.05 to 100 m")
    part = {"name": "imported", "class": "asset", "shape": "mesh", "mesh_path": mesh_path, "yaw_deg": yaw_deg,
            "color": "#808080", "emission": 0.0}
    if target_height:
        part["target_height"] = target_height
    spec = {"asset_id": code, "name": name, "kind": "imported_mesh", "units": "m", "parts": [part],
            "mesh_sha256": file_sha256}
    return spec


def spec_hash_stable(spec: dict) -> str:
    """Hash that ignores the machine-specific mesh_path so the revision depends on content only."""
    clone = json.loads(json.dumps(spec))
    for p in clone.get("parts", []):
        p.pop("mesh_path", None)
    return spec_hash(clone)


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
