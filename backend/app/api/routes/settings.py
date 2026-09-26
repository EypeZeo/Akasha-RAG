"""
设置面板路由模块

管理仅保存在本机的 API 设置：DashScope Key（ASR / Embedding / 视觉共用）
与对话供应商列表（多份保存 + 切换）。全部存于本地 JSON 文件
（`app.services.settings_store`），从不写入数据库，也从不上传。
"""
from __future__ import annotations

from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import settings_store

router = APIRouter(prefix="/settings", tags=["设置"])


def _mask(key: str) -> str:
    key = key or ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _provider_out(provider: settings_store.ChatProvider, active_id: Optional[str]) -> dict:
    return {
        "id": provider.id,
        "display_name": provider.display_name,
        "protocol": provider.protocol,
        "base_url": provider.base_url,
        "model_id": provider.model_id,
        "api_key_masked": _mask(provider.api_key),
        "is_active": provider.id == active_id,
    }


@router.get("/status")
async def get_status():
    """供前端软门禁使用：是否已配置足够信息来跑对话 / 一键入库"""
    return {"success": True, **settings_store.config_status()}


@router.get("/dashscope-key")
async def get_dashscope_key():
    key = settings_store.get_dashscope_key()
    return {"success": True, "configured": bool(key), "api_key_masked": _mask(key) if key else ""}


class SetDashscopeKeyRequest(BaseModel):
    api_key: str


@router.put("/dashscope-key")
async def set_dashscope_key(body: SetDashscopeKeyRequest):
    settings_store.set_dashscope_key(body.api_key)
    return {"success": True}


@router.get("/chat-providers")
async def list_chat_providers():
    providers = settings_store.list_chat_providers()
    active_id = settings_store.get_active_chat_provider_id()
    return {"success": True, "providers": [_provider_out(p, active_id) for p in providers], "active_id": active_id}


@router.post("/chat-providers/detect")
async def detect_chat_provider():
    """Validate the .env DeepSeek key with a non-billing models request.

    A successful check seeds the local provider list only when the user has not
    already saved a custom provider. The response never contains the key.
    """
    provider = settings_store.env_deepseek_provider()
    if provider is None:
        return {"success": True, "status": "not_configured", "provider": None}
    url = f"{provider.base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), trust_env=True) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {provider.api_key}"})
        if response.status_code < 200 or response.status_code >= 300:
            return {"success": True, "status": "invalid", "provider": None}
    except (httpx.HTTPError, OSError):
        return {"success": True, "status": "unreachable", "provider": None}

    saved = settings_store.ensure_env_deepseek_provider()
    return {
        "success": True,
        "status": "valid",
        "provider": _provider_out(saved or provider, settings_store.get_active_chat_provider_id()),
    }


class UpsertChatProviderRequest(BaseModel):
    id: Optional[str] = None
    display_name: str
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: Optional[str] = None
    """留空表示编辑时不修改已保存的 Key（前端从不回显明文 Key，无法在编辑表单里帮用户预填它）"""
    model_id: str


@router.post("/chat-providers")
async def upsert_chat_provider(body: UpsertChatProviderRequest):
    if not body.display_name.strip() or not body.base_url.strip() or not body.model_id.strip():
        raise HTTPException(status_code=400, detail="显示名称 / 地址 / 模型 ID 不能为空")

    api_key = (body.api_key or "").strip()
    if not api_key:
        existing = next((p for p in settings_store.list_chat_providers() if p.id == body.id), None) if body.id else None
        if existing is None:
            raise HTTPException(status_code=400, detail="请填写 API Key")
        api_key = existing.api_key

    provider = settings_store.upsert_chat_provider(
        id=body.id,
        display_name=body.display_name,
        protocol=body.protocol,
        base_url=body.base_url,
        api_key=api_key,
        model_id=body.model_id,
    )
    active_id = settings_store.get_active_chat_provider_id()
    return {"success": True, "provider": _provider_out(provider, active_id)}


@router.delete("/chat-providers/{provider_id}")
async def delete_chat_provider(provider_id: str):
    settings_store.delete_chat_provider(provider_id)
    return {"success": True}


class ActivateChatProviderRequest(BaseModel):
    provider_id: str


@router.post("/chat-providers/activate")
async def activate_chat_provider(body: ActivateChatProviderRequest):
    try:
        provider = settings_store.set_active_chat_provider(body.provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "provider": _provider_out(provider, body.provider_id)}
