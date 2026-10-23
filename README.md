# CozyVerse Breakdown (Cozyverse2D)

A Windows desktop studio that **creates or imports an image**, breaks it into assets, and renders **five separate images per asset** from one 3D model: **front, back, left, right** (strict orthographic Blender cameras) and **perspective** (an independent 3/4 camera). Output is ready for 3D tools such as Tripo and Meshy.

The 3D model, never an image generator, decides what each angle looks like. Spec: `C:\Users\eduni\Desktop\CozyVerse_Breakdown_Agent_Kit`. Stack: Tauri 2, React/TypeScript, a local Python FastAPI worker, SQLite, Blender.

## What it does

| Area | Working |
|---|---|
| **Create** | Diorama or Regular image from a prompt. 53 declarative styles across 2D, 2.5D, 3D and photo, user recipes with blending, exact-prompt preview, capability-gated size/variation/seed/reference options, built-in offline generator, adapters for Google Gemini (Interactions API), OpenAI Images and Kie |
| **Edit** | Immutable versions with full provenance, A/B swipe compare, crop, exact text overlay, flip, AI edit with an optional drawn region, explicit Approve for Breakdown |
| **Decompose** | Draw boxes or detect objects (mock and Google, as unconfirmed proposals), hierarchy, merge, instances, tiny-prop audit, crops and polygon masks, output goal per item (2D layers, unverified 2D reference, 3D proxy, mesh plus five views) |
| **3D** | Parametric proxy wizard for 12 categories, own `.glb` import with a mandatory orientation review, Meshy image-to-3D, editable master diorama base with its own five views plus a top view |
| **Render** | Four orthographic and one perspective camera, each a separate Blender process and PNG; per-view re-render; base policies `none / foundation / local plinth / scene context / full base` proven with object masks |
| **Quality** | Colour-cast, noise and skin-hint analysis, masked previewable repairs that keep the original, an export gate with written override |
| **Export** | Schema-valid ZIP: approved source and lineage, inventory with history, manifests, crops and masks, 2D layers, models, camera data, QA reports. COMPLETE only when everything is; PARTIAL lists exactly why |
| **Costs and privacy** | Keys in the Windows credential store, estimate then explicit approval showing exactly what leaves the computer, daily/monthly caps, local-only mode, no telemetry |

## Run it

Prerequisites: Windows 11, Node 20+, Rust (MSVC), Python 3.11, Blender 4.x or 5.x (auto-detected, or set in Settings).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" pydantic pillow numpy jsonschema httpx keyring pytest
cd apps\desktop
npm install
npm run tauri dev
```

Browser-only UI work: `.\scripts\dev.ps1` starts a dev worker and Vite at `http://127.0.0.1:1420`.

## Test it

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # about 6 minutes, renders with real Blender
.\.venv\Scripts\python.exe -m pytest -q -m "not blender"   # fast subset, no Blender needed
```

`scripts\phase0_demo.py` renders the BAR and the master base with Cycles and writes `evidence\phase0\`.

## Build the installer

```powershell
.\scripts\build_installer.ps1
```

Builds a self-contained Python runtime (no download), the front end and a per-user NSIS installer into `dist-installer\` with a SHA-256 file. The installer registers `.cvbd` project files with your file icon. Blender is not bundled (see `docs/adr/0002`).

## Honest status

Verified here (Windows 11, Blender 5.2.2 LTS, CPU rendering): the test suite, the full UI flow driven in a browser against the real worker, the Tauri app launching and starting its worker, and a silent install of the built installer.

Not verified or not done:
- **No live provider calls.** No API keys were available, so Google, OpenAI, Kie and Meshy are tested against recorded request shapes only. No model shows as verified until a real request succeeds for you.
- **Tripo is disabled**: its request format could not be confirmed from its docs and a wrong call would bill credits.
- Detection quality was not measured against real models. Proposals always start unconfirmed.
- Artifact checks are heuristic (see `docs/adr/0004`). No OCR of sign text, no shape-versus-source comparison.
- Not tested on a clean Windows 11 machine. No code signing, auto-update or SBOM.
- Blender GPU rendering is not selectable; CPU Cycles and Workbench only.
- Several AI proxies are simple shapes. They are stand-ins for review, not finished models.
- `rafter run` (remote security scan) has not run, because it needs a GitHub token for the private repo.

## Docs

`docs/adr/` decisions, `docs/security/` design and reviews, `docs/ACCEPTANCE_STATUS.md` the kit's checklists item by item, `branding/` your icons and splashscreen.
