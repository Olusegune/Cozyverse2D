"""Deterministic offline provider for tests, demos and keyless use. Free. Reports object layout for mock vision."""
from __future__ import annotations

import hashlib
import io
import random
from typing import Any, Optional

from PIL import Image, ImageChops, ImageDraw

from .base import (ASPECTS, Capabilities, CostEstimate, GenerateRequest, ImageProvider, ImageResult, ModelInfo,
                   ProviderError)

LONG_EDGE = {"1K": 768, "2K": 1280}


def _rgb(rng: random.Random, lo=60, hi=235):
    return (rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(lo, hi))


def _size(aspect: str, res: str) -> tuple[int, int]:
    a, b = (int(x) for x in aspect.split(":"))
    long_edge = LONG_EDGE.get(res, 768)
    return (long_edge, max(64, round(long_edge * b / a))) if a >= b else (max(64, round(long_edge * a / b)), long_edge)


def draw_scene(prompt: str, seed: int, scene_mode: str, aspect: str, res: str) -> tuple[Image.Image, list[dict]]:
    h = hashlib.sha256(f"{prompt}|{seed}|{scene_mode}".encode()).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    W, H = _size(aspect, res)
    night = any(w in prompt.lower() for w in ("night", "rain", "neon"))
    sky = (18, 22, 40) if night else (150, 200, 240)
    im = Image.new("RGB", (W, H), sky)
    d = ImageDraw.Draw(im)
    layout: list[dict] = []

    def add(name, cat, box, vis="fully_visible"):
        x0, y0, x1, y1 = box
        layout.append({"name": name, "category": cat, "visibility": vis,
                       "bbox": [round(x0 / W, 4), round(y0 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4)]})

    ground_y = int(H * (0.72 if scene_mode == "DIORAMA" else 0.68))
    if scene_mode == "DIORAMA":                       # raised display base
        d.polygon([(W * 0.06, ground_y), (W * 0.94, ground_y), (W * 0.9, H * 0.9), (W * 0.1, H * 0.9)], fill=(30, 32, 38))
        d.rectangle([W * 0.1, H * 0.9, W * 0.9, H * 0.94], fill=(15, 16, 20))
        add("Display base", "master_base", (W * 0.06, ground_y, W * 0.94, H * 0.94), "fully_visible")
    else:
        d.rectangle([0, ground_y, W, H], fill=(90, 120, 80) if not night else (30, 45, 35))
    n_build = rng.randint(2, 4)
    slot = (W * 0.84) / n_build
    for i in range(n_build):
        bw, bh = slot * rng.uniform(0.62, 0.82), H * rng.uniform(0.28, 0.46)
        x0 = W * 0.08 + i * slot + (slot - bw) / 2
        y1 = ground_y - 2
        col = _rgb(rng)
        d.rectangle([x0, y1 - bh, x0 + bw, y1], fill=col)
        d.polygon([(x0 - 4, y1 - bh), (x0 + bw + 4, y1 - bh), (x0 + bw / 2, y1 - bh - bh * 0.18)], fill=tuple(c // 2 for c in col))
        for wy in range(2):
            for wx in range(2):
                wxs = x0 + bw * (0.18 + 0.4 * wx)
                wys = y1 - bh * (0.85 - 0.35 * wy)
                d.rectangle([wxs, wys, wxs + bw * 0.2, wys + bh * 0.16], fill=(255, 214, 120) if night else (60, 90, 140))
        add(f"Building {i + 1}", "building", (x0 - 4, y1 - bh - bh * 0.18, x0 + bw + 4, y1))
        if i == 0:                                    # sign on the first building
            sx0, sy0 = x0 + bw * 0.06, y1 - bh * 0.62
            d.rectangle([sx0, sy0, sx0 + bw * 0.14, sy0 + bh * 0.3], fill=(30, 90, 255))
            add("Neon sign", "sign", (sx0, sy0, sx0 + bw * 0.14, sy0 + bh * 0.3))
    cx = rng.uniform(0.25, 0.7) * W
    cw, ch = W * 0.16, H * 0.07                       # a vehicle on the ground
    cy = ground_y + (H - ground_y) * 0.35
    d.rectangle([cx, cy - ch, cx + cw, cy], fill=(230, 170, 40))
    d.rectangle([cx + cw * 0.2, cy - ch * 1.6, cx + cw * 0.75, cy - ch], fill=(200, 145, 30))
    for wx in (0.2, 0.75):
        d.ellipse([cx + cw * wx - ch * 0.3, cy - ch * 0.3, cx + cw * wx + ch * 0.3, cy + ch * 0.3], fill=(20, 20, 20))
    add("Taxi", "vehicle", (cx, cy - ch * 1.6, cx + cw, cy + ch * 0.3))
    tx = rng.uniform(0.05, 0.9) * W                   # a tiny prop
    d.rectangle([tx, ground_y + 6, tx + W * 0.012, ground_y + 6 + H * 0.03], fill=(120, 200, 120))
    add("Bottle", "prop", (tx, ground_y + 6, tx + W * 0.012, ground_y + 6 + H * 0.03))
    return im, layout


def edit_image(img: Image.Image, prompt: str, mask: Optional[bytes]) -> Image.Image:
    h = hashlib.sha256(prompt.encode()).digest()
    tint = (h[0], h[1], h[2])
    if mask:
        m = Image.open(io.BytesIO(mask)).convert("L").resize(img.size)
        layer = Image.new("RGB", img.size, tint)
        return Image.composite(layer, img, m.point(lambda v: 255 if v > 127 else 0))
    overlay = Image.new("RGB", img.size, tint)
    return Image.blend(img, overlay, 0.18)


class MockProvider(ImageProvider):
    id = "mock"
    label = "Mock (offline test generator)"
    needs_key = False
    docs_url = ""

    def models(self) -> list[ModelInfo]:
        caps = Capabilities(generate=True, edit=True, mask_edit=True, aspect_ratios=list(ASPECTS),
                            resolutions=["1K", "2K"], max_refs=4, max_variants=4, seed=True, negative_prompt=True,
                            transparency=False, max_prompt_chars=4000)
        return [ModelInfo("mock:scene-v1", "scene-v1", "Mock scene generator", "verified",
                          "Built-in deterministic generator. Not an AI model.", caps, "", None)]

    def health_check(self, key):
        return {"ok": True, "detail": "built in"}

    def estimate(self, req, price_per_image):
        return CostEstimate(0.0, 0.0, "free", True, "Built-in generator. No cost.", free=True)

    def generate(self, req: GenerateRequest, key, cancel=None) -> list[ImageResult]:
        out = []
        for i in range(req.n):
            seed = (req.seed if req.seed is not None else 1234) + i
            if req.mode == "edit":
                if not req.references:
                    raise ProviderError("bad_request", "edit needs a base image")
                base = Image.open(io.BytesIO(req.references[0])).convert("RGB")
                img, layout = edit_image(base, req.prompt, req.mask), None
            else:
                img, layout = draw_scene(req.prompt, seed, req.scene_mode, req.aspect_ratio, req.resolution)
            b = io.BytesIO()
            img.save(b, "PNG")
            meta: dict[str, Any] = {"seed": seed, "mock": True}
            if layout is not None:
                meta["layout"] = layout
            out.append(ImageResult(b.getvalue(), "image/png", meta))
        return out
