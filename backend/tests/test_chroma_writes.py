"""Check writer completion and preservation of existing chunks on failed upsert."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.chroma_service import ChromaService


def service(collection):
    result = object.__new__(ChromaService)
    result._collection = collection
    return result


def test_upsert_completes_without_reentering_a_plain_lock():
    # A subprocess deadline keeps a regression from deadlocking the test runner.
    script = '''
import json
from types import SimpleNamespace
from app.services.chroma_service import ChromaService
calls = []
service = object.__new__(ChromaService)
service._collection = SimpleNamespace(
    get=lambda **kw: {"ids": ["123:0", "123:1"]},
    upsert=lambda **kw: calls.append("upsert"),
    delete=lambda **kw: calls.append(["delete", kw.get("ids")]),
)
assert service.upsert_video_chunks("123", "title", ["body"], [[1.0, 0.0]]) == ["123:0"]
print(json.dumps(calls))
'''
    import os
    env = os.environ.copy()
    executable = sys.executable
    if os.name == "nt":
        executable = sys._base_executable
        env["__PYVENV_LAUNCHER__"] = sys.executable
    result = subprocess.run([executable, "-c", script], env=env, capture_output=True,
                            text=True, timeout=5, check=True,
                            cwd=Path(__file__).resolve().parents[1],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert json.loads(result.stdout) == ["upsert", ["delete", ["123:1"]]]


def test_failed_upsert_preserves_previous_index():
    collection = SimpleNamespace(
        get=Mock(return_value={"ids": ["123:0", "123:1"]}),
        upsert=Mock(side_effect=RuntimeError("disk failure")),
        delete=Mock(),
    )
    with pytest.raises(RuntimeError, match="disk failure"):
        service(collection).upsert_video_chunks("123", "title", ["body"], [[1.0, 0.0]])
    collection.delete.assert_not_called()


def test_empty_index_does_not_query_zero_results():
    collection = SimpleNamespace(count=lambda: 0, query=Mock())
    assert service(collection).search([1.0, 0.0]) == []
    collection.query.assert_not_called()
