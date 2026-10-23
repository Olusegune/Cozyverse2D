"""Blender detection and subprocess execution (argv lists only, never a shell)."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

from .config import BLENDER_DIR, RENDER_TIMEOUT_S

RENDER_SCRIPT = BLENDER_DIR / "render_view.py"
_VER_RE = re.compile(r"Blender\s+(\d+\.\d+(?:\.\d+)?)")


class BlenderError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def _probe(path: str) -> Optional[dict]:
    p = Path(path)
    if not p.is_file() or p.stem.lower() != "blender":
        return None
    try:
        out = subprocess.run([str(p), "--version"], capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _VER_RE.search(out)
    if not m:
        return None
    return {"path": str(p), "version": m.group(1), "device": "CPU"}


def _vkey(info: dict):
    return tuple(int(x) for x in info["version"].split("."))


def detect(configured: Optional[str] = None) -> dict:
    """Return found/path/version/device/candidates. Order: configured, env, PATH, Program Files."""
    cands: list[str] = []
    if configured:
        cands.append(configured)
    if os.environ.get("CV_BLENDER"):
        cands.append(os.environ["CV_BLENDER"])
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for name in ("blender.exe", "blender"):
            cands.append(os.path.join(d, name))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cands.extend(glob.glob(os.path.join(pf, "Blender Foundation", "Blender *", "blender.exe")))
    seen: set[str] = set()
    infos: list[dict] = []
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        info = _probe(c)
        if info:
            infos.append(info)
    if not infos:
        return {"found": False, "path": None, "version": None, "device": None, "candidates": []}
    if configured and infos[0]["path"] == str(Path(configured)):
        best = infos[0]
    else:
        best = max(infos, key=_vkey)
    return {"found": True, **best, "candidates": [i["path"] for i in infos]}


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       stdin=subprocess.DEVNULL)
    else:
        proc.kill()


def run_blender(blender: str, script_args: list[str], cancel: Optional[threading.Event] = None,
                timeout: int = RENDER_TIMEOUT_S, log: Optional[Callable[[str], None]] = None) -> str:
    argv = [blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(RENDER_SCRIPT), "--"] + script_args
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL, creationflags=flags)
    lines: list[str] = []

    def pump():
        assert proc.stdout
        for line in proc.stdout:
            lines.append(line.rstrip())
            if log:
                log(line.rstrip())

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    waited = 0.0
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                kill_tree(proc)
                raise Cancelled("cancelled")
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                waited += 0.2
                if waited > timeout:
                    kill_tree(proc)
                    raise BlenderError(f"render timed out after {timeout}s")
    finally:
        if proc.poll() is None:
            kill_tree(proc)
        t.join(timeout=5)
    out = "\n".join(lines)
    if proc.returncode != 0:
        raise BlenderError(f"blender exited {proc.returncode}: {out[-800:]}")
    return out


def render_view(blender: str, spec_path: Path, out_png: Path, meta_path: Path, *, view: str,
                policy: str, engine: str = "CYCLES", res: int = 1024, samples: int = 64,
                bg: str = "white", mode: str = "render", include: str = "", flat: bool = False,
                quadrant: str = "front_left", elevation: float = 30.0, focal: float = 50.0,
                cancel: Optional[threading.Event] = None) -> dict:
    args = ["--spec", str(spec_path), "--out", str(out_png), "--meta", str(meta_path),
            "--view", view, "--policy", policy, "--engine", engine, "--res", str(res),
            "--samples", str(samples), "--bg", bg, "--mode", mode,
            "--quadrant", quadrant, "--elevation", str(elevation), "--focal", str(focal),
            "--render-call-id", str(uuid.uuid4())]
    if include:
        args += ["--include", include]
    if flat:
        args.append("--flat")
    run_blender(blender, args, cancel=cancel)
    with open(meta_path, "r", encoding="utf8") as fh:
        return json.load(fh)
