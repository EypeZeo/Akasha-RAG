"""Release artifacts must keep the launcher's locked install reproducible."""
import json
import tomllib
from pathlib import Path


def test_release_versions_and_lock_updaters_are_consistent():
    root = Path(__file__).resolve().parents[2]
    version = (root / "version.txt").read_text(encoding="utf-8").strip()
    project = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    lock_source = (root / "backend/uv.lock").read_text(encoding="utf-8")
    locked_project = next(p for p in tomllib.loads(lock_source)["package"] if p["name"] == "akasha-rag")
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    frontend_lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert project["project"]["version"] == version
    assert locked_project["version"] == version
    assert frontend["version"] == frontend_lock["version"] == frontend_lock["packages"][""]["version"] == version
    assert lock_source.count("x-release-please-version") == 1
    config = json.loads((root / "release-please-config.json").read_text(encoding="utf-8"))
    extra_files = config["packages"]["."]["extra-files"]
    assert {"type": "generic", "path": "backend/uv.lock"} in extra_files
    for jsonpath in ("$.version", "$.packages[''].version"):
        assert {"type": "json", "path": "frontend/package-lock.json", "jsonpath": jsonpath} in extra_files
