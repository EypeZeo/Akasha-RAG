"""Project-local ffmpeg discovery.

scripts/bootstrap.ps1 can install a project-local ffmpeg (gyan.dev's Windows
essentials build) when no system ffmpeg is found. media_service.py owns resolving
it -- via the ``.runtime/ffmpeg-dir.txt`` state file bootstrap.ps1 writes, with a
recursive-glob fallback since the build extracts into an unpredictable versioned
folder name (e.g. ``ffmpeg-8.0-essentials_build``).

These tests pin that resolution order so a future refactor can't silently drop the
project-local candidate and regress to "everything installs, transcoding still
fails" on a clean machine with no system ffmpeg.
"""
from pathlib import Path

from app.services import media_service


def _make_ffmpeg_layout(root: Path, bin_subdir: str = "ffmpeg-8.0-essentials_build/bin") -> Path:
    """Create a fake extracted essentials build under root/.runtime/ffmpeg/...; return its bin/ dir."""
    bin_dir = root / ".runtime" / "ffmpeg" / bin_subdir
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "ffmpeg.exe").write_bytes(b"MZ")
    (bin_dir / "ffprobe.exe").write_bytes(b"MZ")
    return bin_dir


def test_finds_ffmpeg_via_the_state_file(tmp_path):
    bin_dir = _make_ffmpeg_layout(tmp_path)
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    (tmp_path / ".runtime" / "ffmpeg-dir.txt").write_text(str(bin_dir), encoding="utf-8")
    found = media_service._find_project_ffmpeg_executable(repo_root=tmp_path)
    assert found == bin_dir / "ffmpeg.exe"


def test_falls_back_to_glob_when_state_file_is_stale(tmp_path):
    """A leftover state file pointing at a since-removed build must not shadow
    a real, differently-versioned install sitting right next to it."""
    bin_dir = _make_ffmpeg_layout(tmp_path)
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    (tmp_path / ".runtime" / "ffmpeg-dir.txt").write_text(
        str(tmp_path / "ffmpeg" / "does-not-exist" / "bin"), encoding="utf-8"
    )
    found = media_service._find_project_ffmpeg_executable(repo_root=tmp_path)
    assert found == bin_dir / "ffmpeg.exe"


def test_falls_back_to_glob_when_state_file_is_missing(tmp_path):
    bin_dir = _make_ffmpeg_layout(tmp_path)
    found = media_service._find_project_ffmpeg_executable(repo_root=tmp_path)
    assert found == bin_dir / "ffmpeg.exe"


def test_returns_none_when_nothing_is_installed(tmp_path):
    assert media_service._find_project_ffmpeg_executable(repo_root=tmp_path) is None


def test_prefers_the_newest_build_on_glob_fallback(tmp_path):
    _make_ffmpeg_layout(tmp_path, "ffmpeg-6.0-essentials_build/bin")
    newest = _make_ffmpeg_layout(tmp_path, "ffmpeg-8.0-essentials_build/bin")
    found = media_service._find_project_ffmpeg_executable(repo_root=tmp_path)
    assert found == newest / "ffmpeg.exe"


def test_resolve_ffmpeg_path_falls_through_to_project_local(tmp_path, monkeypatch):
    """End-to-end: shutil.which and the hardcoded fallbacks all miss, so
    _resolve_ffmpeg_path() must reach the project-local candidate rather than
    raising -- the exact path a clean machine with no system ffmpeg takes."""
    bin_dir = _make_ffmpeg_layout(tmp_path)
    monkeypatch.setattr(media_service.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        media_service, "_find_project_ffmpeg_executable", lambda: bin_dir / "ffmpeg.exe"
    )
    assert media_service._resolve_ffmpeg_path() == str(bin_dir / "ffmpeg.exe")


def test_resolve_ffmpeg_path_still_raises_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr(media_service.shutil, "which", lambda name: None)
    monkeypatch.setattr(media_service, "_find_project_ffmpeg_executable", lambda: None)
    monkeypatch.setattr(media_service.Path, "is_file", lambda self: False)
    try:
        media_service._resolve_ffmpeg_path()
        raise AssertionError("expected MediaPipelineError")
    except media_service.MediaPipelineError as exc:
        assert "ffmpeg" in str(exc)
