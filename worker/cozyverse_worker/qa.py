"""Automated render QA. Findings are diagnostics only; a human review gate sets PASSED."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

WHITE_TOL = 2
MIN_MARGIN = 0.07   # target is 8% per side; small allowance for anti-aliasing


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze_png(path: Path, expected_res: int, background: str = "white") -> dict:
    findings: list[dict] = []
    with Image.open(path) as im:
        im.load()
        w, h = im.size
        mode = im.mode
        arr = np.asarray(im.convert("RGBA"))
    if (w, h) != (expected_res, expected_res):
        findings.append({"code": "BAD_DIMENSIONS", "severity": "critical", "detail": f"{w}x{h}"})
    if background == "white":
        bg = (arr[..., :3] >= 255 - WHITE_TOL).all(axis=-1)
    else:
        bg = arr[..., 3] == 0
    fg = ~bg
    occupancy = float(fg.mean())
    if not fg.any():
        findings.append({"code": "EMPTY_RENDER", "severity": "critical", "detail": "no foreground"})
        return {"width": w, "height": h, "mode": mode, "occupancy": 0.0, "margin": 0.0,
                "bbox": None, "findings": findings}
    ys, xs = np.where(fg)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    margin = min(x0, y0, w - 1 - x1, h - 1 - y1) / float(w)
    if x0 == 0 or y0 == 0 or x1 == w - 1 or y1 == h - 1:
        findings.append({"code": "CROPPED", "severity": "critical", "detail": "foreground touches border"})
    elif margin < MIN_MARGIN:
        findings.append({"code": "LOW_MARGIN", "severity": "warning", "detail": f"{margin:.3f}"})
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    if background == "white" and not (border[:, :3] >= 255 - WHITE_TOL).all():
        findings.append({"code": "BACKGROUND_NOT_WHITE", "severity": "critical", "detail": "border"})
    return {"width": w, "height": h, "mode": mode, "occupancy": occupancy, "margin": margin,
            "bbox": [x0, y0, x1, y1], "findings": findings}


def check_camera(view: str, camera: dict) -> list[dict]:
    """Verify the camera Blender actually used matches the contract."""
    out: list[dict] = []
    proj = camera["projection_matrix"]
    fwd = camera["forward"]
    if view != "perspective":
        if camera["type"] != "ORTHO":
            out.append({"code": "NOT_ORTHO", "severity": "critical", "detail": camera["type"]})
        if abs(proj[3][3] - 1.0) > 1e-6 or any(abs(proj[3][i]) > 1e-6 for i in range(3)):
            out.append({"code": "PROJECTION_NOT_PARALLEL", "severity": "critical", "detail": str(proj[3])})
        expected = {"front": (0, 1, 0), "back": (0, -1, 0), "left": (1, 0, 0), "right": (-1, 0, 0),
                    "top": (0, 0, -1)}[view]
        if any(abs(fwd[i] - expected[i]) > 1e-5 for i in range(3)):
            out.append({"code": "WRONG_DIRECTION", "severity": "critical", "detail": str(fwd)})
        mw = camera["matrix_world"]
        up = (mw[0][1], mw[1][1], mw[2][1])
        if view != "top":
            if abs(up[0]) > 1e-6 or abs(up[1]) > 1e-6 or abs(up[2] - 1) > 1e-6:
                out.append({"code": "ROLL_OR_TILT", "severity": "critical", "detail": str(up)})
    else:
        if camera["type"] != "PERSP":
            out.append({"code": "NOT_PERSP", "severity": "critical", "detail": camera["type"]})
        if abs(proj[3][2] + 1.0) > 1e-6:
            out.append({"code": "PROJECTION_NOT_PERSPECTIVE", "severity": "critical", "detail": str(proj[3])})
    return out
