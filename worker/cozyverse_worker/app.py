"""HTTP API (contracts/openapi.yaml). Loopback only; bearer session token on every route."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Dict, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import MAX_BODY_BYTES, MAX_RESOLUTION, MIN_RESOLUTION, Settings
from .creation import Blocked, CreationError, NeedsApproval
from .secret_store import SecretError, SecretStore
from .styles import StyleError
from .inventory import CATEGORIES, GOALS, KINDS, STATUSES, VISIBILITY, InvalidItem
from .mesh_import import MAX_GLB_BYTES, MeshRejected
from .security import ID_RE, PathEscape, token_ok
from .service import Conflict, NotFound, Service
from .sources import MAX_BYTES as MAX_IMAGE_BYTES
from .sources import ImageRejected

ViewName = Literal["front", "back", "left", "right", "perspective"]
Policy = Literal["none", "building_foundation", "local_plinth", "scene_context", "full_master_base"]
SceneMode = Literal["DIORAMA", "REGULAR_IMAGE"]
OutputGoal = Literal["2D_LAYER_EXPORT", "REFERENCE_ONLY", "3D_PROXY", "3D_RECONSTRUCTION", "SCENE_REASSEMBLY"]
BaseMode = Literal["NONE", "FOUNDATION", "LOCAL_PLINTH", "SCENE_CONTEXT", "MASTER_DIORAMA"]
Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]

ALLOWED_ORIGINS = [
    "http://localhost:1420", "http://127.0.0.1:1420",      # vite dev
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",  # packaged app
]

# Per-route request body limits (bytes). Everything else is capped at MAX_BODY_BYTES.
BODY_LIMITS = [
    (re.compile(r"^/projects/[^/]+/sources$"), MAX_IMAGE_BYTES + 1024),
    (re.compile(r"^/assets/[^/]+/mesh$"), MAX_GLB_BYTES + 1024),
    (re.compile(r"^/providers/[^/]+/key$"), 4096),
    (re.compile(r"^/projects/[^/]+/generate(/estimate)?$"), 1_500_000),
    (re.compile(r"^/styles/import$"), 1_000_000),
]


def body_limit_for(path: str) -> int:
    for rx, lim in BODY_LIMITS:
        if rx.match(path):
            return lim
    return MAX_BODY_BYTES


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    fixtures: list[Literal["bar", "master_base", "calibration_cube", "chart"]] = Field(default_factory=list, max_length=8)
    scene_mode: SceneMode = "DIORAMA"
    output_goal: OutputGoal = "3D_RECONSTRUCTION"


class ProjectPatch(Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    scene_mode: Optional[SceneMode] = None
    output_goal: Optional[OutputGoal] = None
    base_mode: Optional[BaseMode] = None
    style_recipe_id: Optional[str] = Field(default=None, max_length=64)


class BuildProxyBody(Strict):
    """Free-form proxy parameters, validated per category by proxy_spec.clean_params."""
    params: Dict[str, float | int | str | bool] = Field(default_factory=dict, max_length=24)


class RenderBody(Strict):
    resolution: int = Field(default=1024, ge=MIN_RESOLUTION, le=MAX_RESOLUTION)
    background: Literal["white", "transparent"] = "white"
    engine: Literal["CYCLES", "WORKBENCH", "EEVEE"] = "CYCLES"
    samples: int = Field(default=64, ge=1, le=1024)
    base_policies: Optional[Dict[ViewName, Policy]] = None
    views: Optional[list[ViewName]] = Field(default=None, max_length=5)
    perspective_quadrant: Literal["front_left", "front_right", "rear_left", "rear_right"] = "front_left"
    perspective_elevation_degrees: float = Field(default=30.0, ge=0.0, lt=90.0)
    perspective_focal_mm: float = Field(default=50.0, ge=10.0, le=300.0)


class ReviewBody(Strict):
    status: Literal["PASSED", "FAILED", "NEEDS_REVIEW"]
    note: str = Field(default="", max_length=500)


class BlenderPathBody(Strict):
    path: str = Field(min_length=1, max_length=520)


Box = Optional[list[float]]


class InventoryCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    category: Category = "other"
    bbox: Box = Field(default=None, min_length=4, max_length=4)
    polygon: Optional[list[list[float]]] = Field(default=None, max_length=500)
    parent_id: Optional[str] = Field(default=None, max_length=64)
    count: int = Field(default=1, ge=1, le=9999)
    visibility: Literal[VISIBILITY] = "partially_visible"  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    scene_zone: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)
    status: Literal[STATUSES] = "confirmed"  # type: ignore[valid-type]
    goal: Literal[GOALS] = "3D_RECONSTRUCTION"  # type: ignore[valid-type]
    kind: Literal[KINDS] = "modelable"  # type: ignore[valid-type]
    source_revision_id: Optional[str] = Field(default=None, max_length=64)


class InventoryPatch(Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[Category] = None
    bbox: Box = Field(default=None, min_length=4, max_length=4)
    polygon: Optional[list[list[float]]] = Field(default=None, max_length=500)
    parent_id: Optional[str] = Field(default=None, max_length=64)
    count: Optional[int] = Field(default=None, ge=1, le=9999)
    visibility: Optional[Literal[VISIBILITY]] = None  # type: ignore[valid-type]
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scene_zone: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[Literal[STATUSES]] = None  # type: ignore[valid-type]
    goal: Optional[Literal[GOALS]] = None  # type: ignore[valid-type]
    kind: Optional[Literal[KINDS]] = None  # type: ignore[valid-type]


class MergeBody(Strict):
    ids: list[str] = Field(min_length=2, max_length=50)
    into: str = Field(max_length=64)


class InstanceBody(Strict):
    bbox: list[float] = Field(min_length=4, max_length=4)
    polygon: Optional[list[list[float]]] = Field(default=None, max_length=500)


class OrientationBody(Strict):
    yaw_deg: Literal[0, 90, 180, 270] = 0
    target_height: Optional[float] = Field(default=None, ge=0.05, le=100.0)


class KeyBody(Strict):
    key: str = Field(min_length=8, max_length=512)


class PriceBody(Strict):
    model_id: str = Field(max_length=120)
    usd: Optional[float] = Field(default=None, ge=0, le=1000)


class BudgetBody(Strict):
    daily_cap_usd: Optional[float] = Field(default=None, ge=0, le=100000)
    monthly_cap_usd: Optional[float] = Field(default=None, ge=0, le=100000)
    local_only: Optional[bool] = None


class EstimateBody(Strict):
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=2000)
    style_id: Optional[str] = Field(default=None, max_length=64)
    style_strength: Optional[float] = Field(default=None, ge=0, le=1)
    model_id: str = Field(max_length=120)
    aspect_ratio: str = Field(default="1:1", max_length=8)
    resolution: str = Field(default="1K", max_length=8)
    n: int = Field(default=1, ge=1, le=4)
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)
    reference_ids: list[str] = Field(default_factory=list, max_length=8)
    base_source_id: Optional[str] = Field(default=None, max_length=64)
    mask_png_b64: Optional[str] = Field(default=None, max_length=1_000_000)
    mode: Literal["generate", "edit"] = "generate"
    transparent: bool = False


class GenerateBody(Strict):
    estimate_id: str = Field(max_length=64)
    approved: bool = False
    mask_png_b64: Optional[str] = Field(default=None, max_length=1_000_000)


class LocalEditBody(Strict):
    op: Literal["crop", "text_overlay", "flip"]
    box: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)
    text: Optional[str] = Field(default=None, max_length=80)
    x: Optional[float] = Field(default=None, ge=0, le=1)
    y: Optional[float] = Field(default=None, ge=0, le=1)
    size: Optional[int] = Field(default=None, ge=8, le=400)
    color: Optional[str] = Field(default=None, max_length=7)


class StyleBody(Strict):
    name: str = Field(min_length=1, max_length=80)
    medium: str = Field(default="", max_length=60)
    dimensionality: Literal["2D", "2.5D", "3D", "photo"] = "2D"
    palette: str = Field(default="", max_length=400)
    lighting: str = Field(default="", max_length=400)
    linework: str = Field(default="", max_length=400)
    texture: str = Field(default="", max_length=400)
    camera: str = Field(default="", max_length=400)
    negative_constraints: list[str] = Field(default_factory=list, max_length=20)
    reference_ids: list[str] = Field(default_factory=list, max_length=8)
    strength: float = Field(default=0.7, ge=0, le=1)
    blend: list[dict] = Field(default_factory=list, max_length=4)


class FavoriteBody(Strict):
    on: bool = True


class CompileBody(Strict):
    style_id: Optional[str] = Field(default=None, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=2000)
    scene_mode: SceneMode = "REGULAR_IMAGE"
    model_id: Optional[str] = Field(default=None, max_length=120)
    strength: Optional[float] = Field(default=None, ge=0, le=1)


class BodyLimit:
    """Pure ASGI middleware: reject request bodies larger than the per-route limit."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = body_limit_for(scope["path"])
        for k, v in scope["headers"]:
            if k == b"content-length" and int(v or 0) > limit:
                return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
        total = 0

        async def limited():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:
                    raise HTTPException(413, "request too large")
            return msg

        await self.app(scope, limited, send)


def create_app(settings: Settings, secret_store: Optional[SecretStore] = None, registry: Optional[dict] = None) -> FastAPI:
    svc = Service(settings, secret_store, registry)

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else None
        if not token_ok(supplied, settings.token):
            raise HTTPException(401, "unauthorized")

    @asynccontextmanager
    async def lifespan(_app):
        yield
        svc.shutdown()

    app = FastAPI(title="CozyVerse Local Worker API", version=__version__, dependencies=[Depends(auth)],
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = svc
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["authorization", "content-type"], allow_credentials=False)
    app.add_middleware(BodyLimit)

    def _msg(status: int):
        async def handler(_r, e):  # noqa: ANN001
            return JSONResponse({"detail": str(e)}, status_code=status)
        return handler

    app.add_exception_handler(NotFound, lambda r, e: JSONResponse({"detail": f"{e} not found"}, status_code=404))
    app.add_exception_handler(LookupError, lambda r, e: JSONResponse({"detail": f"{e} not found"}, status_code=404))
    app.add_exception_handler(Conflict, _msg(409))
    for exc in (InvalidItem, ImageRejected, MeshRejected):
        app.add_exception_handler(exc, _msg(400))
    app.add_exception_handler(Blocked, _msg(409))
    app.add_exception_handler(NeedsApproval, _msg(428))
    app.add_exception_handler(CreationError, _msg(400))
    app.add_exception_handler(StyleError, _msg(400))
    app.add_exception_handler(SecretError, lambda r, e: JSONResponse({"detail": "the Windows credential store is unavailable"}, status_code=500))
    app.add_exception_handler(ValueError, lambda r, e: JSONResponse({"detail": "invalid request"}, status_code=400))
    app.add_exception_handler(PathEscape, lambda r, e: JSONResponse({"detail": "invalid path"}, status_code=400))

    def _id(value: str) -> str:
        if not ID_RE.match(value):
            raise HTTPException(400, "invalid identifier")
        return value

    # ---------------- system ----------------
    @app.get("/health")
    def health():
        b = svc.blender_info()
        return {"status": "ok", "version": __version__,
                "blender": {k: b[k] for k in ("found", "version", "path", "device")}}

    @app.get("/settings/blender")
    def get_blender():
        return svc.blender_info()

    @app.put("/settings/blender")
    def put_blender(body: BlenderPathBody):
        return svc.set_blender_path(body.path)

    # ---------------- providers, models, costs ----------------
    @app.get("/providers")
    def providers():
        return svc.providers.listing()

    @app.put("/providers/{provider_id}/key", status_code=204)
    def put_key(provider_id: str, body: KeyBody):
        svc.providers.set_key(_id(provider_id), body.key)

    @app.delete("/providers/{provider_id}/key", status_code=204)
    def del_key(provider_id: str):
        svc.providers.delete_key(_id(provider_id))

    @app.post("/providers/{provider_id}/refresh")
    def refresh_provider(provider_id: str):
        try:
            return svc.providers.refresh(_id(provider_id))
        except Exception as e:  # noqa: BLE001 - provider errors are user-facing, keys never appear in them
            from .providers import ProviderError
            if isinstance(e, ProviderError):
                raise Conflict(str(e)) from e
            raise

    @app.get("/models/recommend")
    def recommend(goal: Literal["quick_draft", "quality", "text", "character", "budget"] = "quality"):
        return svc.providers.recommend(goal)

    @app.get("/settings/budget")
    def get_budget():
        return svc.providers.budget()

    @app.put("/settings/budget")
    def put_budget(body: BudgetBody):
        return svc.providers.set_budget(body.daily_cap_usd, body.monthly_cap_usd, body.local_only)

    @app.put("/settings/prices", status_code=204)
    def put_price(body: PriceBody):
        svc.providers.set_price(body.model_id, body.usd)

    @app.get("/settings/usage")
    def usage():
        return svc.providers.usage()

    # ---------------- styles ----------------
    @app.get("/styles")
    def list_styles():
        return svc.styles.list()

    @app.post("/styles", status_code=201)
    def create_style(body: StyleBody):
        return svc.styles.create(body.model_dump())

    @app.get("/styles/export")
    def export_styles():
        return svc.styles.export_recipes()

    @app.post("/styles/import", status_code=201)
    def import_styles(doc: dict):
        return svc.styles.import_recipes(doc)

    @app.post("/styles/compile")
    def compile_style(body: CompileBody):
        caps = {}
        if body.model_id:
            _p, m = svc.providers.model(body.model_id)
            caps = {"max_prompt_chars": m.caps.max_prompt_chars, "negative_prompt": m.caps.negative_prompt}
        return svc.styles.compile(body.style_id, body.prompt, body.negative_prompt, body.scene_mode, caps,
                                  body.strength).as_dict()

    @app.put("/styles/{style_id}")
    def update_style(style_id: str, body: StyleBody):
        return svc.styles.update(_id(style_id), body.model_dump())

    @app.delete("/styles/{style_id}", status_code=204)
    def delete_style(style_id: str):
        svc.styles.delete(_id(style_id))

    @app.post("/styles/{style_id}/favorite", status_code=204)
    def favorite_style(style_id: str, body: FavoriteBody):
        svc.styles.favorite(_id(style_id), body.on)

    # ---------------- create / edit images ----------------
    @app.post("/projects/{project_id}/generate/estimate")
    def estimate(project_id: str, body: EstimateBody):
        svc.get_project(_id(project_id))
        return svc.creator.estimate(project_id, body.model_dump())

    @app.post("/projects/{project_id}/generate", status_code=202)
    def generate(project_id: str, body: GenerateBody):
        svc.get_project(_id(project_id))
        return {"job_id": svc.creator.generate(project_id, _id(body.estimate_id), body.approved, body.mask_png_b64)}

    @app.post("/projects/{project_id}/sources/{source_id}/edit-local", status_code=201)
    def edit_local(project_id: str, source_id: str, body: LocalEditBody):
        svc.get_project(_id(project_id))
        op = body.model_dump(exclude_none=True)
        return svc.creator.edit_local(project_id, _id(source_id), op)

    # ---------------- projects ----------------
    @app.post("/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return svc.create_project(body.name, body.fixtures, body.scene_mode, body.output_goal)

    @app.get("/projects")
    def list_projects():
        return svc.list_projects()

    @app.get("/projects/{project_id}")
    def get_project(project_id: str):
        return svc.get_project(_id(project_id))

    @app.patch("/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch):
        return svc.update_project(_id(project_id), body.model_dump(exclude_unset=True, exclude_none=True))

    @app.get("/projects/{project_id}/assets")
    def list_assets(project_id: str):
        return svc.list_assets(_id(project_id))

    # ---------------- sources ----------------
    @app.post("/projects/{project_id}/sources", status_code=201)
    async def add_source(project_id: str, request: Request):
        data = await request.body()
        return svc.add_source(_id(project_id), data, "IMPORTED")

    @app.get("/projects/{project_id}/sources")
    def list_sources(project_id: str):
        svc.get_project(_id(project_id))
        return svc.sources.list(project_id)

    @app.get("/sources/{source_id}")
    def get_source(source_id: str):
        return svc.sources.get(_id(source_id))

    @app.get("/sources/{source_id}/lineage")
    def source_lineage(source_id: str):
        return svc.sources.lineage(_id(source_id))

    @app.get("/sources/{source_id}/image")
    def source_image(source_id: str, thumb: bool = False):
        p = svc.sources.path(_id(source_id), thumb=thumb)
        return FileResponse(p, media_type="image/webp" if thumb else None,
                            headers={"Cache-Control": "no-store", "Content-Disposition": "inline",
                                     "X-Content-Type-Options": "nosniff"})

    @app.post("/projects/{project_id}/sources/{source_id}/approve")
    def approve_source(project_id: str, source_id: str):
        return svc.approve_source(_id(project_id), _id(source_id))

    # ---------------- inventory ----------------
    @app.post("/projects/{project_id}/inventory", status_code=201)
    def create_item(project_id: str, body: InventoryCreate):
        return svc.create_inventory(_id(project_id), body.model_dump(exclude_unset=True))

    @app.get("/projects/{project_id}/inventory")
    def inventory_tree(project_id: str):
        return svc.inventory_tree(_id(project_id))

    @app.get("/projects/{project_id}/inventory/history")
    def inventory_history(project_id: str):
        svc.get_project(_id(project_id))
        return svc.inv.history(project_id)

    @app.post("/projects/{project_id}/inventory/merge")
    def merge_items(project_id: str, body: MergeBody):
        return svc.merge_inventory(_id(project_id), [_id(i) for i in body.ids], _id(body.into))

    @app.patch("/inventory/{asset_id}")
    def patch_item(asset_id: str, body: InventoryPatch):
        return svc.update_inventory(_id(asset_id), body.model_dump(exclude_unset=True, exclude_none=True))

    @app.delete("/inventory/{asset_id}", status_code=204)
    def delete_item(asset_id: str):
        svc.delete_inventory(_id(asset_id))

    @app.post("/inventory/{asset_id}/instances", status_code=201)
    def add_instance(asset_id: str, body: InstanceBody):
        svc.get_asset(_id(asset_id))
        return svc.inv.add_instance(asset_id, body.bbox, body.polygon)

    @app.get("/inventory/{asset_id}/instances")
    def instances(asset_id: str):
        svc.get_asset(_id(asset_id))
        return svc.inv.instances(asset_id)

    @app.get("/inventory/{asset_id}/crop")
    def crop(asset_id: str, kind: Literal["crop", "mask"] = Query("crop")):
        a = svc.get_asset(_id(asset_id))
        p = svc.inv.crop_path(a, f"{kind}.png")
        if not p.is_file():
            raise NotFound("crop")
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})

    # ---------------- proxy / mesh ----------------
    @app.post("/assets/{asset_id}/build-proxy", status_code=202)
    def build_proxy(asset_id: str, body: Optional[BuildProxyBody] = None):
        params = body.params if body and body.params else None
        return {"job_id": svc.build_proxy(_id(asset_id), params)}

    @app.post("/assets/{asset_id}/mesh", status_code=201)
    async def import_mesh(asset_id: str, request: Request):
        return svc.import_mesh(_id(asset_id), await request.body())

    @app.put("/assets/{asset_id}/mesh/orientation")
    def mesh_orientation(asset_id: str, body: OrientationBody):
        return svc.set_mesh_orientation(_id(asset_id), body.yaw_deg, body.target_height)

    @app.post("/assets/{asset_id}/mesh/approve")
    def mesh_approve(asset_id: str):
        return svc.approve_mesh(_id(asset_id))

    # ---------------- rendering ----------------
    @app.post("/assets/{asset_id}/render-five", status_code=202)
    def render_five(asset_id: str, body: Optional[RenderBody] = None):
        opts = (body or RenderBody()).model_dump()
        return {"job_id": svc.render_five(_id(asset_id), opts)}

    @app.post("/assets/{asset_id}/views/{view}/review")
    def review(asset_id: str, view: ViewName, body: ReviewBody):
        return svc.review_view(_id(asset_id), view, body.status, body.note)

    @app.get("/assets/{asset_id}/views/{view}/image")
    def image(asset_id: str, view: ViewName):
        return FileResponse(svc.view_file(_id(asset_id), view), media_type="image/png",
                            headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})

    # ---------------- jobs / export ----------------
    @app.get("/jobs/{job_id}")
    def job(job_id: str):
        return svc.get_job(_id(job_id))

    @app.delete("/jobs/{job_id}", status_code=202)
    def cancel(job_id: str):
        return svc.cancel_job(_id(job_id))

    @app.post("/projects/{project_id}/export", status_code=202)
    def export(project_id: str):
        return {"job_id": svc.export_project(_id(project_id))}

    return app
