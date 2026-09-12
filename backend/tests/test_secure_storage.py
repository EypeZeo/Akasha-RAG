from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.core.secure_storage import delete_json, protect_text, read_json, write_json


def test_credential_json_is_migrated_from_plaintext_and_removed(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text('{"token":"do-not-store-plain"}', encoding="utf-8")

    assert read_json(path) == {"token": "do-not-store-plain"}
    protected = path.with_name(path.name + ".dpapi")
    assert protected.is_file()
    assert not path.exists()
    if os.name == "nt":
        assert b"do-not-store-plain" not in protected.read_bytes()


def test_protected_credential_json_round_trips_and_can_be_deleted(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"cookies": [{"name": "sessionid", "value": "secret"}]})

    assert read_json(path) == {"cookies": [{"name": "sessionid", "value": "secret"}]}
    delete_json(path)
    assert read_json(path) is None


def test_config_loads_dpapi_protected_dotenv_into_its_own_process(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DASHSCOPE_API_KEY=stored-in-dpapi\n", encoding="utf-8")
    protect_text(dotenv)
    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DASHSCOPE_API_KEY", None)
    environment["PYTHONPATH"] = str(backend_dir)

    completed = subprocess.run(
        [sys.executable, "-c", "from app.core.config import settings; print(settings.dashscope_api_key)"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )

    assert completed.stdout.strip() == "stored-in-dpapi"
