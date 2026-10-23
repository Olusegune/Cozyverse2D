"""Smoke test of an installed (or staged) copy: starts the bundled worker and renders a real view through the API.

Usage:  python scripts/smoke_installed.py <install_dir>
Uses a throwaway data directory and a random token. Exit code 0 means the bundle can build a proxy, render with
Blender and export a schema-valid ZIP.
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def main() -> int:
    install = Path(sys.argv[1]).resolve()
    py = install / "worker" / "python" / "python.exe"
    assert py.is_file(), f"bundled python missing: {py}"
    token = secrets.token_urlsafe(24)
    data = Path(tempfile.mkdtemp(prefix="cv_smoke_"))
    env = {**os.environ, "CV_WORKER_TOKEN": token, "PYTHONPATH": str(install / "worker")}
    env.pop("PYTHONHOME", None)
    proc = subprocess.Popen([str(py), "-s", "-m", "cozyverse_worker", "--port", "0", "--data-dir", str(data)],
                            cwd=install / "worker", env=env, stdout=subprocess.PIPE, text=True)
    try:
        port = json.loads(proc.stdout.readline())["port"]
        base = f"http://127.0.0.1:{port}"

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"null")

        try:
            urllib.request.urlopen(base + "/health", timeout=10)
            print("FAIL: unauthenticated request was accepted")
            return 1
        except urllib.error.HTTPError as e:
            assert e.code == 401
        health = call("GET", "/health")
        print("health:", health["status"], "blender:", health["blender"]["version"])
        assert health["blender"]["found"], "Blender not found"
        pid = call("POST", "/projects", {"name": "Smoke", "fixtures": ["bar"]})["id"]
        aid = call("GET", f"/projects/{pid}/assets")[0]["id"]

        def job(jid):
            for _ in range(600):
                j = call("GET", f"/jobs/{jid}")
                if j["state"] not in ("NEW", "QUEUED", "RUNNING"):
                    return j
                time.sleep(0.5)
            raise TimeoutError

        j = job(call("POST", f"/assets/{aid}/build-proxy", {})["job_id"])
        assert j["state"] == "SUCCEEDED", j
        j = job(call("POST", f"/assets/{aid}/render-five", {"resolution": 256, "engine": "WORKBENCH"})["job_id"])
        assert j["state"] == "SUCCEEDED", j
        for v in ("front", "back", "left", "right", "perspective"):
            p = next(data.rglob(f"{v}.png"))
            assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        for v in ("front", "back", "left", "right", "perspective"):
            call("POST", f"/assets/{aid}/views/{v}/review", {"status": "PASSED"})
        j = job(call("POST", f"/projects/{pid}/export", {})["job_id"])
        assert j["state"] == "SUCCEEDED" and j["result"]["status"] == "COMPLETE", j
        names = zipfile.ZipFile(j["result"]["path"]).namelist()
        assert all(f"assets/BLD-003/views/{v}.png" in names for v in ("front", "back", "left", "right", "perspective"))
        print("OK: proxy built, five views rendered, export COMPLETE,", len(names), "files in the ZIP")
        return 0
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
