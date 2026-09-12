"""
BUG-02 补丁回归测试：平台级/全量清空必须区分"集合本来就不存在"和真实故障

`clear_platform()`/`clear_all()` 此前用 `except Exception: pass` 吞掉
`delete_collection()` 的所有异常，导致锁/磁盘/损坏这类真实故障也会被当成
"清空成功"，随后调用方 (`knowledge.py::clear_all_knowledge`) 会继续跑
SQLite UPDATE，造成"数据库说已清空、向量库其实还在"的不一致。现在只应该
吞掉 `chromadb.errors.NotFoundError`（集合本来就不存在，幂等/可重试的
预期情形），其余异常必须继续往上抛，且不能把集合标记为已重建。
"""
from unittest.mock import Mock

import chromadb.errors
import pytest

from app.services.chroma_service import ChromaService


def make_service(delete_collection_mock):
    svc = object.__new__(ChromaService)
    svc._client = Mock(delete_collection=delete_collection_mock)
    svc._collections = {"douyin": "old-douyin-collection", "bilibili": "old-bilibili-collection"}
    return svc


def test_clear_platform_tolerates_not_found():
    svc = make_service(Mock(side_effect=chromadb.errors.NotFoundError("gone")))
    svc._create_collection = Mock(return_value="new-douyin-collection")

    svc.clear_platform("douyin")

    assert svc._collections["douyin"] == "new-douyin-collection"


def test_clear_platform_propagates_genuine_failure_and_does_not_recreate():
    svc = make_service(Mock(side_effect=OSError("disk is locked")))
    svc._create_collection = Mock(return_value="new-douyin-collection")

    with pytest.raises(OSError):
        svc.clear_platform("douyin")

    # The route must not be able to treat this as a successful clear: the
    # old collection reference must stay exactly as it was.
    svc._create_collection.assert_not_called()
    assert svc._collections["douyin"] == "old-douyin-collection"


def test_clear_all_propagates_genuine_failure_and_stops_at_the_failing_platform():
    svc = make_service(Mock(side_effect=OSError("disk is locked")))
    svc._create_collection = Mock(return_value="new-collection")

    with pytest.raises(OSError):
        svc.clear_all()

    svc._create_collection.assert_not_called()
    assert svc._collections["douyin"] == "old-douyin-collection"
    assert svc._collections["bilibili"] == "old-bilibili-collection"


def test_clear_all_tolerates_not_found_for_every_platform():
    svc = make_service(Mock(side_effect=chromadb.errors.NotFoundError("gone")))
    svc._create_collection = Mock(side_effect=lambda platform: f"new-{platform}-collection")

    svc.clear_all()

    assert svc._collections["douyin"] == "new-douyin-collection"
    assert svc._collections["bilibili"] == "new-bilibili-collection"
