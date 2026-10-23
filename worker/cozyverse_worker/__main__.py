"""Entry point: python -m cozyverse_worker [--port 0] [--data-dir DIR]

The token comes from CV_WORKER_TOKEN (set by the Tauri host). If unset (dev), one is generated and
printed once on stdout, which only the parent process can read.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings, default_data_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    supplied = os.environ.get("CV_WORKER_TOKEN")
    token = supplied or secrets.token_urlsafe(32)
    settings = Settings(data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(), token=token)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))   # loopback only
    sock.listen(128)   # accept (queue) connections immediately; the host may call before uvicorn is up
    port = sock.getsockname()[1]
    ready = {"event": "ready", "port": port}
    if not supplied:
        ready["token"] = token
    print(json.dumps(ready), flush=True)

    config = uvicorn.Config(create_app(settings), log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    sys.exit(main())
