#!/usr/bin/env python3
"""Run the frontend test suite (or a filtered part of it) N times in a row and record every run.

    python frontend/scripts/run_vitest_loop.py --runs 30 --output .artifacts/pr-c-vitest-loop.jsonl
    python frontend/scripts/run_vitest_loop.py --runs 30 --output out.jsonl \\
        --vitest-args "src/components/SourcesPanel.platform-scope.test.tsx -t P1"

Every run is one JSON line: index, start time, elapsed seconds, exit code, the vitest summary lines and a
tail of stdout/stderr. A run that exceeds --timeout is killed (the whole process tree), recorded with
exit_code "timeout", and the loop carries on with the next run — one hang never costs the other runs.
The last line is a summary. The script always writes it, and exits non-zero if any run did not exit 0.

Runs are serial on purpose: the point is repeated-run stability, not throughput.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TAIL_CHARS = 1500


def frontend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def cpu_load_percent() -> float | None:
    """Best-effort system-wide CPU load, for judging whether a slow run coincided with other work."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            return float(out) if out else None
        return os.getloadavg()[0] * 100.0 / (os.cpu_count() or 1)
    except Exception:  # noqa: BLE001 — a missing measurement must never fail a run
        return None


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and everything it started (npm/npx start node as a grandchild)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        proc.kill()


def tail(text: str) -> str:
    return text[-TAIL_CHARS:] if len(text) > TAIL_CHARS else text


ANSI = re.compile(r"\x1b\[[0-9;]*m")
SUMMARY = re.compile(r"^\s*(Test Files|Tests)\s.*$", re.MULTILINE)


def one_run(index: int, command: list[str], cwd: Path, timeout: float) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    load_before = cpu_load_percent()
    t0 = time.monotonic()
    popen_kwargs: dict = {"cwd": cwd, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
                          "encoding": "utf-8", "errors": "replace"}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code: int | str = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        exit_code = "timeout"
    elapsed = time.monotonic() - t0
    clean = ANSI.sub("", stdout or "")
    return {
        "type": "run",
        "run": index,
        "started": started,
        "elapsed_s": round(elapsed, 1),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "cpu_load_before": load_before,
        "cpu_load_after": cpu_load_percent(),
        "vitest_summary": [m.group(0).strip() for m in SUMMARY.finditer(clean)],
        "stdout_tail": tail(clean),
        "stderr_tail": tail(ANSI.sub("", stderr or "")),
    }


def tool_version(cmd: list[str], cwd: Path) -> str | None:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=30, help="number of serial runs (default 30)")
    parser.add_argument("--output", required=True, help="JSONL file to write (created/overwritten)")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-run timeout in seconds (default 600)")
    parser.add_argument("--vitest-args", default="", help="extra arguments for `vitest run`, as one quoted string")
    args = parser.parse_args()

    cwd = frontend_dir()
    npx = shutil.which("npx")
    if npx is None:
        print("npx not found on PATH", file=sys.stderr)
        return 2
    command = [npx, "vitest", "run", *shlex.split(args.vitest_args)]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = {
        "type": "header",
        "command": command,
        "cwd": str(cwd),
        "runs": args.runs,
        "timeout_s": args.timeout,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "node": tool_version(["node", "--version"], cwd),
        "vitest": tool_version([npx, "vitest", "--version"], cwd),
        "git_head": tool_version(["git", "rev-parse", "HEAD"], cwd),
    }
    results: list[dict] = []
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            for i in range(1, args.runs + 1):
                record = one_run(i, command, cwd, args.timeout)
                results.append(record)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"run {i}/{args.runs}: exit={record['exit_code']} {record['elapsed_s']}s", flush=True)
        finally:
            elapsed = [r["elapsed_s"] for r in results]
            failures = [r["run"] for r in results if r["exit_code"] != 0]
            summary = {
                "type": "summary",
                "completed_runs": len(results),
                "requested_runs": args.runs,
                "passed": sum(1 for r in results if r["exit_code"] == 0),
                "failed": len(failures),
                "timeouts": sum(1 for r in results if r["timed_out"]),
                "failed_runs": failures,
                "elapsed_min_s": min(elapsed) if elapsed else None,
                "elapsed_max_s": max(elapsed) if elapsed else None,
                "elapsed_mean_s": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
            }
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    incomplete = len(results) < args.runs
    return 1 if (summary["failed"] or incomplete) else 0


if __name__ == "__main__":
    sys.exit(main())
