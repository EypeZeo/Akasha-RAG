"""Windows DPAPI-backed persistence for local credentials.

Only a small, audited surface writes credential-bearing JSON.  On Windows the
ciphertext is bound to the interactive Windows account; the non-Windows
fallback exists solely for development and CI, where the desktop launcher does
not support running the application.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from pathlib import Path
from typing import Any

_MAGIC = b"AKASHA-DPAPI-v1\0"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _encrypted_path(path: Path) -> Path:
    return path.with_name(path.name + ".dpapi")


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    raw = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
    protected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(ctypes.byref(source), "Akasha-RAG", None, None, None, 0, ctypes.byref(protected)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(protected.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    raw = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
    clear = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(clear)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(clear.pbData)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def read_text(path: Path) -> str | None:
    """Read a DPAPI-protected text file or its legacy plaintext predecessor."""
    protected_path = _encrypted_path(path)
    if protected_path.is_file():
        encoded = protected_path.read_bytes()
        if not encoded.startswith(_MAGIC):
            raise ValueError(f"invalid protected credential file: {protected_path}")
        return _unprotect(base64.b64decode(encoded[len(_MAGIC) :], validate=True)).decode("utf-8")
    return path.read_text(encoding="utf-8") if path.is_file() else None


def protect_text(path: Path) -> None:
    """Protect an existing text file only after its contents have been validated."""
    raw = path.read_bytes()
    _atomic_write(_encrypted_path(path), _MAGIC + base64.b64encode(_protect(raw)))
    path.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    """Atomically protect JSON and remove the plaintext predecessor."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = _MAGIC + base64.b64encode(_protect(raw))
    _atomic_write(_encrypted_path(path), encrypted)
    path.unlink(missing_ok=True)


def read_json(path: Path) -> Any | None:
    """Read protected JSON; atomically migrate an existing legacy JSON file."""
    protected_path = _encrypted_path(path)
    if protected_path.is_file():
        encoded = protected_path.read_bytes()
        if not encoded.startswith(_MAGIC):
            raise ValueError(f"invalid protected credential file: {protected_path}")
        return json.loads(_unprotect(base64.b64decode(encoded[len(_MAGIC) :], validate=True)).decode("utf-8"))
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    write_json(path, value)
    return value


def storage_signature(path: Path) -> tuple[bool, int]:
    """`(exists, mtime_ns)` of whichever file `read_json(path)` would actually
    read (the protected file if present, else a legacy plaintext file) --
    lets a caller cheaply detect "has this changed since I last read it"
    without re-reading/decrypting the content.
    """
    protected_path = _encrypted_path(path)
    target = protected_path if protected_path.is_file() else path
    try:
        return True, target.stat().st_mtime_ns
    except FileNotFoundError:
        return False, 0


def delete_json(path: Path) -> None:
    path.unlink(missing_ok=True)
    _encrypted_path(path).unlink(missing_ok=True)


def materialize_json(path: Path) -> Path | None:
    """Create a short-lived plaintext file for APIs that require a file path.

    Callers must invoke :func:`remove_materialized` immediately after the API
    consumes it.  Playwright reads storage state synchronously during context
    creation, so the plaintext never needs to persist between operations.
    """
    value = read_json(path)
    if value is None:
        return None
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".state.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    return Path(tmp_name)


def remove_materialized(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)
