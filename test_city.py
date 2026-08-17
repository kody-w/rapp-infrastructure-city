#!/usr/bin/env python3
"""Deterministic city-model, layout, and approval-gate tests."""

import copy
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

dense_raw = copy.deepcopy(RAW)
dense_raw["repositories"][0]["workflows"] = [
    {
        "id": index,
        "name": f"workflow-{index:03d}",
        "state": "active",
        "latest_run": {
            "status": "completed",
            "conclusion": "success",
            "database_id": 1000 + index,
        },
    }
    for index in range(1, 101)
]
dense_layout = build_layout(build_snapshot(dense_raw).to_dict())
assert dense_layout["summary"]["features"] == 100
assert "workflow:owner/repo:100" in dense_layout["entity_index"]
dense_repository = next(
    structure
    for structure in dense_layout["structures"]
    if structure["kind"] == "repository"
)
dense_positions = [
    tuple(feature["position"])
    for feature in dense_repository["features"]
]
assert len(dense_positions) == 100
assert len(dense_positions) == len(set(dense_positions))

def repository_raw(count, prefix="repo"):
    return {
        "observed_at": RAW["observed_at"],
        "machines": [],
        "daemons": [],
        "sentinels": [],
        "repositories": [
            {
                "name": f"{prefix}{index:04d}",
                "name_with_owner": f"owner/{prefix}{index:04d}",
                "workflows": [],
            }
            for index in range(count)
        ],
    }


scale_layout = build_layout(build_snapshot(repository_raw(703)).to_dict())
assert scale_layout["summary"]["structures"] == 703
for structure in scale_layout["structures"]:
    minimum, maximum = structure["bounds"]["min"], structure["bounds"]["max"]
    assert -220 <= minimum[0] <= maximum[0] <= 220
    assert 64 <= minimum[2] <= maximum[2] <= 370

machine_raw = repository_raw(1)
machine_raw["machines"] = [
    {
        "id": f"machine-{index}",
        "name": f"machine-{index}",
        "online": True,
    }
    for index in range(36)
]
machine_layout = build_layout(build_snapshot(machine_raw).to_dict())
assert len([
    structure
    for structure in machine_layout["structures"]
    if structure["kind"] == "machine"
]) == 36

first_snapshot = build_snapshot(repository_raw(561, "repo")).to_dict()
first_layout = build_layout(first_snapshot)
added_raw = repository_raw(561, "repo")
added_raw["repositories"].append({
    "name": "aaa-new",
    "name_with_owner": "owner/aaa-new",
    "workflows": [],
})
second_layout = build_layout(
    build_snapshot(added_raw).to_dict(),
    previous_layout=first_layout,
)
first_origins = {
    item["entity_id"]: item["origin"]
    for item in first_layout["structures"]
}
second_origins = {
    item["entity_id"]: item["origin"]
    for item in second_layout["structures"]
}
assert all(
    second_origins[entity_id] == origin
    for entity_id, origin in first_origins.items()
)

try:
    build_layout(build_snapshot(repository_raw(751)).to_dict())
    raise AssertionError("structure cap should fail closed")
except ValueError as exc:
    assert "structures exceeds" in str(exc)

feature_cap_raw = repository_raw(31)
for repo_value in feature_cap_raw["repositories"]:
    repo_value["workflows"] = [
        {"id": index, "name": f"workflow-{index}", "state": "active"}
        for index in range(49)
    ]
try:
    build_layout(build_snapshot(feature_cap_raw).to_dict())
    raise AssertionError("feature cap should fail closed")
except ValueError as exc:
    assert "features exceeds" in str(exc)

operation_cap_raw = repository_raw(1)
operation_cap_raw["daemons"] = [
    {
        "label": f"com.rapp.failure-{index}",
        "loaded": False,
        "pid": None,
        "last_exit": 1,
    }
    for index in range(715)
]
try:
    build_layout(build_snapshot(operation_cap_raw).to_dict())
    raise AssertionError("operation cap should fail closed")
except ValueError as exc:
    assert "operations exceeds" in str(exc)

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
requests = repair_approval.read_json(repair_approval.REQUESTS, {})
requests[record["token"]]["expires_at"] = (
    requests[record["token"]]["expires_at"].replace("+00:00", "Z")
)
repair_approval.write_json(repair_approval.REQUESTS, requests)
record = requests[record["token"]]
dedupe_results = []
dedupe_threads = [
    threading.Thread(
        target=lambda: dedupe_results.append(
            repair_approval.request(
                "daemon:com.rapp.good",
                {
                    "id": "restart",
                    "label": "Restart",
                    "kind": "launchd_restart",
                    "payload": {"label": "com.rapp.good"},
                    "approval_required": True,
                },
                "player",
            )["token"]
        )
    )
    for _ in range(4)
]
for thread in dedupe_threads:
    thread.start()
for thread in dedupe_threads:
    thread.join()
assert dedupe_results == [record["token"]] * 4
assert len(repair_approval.read_json(repair_approval.REQUESTS, {})) == 1
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

cancel_request = repair_approval.request(
    "workflow:owner/repo:cancel",
    {
        "id": "rerun",
        "label": "Rerun",
        "kind": "github_rerun",
        "payload": {"repository": "owner/repo", "run_id": 456},
        "approval_required": True,
    },
    "player",
)
assert repair_approval.cancel(cancel_request["token"])["status"] == "cancelled"
try:
    repair_approval.execute(cancel_request["token"])
    raise AssertionError("cancelled token should not execute")
except ValueError:
    pass

future_request = repair_approval.request(
    "daemon:com.rapp.future",
    {
        "id": "restart",
        "label": "Restart",
        "kind": "launchd_restart",
        "payload": {"label": "com.rapp.future"},
        "approval_required": True,
    },
    "player",
)
requests = repair_approval.read_json(repair_approval.REQUESTS, {})
requests[future_request["token"]]["created_at"] = "2999-01-01T00:00:00Z"
requests[future_request["token"]]["expires_at"] = "2999-01-01T00:10:00Z"
repair_approval.write_json(repair_approval.REQUESTS, requests)
try:
    repair_approval.execute(future_request["token"])
    raise AssertionError("future-created token should fail")
except ValueError as exc:
    assert "creation time is in the future" in str(exc)
assert repair_approval.read_json(
    repair_approval.REQUESTS,
    {},
)[future_request["token"]]["status"] == "invalid"

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

original_state = city_daemon.STATE
original_collect_all = city_daemon.collect_all
original_publish = city_daemon.publish
city_daemon.STATE = tmp / "dry-run-state"
city_daemon.collect_all = lambda owner="kody-w": copy.deepcopy(RAW)
try:
    dry_result = city_daemon._tick(apply=False)
    assert dry_result["bridge"] == {"dry_run": True}
    assert not city_daemon.STATE.exists()
    city_daemon.publish = lambda layout: (
        (_ for _ in ()).throw(RuntimeError("injected publish failure"))
    )
    try:
        city_daemon._tick(apply=True)
        raise AssertionError("publish failure should propagate")
    except RuntimeError as exc:
        assert "injected publish failure" in str(exc)
    assert not city_daemon.STATE.exists()
finally:
    city_daemon.STATE = original_state
    city_daemon.collect_all = original_collect_all
    city_daemon.publish = original_publish

print("city model: 44 assertions passed")
