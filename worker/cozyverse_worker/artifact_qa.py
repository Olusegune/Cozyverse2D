"""Visual artifact analysis and bounded, masked, previewable repairs (doc 15).

Heuristics, not guarantees. Intentional warm lamps and neon (saturated or emissive pixels) are protected, noise is
measured only in flat regions (rust, corrugation and fabric texture are not noise), skin is never auto-fixed, and
every repair keeps the original and is applied only after explicit acceptance.
"""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageFilter

# ----------------------------------------------------------------------------- colour
_M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
_WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _M.T / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) * 27 / 24389) * _WHITE
    lin = np.clip(xyz @ np.linalg.inv(_M).T, 0, 1)
    c = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return np.clip(np.rint(c * 255), 0, 255).astype(np.uint8)


def delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.sqrt(((a - b) ** 2).sum(axis=-1))


# ----------------------------------------------------------------------------- settings
@dataclass
class ArtifactSettings:
    neutralize_cast: bool = True            # "Neutralize unwanted global yellow/orange cast"
    preserve_warm_lights: bool = True       # "Preserve intentional amber lamps and neon glow"
    reduce_noise: bool = True               # "Reduce speckle/chroma noise"
    avoid_waxy_skin: bool = True            # "Avoid waxy/plastic skin"
    preserve_texture: bool = True           # "Preserve texture microdetail"
    check_shape: bool = True
    check_text: bool = True
    check_background: bool = True
    check_consistency: bool = True
    show_diff: bool = True
    mode: str = "studio"                    # "studio" (neutral reference) or "scene_grade" (deliberate warm grade)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ArtifactSettings":
        base = cls()
        for k, v in (d or {}).items():
            if not hasattr(base, k):
                raise ValueError(f"unknown setting {k}")
            if k == "mode":
                if v not in ("studio", "scene_grade"):
                    raise ValueError("mode must be studio or scene_grade")
            elif not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
            setattr(base, k, v)
        return base


NEUTRAL_MAX_CHROMA = 26.0      # candidate neutral surfaces
PROTECT_CHROMA = 40.0          # saturated lamps / neon / signage are never corrected
CAST_WARN, CAST_CRITICAL = 4.0, 14.0
NOISE_WARN, NOISE_CRITICAL = 1.0, 4.0       # sigma of luminance residual in flat regions (L units, 0-100)


def _arr(img) -> tuple[np.ndarray, Optional[np.ndarray]]:
    if isinstance(img, np.ndarray):
        a = img
    else:
        a = np.asarray(img.convert("RGBA"))
    if a.shape[-1] == 4:
        return a[..., :3], a[..., 3]
    return a, None


def foreground(rgb: np.ndarray, alpha: Optional[np.ndarray], white_bg: bool = True) -> np.ndarray:
    if alpha is not None and (alpha < 255).any():
        return alpha > 8
    return ~((rgb >= 253).all(axis=-1)) if white_bg else np.ones(rgb.shape[:2], bool)


# ----------------------------------------------------------------------------- analyses
def _peaks(a: np.ndarray, b: np.ndarray, bins: np.ndarray, floor: int, limit: int = 5) -> list[dict]:
    """Distinct clusters in (a*, b*) space, strongest first."""
    h, ea, eb = np.histogram2d(a, b, bins=[bins, bins])
    ca, cb = (ea[:-1] + ea[1:]) / 2, (eb[:-1] + eb[1:]) / 2
    out = []
    for _ in range(limit):
        ia, ib = np.unravel_index(np.argmax(h), h.shape)
        if h[ia, ib] < 1:
            break
        pa, pb = float(ca[ia]), float(cb[ib])
        members = np.hypot(a - pa, b - pb) < 3.0
        if int(members.sum()) < floor:
            break
        out.append({"a": float(np.median(a[members])), "b": float(np.median(b[members])), "count": int(members.sum())})
        near = (np.hypot(ca[:, None] - pa, cb[None, :] - pb) < 4.5)
        h = np.where(near, 0, h)
    return out


def cast_estimate(lab: np.ndarray, fg: np.ndarray) -> dict:
    """Is there a global colour cast?

    * Truly neutral surfaces present: no cast (a cast would have shifted them too).
    * Several distinct neutral clusters all shifted the same way: confident cast.
    * One warm/cool cluster only: could be a real surface colour (a beige wall), so it is low confidence and left to a person.
    """
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    chroma = np.hypot(a, b)
    cand = fg & (L > 12) & (L < 94) & (chroma <= NEUTRAL_MAX_CHROMA)
    n_fg = int(fg.sum())
    n = int(cand.sum())
    if n < max(300, 0.01 * n_fg):
        return {"ok": False, "reason": "not enough neutral surfaces to estimate a cast", "neutral_pixels": n}
    bins = np.arange(-NEUTRAL_MAX_CHROMA - 1, NEUTRAL_MAX_CHROMA + 2, 1.5)
    peaks = _peaks(a[cand], b[cand], bins, floor=max(120, int(0.01 * n_fg)))
    if not peaks:
        return {"ok": False, "reason": "no dominant neutral surface", "neutral_pixels": n}
    pure = [p for p in peaks if np.hypot(p["a"], p["b"]) < 2.5 and p["count"] >= 0.03 * n_fg]
    dom = peaks[0]
    share = dom["count"] / float(n)
    mag = float(np.hypot(dom["a"], dom["b"]))
    if pure:
        return {"ok": True, "a": 0.0, "b": 0.0, "magnitude": 0.0, "direction": "none", "confidence": "high",
                "neutral_pixels": n, "share": share, "peaks": len(peaks), "neutral_surfaces_present": True,
                "curve": [], "mask": np.zeros_like(fg)}
    ang = lambda p: np.degrees(np.arctan2(p["b"], p["a"]))
    agree = [p for p in peaks[1:] if np.hypot(p["a"], p["b"]) >= 3.0 and abs((ang(p) - ang(dom) + 180) % 360 - 180) < 35]
    confidence = "high" if agree else "low"
    near = cand & (np.hypot(a - dom["a"], b - dom["b"]) < 4.0)
    ca, cb = dom["a"], dom["b"]
    direction = "yellow" if cb > 0 and cb >= abs(ca) else "orange" if cb > 0 and ca > 0 else "other"
    # A multiplicative cast tints light greys more than dark ones, so also measure the cast per lightness band.
    curve = []
    for lo, hi in ((12, 35), (35, 55), (55, 75), (75, 94)):
        band = cand & (L >= lo) & (L < hi)
        if band.sum() < 150:
            continue
        bp = _peaks(a[band], b[band], bins, floor=100, limit=1)
        if bp:
            nb = band & (np.hypot(a - bp[0]["a"], b - bp[0]["b"]) < 4.0)
            if nb.sum() >= 0.2 * band.sum():
                curve.append([float(np.median(L[nb])), bp[0]["a"], bp[0]["b"]])
    return {"ok": True, "a": ca, "b": cb, "magnitude": mag, "direction": direction, "confidence": confidence,
            "neutral_pixels": n, "share": share, "peaks": len(peaks), "neutral_surfaces_present": False,
            "mask": near, "curve": curve}


def noise_estimate(lab: np.ndarray, fg: np.ndarray, block: int = 16) -> dict:
    """Noise sigma from luminance residual, measured only in flat (low-gradient) blocks inside the subject."""
    L = lab[..., 0].astype(np.float32)
    img = Image.fromarray(np.clip(L * 2.55, 0, 255).astype(np.uint8))
    blur = np.asarray(img.filter(ImageFilter.GaussianBlur(1.6)), dtype=np.float32) / 2.55
    resid = L - blur
    gy, gx = np.gradient(blur)
    grad = np.hypot(gx, gy)
    H, W = L.shape
    hb, wb = H // block, W // block
    flat = np.zeros((H, W), bool)
    stds = []
    for by in range(hb):
        for bx in range(wb):
            sl = (slice(by * block, (by + 1) * block), slice(bx * block, (bx + 1) * block))
            if fg[sl].mean() < 0.98:
                continue
            if grad[sl].mean() < 0.4 and np.abs(blur[sl] - blur[sl].mean()).max() < 6.0:
                stds.append(float(resid[sl].std()))
                flat[sl] = True
    if len(stds) < 6:
        return {"ok": False, "reason": "not enough flat surface to measure noise", "flat_blocks": len(stds)}
    return {"ok": True, "sigma": float(np.median(stds)), "flat_blocks": len(stds), "mask": flat}


def waxy_estimate(lab: np.ndarray, fg: np.ndarray) -> dict:
    """Advisory only: very low fine detail plus broad specular patches in skin-like hues. Always needs a human."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    chroma = np.hypot(a, b)
    skin = fg & (hue > 25) & (hue < 85) & (chroma > 8) & (chroma < 50) & (L > 25) & (L < 90)
    if skin.sum() < 400:
        return {"ok": False, "reason": "no skin-like region found"}
    lap = np.abs(np.gradient(np.gradient(L, axis=0), axis=0) + np.gradient(np.gradient(L, axis=1), axis=1))
    detail = float(lap[skin].mean())
    spec = float(((L > 88) & (chroma < 18) & fg).sum()) / float(max(1, skin.sum()))
    return {"ok": True, "detail": detail, "specular_share": spec, "skin_pixels": int(skin.sum()),
            "suspect": detail < 0.15 or spec > 0.08}


def analyze(img, settings: Optional[ArtifactSettings] = None, *, category: Optional[str] = None,
            white_bg: bool = True) -> dict:
    s = settings or ArtifactSettings()
    rgb, alpha = _arr(img)
    lab = srgb_to_lab(rgb)
    fg = foreground(rgb, alpha, white_bg)
    findings: list[dict] = []
    metrics: dict[str, Any] = {}
    masks: dict[str, np.ndarray] = {}
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    protected = fg & (chroma > PROTECT_CHROMA)
    masks["protected"] = protected
    metrics["protected_share"] = float(protected.sum()) / float(max(1, fg.sum()))

    if s.neutralize_cast:
        c = cast_estimate(lab, fg)
        metrics["cast"] = {k: v for k, v in c.items() if k != "mask"}
        if c["ok"]:
            masks["neutral"] = c["mask"]
            if s.mode == "scene_grade":
                findings.append({"code": "CAST_SCENE_GRADE", "severity": "info", "score": c["magnitude"],
                                 "detail": "Warm scene grade is on. A cast is expected and left alone."})
            elif c["magnitude"] >= CAST_WARN and c["confidence"] == "low":
                findings.append({"code": "COLOR_CAST", "severity": "review", "score": c["magnitude"],
                                 "detail": f"The main light surface leans {c['direction']} by {c['magnitude']:.1f} (Lab), but no other neutral "
                                           "surface confirms a cast. It may simply be the real colour of the object. A person should decide."})
            elif c["magnitude"] >= CAST_WARN:
                sev = "critical" if c["magnitude"] >= CAST_CRITICAL else "warning"
                findings.append({"code": "COLOR_CAST", "severity": sev, "score": c["magnitude"],
                                 "detail": f"Neutral surfaces lean {c['direction']} by {c['magnitude']:.1f} (Lab), confirmed by several surfaces. "
                                           "Lamps and neon are protected. Prefer re-rendering with neutral lighting."})
    if s.reduce_noise:
        n = noise_estimate(lab, fg)
        metrics["noise"] = {k: v for k, v in n.items() if k != "mask"}
        if n["ok"]:
            masks["flat"] = n["mask"]
            if n["sigma"] >= NOISE_WARN:
                sev = "critical" if n["sigma"] >= NOISE_CRITICAL else "warning"
                findings.append({"code": "NOISE", "severity": sev, "score": n["sigma"],
                                 "detail": f"Speckle in flat areas (sigma {n['sigma']:.2f}). Textured areas are excluded. "
                                           "Prefer more render samples or a denoiser pass first."})
    if s.avoid_waxy_skin and category in ("character", "animal"):
        w = waxy_estimate(lab, fg)
        metrics["waxy"] = w
        if w.get("ok") and w["suspect"]:
            findings.append({"code": "WAXY_SKIN_SUSPECT", "severity": "review", "score": w["detail"],
                             "detail": "Skin may look smooth or glossy. This is a hint only. A person must judge it; no automatic fix."})
    if s.check_text:
        findings.append({"code": "TEXT_MANUAL_REVIEW", "severity": "info", "score": 0.0,
                         "detail": "Sign lettering is not checked automatically. Compare against the approved transcription."})
    if s.check_shape:
        findings.append({"code": "SHAPE_MANUAL_REVIEW", "severity": "info", "score": 0.0,
                         "detail": "Silhouettes come from the canonical model. Check parts and layout against the source."})
    return {"settings": asdict(s), "findings": findings, "metrics": metrics, "masks": masks,
            "limits": "Automated scores are fallible. Human review is required for skin and intentional looks."}


def public_report(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if k != "masks"}


def consistency_findings(boxes: dict[str, list[int]], res: int) -> list[dict]:
    """The four elevations share one scale and one vertical extent, so their silhouette heights must agree."""
    heights = {v: (b[3] - b[1] + 1) for v, b in boxes.items() if v in ("front", "back", "left", "right") and b}
    if len(heights) < 2:
        return []
    lo, hi = min(heights.values()), max(heights.values())
    if (hi - lo) / float(res) > 0.02:
        return [{"code": "INCONSISTENT_HEIGHT", "severity": "critical", "score": (hi - lo) / float(res),
                 "detail": f"Elevation heights differ ({lo}-{hi}px). The views may come from different model revisions."}]
    return []


# ----------------------------------------------------------------------------- repairs
def neutralize_cast(rgb: np.ndarray, cast_ab: tuple[float, float], fg: np.ndarray, strength: float = 1.0,
                    max_de: float = 14.0, curve: Optional[list] = None) -> tuple[np.ndarray, np.ndarray]:
    """Shift a*/b* toward neutral, weighted so saturated pixels (lamps, neon, signs) are left untouched.

    With a per-lightness `curve` ([[L, a, b], ...]) the shift follows the measured cast at each lightness; otherwise a
    single (a, b) is used. The shift is bounded by max_de.
    """
    lab = srgb_to_lab(rgb)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    w = np.clip((PROTECT_CHROMA - chroma) / (PROTECT_CHROMA - NEUTRAL_MAX_CHROMA), 0, 1)     # 1 for greys, 0 for saturated
    w = w * fg.astype(np.float64)
    if curve and len(curve) >= 2:
        pts = sorted(curve)
        Ls = np.array([0.0] + [p[0] for p in pts] + [100.0])
        As = np.array([0.0] + [p[1] for p in pts] + [pts[-1][1]])
        Bs = np.array([0.0] + [p[2] for p in pts] + [pts[-1][2]])
        da = -np.interp(lab[..., 0], Ls, As) * strength
        db = -np.interp(lab[..., 0], Ls, Bs) * strength
    else:
        da = np.full(lab.shape[:2], -cast_ab[0] * strength)
        db = np.full(lab.shape[:2], -cast_ab[1] * strength)
    mag = np.hypot(da, db)
    scale = np.where(mag > max_de, max_de / np.maximum(mag, 1e-9), 1.0)     # bounded correction
    out = lab.copy()
    out[..., 1] += da * scale * w
    out[..., 2] += db * scale * w
    result = rgb.copy()                                  # untouched pixels stay byte-identical (white stays 255)
    touched = w > 0
    result[touched] = lab_to_srgb(out[touched])
    return result, w


def reduce_noise(rgb: np.ndarray, flat_mask: np.ndarray, strength: float = 0.7) -> tuple[np.ndarray, np.ndarray]:
    """Blur only inside flat regions (feathered), so texture and edges keep their detail."""
    img = Image.fromarray(rgb)
    blurred = np.asarray(img.filter(ImageFilter.GaussianBlur(1.8)), dtype=np.float64)
    m = Image.fromarray((flat_mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    w = (np.asarray(m, dtype=np.float64) / 255.0)[..., None] * strength
    return np.clip(rgb * (1 - w) + blurred * w, 0, 255).astype(np.uint8), w[..., 0]


def diff_heatmap(before: np.ndarray, after: np.ndarray) -> Image.Image:
    d = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=-1).astype(np.float64)
    d = np.clip(d * 6, 0, 255)
    r = np.clip(d * 2, 0, 255)
    g = np.clip(d * 2 - 255, 0, 255)
    return Image.fromarray(np.stack([r, g, 40 * (d > 0)], axis=-1).astype(np.uint8))


def mask_image(m: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8))


def to_png(a: np.ndarray, alpha: Optional[np.ndarray] = None) -> bytes:
    im = Image.fromarray(a)
    if alpha is not None:
        im.putalpha(Image.fromarray(alpha))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()
