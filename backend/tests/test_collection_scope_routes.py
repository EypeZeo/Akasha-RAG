"""Route-level contract for collection scope (issue #34).

Two platforms share the remote collection id ``same`` and the remote item id
``shared``. A request that cannot name exactly one collection must fail the same
way everywhere (HTTP 400, no side effects); a request that names the platform
must act on that platform's collection only; over-long identifiers are 422.
The export worker re-checks the scope itself (the data can change between
queueing and running), so that path is pinned here too.
"""
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.main import app
from app.models.entities import (
    CollectionItemRelation,
    ContentItem,
    FavoriteCollection,
    IngestionItem,
)
from app.services import chroma_service as chroma_module
from app.services import export_worker
from app.services import rag_service as rag_module
from app.services.batch_export_service import batch_export_service
from app.services.collection_scope import AmbiguousCollectionError, CollectionNotFoundError
from app.services.worker import worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'scope.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    ids = {}
    with factory() as db:
        dy = FavoriteCollection(platform="douyin", remote_collection_id="same", title="Douyin same")
        bili = FavoriteCollection(platform="bilibili", remote_collection_id="same", title="Bilibili same")
        only_dy = FavoriteCollection(platform="douyin", remote_collection_id="only-dy", title="Only douyin")
        db.add_all([dy, bili, only_dy])
        db.flush()
        for platform, remote, col in (("douyin", "shared", dy), ("bilibili", "shared", bili), ("douyin", "dy-only", only_dy)):
            item = ContentItem(platform=platform, remote_item_id=remote, title=f"{platform}:{remote}", duration=30)
            db.add(item)
            db.flush()
            db.add(IngestionItem(content_item_id=item.id, status="pending", transcript_text="text"))
            db.add(CollectionItemRelation(collection_id=col.id, content_item_id=item.id))
            ids[(platform, remote)] = item.id
        db.commit()

    fake_chroma = SimpleNamespace(delete_by_video=Mock(), clear_platform=Mock(), clear_all=Mock())
    monkeypatch.setattr(chroma_module, "get_chroma_service", lambda: fake_chroma)
    monkeypatch.setattr(worker, "blocked_platforms", lambda: set())
    monkeypatch.setattr(worker, "has_active_tasks", lambda: False)
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda task, fn, item_ids, **kw: submitted.extend(item_ids) or task)
    queued = Mock(return_value="task-1")
    monkeypatch.setattr(export_worker, "submit_export", queued)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as client:
        yield SimpleNamespace(client=client, ids=ids, chroma=fake_chroma, submitted=submitted, queued=queued)


def call(env, endpoint, platform=None, collection="same", **extra):
    c = env.client
    if endpoint == "sync":
        body = {"collection_id": collection, **extra}
        if platform:
            body["platform"] = platform
        return c.post("/api/knowledge/sync", json=body)
    if endpoint == "pending":
        params = {"collection_id": collection, **extra}
        if platform:
            params["platform"] = platform
        return c.get("/api/knowledge/pending", params=params)
    if endpoint == "clear":
        body = {"collection_id": collection, **extra}
        if platform:
            body["platform"] = platform
        return c.post("/api/knowledge/clear-all", json=body)
    if endpoint == "export":
        body = {"collection_id": collection, **extra}
        if platform:
            body["platform"] = platform
        return c.post("/api/knowledge/export/batch", json=body)
    if endpoint == "videos":
        params = dict(extra)
        if platform:
            params["platform"] = platform
        return c.get(f"/api/favorites/collections/{collection}/videos", params=params)
    if endpoint in ("ask", "stream"):
        body = {"query": "explain the deployment plan", "collection_id": collection, **extra}
        if platform:
            body["platform"] = platform
        return c.post("/api/chat/ask" if endpoint == "ask" else "/api/chat/ask/stream", json=body)
    raise AssertionError(endpoint)


ENDPOINTS = ["sync", "pending", "clear", "export", "videos"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("platform", [None, "all"])
def test_an_ambiguous_collection_is_a_400_with_no_side_effects(env, endpoint, platform):
    response = call(env, endpoint, platform)

    assert response.status_code == 400
    assert "platform" in response.json()["detail"]
    assert env.submitted == []
    env.queued.assert_not_called()
    env.chroma.delete_by_video.assert_not_called()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_an_ambiguous_collection_is_not_logged_as_a_server_error(env, endpoint, caplog):
    with caplog.at_level(logging.ERROR):
        assert call(env, endpoint).status_code == 400
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.fixture
def chat(env, monkeypatch):
    """Chat with retrieval stubbed out: what matters here is the scope handed to it."""
    monkeypatch.setattr(rag_module, "get_chroma_service", lambda: SimpleNamespace(count=lambda: 1))
    reached = []

    def stop_at_retrieval(route, query, db, scope_ids, platform):
        reached.append(scope_ids)
        raise RuntimeError("retrieval reached")

    monkeypatch.setattr(rag_module.rag_service, "_retrieve_hits_for_route", stop_at_retrieval)
    return SimpleNamespace(env=env, reached=reached)


@pytest.mark.parametrize("platform", [None, "all"])
@pytest.mark.parametrize("endpoint", ["ask", "stream"])
def test_chat_reports_an_ambiguous_collection_as_an_error_without_logging_it(chat, endpoint, platform, caplog):
    with caplog.at_level(logging.ERROR):
        response = call(chat.env, endpoint, platform)

    assert response.status_code == 200
    if endpoint == "ask":
        assert response.json()["success"] is False
        assert "specify platform" in response.json()["message"]
    else:
        assert "event: error" in response.text and "specify platform" in response.text
        assert "event: delta" not in response.text
    assert chat.reached == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.parametrize("platform", ["douyin", "bilibili"])
@pytest.mark.parametrize("endpoint", ["ask", "stream"])
def test_chat_retrieval_scope_is_the_named_platforms_collection_only(chat, endpoint, platform):
    call(chat.env, endpoint, platform)
    assert chat.reached == [{(platform, "shared")}]


@pytest.mark.parametrize("platform", ["douyin", "bilibili"])
def test_naming_the_platform_acts_on_that_platforms_collection_only(env, platform):
    shared = env.ids[(platform, "shared")]

    pending = call(env, "pending", platform)
    assert pending.status_code == 200
    assert [row["id"] for row in pending.json()["items"]] == [shared]

    videos = call(env, "videos", platform)
    assert videos.status_code == 200
    assert [row["id"] for row in videos.json()["items"]] == [shared]
    assert videos.json()["video_count"] + videos.json()["note_count"] == 1

    assert call(env, "sync", platform).status_code == 200
    assert env.submitted == [shared]

    cleared = call(env, "clear", platform)
    assert cleared.status_code == 200 and cleared.json()["success"] is True
    assert [c.kwargs["content_item_id"] for c in env.chroma.delete_by_video.call_args_list] == [shared]

    exported = call(env, "export", platform)
    assert exported.status_code == 200
    assert env.queued.call_args.args[0]["platform"] == platform


def test_a_collection_that_does_not_exist_on_the_requested_platform(env):
    assert call(env, "pending", "bilibili", "only-dy").json()["items"] == []
    assert call(env, "videos", "bilibili", "only-dy").json()["items"] == []
    assert call(env, "sync", "bilibili", "only-dy").json()["task_id"] is None
    assert call(env, "clear", "bilibili", "only-dy").json()["success"] is False
    # Export used to accept this and fail later in the worker.
    missing = call(env, "export", "bilibili", "only-dy")
    assert missing.status_code == 404
    env.queued.assert_not_called()


def test_export_rejects_ambiguous_selected_ids_before_queueing_a_task(env):
    response = call(env, "export", None, collection=None, selected_ids=["shared"])
    assert response.status_code == 400
    env.queued.assert_not_called()

    accepted = call(env, "export", "douyin", collection=None, selected_ids=["shared"])
    assert accepted.status_code == 200
    env.queued.assert_called_once()


def test_the_export_worker_checks_the_scope_again_because_data_can_change_after_queueing(env):
    with session_module.session_factory() as db:
        # A task queued by a client that named no platform.
        with pytest.raises(AmbiguousCollectionError):
            batch_export_service.get_exportable_videos(db, selected_ids=["shared"])
        with pytest.raises(AmbiguousCollectionError):
            batch_export_service.get_exportable_videos(db, collection_id="same")
        # A collection that vanished must fail, not silently widen the export to every item.
        with pytest.raises(CollectionNotFoundError):
            batch_export_service.get_exportable_videos(db, collection_id="only-dy", platform="bilibili")

        db.execute(update(IngestionItem).values(status="done"))
        db.commit()
        rows = batch_export_service.get_exportable_videos(db, selected_ids=["shared"], platform="douyin")
        assert [item.id for _cache, item in rows] == [env.ids[("douyin", "shared")]]


LONG = "x" * 65


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_an_over_long_collection_id_is_a_422_everywhere(env, endpoint):
    assert call(env, endpoint, "douyin", collection=LONG).status_code == 422


@pytest.mark.parametrize("endpoint", ["sync", "export"])
def test_selected_ids_are_bounded_in_count_and_item_length(env, endpoint):
    too_long_item = call(env, endpoint, "douyin", collection=None, selected_ids=[LONG])
    too_many = call(env, endpoint, "douyin", collection=None, selected_ids=["a"] * 10001)
    assert too_long_item.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_an_unknown_platform_is_a_422_everywhere(env, endpoint):
    assert call(env, endpoint, "youtube").status_code == 422


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("content_type", "pdf", id="content_type"),
        pytest.param("format", "rtf", id="format"),
        pytest.param("pack_mode", "tar", id="pack_mode"),
        pytest.param("target_dir", "x" * 4097, id="target_dir-too-long"),
    ],
)
def test_export_options_are_closed_sets_and_bounded(env, field, value):
    response = call(env, "export", "douyin", collection=None, **{field: value})
    assert response.status_code == 422
    env.queued.assert_not_called()
