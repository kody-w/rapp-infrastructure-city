#!/usr/bin/env python3
"""Deterministic city-model, layout, and approval-gate tests."""

import os
import pathlib
import subprocess
import tempfile
import threading
import time

import city_daemon
import repair_approval
from city_layout import build_layout
from city_model import build_snapshot

RAW = {
    "observed_at": "2026-08-15T12:00:00+00:00",
    "machines": [
        {"id": "one", "name": "one", "online": True, "os": "macOS", "ip": "100.1.1.1"},
        {"id": "two", "name": "two", "online": False, "os": "linux", "ip": "100.1.1.2"},
    ],
    "daemons": [
        {"label": "com.rapp.good", "loaded": True, "pid": 10, "last_exit": 0},
        {"label": "com.rapp.bad", "loaded": True, "pid": None, "last_exit": 1},
    ],
    "sentinels": [
        {"id": "s", "name": "sentinel", "status": "warning", "detail": "stale"},
    ],
    "repositories": [
        {
            "name": "repo",
            "name_with_owner": "owner/repo",
            "url": "https://github.com/owner/repo",
            "pushed_at": "2026-08-15T11:00:00Z",
            "archived": False,
            "private": False,
            "workflows": [
                {
                    "id": 1,
                    "name": "green",
                    "state": "active",
                    "latest_run": {
                        "status": "completed",
                        "conclusion": "success",
                        "database_id": 100,
                    },
                },
                {
                    "id": 2,
                    "name": "red",
                    "state": "active",
                    "latest_run": {
                        "status": "completed",
                        "conclusion": "failure",
                        "database_id": 101,
                    },
                },
            ],
        }
    ],
}

snapshot = build_snapshot(RAW).to_dict()
assert snapshot["summary"]["kind_counts"] == {
    "machine": 2,
    "daemon": 2,
    "sentinel": 1,
    "repository": 1,
    "workflow": 2,
}
repo = next(item for item in snapshot["entities"] if item["kind"] == "repository")
assert repo["status"] == "critical"
assert [child["status"] for child in repo["children"]] == ["healthy", "critical"]

layout = build_layout(snapshot)
assert layout["summary"]["structures"] == 6
assert layout["summary"]["features"] == 2
assert layout["summary"]["overall_status"] == "critical"
assert "workflow:owner/repo:1" in layout["entity_index"]
assert "workflow:owner/repo:2" in layout["entity_index"]
assert build_layout(snapshot) == layout
for structure in layout["structures"]:
    minimum, maximum = structure["bounds"]["min"], structure["bounds"]["max"]
    assert -220 <= minimum[0] <= maximum[0] <= 220
    assert 4 <= minimum[1] <= maximum[1] <= 40
    assert 64 <= minimum[2] <= maximum[2] <= 370

tmp = pathlib.Path(tempfile.mkdtemp(prefix="city-approval-test-"))
repair_approval.STATE = tmp
repair_approval.REQUESTS = tmp / "requests.json"
repair_approval.AUDIT = tmp / "audit.jsonl"
record = repair_approval.request(
    "daemon:com.rapp.good",
    {
        "id": "restart",
        "label": "Restart",
        "kind": "launchd_restart",
        "payload": {"label": "com.rapp.good"},
        "approval_required": True,
    },
    "player",
)
assert record["status"] == "pending"
try:
    repair_approval.execute("NOTREAL")
    raise AssertionError("unknown token should fail")
except ValueError:
    pass
try:
    repair_approval.request(
        "x",
        {"kind": "launchd_restart", "payload": {}, "approval_required": False},
        "player",
    )
    raise AssertionError("ungated repair should fail")
except ValueError:
    pass

calls = []
original_run = subprocess.run
def fake_run(command, **kwargs):
    calls.append(command)
    time.sleep(0.05)
    return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
repair_approval.subprocess.run = fake_run
outcomes = []
def approve():
    try:
        outcomes.append(repair_approval.execute(record["token"])["status"])
    except ValueError:
        outcomes.append("rejected")
threads = [threading.Thread(target=approve) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
repair_approval.subprocess.run = original_run
assert calls and len(calls) == 1
assert sorted(outcomes) == ["executed", "rejected"]

timeout_request = repair_approval.request(
    "daemon:com.rapp.timeout",
    {
        "id": "restart",
        "label": "Restart",
        "kind": "launchd_restart",
        "payload": {"label": "com.rapp.timeout"},
        "approval_required": True,
    },
    "player",
)
repair_approval.subprocess.run = lambda *args, **kwargs: (
    (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 120))
)
try:
    repair_approval.execute(timeout_request["token"])
    raise AssertionError("timeout should fail")
except subprocess.TimeoutExpired:
    pass
finally:
    repair_approval.subprocess.run = original_run
requests = repair_approval.read_json(repair_approval.REQUESTS, {})
assert requests[timeout_request["token"]]["status"] == "failed"
assert "TimeoutExpired" in requests[timeout_request["token"]]["error"]

city_daemon.STATE = tmp / "daemon-state"
lock_results = []
with city_daemon.tick_lock() as acquired:
    assert acquired
    def try_lock():
        with city_daemon.tick_lock() as second:
            lock_results.append(second)
    contender = threading.Thread(target=try_lock)
    contender.start()
    contender.join()
assert lock_results == [False]

print("city model: 22 assertions passed")
