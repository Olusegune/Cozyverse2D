"""HTTP API (contracts/openapi.yaml). Loopback only; bearer session token on every route."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Dict, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .config import MAX_BODY_BYTES, MAX_RESOLUTION, MIN_RESOLUTION, Settings
from .security import ID_RE, PathEscape, token_ok
from .service import VIEWS, Conflict, NotFound, Service, FIXTURES

ViewName = Literal["front", "back", "left", "right", "perspective"]
Policy = Literal["none", "building_foundation", "local_plinth", "scene_context", "full_master_base"]

ALLOWED_ORIGINS = [
    "http://localhost:1420", "http://127.0.0.1:1420",      # vite dev
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",  # packaged app
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    fixtures: list[Literal["bar", "master_base", "calibration_cube", "chart"]] = Field(
        default_factory=lambda: ["bar"], max_length=8)


class BuildProxyBody(Strict):
    plinth_margin: float = Field(default=0.5, ge=0.0, le=5.0)
    plinth_height: float = Field(default=0.2, ge=0.02, le=1.0)


class RenderBody(Strict):
    resolution: int = Field(default=1024, ge=MIN_RESOLUTION, le=MAX_RESOLUTION)
    background: Literal["white", "transparent"] = "white"
    engine: Literal["CYCLES", "WORKBENCH", "EEVEE"] = "CYCLES"
    samples: int = Field(default=64, ge=1, le=1024)
    base_policies: Optional[Dict[ViewName, Policy]] = None
    perspective_quadrant: Literal["front_left", "front_right", "rear_left", "rear_right"] = "front_left"
    perspective_elevation_degrees: float = Field(default=30.0, ge=0.0, lt=90.0)
    perspective_focal_mm: float = Field(default=50.0, ge=10.0, le=300.0)


class ReviewBody(Strict):
    status: Literal["PASSED", "FAILED", "NEEDS_REVIEW"]
    note: str = Field(default="", max_length=500)


class BlenderPathBody(Strict):
    path: str = Field(min_length=1, max_length=520)


class BodyLimit:
    """Pure ASGI middleware: reject request bodies larger than MAX_BODY_BYTES."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope["headers"]:
            if k == b"content-length" and int(v or 0) > self.limit:
                return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
        total = 0

        async def limited():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > self.limit:
                    raise HTTPException(413, "request too large")
            return msg

        await self.app(scope, limited, send)


def create_app(settings: Settings) -> FastAPI:
    svc = Service(settings)

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
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["authorization", "content-type"], allow_credentials=False)
    app.add_middleware(BodyLimit)

    @app.exception_handler(NotFound)
    async def _nf(_r, e):  # noqa: ANN001
        return JSONResponse({"detail": f"{e} not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def _cf(_r, e):  # noqa: ANN001
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _ve(_r, e):  # noqa: ANN001
        return JSONResponse({"detail": "invalid request"}, status_code=400)

    @app.exception_handler(PathEscape)
    async def _pe(_r, e):  # noqa: ANN001
        return JSONResponse({"detail": "invalid path"}, status_code=400)

    def _id(value: str) -> str:
        if not ID_RE.match(value):
            raise HTTPException(400, "invalid identifier")
        return value

    @app.get("/health")
    def health():
        b = svc.blender_info()
        return {"status": "ok", "version": __version__, "blender": {k: b[k] for k in ("found", "version", "path", "device")}}

    @app.get("/settings/blender")
    def get_blender():
        return svc.blender_info()

    @app.put("/settings/blender")
    def put_blender(body: BlenderPathBody):
        return svc.set_blender_path(body.path)

    @app.post("/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return svc.create_project(body.name, body.fixtures)

    @app.get("/projects")
    def list_projects():
        return svc.list_projects()

    @app.get("/projects/{project_id}/assets")
    def list_assets(project_id: str):
        return svc.list_assets(_id(project_id))

    @app.post("/assets/{asset_id}/build-proxy", status_code=202)
    def build_proxy(asset_id: str, body: Optional[BuildProxyBody] = None):
        params = body.model_dump() if body else None
        return {"job_id": svc.build_proxy(_id(asset_id), params)}

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
