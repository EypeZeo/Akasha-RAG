"""
BUG-10/NET-04 回归测试：TTL 内跳过重新富化，且预读不占用请求 Session

验证 `sync_from_bilibili` 的三条契约：
1. TTL 未过期、且已有真实分 P 数据的视频，本轮不会再调用 get_video_info；
2. 从未富化过 / TTL 已过期的视频，本轮会调用 get_video_info；
3. 预读走的是独立的短生命周期只读 Session——用 monkeypatch
   `app.db.session.session_factory` 验证这一点能生效（如果预读用的是
   传入的 db，这个 monkeypatch 不会影响结果，但下面第二个断言——网络
   调用期间引擎连接池里没有连接被占用——能直接证明预读 session 已经
   在网络 await 之前关闭，不是靠 session_factory 是否被换掉这件事本身
   来证明）。
"""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db import session as session_module
from app.db.base import Base
from app.models.entities import ContentItem, ContentPart
from app.services.favorites_service import _utcnow, favorites_service


@pytest.fixture
def db_and_engine(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'ttl.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)
    db = factory()
    yield db, engine
    db.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_ttl_skip_reuses_fresh_parts_and_preread_holds_no_connection(db_and_engine, monkeypatch):
    db, engine = db_and_engine
    from app.services.bilibili.client import bilibili_client

    fresh_item = ContentItem(
        platform="bilibili", remote_item_id="BV_fresh", canonical_url="https://x/BV_fresh",
        title="新鲜", author="up", duration=200, content_kind="video", part_count=1,
        is_active=True, last_enriched_at=_utcnow(),
    )
    db.add(fresh_item)
    db.flush()
    db.add(ContentPart(
        content_item_id=fresh_item.id, remote_part_id="100", part_index=1, part_title="P1",
        duration=200, transcript_source="whisper_asr", transcript_version="1", time_range="0-200",
    ))

    stale_item = ContentItem(
        platform="bilibili", remote_item_id="BV_stale", canonical_url="https://x/BV_stale",
        title="过期", author="up", duration=200, content_kind="video", part_count=1,
        is_active=True,
        last_enriched_at=_utcnow() - timedelta(hours=settings.bilibili_enrichment_ttl_hours + 1),
    )
    db.add(stale_item)
    db.flush()
    db.add(ContentPart(
        content_item_id=stale_item.id, remote_part_id="200", part_index=1, part_title="旧P1",
        duration=200, transcript_source="whisper_asr", transcript_version="1", time_range="0-200",
    ))
    db.commit()

    monkeypatch.setattr(bilibili_client, "_cookies", {"SESSDATA": "test_sessdata"})
    monkeypatch.setattr(
        bilibili_client, "get_user_favorites",
        AsyncMock(return_value=[{"id": 1, "title": "默认收藏夹", "media_count": 3, "cover": ""}]),
    )
    mock_content = {
        "medias": [
            {"bvid": "BV_fresh", "title": "新鲜", "duration": 200, "upper": {"name": "up"}},
            {"bvid": "BV_stale", "title": "过期", "duration": 200, "upper": {"name": "up"}},
            {"bvid": "BV_new", "title": "全新", "duration": 50, "upper": {"name": "up"}},
        ],
        "has_more": False,
    }
    monkeypatch.setattr(bilibili_client, "get_favorite_content", AsyncMock(return_value=mock_content))

    checked_out_during_calls = []

    async def _fake_get_video_info(bvid):
        # This runs "during the network await" -- the short-lived read
        # session used for the pre-read must already be closed by now, so
        # the engine's pool should have nothing checked out.
        checked_out_during_calls.append(engine.pool.checkedout())
        return {"pages": [{"cid": 999, "page": 1, "part": "P1", "duration": 100}]}

    get_video_info_mock = AsyncMock(side_effect=_fake_get_video_info)
    monkeypatch.setattr(bilibili_client, "get_video_info", get_video_info_mock)

    await favorites_service.sync_from_bilibili(db)

    called_bvids = {c.args[0] for c in get_video_info_mock.call_args_list}
    assert called_bvids == {"BV_stale", "BV_new"}
    assert "BV_fresh" not in called_bvids

    assert checked_out_during_calls
    assert all(count == 0 for count in checked_out_during_calls)

    db.expire_all()
    fresh_after = db.query(ContentItem).filter_by(remote_item_id="BV_fresh").one()
    fresh_parts = db.query(ContentPart).filter_by(content_item_id=fresh_after.id).all()
    assert [p.remote_part_id for p in fresh_parts] == ["100"]
