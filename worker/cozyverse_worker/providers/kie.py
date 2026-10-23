"""Kie.ai aggregator. createTask + recordInfo polling; results are URLs fetched through the SSRF-safe downloader."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from ..safe_fetch import FetchRefused, fetch_bytes
from .base import (Capabilities, GenerateRequest, ImageProvider, ImageResult, ModelInfo, ProviderError,
                   map_http_error, request_with_retry)

BASE = "https://api.kie.ai"
DOCS = "https://docs.kie.ai/market/google/nano-banana"
RATIOS = ["1:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3", "5:4", "4:5", "21:9"]

CATALOG = [
    ("google/nano-banana",
     "Nano Banana via Kie", Capabilities(generate=True, edit=False, aspect_ratios=RATIOS, resolutions=["1K"], max_refs=0,
                                         max_variants=1, seed=False, negative_prompt=False, transparency=False,
                                         max_prompt_chars=20000, async_task=True), "unknown", "Slug and fields confirmed in Kie docs."),
    ("google/nano-banana-edit", "Nano Banana Edit via Kie",
     Capabilities(generate=False, edit=False, async_task=True), "disabled",
     "Request fields for image input are not verified yet. Disabled until verified."),
    ("nano-banana-pro", "Nano Banana Pro via Kie",
     Capabilities(generate=False, edit=False, async_task=True), "disabled",
     "Model slug and fields are not verified yet. Disabled until verified."),
]


class KieProvider(ImageProvider):
    id = "kie"
    label = "Kie.ai (aggregator)"
    docs_url = DOCS
    poll_schedule = (2, 3, 3, 5, 5, 8, 8, 10)
    max_wait_s = 300

    def __init__(self, *a, fetcher: Callable[..., tuple[bytes, str]] = fetch_bytes, **kw):
        super().__init__(*a, **kw)
        self._fetch = fetcher

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(f"kie:{slug}", slug, label, status, detail, caps, DOCS) for slug, label, caps, status, detail in CATALOG]

    @staticmethod
    def _auth(key: Optional[str]) -> dict:
        if not key:
            raise ProviderError("no_key", "no Kie API key is saved")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _body(r) -> dict:
        """Kie returns HTTP 200 even for failures; the real status is body.code."""
        if r.status_code != 200:
            raise map_http_error(r)
        j = r.json()
        code = j.get("code")
        if code == 401:
            raise ProviderError("auth", "Kie rejected the API key")
        if code == 402:
            raise ProviderError("billing", "Kie reports insufficient credits")
        if code != 200:
            raise ProviderError("bad_request", f"Kie error {code}: {str(j.get('msg', ''))[:200]}")
        return j

    def health_check(self, key: Optional[str]) -> dict:
        # Kie documents no free listing endpoint. A task-detail lookup for a fake id checks the key without cost.
        with self._client_factory() as c:
            r = request_with_retry(c, "GET", f"{BASE}/api/v1/jobs/recordInfo", headers=self._auth(key),
                                   params={"taskId": "cozyverse-key-check"}, sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        code = r.json().get("code")
        if code == 401:
            raise ProviderError("auth", "Kie rejected the API key")
        return {"ok": True, "visible": [], "detail": "Key accepted. Kie has no model list endpoint; run one generation to verify a model."}

    def generate(self, req: GenerateRequest, key: Optional[str], cancel: Optional[Any] = None) -> list[ImageResult]:
        m = self.model(req.model_id)
        headers = self._auth(key)
        out: list[ImageResult] = []
        with self._client_factory() as c:
            for _ in range(req.n):
                body = {"model": m.slug, "input": {"prompt": req.prompt, "output_format": "png", "aspect_ratio": req.aspect_ratio}}
                r = request_with_retry(c, "POST", f"{BASE}/api/v1/jobs/createTask", headers=headers, json=body, sleep=self._sleep)
                task = self._body(r).get("data", {}).get("taskId")
                if not task:
                    raise ProviderError("bad_response", "Kie did not return a task id")
                urls = self._wait(c, headers, task, cancel)
                for u in urls[:1]:
                    try:
                        raw, ctype = self._fetch(u)
                    except FetchRefused as e:
                        raise ProviderError("download", f"result download refused: {e}") from e
                    out.append(ImageResult(raw, ctype or "image/png", {"provider_model": m.slug, "remote_task_id": task}))
        return out

    def _wait(self, client, headers, task: str, cancel) -> list[str]:
        waited, i = 0.0, 0
        while waited <= self.max_wait_s:
            if cancel is not None and cancel.is_set():
                raise ProviderError("cancelled", f"stopped locally; Kie task {task} may still run and be billed")
            r = request_with_retry(client, "GET", f"{BASE}/api/v1/jobs/recordInfo", headers=headers,
                                   params={"taskId": task}, sleep=self._sleep)
            data = self._body(r).get("data", {})
            state = data.get("state")
            if state == "success":
                try:
                    urls = json.loads(data.get("resultJson") or "{}").get("resultUrls", [])
                except ValueError as e:
                    raise ProviderError("bad_response", "Kie returned unreadable results") from e
                if not urls:
                    raise ProviderError("no_image", "Kie finished without an image")
                return [u for u in urls if isinstance(u, str)]
            if state == "fail":
                raise ProviderError("generation_failed", f"Kie task failed: {str(data.get('failMsg', ''))[:200]}")
            delay = self.poll_schedule[min(i, len(self.poll_schedule) - 1)]
            self._sleep(delay)
            waited += delay
            i += 1
        raise ProviderError("timeout", f"Kie task {task} did not finish in time; it may still complete and be billed")
