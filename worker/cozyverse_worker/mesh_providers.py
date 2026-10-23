"""Image-to-3D providers (doc 06 interface): capabilities, estimate, submit, poll, cancel, download, validate.

Meshy follows https://docs.meshy.ai/en/api/image-to-3d (checked 2026-10-23). Tripo is registered but disabled: its
current request format could not be verified, and a wrong guess would bill real credits.
"""
from __future__ import annotations

import base64
import json
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from .mesh_import import MeshRejected, validate_glb
from .providers.base import (CostEstimate, ModelInfo, ProviderError, Sleeper, map_http_error, request_with_retry)
from .providers.base import Capabilities
from .safe_fetch import FetchRefused, fetch_bytes

MESHY_BASE = "https://api.meshy.ai"
MESHY_DOCS = "https://docs.meshy.ai/en/api/image-to-3d"


@dataclass
class MeshRequest:
    image_png: bytes
    options: dict = field(default_factory=dict)      # target_polycount, texture, pbr, pose_mode


@dataclass
class MeshTask:
    state: str                                        # queued | running | succeeded | failed | cancelled
    progress: float = 0.0
    glb_url: Optional[str] = None
    error: str = ""
    credits: Optional[float] = None
    glb_bytes: Optional[bytes] = None                 # only the mock returns bytes directly


@dataclass
class CancelResult:
    supported: bool
    note: str


class MeshProvider(ABC):
    id: str = ""
    label: str = ""
    needs_key: bool = True
    docs_url: str = ""

    def __init__(self, client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=120.0),
                 sleep: Sleeper = time.sleep, fetcher: Callable[..., tuple[bytes, str]] = fetch_bytes):
        self._client_factory, self._sleep, self._fetch = client_factory, sleep, fetcher

    @abstractmethod
    def models(self) -> list[ModelInfo]: ...

    def model(self, model_id: str) -> ModelInfo:
        for m in self.models():
            if m.id == model_id:
                return m
        raise ProviderError("model_not_found", "unknown model")

    @abstractmethod
    def health_check(self, key: Optional[str]) -> dict: ...

    def estimate(self, req: MeshRequest, price: Optional[float]) -> CostEstimate:
        if price is None:
            return CostEstimate(None, None, "unknown", False,
                                "The app does not know the current price of a mesh task. Check the provider's pricing "
                                "page or enter your own per-task price in Models and Costs.")
        return CostEstimate(price, price, "task", False, "Based on the per-task price you entered.")

    @abstractmethod
    def submit(self, req: MeshRequest, key: Optional[str]) -> str: ...

    @abstractmethod
    def poll(self, task_id: str, key: Optional[str]) -> MeshTask: ...

    @abstractmethod
    def cancel(self, task_id: str, key: Optional[str]) -> CancelResult: ...

    def download(self, task: MeshTask) -> bytes:
        if task.glb_bytes is not None:
            return task.glb_bytes
        if not task.glb_url:
            raise ProviderError("no_model", "the provider finished without a model file")
        try:
            data, _ = self._fetch(task.glb_url, allowed_types=("model/", "application/octet-stream", "application/"),
                                  max_bytes=100 * 1024 * 1024)
        except FetchRefused as e:
            raise ProviderError("download", f"model download refused: {e}") from e
        return data

    @staticmethod
    def validate_mesh(data: bytes) -> dict:
        try:
            return validate_glb(data)
        except MeshRejected as e:
            raise ProviderError("bad_model", f"the downloaded model was rejected: {e}") from e


class MeshyProvider(MeshProvider):
    id = "meshy"
    label = "Meshy (image to 3D)"
    docs_url = MESHY_DOCS

    def models(self) -> list[ModelInfo]:
        caps = Capabilities(generate=True, edit=False, async_task=True, max_refs=1, resolutions=["standard"])
        return [ModelInfo("meshy:image-to-3d", "image-to-3d", "Meshy Image to 3D", "unknown",
                          "Charges credits per task. Reconstructs unseen sides by guessing.", caps, MESHY_DOCS)]

    @staticmethod
    def _h(key: Optional[str]) -> dict:
        if not key:
            raise ProviderError("no_key", "no Meshy API key is saved")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def health_check(self, key):
        with self._client_factory() as c:
            r = request_with_retry(c, "GET", f"{MESHY_BASE}/openapi/v1/image-to-3d", headers=self._h(key),
                                   params={"page_size": 1}, sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        return {"ok": True, "visible": ["image-to-3d"], "detail": "Key accepted. Run one task to verify the model."}

    def submit(self, req: MeshRequest, key):
        o = req.options
        body: dict[str, Any] = {"image_url": "data:image/png;base64," + base64.b64encode(req.image_png).decode(),
                                "model_type": "standard", "ai_model": "latest", "topology": "triangle",
                                "should_texture": bool(o.get("texture", True))}
        if o.get("pbr") and body["should_texture"]:
            body["enable_pbr"] = True
        if o.get("target_polycount"):
            body["target_polycount"] = int(o["target_polycount"])
        if o.get("pose_mode") in ("a-pose", "t-pose"):
            body["pose_mode"] = o["pose_mode"]
        with self._client_factory() as c:
            r = request_with_retry(c, "POST", f"{MESHY_BASE}/openapi/v1/image-to-3d", headers=self._h(key), json=body,
                                   sleep=self._sleep, retries=0)       # never auto-retry a billed create call
        if r.status_code not in (200, 202):
            raise map_http_error(r)
        tid = r.json().get("result")
        if not tid:
            raise ProviderError("bad_response", "Meshy did not return a task id")
        return str(tid)

    def poll(self, task_id, key):
        with self._client_factory() as c:
            r = request_with_retry(c, "GET", f"{MESHY_BASE}/openapi/v1/image-to-3d/{task_id}", headers=self._h(key),
                                   sleep=self._sleep)
        if r.status_code != 200:
            raise map_http_error(r)
        j = r.json()
        st = {"PENDING": "queued", "IN_PROGRESS": "running", "SUCCEEDED": "succeeded", "FAILED": "failed",
              "CANCELED": "cancelled"}.get(j.get("status"), "running")
        return MeshTask(st, float(j.get("progress", 0)) / 100.0, (j.get("model_urls") or {}).get("glb"),
                        str((j.get("task_error") or {}).get("message", ""))[:200], j.get("consumed_credits"))

    def cancel(self, task_id, key):
        with self._client_factory() as c:
            r = request_with_retry(c, "DELETE", f"{MESHY_BASE}/openapi/v1/image-to-3d/{task_id}", headers=self._h(key),
                                   sleep=self._sleep, retries=0)
        if r.status_code == 409:
            return CancelResult(False, "The task is already running and cannot be cancelled. Credits may still be charged.")
        if r.status_code == 200:
            return CancelResult(True, "Deleted. Credits are refunded only if the task had not started.")
        raise map_http_error(r)


class TripoProvider(MeshProvider):
    id = "tripo"
    label = "Tripo (image to 3D)"
    docs_url = "https://developers.tripo3d.com/en/docs/introduction"

    def models(self) -> list[ModelInfo]:
        return [ModelInfo("tripo:image-to-model", "image-to-model", "Tripo Image to Model", "disabled",
                          "Disabled: the current request format could not be verified from Tripo's documentation, and "
                          "a wrong request would bill real credits. It will be enabled once verified.",
                          Capabilities(generate=False, async_task=True), self.docs_url)]

    def health_check(self, key):
        raise ProviderError("unverified", "Tripo support is disabled until its request format is verified.")

    def submit(self, req, key):
        raise ProviderError("unverified", "Tripo support is disabled until its request format is verified.")

    def poll(self, task_id, key):
        raise ProviderError("unverified", "Tripo support is disabled until its request format is verified.")

    def cancel(self, task_id, key):
        return CancelResult(False, "Not available.")


# ----------------------------------------------------------------------------- mock (tests, offline demo)
def box_glb(w: float, h: float, d: float, rgb=(0.78, 0.5, 0.3)) -> bytes:
    """A valid binary glTF with one coloured box (Y up)."""
    x, y, z = w / 2, h, d / 2
    verts = [(-x, 0, -z), (x, 0, -z), (x, 0, z), (-x, 0, z), (-x, y, -z), (x, y, -z), (x, y, z), (-x, y, z)]
    idx = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]
    pos = b"".join(struct.pack("<3f", *v) for v in verts)
    ind = b"".join(struct.pack("<H", i) for i in idx)
    ind += b"\0" * (-len(ind) % 4)
    binary = pos + ind
    doc = {"asset": {"version": "2.0", "generator": "cozyverse-mock"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [*rgb, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                          "min": [-x, 0, -z], "max": [x, y, z]},
                         {"bufferView": 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx) * 2, "target": 34963}],
           "buffers": [{"byteLength": len(binary)}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class MockMeshProvider(MeshProvider):
    id = "mock_mesh"
    label = "Mock mesh (offline test generator)"
    needs_key = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._tasks: dict[str, bytes] = {}

    def models(self):
        return [ModelInfo("mock_mesh:box-v1", "box-v1", "Mock box mesh", "verified", "Built in. Not an AI model.",
                          Capabilities(generate=True, async_task=False, max_refs=1, resolutions=["standard"]), "")]

    def health_check(self, key):
        return {"ok": True, "visible": ["box-v1"], "detail": "built in"}

    def estimate(self, req, price):
        return CostEstimate(0.0, 0.0, "free", True, "Built-in generator. No cost.", free=True)

    def submit(self, req, key):
        import io
        from PIL import Image
        with Image.open(io.BytesIO(req.image_png)) as im:
            w, h = im.size
        tid = f"mock-{len(self._tasks) + 1}"
        self._tasks[tid] = box_glb(1.0, max(0.2, min(4.0, h / w)), 0.8)
        return tid

    def poll(self, task_id, key):
        if task_id not in self._tasks:
            raise ProviderError("not_found", "unknown task")
        return MeshTask("succeeded", 1.0, glb_bytes=self._tasks[task_id])

    def cancel(self, task_id, key):
        return CancelResult(False, "Mock tasks finish instantly.")


def build_mesh_registry(**kw) -> dict[str, MeshProvider]:
    return {p.id: p for p in (MockMeshProvider(**kw), MeshyProvider(**kw), TripoProvider(**kw))}
