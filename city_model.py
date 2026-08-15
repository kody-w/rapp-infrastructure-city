#!/usr/bin/env python3
"""Deterministic infrastructure model for the Minecraft city."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "rapp-infrastructure-city/1"
STATUSES = ("healthy", "active", "warning", "critical", "offline", "unknown")
SEVERITY = {
    "healthy": 0,
    "active": 1,
    "unknown": 2,
    "warning": 3,
    "offline": 4,
    "critical": 5,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def worst_status(statuses: Iterable[str], default: str = "unknown") -> str:
    known = [status for status in statuses if status in SEVERITY]
    return max(known, key=SEVERITY.get) if known else default


def workflow_status(workflow: Dict[str, Any]) -> str:
    state = str(workflow.get("state") or "active").lower()
    if state not in ("active", "enabled", "unknown"):
        return "offline"
    run = workflow.get("latest_run") or {}
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status in ("queued", "in_progress", "waiting", "requested", "pending"):
        return "active"
    if conclusion == "success":
        return "healthy"
    if conclusion in (
        "failure",
        "timed_out",
        "action_required",
        "startup_failure",
    ):
        return "critical"
    if conclusion in ("cancelled", "skipped", "neutral", "stale"):
        return "warning"
    return "unknown"


@dataclass
class Evidence:
    source: str
    detail: str
    observed_at: str
    url: Optional[str] = None


@dataclass
class RepairAction:
    id: str
    label: str
    kind: str
    payload: Dict[str, Any]
    approval_required: bool = True


@dataclass
class Entity:
    id: str
    kind: str
    name: str
    status: str
    evidence: List[Evidence] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    repairs: List[RepairAction] = field(default_factory=list)
    children: List["Entity"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    generated_at: str
    entities: List[Entity]

    def to_dict(self) -> Dict[str, Any]:
        counts = {status: 0 for status in STATUSES}
        kinds: Dict[str, int] = {}
        for entity in walk_entities(self.entities):
            counts[entity.status] = counts.get(entity.status, 0) + 1
            kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
        return {
            "schema": SCHEMA,
            "generated_at": self.generated_at,
            "summary": {
                "top_level": len(self.entities),
                "all_entities": sum(kinds.values()),
                "status_counts": counts,
                "kind_counts": kinds,
                "overall_status": worst_status(
                    (entity.status for entity in self.entities),
                    default="healthy",
                ),
            },
            "entities": [entity.to_dict() for entity in self.entities],
        }


def walk_entities(entities: Iterable[Entity]) -> Iterable[Entity]:
    for entity in entities:
        yield entity
        yield from walk_entities(entity.children)


def evidence(source: str, detail: str, observed_at: str, url: str = None) -> Evidence:
    return Evidence(source=source, detail=detail, observed_at=observed_at, url=url)


def build_snapshot(raw: Dict[str, Any]) -> Snapshot:
    observed_at = raw.get("observed_at") or utc_now()
    entities: List[Entity] = []

    for machine in sorted(raw.get("machines", []), key=lambda item: item["name"].lower()):
        online = machine.get("online")
        status = "healthy" if online else "offline"
        entities.append(
            Entity(
                id=f"machine:{machine['id']}",
                kind="machine",
                name=machine["name"],
                status=status,
                evidence=[
                    evidence(
                        "tailscale",
                        (
                            f"online={bool(online)} os={machine.get('os') or 'unknown'} "
                            f"ip={machine.get('ip') or 'unknown'}"
                        ),
                        observed_at,
                    )
                ],
                metrics={"os": machine.get("os"), "ip": machine.get("ip")},
            )
        )

    for daemon in sorted(raw.get("daemons", []), key=lambda item: item["label"]):
        loaded = daemon.get("loaded", True)
        running = bool(daemon.get("pid"))
        exit_code = daemon.get("last_exit")
        if running:
            status = "healthy"
        elif not loaded:
            status = "critical"
        elif exit_code in (0, "0", None):
            status = "warning"
        else:
            status = "critical"
        repairs = []
        if loaded and daemon.get("domain", "gui") == "gui":
            repairs.append(
                RepairAction(
                    id="restart",
                    label="Restart supervised daemon",
                    kind="launchd_restart",
                    payload={"label": daemon["label"]},
                )
            )
        entities.append(
            Entity(
                id=f"daemon:{daemon['label']}",
                kind="daemon",
                name=daemon["label"],
                status=status,
                evidence=[
                    evidence(
                        "launchd",
                        (
                            f"domain={daemon.get('domain') or 'gui'} "
                            f"loaded={loaded} pid={daemon.get('pid') or '-'} "
                            f"last_exit={exit_code}"
                        ),
                        observed_at,
                    )
                ],
                metrics={
                    "pid": daemon.get("pid"),
                    "last_exit": exit_code,
                    "domain": daemon.get("domain", "gui"),
                },
                repairs=repairs,
            )
        )

    for sentinel in sorted(raw.get("sentinels", []), key=lambda item: item["name"]):
        status = sentinel.get("status")
        if status not in STATUSES:
            status = "unknown"
        entities.append(
            Entity(
                id=f"sentinel:{sentinel['id']}",
                kind="sentinel",
                name=sentinel["name"],
                status=status,
                evidence=[
                    evidence(
                        sentinel.get("source", "heartbeat"),
                        sentinel.get("detail", "no detail"),
                        sentinel.get("observed_at", observed_at),
                    )
                ],
                metrics={"age_seconds": sentinel.get("age_seconds")},
            )
        )

    for repo in sorted(raw.get("repositories", []), key=lambda item: item["name"].lower()):
        workflows = []
        for workflow in sorted(
            repo.get("workflows", []),
            key=lambda item: item["name"].lower(),
        ):
            status = workflow_status(workflow)
            run = workflow.get("latest_run") or {}
            repairs = []
            if run.get("database_id") and status in ("critical", "warning"):
                repairs.append(
                    RepairAction(
                        id="rerun",
                        label="Rerun latest workflow",
                        kind="github_rerun",
                        payload={
                            "repository": repo["name_with_owner"],
                            "run_id": run["database_id"],
                        },
                    )
                )
            workflows.append(
                Entity(
                    id=f"workflow:{repo['name_with_owner']}:{workflow['id']}",
                    kind="workflow",
                    name=workflow["name"],
                    status=status,
                    evidence=[
                        evidence(
                            "github-actions",
                            (
                                f"state={workflow.get('state') or 'unknown'} "
                                f"status={run.get('status') or 'none'} "
                                f"conclusion={run.get('conclusion') or 'none'}"
                            ),
                            observed_at,
                            run.get("url") or workflow.get("url"),
                        )
                    ],
                    metrics={"path": workflow.get("path")},
                    repairs=repairs,
                )
            )
        workflow_health = [
            item.status for item in workflows if item.status != "unknown"
        ]
        if repo.get("archived"):
            repo_status = "offline"
        else:
            repo_status = worst_status(workflow_health, default="healthy")
            if repo.get("collection_error") and repo_status == "healthy":
                repo_status = "warning"
        entities.append(
            Entity(
                id=f"repo:{repo['name_with_owner']}",
                kind="repository",
                name=repo["name"],
                status=repo_status,
                evidence=[
                    evidence(
                        "github",
                        (
                            f"archived={bool(repo.get('archived'))} "
                            f"private={bool(repo.get('private'))} "
                            f"pushed_at={repo.get('pushed_at') or 'unknown'} "
                            f"collection_error={repo.get('collection_error') or 'none'}"
                        ),
                        observed_at,
                        repo.get("url"),
                    )
                ],
                metrics={
                    "private": bool(repo.get("private")),
                    "pushed_at": repo.get("pushed_at"),
                    "workflow_count": len(workflows),
                },
                children=workflows,
            )
        )

    return Snapshot(generated_at=observed_at, entities=entities)
