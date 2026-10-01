"""Cancelled queued jobs must not enter download or paid model functions."""
from unittest.mock import Mock
import threading

from app.services.worker import Worker


def test_cancelled_queued_task_does_not_run_and_does_not_block_next_task():
    worker = Worker()
    cancelled_body = Mock()
    following_body = Mock()
    worker.submit("cancelled", cancelled_body)
    worker.submit("following", following_body)
    assert worker.cancel("cancelled")
    worker.start()
    try:
        worker._queue.join()
        cancelled_body.assert_not_called()
        following_body.assert_called_once_with(task_id="following")
        assert worker.get_progress("cancelled")["status"] == "cancelled"
        worker.update_progress("cancelled", 1, 2, "late progress")
        assert worker.get_progress("cancelled")["message"] == "已取消"
        assert not worker.has_active_tasks()
    finally:
        worker.stop()
        worker._thread.join(timeout=2)


def test_maintenance_guard_prevents_new_task_publication():
    worker = Worker()
    started = threading.Event()
    submitted = threading.Event()

    def submit():
        started.set()
        worker.submit("new", lambda **_: None)
        submitted.set()

    with worker.submission_guard():
        # Reentrant so start_sync may protect its DB preparation and call submit.
        with worker.submission_guard():
            assert not worker.has_active_tasks()
        thread = threading.Thread(target=submit)
        thread.start()
        assert started.wait(timeout=2)
        assert not submitted.wait(timeout=.1)
        assert worker.get_progress("new") is None
    thread.join(timeout=2)
    assert submitted.is_set()
    assert worker.has_active_tasks()


def test_cancel_request_does_not_release_active_guard_before_job_finishes():
    worker = Worker()
    worker.submit("new", lambda **_: None)
    worker.cancel("new")
    assert worker.has_active_tasks()


def test_stop_cancels_queued_jobs_and_they_do_not_run_on_restart():
    worker = Worker()
    body = Mock()
    worker.submit("queued", body)
    worker.stop()
    assert worker.get_progress("queued")["status"] == "cancelled"
    assert worker.get_progress("queued")["finished_at"]
    assert worker._queue.unfinished_tasks == 0
    worker.start()
    try:
        worker._queue.join()
        body.assert_not_called()
    finally:
        worker.stop()
        worker._thread.join(timeout=2)


def test_stop_signals_running_job_cancellation_at_checkpoint():
    worker = Worker()
    entered = threading.Event()
    check_cancel = threading.Event()
    cancellation_seen = threading.Event()

    def body(*, task_id):
        entered.set()
        assert check_cancel.wait(timeout=2)
        if worker.is_cancelled(task_id):
            cancellation_seen.set()

    worker.submit("running", body)
    worker.start()
    assert entered.wait(timeout=2)
    worker.stop()
    check_cancel.set()
    worker._thread.join(timeout=2)
    assert cancellation_seen.is_set()
    assert worker.get_progress("running")["status"] == "cancelled"


def test_immediate_restart_reuses_live_worker_and_executes_new_task():
    worker = Worker()
    entered = threading.Event()
    release = threading.Event()
    new_ran = threading.Event()

    def old_body(*, task_id):
        entered.set()
        assert release.wait(timeout=2)

    worker.submit("old", old_body)
    worker.start()
    old_thread = worker._thread
    try:
        assert entered.wait(timeout=2)
        worker.stop()
        worker.start()
        assert worker._thread is old_thread
        worker.submit("new", lambda **_: new_ran.set())
        release.set()
        assert new_ran.wait(timeout=2)
        worker._queue.join()
        assert worker.get_progress("old")["status"] == "cancelled"
        assert worker.get_progress("new")["status"] == "done"
    finally:
        release.set()
        worker.stop()
        worker._thread.join(timeout=2)


def test_restart_replaces_live_thread_that_has_committed_to_exit():
    exit_decided = threading.Event()
    release_exit = threading.Event()
    new_ran = threading.Event()

    class DelayedExitWorker(Worker):
        def _run(self):
            super()._run()
            exit_decided.set()
            assert release_exit.wait(timeout=3)

    worker = DelayedExitWorker()
    worker.start()
    old_thread = worker._thread
    try:
        worker.stop()
        assert exit_decided.wait(timeout=2)
        assert old_thread.is_alive()
        worker.start()
        assert worker._thread is not old_thread
        worker.submit("new", lambda **_: new_ran.set())
        assert new_ran.wait(timeout=2)
        worker._queue.join()
        assert worker.get_progress("new")["status"] == "done"
    finally:
        release_exit.set()
        worker.stop()
        old_thread.join(timeout=2)
        worker._thread.join(timeout=2)
