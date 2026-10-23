import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "worker", ROOT / "blender"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cozyverse_worker import blender_adapter  # noqa: E402
import proxy_spec  # noqa: E402


@pytest.fixture(scope="session")
def blender():
    info = blender_adapter.detect()
    if not info["found"]:
        pytest.skip("Blender not installed")
    return info["path"]


@pytest.fixture(scope="session")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("renders")


def write_spec(path: Path, spec: dict) -> Path:
    path.write_text(json.dumps(spec), encoding="utf8")
    return path
