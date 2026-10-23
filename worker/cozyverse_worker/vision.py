"""Optional AI detection of inventory proposals. Proposals are never trusted: they enter as 'unconfirmed'."""
from __future__ import annotations

import base64
import io
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from .inventory import CATEGORIES, VISIBILITY
from .providers.base import CostEstimate, ProviderError, Sleeper, map_http_error, request_with_retry

GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_DETECTIONS = 200
PROMPT = (
    "You are inspecting an image to list every distinct physical object, building, vehicle, character, sign, light and "
    "small prop that could be modelled in 3D, including tiny items (jars, bottles, boxes, shutters). Return ONLY a JSON "
    "array. Each element: {\"label\": short name, \"category\": one of " + ", ".join(sorted(CATEGORIES)) +
    ", \"box_2d\": [ymin, xmin, ymax, xmax] as integers 0-1000, \"confidence\": 0-1, \"visibility\": one of " +
    ", ".join(VISIBILITY) + ", \"parent\": label of the object it belongs to or null}. Do not invent objects. "
    "If unsure, lower the confidence.")


@dataclass
class Detection:
    name: str
    category: str
    bbox: list[float]                        # normalized [x, y, w, h] in the coordinates of the image that was analysed
    confidence: float = 0.5
    visibility: str = "partially_visible"
    parent: Optional[str] = None


def clean_detection(d: dict) -> Optional[Detection]:
    """Validate one provider item. Anything malformed is dropped, never repaired by guessing."""
    try:
        name = str(d["label"]).strip()[:120]
        cat = d.get("category", "other")
        cat = cat if cat in CATEGORIES and cat not in ("master_base", "calibration") else "other"
        if "bbox" in d:
            x, y, w, h = (float(v) for v in d["bbox"])
        else:
            y0, x0, y1, x1 = (float(v) for v in d["box_2d"])
            x, y, w, h = x0 / 1000.0, y0 / 1000.0, (x1 - x0) / 1000.0, (y1 - y0) / 1000.0
        if not name or w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1.002 or y + h > 1.002:
            return None
        w, h = min(w, 1 - x), min(h, 1 - y)
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
        vis = d.get("visibility", "partially_visible")
        vis = vis if vis in VISIBILITY else "partially_visible"
        parent = d.get("parent")
        return Detection(name, cat, [round(x, 5), round(y, 5), round(w, 5), round(h, 5)], conf, vis,
                         str(parent)[:120] if parent else None)
    except (KeyError, TypeError, ValueError):
        return None


def parse_json_detections(text: str) -> list[Detection]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ProviderError("bad_response", "the model did not return a JSON list")
    try:
        items = json.loads(m.group(0))
    except ValueError as e:
        raise ProviderError("bad_response", "the model returned unreadable JSON") from e
    if not isinstance(items, list):
        raise ProviderError("bad_response", "the model did not return a JSON list")
    out = [d for d in (clean_detection(i) for i in items[:MAX_DETECTIONS] if isinstance(i, dict)) if d]
    return out


def iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0.0, min(ax1, bx1) - max(ax0, bx0)), max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def merge_detections(groups: list[list[Detection]], thr: float = 0.5) -> list[Detection]:
    """Merge passes. Same-category boxes that overlap are one object; the more confident one wins."""
    out: list[Detection] = []
    for g in groups:
        for d in g:
            hit = next((o for o in out if o.category == d.category and iou(o.bbox, d.bbox) >= thr), None)
            if hit is None:
                out.append(d)
            elif d.confidence > hit.confidence:
                out[out.index(hit)] = d
    return out


def tile_regions(n: int, overlap: float = 0.15) -> list[list[float]]:
    """n x n overlapping tiles as normalized [x, y, w, h] regions for the small-prop second pass."""
    if n < 2:
        return []
    size = 1.0 / n + overlap
    size = min(1.0, size)
    step = (1.0 - size) / (n - 1)
    return [[round(i * step, 5), round(j * step, 5), round(size, 5), round(size, 5)] for j in range(n) for i in range(n)]


def to_full(d: Detection, region: list[float]) -> Detection:
    rx, ry, rw, rh = region
    return Detection(d.name, d.category, [round(rx + d.bbox[0] * rw, 5), round(ry + d.bbox[1] * rh, 5),
                                           round(d.bbox[2] * rw, 5), round(d.bbox[3] * rh, 5)],
                     d.confidence, d.visibility, d.parent)


class VisionProvider(ABC):
    id: str = ""
    label: str = ""
    needs_key: bool = True
    needs_model: bool = False
    docs_url: str = ""

    def __init__(self, client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=120.0),
                 sleep: Sleeper = time.sleep):
        self._client_factory, self._sleep = client_factory, sleep

    @abstractmethod
    def detect(self, image_png: bytes, key: Optional[str], model: Optional[str], region: Optional[list[float]] = None,
               hints: Optional[dict] = None) -> list[Detection]: ...

    def estimate(self, price: Optional[float], calls: int) -> CostEstimate:
        if price is None:
            return CostEstimate(None, None, "unknown", False,
                                "The app does not know this model's price. The provider will bill your account.")
        return CostEstimate(price * calls, price * calls, "call", False, "Based on the per-call price you entered.")


class MockVision(VisionProvider):
    id = "mock_vision"
    label = "Mock detector (offline test generator)"
    needs_key = False

    def estimate(self, price, calls):
        return CostEstimate(0.0, 0.0, "free", True, "Built-in detector. No cost.", free=True)

    def detect(self, image_png, key, model, region=None, hints=None):
        layout = (hints or {}).get("layout")
        if not layout:
            return []                                     # nothing known offline: the user draws items manually
        out = []
        for i, o in enumerate(layout):
            x, y, w, h = o["bbox"]
            if region:
                rx, ry, rw, rh = region
                if not (x >= rx and y >= ry and x + w <= rx + rw and y + h <= ry + rh):
                    continue                              # a tile only reports objects fully inside it
                x, y, w, h = (x - rx) / rw, (y - ry) / rh, w / rw, h / rh
            conf = 0.9 - 0.07 * (i % 4)
            out.append(Detection(o["name"], o.get("category", "other") if o.get("category") in CATEGORIES else "other",
                                 [round(x, 5), round(y, 5), round(w, 5), round(h, 5)], conf, o.get("visibility", "fully_visible")))
        return out


class GoogleVision(VisionProvider):
    """Experimental: Interactions API with image input and a JSON-in-text answer (not verified live)."""
    id = "google_vision"
    label = "Google Gemini vision (experimental)"
    needs_model = True
    docs_url = "https://ai.google.dev/gemini-api/docs/image-generation"

    def detect(self, image_png, key, model, region=None, hints=None):
        if not key:
            raise ProviderError("no_key", "no Google API key is saved")
        if not model or not re.fullmatch(r"[A-Za-z0-9._-]{3,80}", model):
            raise ProviderError("bad_request", "choose a Gemini model slug that accepts image input")
        body = {"model": model, "input": [{"type": "text", "text": PROMPT},
                                          {"type": "image", "mime_type": "image/png",
                                           "data": base64.b64encode(image_png).decode()}]}
        with self._client_factory() as c:
            r = request_with_retry(c, "POST", f"{GOOGLE_BASE}/interactions", headers={"x-goog-api-key": key,
                                   "Content-Type": "application/json"}, json=body, sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        text = ""
        doc = r.json()
        for step in doc.get("steps", []) or []:
            if step.get("type") == "model_output":
                for part in step.get("content", []) or []:
                    if part.get("type") == "text":
                        text += part.get("text", "")
        text = text or str(doc.get("output_text", ""))
        return parse_json_detections(text)


def build_vision_registry(**kw) -> dict[str, VisionProvider]:
    return {p.id: p for p in (MockVision(**kw), GoogleVision(**kw))}
