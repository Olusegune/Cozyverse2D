"""OpenAI Images API (direct). Endpoints and fields per developers.openai.com docs checked 2026-10-23."""
from __future__ import annotations

import base64
import io
from typing import Any, Optional

from PIL import Image

from .base import (Capabilities, GenerateRequest, ImageProvider, ImageResult, ModelInfo, ProviderError,
                   map_http_error, request_with_retry)

BASE = "https://api.openai.com/v1"
DOCS = "https://developers.openai.com/api/docs/guides/image-generation"
RATIOS = ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"]
# (w, h) multiples of 16, 655,360 <= pixels <= 8,294,400, ratio within 1:3..3:1
SIZES = {
    "1K": {"1:1": (1024, 1024), "3:2": (1536, 1024), "2:3": (1024, 1536), "4:3": (1536, 1152), "3:4": (1152, 1536),
           "16:9": (1536, 864), "9:16": (864, 1536), "21:9": (1920, 816)},
    "2K": {"1:1": (2048, 2048), "3:2": (2048, 1360), "2:3": (1360, 2048), "4:3": (2048, 1536), "3:4": (1536, 2048),
           "16:9": (2048, 1152), "9:16": (1152, 2048), "21:9": (2560, 1088)},
}


def _caps(edit=True, mask=True) -> Capabilities:
    return Capabilities(generate=True, edit=edit, mask_edit=mask, aspect_ratios=RATIOS, resolutions=["1K", "2K"],
                        max_refs=16, max_variants=4, seed=False, negative_prompt=False, transparency=True,
                        max_prompt_chars=32000, async_task=False)


CATALOG = [
    ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst (editing precision)", _caps(), "unknown", "Named in the current docs."),
    ("gpt-image-2.5-flare", "GPT Image 2.5 Flare (fast)", _caps(), "unknown", "Named in the current docs."),
    ("gpt-image-2", "GPT Image 2", _caps(edit=False, mask=False), "unknown",
     "Appears in pricing but is not named in the image guide. Capabilities unverified."),
]


def to_openai_mask(mask_png: bytes, base_png: bytes) -> bytes:
    """CozyVerse masks are white where the edit should happen. OpenAI wants a same-size RGBA mask whose
    transparent pixels mark the edit area, so convert (alpha 0 where white, 255 elsewhere)."""
    with Image.open(io.BytesIO(base_png)) as base:
        size = base.size
    with Image.open(io.BytesIO(mask_png)) as m:
        lum = m.convert("L").resize(size, Image.NEAREST)
    alpha = lum.point(lambda v: 0 if v > 127 else 255)
    out = Image.new("RGBA", size, (0, 0, 0, 255))
    out.putalpha(alpha)
    b = io.BytesIO()
    out.save(b, "PNG")
    return b.getvalue()


class OpenAIProvider(ImageProvider):
    id = "openai"
    label = "OpenAI GPT Image (direct)"
    docs_url = DOCS

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(f"openai:{slug}", slug, label, status, detail, caps, DOCS) for slug, label, caps, status, detail in CATALOG]

    @staticmethod
    def _auth(key: Optional[str]) -> dict:
        if not key:
            raise ProviderError("no_key", "no OpenAI API key is saved")
        return {"Authorization": f"Bearer {key}"}

    def health_check(self, key: Optional[str]) -> dict:
        with self._client_factory() as c:
            r = request_with_retry(c, "GET", f"{BASE}/models", headers=self._auth(key), sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        ids = {m.get("id") for m in r.json().get("data", [])}
        return {"ok": True, "visible": sorted(s for s, *_ in CATALOG if s in ids),
                "detail": "Key accepted. Listed models are available to your account."}

    def generate(self, req: GenerateRequest, key: Optional[str], cancel: Optional[Any] = None) -> list[ImageResult]:
        m = self.model(req.model_id)
        w, h = SIZES[req.resolution][req.aspect_ratio]
        size = f"{w}x{h}"
        headers = self._auth(key)
        with self._client_factory() as c:
            if req.mode == "edit":
                if not req.references:
                    raise ProviderError("bad_request", "edit needs a base image")
                files = [("image[]", (f"image{i}.png", ref, "image/png")) for i, ref in enumerate(req.references)]
                if req.mask is not None:
                    files.append(("mask", ("mask.png", to_openai_mask(req.mask, req.references[0]), "image/png")))
                data = {"model": m.slug, "prompt": req.prompt, "n": str(req.n), "size": size}
                r = request_with_retry(c, "POST", f"{BASE}/images/edits", headers=headers, data=data, files=files,
                                       sleep=self._sleep)
            else:
                body = {"model": m.slug, "prompt": req.prompt, "n": req.n, "size": size, "output_format": "png"}
                if req.transparent:
                    body["background"] = "transparent"
                r = request_with_retry(c, "POST", f"{BASE}/images/generations", headers={**headers, "Content-Type": "application/json"},
                                       json=body, sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        out = []
        for item in r.json().get("data", []):
            b64 = item.get("b64_json")
            if not b64:
                raise ProviderError("no_image", "the model returned no image data")
            try:
                out.append(ImageResult(base64.b64decode(b64, validate=True), "image/png", {"provider_model": m.slug, "size": size}))
            except ValueError as e:
                raise ProviderError("bad_response", "the provider returned invalid image data") from e
        if not out:
            raise ProviderError("no_image", "the model returned no images")
        return out
