#!/usr/bin/env python3
"""Collect every durable evidence source represented by the city."""

import concurrent.futures
import json
import os
import plistlib
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

HOME = Path.home()
DEFAULT_CACHE = HOME / ".rapp" / "hub" / "minecraft" / "infrastructure-city" / "github-cache.json"
TAILSCALE_SOCKET = HOME / ".local" / "share" / "tailscale-user" / "tailscaled.sock"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_json(command: List[str], timeout: int = 30) -> Any:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{command[0]} failed: {(result.stderr or result.stdout)[:300]}"
        )
    return json.loads(result.stdout)


def collect_machines() -> List[Dict[str, Any]]:
    if not TAILSCALE_SOCKET.exists():
        return []
    data = run_json(
        ["tailscale", f"--socket={TAILSCALE_SOCKET}", "status", "--json"],
        timeout=15,
    )
    machines = []
    self_value = data.get("Self") or {}
    if self_value:
        machines.append(
            {
                "id": self_value.get("StableID") or self_value.get("ID") or "self",
                "name": self_value.get("HostName") or "self",
                "online": bool(self_value.get("Online")),
                "os": self_value.get("OS"),
                "ip": (self_value.get("TailscaleIPs") or [None])[0],
            }
        )
    for peer in (data.get("Peer") or {}).values():
        machines.append(
            {
                "id": peer.get("StableID") or peer.get("ID") or peer.get("HostName"),
                "name": peer.get("HostName") or peer.get("DNSName") or "unknown",
                "online": bool(peer.get("Online")),
                "os": peer.get("OS"),
                "ip": (peer.get("TailscaleIPs") or [None])[0],
            }
        )
    return machines


def expected_launchd_services() -> List[Dict[str, str]]:
    roots = [
        (HOME / "Library" / "LaunchAgents", "gui"),
        (Path("/Library/LaunchDaemons"), "system"),
    ]
    prefixes = ("com.rapp.", "com.openrappter.", "com.brainstem.", "io.rapp.")
    services = {}
    for root, domain in roots:
        if not root.exists():
            continue
        for target in root.glob("*.plist"):
            try:
                with open(target, "rb") as handle:
                    label = plistlib.load(handle).get("Label")
                if label and label.startswith(prefixes):
                    services[label] = {"label": label, "domain": domain}
            except Exception:
                continue
    return [services[label] for label in sorted(services)]


def system_service(service: Dict[str, str]) -> Dict[str, Any]:
    label = service["label"]
    result = subprocess.run(
        ["launchctl", "print", f"system/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return {
            **service,
            "loaded": False,
            "pid": None,
            "last_exit": None,
        }
    pid = None
    running = False
    last_exit = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid = "):
            value = stripped.split("=", 1)[1].strip()
            pid = int(value) if value.isdigit() else None
        elif stripped.startswith("state = "):
            running = running or stripped.split("=", 1)[1].strip() == "running"
        elif stripped.startswith("last exit code = "):
            value = stripped.split("=", 1)[1].strip()
            last_exit = int(value) if value.lstrip("-").isdigit() else None
    return {
        **service,
        "loaded": True,
        "pid": pid if running or pid else None,
        "last_exit": last_exit,
    }


def collect_daemons() -> List[Dict[str, Any]]:
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    loaded = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, last_exit, label = parts
        loaded[label] = {
            "label": label,
            "loaded": True,
            "pid": int(pid) if pid.isdigit() else None,
            "last_exit": int(last_exit) if last_exit.lstrip("-").isdigit() else None,
        }
    output = []
    for service in expected_launchd_services():
        label = service["label"]
        if service["domain"] == "system":
            output.append(system_service(service))
            continue
        output.append(
            {
                **loaded.get(
                    label,
                    {
                        "label": label,
                        "loaded": False,
                        "pid": None,
                        "last_exit": None,
                    },
                ),
                "domain": "gui",
            }
        )
    return output


def heartbeat(
    identifier: str,
    name: str,
    path: Path,
    healthy_seconds: int = 900,
) -> Dict[str, Any]:
    observed = now_iso()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        stamp = value.get("at") or value.get("updated_at")
        if stamp:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            age = max(
                0,
                (datetime.now(timezone.utc) - when).total_seconds(),
            )
        else:
            age = max(0, time.time() - path.stat().st_mtime)
        declared = str(value.get("status") or "healthy").lower()
        if declared == "critical":
            status = "critical"
        elif age > healthy_seconds * 4:
            status = "critical"
        elif declared == "degraded" or age > healthy_seconds:
            status = "warning"
        else:
            status = "healthy"
        detail = (
            f"status={declared} age={int(age)}s "
            f"failed={','.join(value.get('failed') or []) or 'none'}"
        )
        return {
            "id": identifier,
            "name": name,
            "status": status,
            "source": str(path).replace(str(HOME), "~"),
            "detail": detail,
            "observed_at": observed,
            "age_seconds": int(age),
        }
    except Exception as exc:
        return {
            "id": identifier,
            "name": name,
            "status": "critical",
            "source": str(path).replace(str(HOME), "~"),
            "detail": f"heartbeat unreadable: {type(exc).__name__}",
            "observed_at": observed,
            "age_seconds": None,
        }


def file_heartbeat(
    identifier: str,
    name: str,
    path: Path,
    healthy_seconds: int,
) -> Dict[str, Any]:
    observed = now_iso()
    try:
        age = max(0, time.time() - path.stat().st_mtime)
        if age <= healthy_seconds:
            status = "healthy"
        elif age <= healthy_seconds * 4:
            status = "warning"
        else:
            status = "critical"
        return {
            "id": identifier,
            "name": name,
            "status": status,
            "source": str(path).replace(str(HOME), "~"),
            "detail": f"log heartbeat age={int(age)}s",
            "observed_at": observed,
            "age_seconds": int(age),
        }
    except Exception as exc:
        return {
            "id": identifier,
            "name": name,
            "status": "critical",
            "source": str(path).replace(str(HOME), "~"),
            "detail": f"log heartbeat unreadable: {type(exc).__name__}",
            "observed_at": observed,
            "age_seconds": None,
        }


def collect_sentinels() -> List[Dict[str, Any]]:
    return [
        heartbeat(
            "localfirsttools",
            "localFirstTools sentinel",
            HOME
            / ".rapp-sentinel"
            / "localfirsttools"
            / "state"
            / "last_run.json",
        ),
        file_heartbeat(
            "voice-assistant",
            "Google Voice assistant",
            HOME / ".rappter-chrome" / "voice-assistant.log",
            healthy_seconds=180,
        ),
    ]


def read_cache(path: Path, ttl: int = None) -> Any:
    try:
        if ttl is not None and time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def cached_repositories(payload: Any, owner: str):
    if (
        isinstance(payload, dict)
        and payload.get("owner") == owner
        and isinstance(payload.get("repositories"), list)
    ):
        return payload["repositories"]
    # Migrate the original list-only cache only when every row proves it
    # belongs to the requested owner.
    if (
        isinstance(payload, list)
        and all(
            str(item.get("name_with_owner") or "").startswith(f"{owner}/")
            for item in payload
        )
    ):
        return payload
    return None


def collect_repository(owner: str, repo: Dict[str, Any]) -> Dict[str, Any]:
    name = repo["name"]
    full = f"{owner}/{name}"
    workflows_payload = run_json(
        ["gh", "api", f"repos/{full}/actions/workflows?per_page=100"],
        timeout=30,
    )
    runs_payload = run_json(
        ["gh", "api", f"repos/{full}/actions/runs?per_page=100"],
        timeout=30,
    )
    latest_by_id = {}
    for run in runs_payload.get("workflow_runs", []):
        latest_by_id.setdefault(
            run.get("workflow_id"),
            {
                "databaseId": run.get("id"),
                "database_id": run.get("id"),
                "workflowName": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "createdAt": run.get("created_at"),
                "url": run.get("html_url"),
            },
        )
    missing = [
        workflow
        for workflow in workflows_payload.get("workflows", [])
        if workflow["id"] not in latest_by_id
    ]

    def latest_for_workflow(workflow):
        payload = run_json(
            [
                "gh",
                "api",
                (
                    f"repos/{full}/actions/workflows/{workflow['id']}"
                    "/runs?per_page=1"
                ),
            ],
            timeout=30,
        )
        values = payload.get("workflow_runs") or []
        if not values:
            return workflow["id"], None
        value = values[0]
        return workflow["id"], {
            "databaseId": value.get("id"),
            "database_id": value.get("id"),
            "workflowName": workflow["name"],
            "status": value.get("status"),
            "conclusion": value.get("conclusion"),
            "createdAt": value.get("created_at"),
            "url": value.get("html_url"),
        }

    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for workflow_id, value in pool.map(latest_for_workflow, missing):
                latest_by_id[workflow_id] = value
    workflows = []
    for workflow in workflows_payload.get("workflows", []):
        workflows.append(
            {
                "id": workflow["id"],
                "name": workflow["name"],
                "path": workflow.get("path"),
                "state": workflow.get("state"),
                "url": workflow.get("html_url"),
                "latest_run": latest_by_id.get(workflow["id"]),
            }
        )
    return {
        "name": name,
        "name_with_owner": full,
        "url": repo.get("url"),
        "pushed_at": repo.get("pushedAt"),
        "archived": bool(repo.get("isArchived")),
        "private": bool(repo.get("isPrivate")),
        "workflows": workflows,
    }


def collect_repositories(
    owner: str = "kody-w",
    cache_path: Path = DEFAULT_CACHE,
    cache_ttl: int = 900,
) -> List[Dict[str, Any]]:
    cached = read_cache(cache_path, cache_ttl)
    fresh = cached_repositories(cached, owner)
    if fresh is not None:
        return fresh
    stale = cached_repositories(
        read_cache(cache_path),
        owner,
    )
    stale_by_name = {
        item["name"]: item
        for item in (stale or [])
        if item.get("name")
    }
    try:
        repos = run_json(
            [
                "gh",
                "repo",
                "list",
                owner,
                "--limit",
                "1000",
                "--json",
                "name,isArchived,isPrivate,pushedAt,url",
            ],
            timeout=60,
        )
    except Exception as exc:
        if stale is None:
            raise
        detail = f"repository list failed: {exc}"[:300]
        return [
            {
                **item,
                "collection_error": detail,
            }
            for item in stale
        ]
    output = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(collect_repository, owner, repo): repo["name"]
            for repo in repos
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                output.append(future.result())
            except Exception as exc:
                name = futures[future]
                detail = str(exc)
                errors.append((name, detail))
                fallback = stale_by_name.get(name)
                if fallback:
                    output.append({
                        **fallback,
                        "collection_error": detail[:300],
                    })
                else:
                    source = next(repo for repo in repos if repo["name"] == name)
                    output.append({
                        "name": name,
                        "name_with_owner": f"{owner}/{name}",
                        "url": source.get("url"),
                        "pushed_at": source.get("pushedAt"),
                        "archived": bool(source.get("isArchived")),
                        "private": bool(source.get("isPrivate")),
                        "workflows": [],
                        "collection_error": detail[:300],
                    })
    output.sort(key=lambda item: item["name"].lower())
    write_cache(cache_path, {"owner": owner, "repositories": output})
    return output


def collect_all(owner: str = "kody-w") -> Dict[str, Any]:
    return {
        "observed_at": now_iso(),
        "machines": collect_machines(),
        "daemons": collect_daemons(),
        "sentinels": collect_sentinels(),
        "repositories": collect_repositories(owner=owner),
    }
