from types import SimpleNamespace

import pytest

from app.api.routes import settings as settings_route
from app.services import settings_store


@pytest.mark.asyncio
async def test_detect_deepseek_persists_valid_env_provider(monkeypatch):
    provider = settings_store.ChatProvider(
        id="env-deepseek",
        display_name="DeepSeek",
        protocol="openai",
        base_url="https://api.deepseek.com",
        api_key="real-key",
        model_id="deepseek-chat",
    )
    saved = []
    monkeypatch.setattr(settings_store, "env_deepseek_provider", lambda: provider)
    monkeypatch.setattr(settings_store, "ensure_env_deepseek_provider", lambda: saved.append(provider) or provider)
    monkeypatch.setattr(settings_store, "get_active_chat_provider_id", lambda: provider.id)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers):
            assert url == "https://api.deepseek.com/models"
            assert headers["Authorization"] == "Bearer real-key"
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(settings_route.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await settings_route.detect_chat_provider()

    assert result["status"] == "valid"
    assert result["provider"]["display_name"] == "DeepSeek"
    assert saved == [provider]


@pytest.mark.asyncio
async def test_detect_deepseek_does_not_create_provider_for_invalid_key(monkeypatch):
    monkeypatch.setattr(settings_store, "env_deepseek_provider", lambda: None)

    result = await settings_route.detect_chat_provider()

    assert result == {"success": True, "status": "not_configured", "provider": None}
