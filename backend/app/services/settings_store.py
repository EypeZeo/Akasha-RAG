"""Locally-stored, editable-at-runtime API settings: DashScope key + saved chat providers.

Kept out of the SQLite database and out of `.env`: the Settings UI needs to add,
edit, and switch these without an app restart. Mirrors the `bilibili_state.json`
precedent for storing real credential material in a local JSON file under
`app/storage/` (atomic tempfile-then-replace write, never touched by git).

`.env`'s `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` remain a valid way to configure
the app (documented in the README) and are used as the fallback when nothing has
been saved here yet, so existing `.env`-only setups keep working unchanged.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import settings

_LOCK = threading.Lock()


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
    path = _store_path()
    if not path.is_file():
        return ApiSettings()
    try:
        return ApiSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return ApiSettings()


def _write(data: ApiSettings) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix="api_settings_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data.model_dump_json(indent=2))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


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
