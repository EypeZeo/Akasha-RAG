"""Locally-stored, editable-at-runtime API settings: DashScope key + saved chat providers.

Kept out of the SQLite database and out of `.env`: the Settings UI needs to add,
edit, and switch these without an app restart.  On Windows it is stored with
DPAPI under `app/storage/`, bound to the current Windows account.

`.env`'s `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` remain a valid way to configure
the app (documented in the README) and are used as the fallback when nothing has
been saved here yet, so existing `.env`-only setups keep working unchanged.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.secure_storage import read_json, storage_signature, write_json

_LOCK = threading.Lock()
# (signature, parsed value) of the last _read(). Guarded by _LOCK, which
# every public function here already holds for its whole read-modify-write
# span, so no separate lock is needed for the cache itself.
_cache: tuple[tuple[bool, int], ApiSettings] | None = None


class ChatProvider(BaseModel):
    id: str
    display_name: str
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: str
    model_id: str


class ApiSettings(BaseModel):
    dashscope_api_key: str = ""
    chat_providers: list[ChatProvider] = Field(default_factory=list)
    active_chat_provider_id: Optional[str] = None


def _store_path() -> Path:
    # Relative like `chroma_persist_dir` / `bilibili_state_path`: resolved against
    # the process CWD, which is `backend/` for every entry point (uvicorn, pytest,
    # the launcher). Do not join it against `settings.project_root` — that property
    # already points at `backend/app`, and joining would double up the `app/` segment.
    return Path(settings.api_settings_path)


def _read() -> ApiSettings:
    global _cache
    path = _store_path()
    signature = storage_signature(path)
    if _cache is not None and _cache[0] == signature:
        return _cache[1].model_copy(deep=True)
    try:
        data = read_json(path)
        parsed = ApiSettings.model_validate(data) if data is not None else ApiSettings()
    except Exception:
        parsed = ApiSettings()
    _cache = (signature, parsed)
    return parsed.model_copy(deep=True)


def _write(data: ApiSettings) -> None:
    global _cache
    path = _store_path()
    write_json(path, data.model_dump(mode="json"))
    _cache = None


def get_dashscope_key() -> str:
    with _LOCK:
        stored = _read().dashscope_api_key.strip()
    return stored or settings.dashscope_api_key.strip()


def set_dashscope_key(key: str) -> None:
    with _LOCK:
        data = _read()
        data.dashscope_api_key = key.strip()
        _write(data)


def list_chat_providers() -> list[ChatProvider]:
    with _LOCK:
        data = _read()
    return data.chat_providers


def get_active_chat_provider_id() -> Optional[str]:
    with _LOCK:
        data = _read()
    if not data.chat_providers:
        return None
    if any(p.id == data.active_chat_provider_id for p in data.chat_providers):
        return data.active_chat_provider_id
    return data.chat_providers[0].id


def get_active_chat_provider() -> Optional[ChatProvider]:
    with _LOCK:
        data = _read()
    if not data.chat_providers:
        return None
    for provider in data.chat_providers:
        if provider.id == data.active_chat_provider_id:
            return provider
    return data.chat_providers[0]


def upsert_chat_provider(
    *,
    id: Optional[str],
    display_name: str,
    protocol: str,
    base_url: str,
    api_key: str,
    model_id: str,
) -> ChatProvider:
    with _LOCK:
        data = _read()
        provider_id = id or uuid.uuid4().hex[:12]
        provider = ChatProvider(
            id=provider_id,
            display_name=display_name.strip(),
            protocol=protocol,  # type: ignore[arg-type]
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            model_id=model_id.strip(),
        )
        replaced = False
        next_providers = []
        for existing in data.chat_providers:
            if existing.id == provider_id:
                next_providers.append(provider)
                replaced = True
            else:
                next_providers.append(existing)
        if not replaced:
            next_providers.append(provider)
        data.chat_providers = next_providers
        if data.active_chat_provider_id is None:
            data.active_chat_provider_id = provider_id
        _write(data)
        return provider


def delete_chat_provider(provider_id: str) -> None:
    with _LOCK:
        data = _read()
        data.chat_providers = [p for p in data.chat_providers if p.id != provider_id]
        if data.active_chat_provider_id == provider_id:
            data.active_chat_provider_id = data.chat_providers[0].id if data.chat_providers else None
        _write(data)


def set_active_chat_provider(provider_id: str) -> ChatProvider:
    with _LOCK:
        data = _read()
        match = next((p for p in data.chat_providers if p.id == provider_id), None)
        if match is None:
            raise ValueError(f"未知的供应商 ID: {provider_id}")
        data.active_chat_provider_id = provider_id
        _write(data)
        return match


def config_status() -> dict:
    """Whether there is enough configured to actually run ingest / chat.

    Used by the frontend's soft gate: the app always starts, but "一键入库"
    and sending a chat message check this first and show a modal instead of
    letting the request fail deep inside the pipeline.
    """
    from app.core.startup_preflight import is_placeholder_api_key

    provider = get_active_chat_provider()
    if provider is not None:
        chat_ready = bool(provider.api_key.strip()) and not is_placeholder_api_key(provider.api_key)
    else:
        chat_ready = not is_placeholder_api_key(settings.deepseek_api_key)

    ingest_ready = not is_placeholder_api_key(get_dashscope_key())

    return {"chat_ready": chat_ready, "ingest_ready": ingest_ready}
