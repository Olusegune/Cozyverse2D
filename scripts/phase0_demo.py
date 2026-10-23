"""Phase 0 evidence run: BAR proxy -> five independent Blender renders -> ZIP. No API needed.

Usage: .venv\\Scripts\\python.exe scripts/phase0_demo.py [--res 1024] [--samples 64] [--engine CYCLES]
Outputs go to evidence/phase0/ (five PNGs, per-view camera JSON, export ZIP, summary.json).
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "worker"), str(ROOT / "blender")]

from cozyverse_worker.config import Settings  # noqa: E402
from cozyverse_worker.service import Service, VIEWS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", type=int, default=1024)
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--engine", default="CYCLES")
    a = ap.parse_args()

    out = ROOT / "evidence" / "phase0"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    svc = Service(Settings(data_dir=Path(tempfile.mkdtemp(prefix="cv_phase0_")), token="demo"))
    print("Blender:", svc.blender_info())
    proj = svc.create_project("Rainy Night Market", ["bar", "master_base"])
    assets = {x["code"]: x for x in svc.list_assets(proj["id"])}
    summary = {"engine": a.engine, "resolution": a.res, "assets": {}}
    for code in ("BLD-003", "BASE-001"):
        aid = assets[code]["id"]
        t0 = time.time()
        job = svc.wait_job(svc.build_proxy(aid))
        assert job["state"] == "SUCCEEDED", job
        job = svc.wait_job(svc.render_five(aid, {
            "resolution": a.res, "background": "white", "engine": a.engine, "samples": a.samples,
            "base_policies": None, "perspective_quadrant": "front_left",
            "perspective_elevation_degrees": 30.0, "perspective_focal_mm": 50.0}))
        print(code, job["state"], job["message"], f"{time.time() - t0:.1f}s")
        assert job["state"] == "SUCCEEDED", job
        view = next(x for x in svc.list_assets(proj["id"]) if x["code"] == code)
        d = svc.asset_dir(svc.get_asset(aid))
        (out / code).mkdir()
        for v in VIEWS:
            shutil.copy(d / "views" / f"{v}.png", out / code / f"{v}.png")
            shutil.copy(d / "qa" / f"{v}_meta.json", out / code / f"{v}_camera.json")
        summary["assets"][code] = {v: {"status": view["views"][v]["status"],
                                       "camera_type": view["views"][v]["camera_type"],
                                       "sha256": view["views"][v]["sha256"]} for v in VIEWS}
        for v in VIEWS:
            svc.review_view(aid, v, "PASSED", "phase0 evidence run (automated checks only; human review pending)")
    job = svc.wait_job(svc.export_project(proj["id"]))
    shutil.copy(job["result"]["path"], out / "phase0_export.zip")
    summary["export"] = {"status": job["result"]["status"], "zip": "phase0_export.zip"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
    print(json.dumps(summary["export"]))
    svc.shutdown()


if __name__ == "__main__":
    main()
