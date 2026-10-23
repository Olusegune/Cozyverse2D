"""Runtime configuration. No secrets are persisted here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BLENDER_DIR = REPO_ROOT / "blender"
SCHEMA_DIR = REPO_ROOT / "schemas"

MAX_BODY_BYTES = 1_000_000
RENDER_TIMEOUT_S = 600
MAX_RESOLUTION = 4096
MIN_RESOLUTION = 128


def default_data_dir() -> Path:
    env = os.environ.get("CV_DATA_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "CozyVerse2D"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    token: str = ""
    blender_path: str | None = None

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cozyverse.sqlite3"
