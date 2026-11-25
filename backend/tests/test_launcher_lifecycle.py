"""Interrupted startup must not leave backend/frontend processes running."""
import importlib
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
launcher = importlib.import_module("launcher")


@pytest.mark.parametrize("error", [KeyboardInterrupt(), OSError("frontend cannot start")])
def test_startup_failure_always_runs_shutdown(tmp_path, monkeypatch, error):
    monkeypatch.setattr(launcher, "LOGS_DIR", tmp_path)
    app = launcher.UnifiedLauncher()
    app.backend_proc = Mock()

    def interrupted_startup():
        raise error

    monkeypatch.setattr(app, "_run_services", interrupted_startup)
    shutdown = Mock(side_effect=lambda **kwargs: app.log_file.close())
    monkeypatch.setattr(app, "shutdown", shutdown)
    if isinstance(error, KeyboardInterrupt):
        assert app.run() == 0
    else:
        with pytest.raises(OSError, match="frontend cannot start"):
            app.run()
    shutdown.assert_called_once_with(wait_for_key=False)


def test_backend_receives_the_resolved_launcher_language(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(launcher, "_LANG", "en")
    monkeypatch.setenv("AKASHA_LANG", "zh")
    popen = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    app = launcher.UnifiedLauncher()
    with app.log_file:
        app.start_backend()
    child_env = popen.call_args.kwargs["env"]
    assert child_env["AKASHA_TERMINAL_LANGUAGE"] == "en"
    assert child_env["PYTHONIOENCODING"] == "utf-8"


def test_vite_startup_marker_uses_ascii(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "LOGS_DIR", tmp_path)
    app = launcher.UnifiedLauncher()
    pipe = BytesIO("\x1b[32m➜\x1b[39m  Local: http://localhost:5173/\n".encode("utf-8"))
    with app.log_file:
        app.stream_reader(pipe, "[FRONTEND]", "")
    console = capsys.readouterr().out
    assert "➜" not in console
    assert ">  Local: http://localhost:5173/" in app.log_file_path.read_text(encoding="utf-8")
    assert app.frontend_ready
