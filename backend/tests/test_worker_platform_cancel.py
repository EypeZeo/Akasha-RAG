from app.services.worker import Worker


def test_platform_block_is_scoped_and_reversible():
    worker = Worker()
    worker.block_platform("douyin")
    assert worker.is_platform_blocked("douyin")
    assert not worker.is_platform_blocked("bilibili")

    worker.unblock_platform("douyin")
    assert not worker.is_platform_blocked("douyin")
