"""
PR2B-3 回归测试：system.py 的阻塞调用必须挪出事件循环

`pick_directory` 之前直接同步调 `subprocess.run(..., timeout=180)`，
`get_recent_logs` 之前 `f.readlines()` 整个文件——两者都在 `async def`
路由里内联执行，会卡住事件循环。验证方式：用 `threading.Event` 让被
monkeypatch 的阻塞函数先 `started.set()` 再等待一个 `gate`，同时并发跑一个
纯 `asyncio.sleep(0)` 计数循环；只要计数在阻塞函数卡住期间仍然增长，就
证明事件循环没有被这次调用占住——这是确定性的，不依赖具体耗时。
"""
from __future__ import annotations

import asyncio
import builtins
import threading

import pytest

from app.api.routes import system


async def _assert_loop_not_blocked_while(block_started: threading.Event, release_gate: threading.Event) -> None:
    ticks = 0

    async def ticker():
        nonlocal ticks
        while not release_gate.is_set():
            ticks += 1
            await asyncio.sleep(0)

    ticker_task = asyncio.create_task(ticker())
    await asyncio.get_running_loop().run_in_executor(None, block_started.wait, 5)
    # Give the ticker a few scheduling turns while the "blocking" call is
    # still parked on release_gate.
    for _ in range(5):
        await asyncio.sleep(0)
    assert ticks > 0, "event loop did not tick while the blocking call was in flight"
    release_gate.set()
    await ticker_task


@pytest.mark.asyncio
async def test_pick_directory_offloads_subprocess_run(monkeypatch):
    started = threading.Event()
    gate = threading.Event()

    def fake_run(*args, **kwargs):
        started.set()
        gate.wait(timeout=5)
        return system.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(system.subprocess, "run", fake_run)

    route_task = asyncio.create_task(system.pick_directory(system.PickDirectoryRequest()))
    await _assert_loop_not_blocked_while(started, gate)
    result = await route_task
    assert result == {"success": False, "cancelled": True}


@pytest.mark.asyncio
async def test_logs_route_offloads_tail_read(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "LOG_DIR", tmp_path)
    (tmp_path / "all_20260913.log").write_text("line\n" * 10, encoding="utf-8")

    started = threading.Event()
    gate = threading.Event()
    real_tail_lines = system._tail_lines

    def fake_tail_lines(path, max_lines, chunk_size=8192):
        started.set()
        gate.wait(timeout=5)
        return real_tail_lines(path, max_lines, chunk_size)

    monkeypatch.setattr(system, "_tail_lines", fake_tail_lines)

    route_task = asyncio.create_task(system.get_recent_logs(lines=3, log_type="all"))
    await _assert_loop_not_blocked_while(started, gate)
    result = await route_task
    assert result["success"] is True
    assert result["lines"] == ["line", "line", "line"]


def test_tail_lines_reads_a_bounded_amount_not_the_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "big.log"
    total_lines = 50_000
    path.write_text("\n".join(f"line-{i}" for i in range(total_lines)) + "\n", encoding="utf-8")
    file_size = path.stat().st_size

    read_sizes: list[int] = []
    real_open = builtins.open

    class _CountingFile:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._f.__exit__(*exc)

        def seek(self, *a, **kw):
            return self._f.seek(*a, **kw)

        def tell(self):
            return self._f.tell()

        def read(self, size=-1):
            data = self._f.read(size)
            read_sizes.append(len(data))
            return data

    def counting_open(file, mode="rb", *a, **kw):
        return _CountingFile(real_open(file, mode, *a, **kw))

    monkeypatch.setattr(system, "open", counting_open, raising=False)
    lines = system._tail_lines(path, max_lines=5)

    assert lines == [f"line-{i}" for i in range(total_lines - 5, total_lines)]
    assert sum(read_sizes) < file_size / 10, "tail read pulled in far more than a bounded slice of the file"
