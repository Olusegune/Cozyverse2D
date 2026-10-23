"""Google Gemini API (direct). Image output uses the Interactions API (docs checked 2026-10-23)."""
from __future__ import annotations

import base64
from typing import Any, Optional

from .base import (ASPECTS, Capabilities, GenerateRequest, ImageProvider, ImageResult, ModelInfo, ProviderError,
                   map_http_error, request_with_retry)

BASE = "https://generativelanguage.googleapis.com/v1beta"
DOCS = "https://ai.google.dev/gemini-api/docs/image-generation"


def _caps(edit=True, refs=14, res=("1K", "2K", "4K"), variants=1) -> Capabilities:
    return Capabilities(generate=True, edit=edit, mask_edit=False, aspect_ratios=list(ASPECTS), resolutions=list(res),
                        max_refs=refs, max_variants=variants, seed=False, negative_prompt=False, transparency=False,
                        max_prompt_chars=30000, async_task=False)


CATALOG = [   # slug, label, caps, status, detail
    ("gemini-nano-banana-2.1", "Nano Banana 2.1", _caps(), "unknown", ""),
    ("gemini-3.1-flash-lite-image", "Nano Banana 2 Lite", _caps(refs=1, res=("1K",)), "unknown",
     "Single-image edits only; not optimised for several references."),
    ("gemini-3.1-flash-image", "Nano Banana 2", _caps(res=("512px", "1K", "2K", "4K")), "unknown", ""),
    ("gemini-3-pro-image", "Nano Banana Pro", _caps(), "unknown", ""),
    ("gemini-2.5-flash-image", "Nano Banana (original)", _caps(refs=3), "deprecated",
     "Google recommends migrating to Nano Banana 2 Lite."),
]


class GoogleProvider(ImageProvider):
    id = "google"
    label = "Google Gemini (direct)"
    docs_url = DOCS

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(f"google:{slug}", slug, label, status, detail, caps, DOCS) for slug, label, caps, status, detail in CATALOG]

    def _headers(self, key: Optional[str]) -> dict:
        if not key:
            raise ProviderError("no_key", "no Google API key is saved")
        return {"x-goog-api-key": key, "Content-Type": "application/json"}

    def health_check(self, key: Optional[str]) -> dict:
        """Free call: list models. Tells which catalogue models the key can see. Does not prove generation works."""
        with self._client_factory() as c:
            r = request_with_retry(c, "GET", f"{BASE}/models", headers=self._headers(key), params={"pageSize": 200},
                                   sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        names = {m.get("name", "").split("/")[-1] for m in r.json().get("models", [])}
        return {"ok": True, "visible": sorted(slug for slug, *_ in CATALOG if slug in names),
                "detail": "Key accepted. Listed models are available; run one generation to verify a model."}

    def generate(self, req: GenerateRequest, key: Optional[str], cancel: Optional[Any] = None) -> list[ImageResult]:
        m = self.model(req.model_id)
        prompt = req.prompt
        if req.mode == "edit" and req.references:
            blocks: list[dict] = [{"type": "text", "text": prompt}]
        else:
            blocks = [{"type": "text", "text": prompt}]
        for ref in req.references:
            blocks.append({"type": "image", "mime_type": "image/png", "data": base64.b64encode(ref).decode()})
        body = {"model": m.slug, "input": blocks if len(blocks) > 1 else prompt,
                "response_format": {"type": "image", "mime_type": "image/png", "aspect_ratio": req.aspect_ratio,
                                    "image_size": req.resolution}}
        results: list[ImageResult] = []
        with self._client_factory() as c:
            for _ in range(req.n):
                if cancel is not None and cancel.is_set():
                    break
                r = request_with_retry(c, "POST", f"{BASE}/interactions", headers=self._headers(key), json=body,
                                       sleep=self._sleep)
                if r.status_code != 200:
                    raise map_http_error(r)
                results.append(self._parse(r.json(), m.slug))
        return results

    @staticmethod
    def _parse(doc: dict, slug: str) -> ImageResult:
        data = None
        for step in doc.get("steps", []) or []:
            if step.get("type") == "model_output":
                for part in step.get("content", []) or []:
                    if part.get("type") == "image" and part.get("data"):
                        data = part["data"]
        if data is None:
            out = doc.get("output_image") or {}
            data = out.get("data")
        if not data:
            raise ProviderError("no_image", "the model returned no image (it may have refused the prompt)")
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError as e:
            raise ProviderError("bad_response", "the provider returned invalid image data") from e
        return ImageResult(raw, "image/png", {"provider_model": slug})
