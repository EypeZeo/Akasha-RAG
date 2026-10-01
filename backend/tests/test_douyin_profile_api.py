"""A profile captured after login must reach the durable account display."""
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import auth
from app.db.base import Base
from app.db.session import get_db
from app.models.entities import SourceAccount
from app.services.adapters.base import AuthStatus


def test_platform_status_persists_fresh_douyin_avatar_and_keeps_it_on_empty_capture(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    profile = {"nickname": "Account owner", "avatar_url": "https://p3-sign.douyinpic.com/tos-cn/avatar.webp"}
    monkeypatch.setattr(auth.collector, "get_status", lambda: ("syncing", "syncing"))
    monkeypatch.setattr(auth.collector, "get_profile", lambda: dict(profile))
    monkeypatch.setattr(auth.bilibili_client, "get_auth_status", AsyncMock(return_value=AuthStatus("bilibili", False)))
    monkeypatch.setattr(auth.zhihu_collector, "get_status", lambda: {"status": "idle"})

    def database():
        with Session(engine) as db:
            yield db
            db.commit()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            first = client.get("/api/auth/platforms")
            assert first.status_code == 200
            item = next(p for p in first.json()["platforms"] if p["platform"] == "douyin")
            assert item["is_logged_in"]
            assert item["avatar_url"] == profile["avatar_url"]
            with Session(engine) as db:
                account = db.scalar(select(SourceAccount).where(SourceAccount.platform == "douyin"))
                assert account.nickname == profile["nickname"]
                assert account.avatar_url == profile["avatar_url"]
            expected_avatar = profile["avatar_url"]
            profile.clear()
            second = client.get("/api/auth/platforms")
            item = next(p for p in second.json()["platforms"] if p["platform"] == "douyin")
            assert item["avatar_url"] == expected_avatar
    finally:
        engine.dispose()
