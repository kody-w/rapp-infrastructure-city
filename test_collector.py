#!/usr/bin/env python3
"""Collector contract tests for real GitHub field shapes and cache ownership."""

import json
import pathlib
import tempfile

import city_collector

original_run_json = city_collector.run_json
original_collect_repository = city_collector.collect_repository


def fake_run_json(command, timeout=30):
    joined = " ".join(command)
    if "/actions/workflows/" in joined and "/runs?per_page=1" in joined:
        workflow_id = int(joined.split("/actions/workflows/", 1)[1].split("/", 1)[0])
        return {
            "workflow_runs": [{
                "id": 100 + workflow_id,
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-08-15T00:00:00Z",
                "html_url": "https://example.invalid/run",
            }]
        }
    if "actions/workflows" in joined:
        return {
            "workflows": [
                {
                    "id": 1,
                    "name": "CI",
                    "path": ".github/workflows/ci-one.yml",
                    "state": "active",
                    "html_url": "https://example.invalid/workflow/1",
                },
                {
                    "id": 2,
                    "name": "CI",
                    "path": ".github/workflows/ci-two.yml",
                    "state": "active",
                    "html_url": "https://example.invalid/workflow/2",
                },
            ]
        }
    if "actions/runs?per_page=100" in joined:
        return {"workflow_runs": []}
    raise AssertionError(command)


city_collector.run_json = fake_run_json
try:
    repo = city_collector.collect_repository(
        "owner",
        {
            "name": "repo",
            "url": "https://example.invalid/repo",
            "pushedAt": "2026-08-15T00:00:00Z",
            "isArchived": False,
            "isPrivate": False,
        },
    )
finally:
    city_collector.run_json = original_run_json

runs = [workflow["latest_run"]["database_id"] for workflow in repo["workflows"]]
assert runs == [101, 102]

tmp = pathlib.Path(tempfile.mkdtemp(prefix="city-cache-test-"))
cache = tmp / "cache.json"
cache.write_text(json.dumps({
    "owner": "wrong-owner",
    "repositories": [{"name_with_owner": "wrong-owner/repo"}],
}))
city_collector.run_json = lambda command, timeout=30: []
city_collector.collect_repository = lambda owner, repo: repo
try:
    repos = city_collector.collect_repositories(
        owner="right-owner",
        cache_path=cache,
        cache_ttl=10**9,
    )
finally:
    city_collector.run_json = original_run_json
    city_collector.collect_repository = original_collect_repository
assert repos == []
updated = json.loads(cache.read_text())
assert updated["owner"] == "right-owner"

legacy = tmp / "legacy.json"
legacy.write_text(json.dumps([{
    "name": "repo",
    "name_with_owner": "right-owner/repo",
}]))
assert city_collector.cached_repositories(
    json.loads(legacy.read_text()),
    "right-owner",
)[0]["name"] == "repo"
assert city_collector.cached_repositories(
    json.loads(legacy.read_text()),
    "wrong-owner",
) is None

print("city collector: 6 assertions passed")
