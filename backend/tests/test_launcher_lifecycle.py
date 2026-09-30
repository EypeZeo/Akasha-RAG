"""Interrupted startup must not leave backend/frontend processes running."""
import importlib
import sys
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
