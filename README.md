# CozyVerse Breakdown (Cozyverse2D)

Windows desktop tool that turns a diorama or regular image into an asset inventory and a canonical 3D proxy, then renders **five separate images per asset** from that one model: **front, back, left, right** (strict orthographic Blender cameras) and **perspective** (independent 3/4 camera). Spec: `C:\Users\eduni\Desktop\CozyVerse_Breakdown_Agent_Kit` (docs 01-20).

Stack: Tauri 2 + React/TypeScript, local Python FastAPI worker, SQLite, Blender Python adapter.

## Status: Phase 0 (camera proof) complete, Phase 1 not started

Working and tested:
- Editable proxy specs: asymmetric `BLD-003` BAR (rear door, left red vent, right blue AC, back-right roof tank, front signs/awning), `BASE-001` master base, calibration cube, neutral test chart.
- Camera rig built from explicit rotation matrices (zero tilt/roll), shared ortho scale, >= 8% margin per side, true perspective fifth camera (quadrant, elevation, focal configurable).
- One Blender process per view, one PNG per process, written to temp then renamed. Metadata sidecar per view holds the camera matrices read back from Blender, revision hash, engine, pid, call id.
- Base policies `none | building_foundation | local_plinth | scene_context | full_master_base`, proven by object-class mask renders (plinth never leaks into elevations; master base only on request).
- Worker API per `contracts/openapi.yaml` plus review/settings endpoints: bearer token on every route, loopback only, strict schemas, 1 MB body cap, single-Blender job queue, cancel kills the process tree, restart marks interrupted jobs.
- Review gate: renders land as `DRAFT`/`NEEDS_REVIEW`; only a human click sets `PASSED`. Changing the model invalidates every view (`STALE`). Export is `PARTIAL` until every asset is `PASSED`.
- Schema-valid ZIP export (`asset.json`, per-view camera/render-job JSON, mesh GLB + spec, QA report, scene + base manifests, SHA256 read-back).
- Desktop shell: Tauri host starts the worker with a random port and session token, splashscreen, app icon, `Render 5 Views`, five view cards, jobs drawer, Open Folder, Export ZIP.

## Run

Prereqs: Windows 11, Node 20+, Rust (MSVC), Python 3.11, Blender 4.x/5.x installed (auto-detected, or set the path in the UI).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" pydantic pillow numpy jsonschema pytest httpx
cd apps\desktop
npm install
npm run tauri dev
```

Tests (real Blender renders, about 2 minutes):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Evidence run (Cycles, 1024 px, BAR + BASE-001, ZIP): `.\.venv\Scripts\python.exe scripts\phase0_demo.py` writes `evidence/phase0/`.

## Verified in this session (Windows 11, Blender 5.2.2 LTS, CPU)

- `pytest`: 43 passed (camera math, per-face leak tests, matrices from Blender, separate invocations, base policies, Cycles white background, neutral chart, API golden path, cancel with no orphan process, restart reopen, input validation).
- `phase0_demo.py`: BLD-003 five views in 30 s, BASE-001 in 22 s, export status COMPLETE after review.
- UI driven in a browser against the real worker: build proxy, render 5 views (Cycles 1024), export reports `PARTIAL` before review.
- Tauri dev app launched: worker started by the Rust host, window reached the studio, unauthenticated request to the worker returns 401, closing the window leaves no worker process.

## Known limitations (not done)

- No installer yet. `tauri build` was not run; the packaged app needs a bundled Python runtime for the worker (release build currently reports it missing). Blender is not bundled.
- `.cvbd` file association is declared, but the app does not open a project file yet, and the supplied file icon (`apps/desktop/src-tauri/icons/file.ico`) is not wired in; Tauri has no per-association icon, so this needs an NSIS hook.
- No per-view re-render (the whole set renders together), no GPU device selection (CPU Cycles), no import of existing meshes, no image import, no vision/AI, no style engine or provider adapters (Phases 1 to 3).
- Visual artifact QA is limited to dimensions, white background, margin/cropping and camera checks. Yellow-cast, noise and waxy-skin analysis from doc 15 is not implemented.
- Blender 5.2 LTS is used instead of the kit's 4.x (see `docs/adr/0001-blender-version-and-layout.md`).
- Nothing has been pushed to GitHub.

## Branding

`branding/` holds the supplied app icon, file icon and splashscreen (plus transparent-corner versions used for the Windows icon set).

## Security

Design walk: `docs/security/phase0-design.md`. Review record: `docs/security/phase0-review.md`.
