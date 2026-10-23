"""Provider contract: capability-gated image generation. No silent fallback, no hidden rewrites."""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

import httpx

STATUSES = ("verified", "available", "not_entitled", "deprecated", "disabled", "experimental", "unknown")
ASPECTS = ["1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class Capabilities:
    generate: bool = True
    edit: bool = False
    mask_edit: bool = False
    aspect_ratios: list[str] = field(default_factory=lambda: list(ASPECTS))
    resolutions: list[str] = field(default_factory=lambda: ["1K"])
    max_refs: int = 0
    max_variants: int = 1
    seed: bool = False
    negative_prompt: bool = False
    transparency: bool = False
    max_prompt_chars: Optional[int] = None
    async_task: bool = False


@dataclass
class ModelInfo:
    id: str                    # our stable id: "<provider>:<slug>"
    slug: str                  # what the provider API expects
    label: str
    status: str = "unknown"
    status_detail: str = ""
    caps: Capabilities = field(default_factory=Capabilities)
    source_url: str = ""
    last_checked: Optional[float] = None

    def public(self) -> dict:
        d = asdict(self)
        d["caps"] = asdict(self.caps)
        return d


@dataclass
class CostEstimate:
    usd_min: Optional[float]
    usd_max: Optional[float]
    unit: str                  # "image", "free", "unknown"
    exact: bool
    note: str = ""
    free: bool = False

    def public(self) -> dict:
        return asdict(self)


@dataclass
class GenerateRequest:
    prompt: str
    model_id: str
    negative_prompt: str = ""
    aspect_ratio: str = "1:1"
    resolution: str = "1K"
    n: int = 1
    seed: Optional[int] = None
    references: list[bytes] = field(default_factory=list)
    mask: Optional[bytes] = None
    mode: str = "generate"     # or "edit"
    transparent: bool = False
    scene_mode: str = "REGULAR_IMAGE"


@dataclass
class ImageResult:
    data: bytes
    mime: str
    meta: dict[str, Any] = field(default_factory=dict)


Sleeper = Callable[[float], None]


def request_with_retry(client: httpx.Client, method: str, url: str, *, headers: dict, retries: int = 3,
                       sleep: Sleeper = time.sleep, **kw) -> httpx.Response:
    """Exponential backoff with jitter for 429/5xx and transport errors. Errors never echo request headers."""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        delay: Optional[float] = None
        try:
            r = client.request(method, url, headers=headers, **kw)
        except httpx.TransportError as e:
            last = e
        else:
            if r.status_code == 429 or r.status_code >= 500:
                last = ProviderError("http_" + str(r.status_code), f"provider returned HTTP {r.status_code}", True)
                ra = r.headers.get("retry-after")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
            else:
                return r
        if attempt < retries:
            sleep((delay if last and isinstance(last, ProviderError) and delay else 2 ** attempt) + random.random() * 0.25)
    if isinstance(last, ProviderError):
        raise last
    raise ProviderError("network", "could not reach the provider", True)


def map_http_error(r: httpx.Response) -> ProviderError:
    detail = ""
    try:
        j = r.json()
        err = j.get("error", j) if isinstance(j, dict) else {}
        detail = str(err.get("message") or err.get("msg") or "")[:240] if isinstance(err, dict) else ""
    except ValueError:
        pass
    s = r.status_code
    if s in (401, 403):
        return ProviderError("auth", "the provider rejected the API key or your account is not entitled to this model")
    if s == 404:
        return ProviderError("model_not_found", "the provider does not know this model or endpoint")
    if s == 400 or s == 422:
        return ProviderError("bad_request", "the provider rejected the request" + (f": {detail}" if detail else ""))
    if s == 402:
        return ProviderError("billing", "the provider reports a billing or credit problem")
    return ProviderError("http_" + str(s), f"provider returned HTTP {s}")


class ImageProvider(ABC):
    id: str = ""
    label: str = ""
    needs_key: bool = True
    docs_url: str = ""

    def __init__(self, client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=120.0),
                 sleep: Sleeper = time.sleep):
        self._client_factory = client_factory
        self._sleep = sleep

    @abstractmethod
    def models(self) -> list[ModelInfo]: ...

    def model(self, model_id: str) -> ModelInfo:
        for m in self.models():
            if m.id == model_id:
                return m
        raise ProviderError("model_not_found", "unknown model")

    @abstractmethod
    def health_check(self, key: Optional[str]) -> dict: ...

    @abstractmethod
    def generate(self, req: GenerateRequest, key: Optional[str], cancel: Optional[Any] = None) -> list[ImageResult]: ...

    def estimate(self, req: GenerateRequest, price_per_image: Optional[float]) -> CostEstimate:
        if price_per_image is None:
            return CostEstimate(None, None, "unknown", False,
                                "The app does not know this model's current price. Check the provider's pricing page "
                                "or enter your own per-image price in Models and Costs. The provider will bill your account.")
        t = price_per_image * req.n
        return CostEstimate(t, t, "image", False, "Based on the per-image price you entered; the final bill may differ.")

    def validate_request(self, req: GenerateRequest) -> list[str]:
        """Capability gate. Returns problems; empty means the request is supported."""
        m = self.model(req.model_id)
        c, problems = m.caps, []
        if m.status in ("disabled", "deprecated", "not_entitled"):
            problems.append(f"{m.label} is {m.status.replace('_', ' ')}: {m.status_detail or 'choose another model'}")
        if req.mode == "generate" and not c.generate:
            problems.append("this model cannot generate images")
        if req.mode == "edit" and not c.edit:
            problems.append("this model cannot edit images")
        if req.mask is not None and not c.mask_edit:
            problems.append("this model does not support mask editing")
        if req.aspect_ratio not in c.aspect_ratios:
            problems.append(f"aspect ratio {req.aspect_ratio} is not supported")
        if req.resolution not in c.resolutions:
            problems.append(f"resolution {req.resolution} is not supported")
        if len(req.references) > c.max_refs and req.mode == "generate":
            problems.append(f"at most {c.max_refs} reference images are supported")
        if not 1 <= req.n <= c.max_variants:
            problems.append(f"between 1 and {c.max_variants} variations are supported")
        if req.seed is not None and not c.seed:
            problems.append("this model does not support a seed")
        if req.transparent and not c.transparency:
            problems.append("this model cannot make transparent images")
        if c.max_prompt_chars and len(req.prompt) > c.max_prompt_chars:
            problems.append(f"prompt is longer than {c.max_prompt_chars} characters")
        return problems
