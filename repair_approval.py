#!/usr/bin/env python3
"""One-time approval gate for Minecraft-originated repair requests."""

import json
import fcntl
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from contextlib import contextmanager

STATE = Path(
    os.environ.get(
        "RAPP_CITY_STATE_DIR",
        Path.home() / ".rapp" / "hub" / "minecraft" / "infrastructure-city",
    )
)
REQUESTS = STATE / "repair-requests.json"
AUDIT = STATE / "repair-audit.jsonl"
LABEL = re.compile(r"^(com\.(?:rapp|openrappter|brainstem)\.|io\.rapp\.)[A-Za-z0-9_.-]+$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def now():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or now()).isoformat(timespec="seconds")


def parse_iso(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def valid_repository(value):
    text = str(value or "")
    if not REPO.fullmatch(text):
        return False
    owner, name = text.split("/", 1)
    return owner not in (".", "..") and name not in (".", "..")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def audit(record):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": iso(), **record}) + "\n")


@contextmanager
def request_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    handle = open(STATE / ".repair.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def request(entity_id: str, action: Dict[str, Any], player: str) -> Dict[str, Any]:
    if not action.get("approval_required", True):
        raise ValueError("repair action must require approval")
    deduplicated = False
    with request_lock():
        requests = read_json(REQUESTS, {})
        for existing in requests.values():
            try:
                current = now()
                live = (
                    parse_iso(existing["created_at"])
                    <= current + timedelta(minutes=5)
                    and current <= parse_iso(existing["expires_at"])
                )
            except Exception:
                live = False
            if (
                live
                and existing.get("status") == "pending"
                and existing.get("entity_id") == entity_id
                and existing.get("player") == player
                and existing.get("action") == action
            ):
                record = existing
                deduplicated = True
                break
        else:
            token = secrets.token_hex(3).upper()
            while token in requests:
                token = secrets.token_hex(3).upper()
            record = {
                "token": token,
                "entity_id": entity_id,
                "action": action,
                "player": player,
                "created_at": iso(),
                "expires_at": iso(now() + timedelta(minutes=10)),
                "status": "pending",
            }
            requests[token] = record
            write_json(REQUESTS, requests)
    audit({
        "event": "deduplicated" if deduplicated else "requested",
        "token": record["token"],
        "entity_id": entity_id,
        "player": player,
    })
    return record


def execute(token: str) -> Dict[str, Any]:
    with request_lock():
        requests = read_json(REQUESTS, {})
        record = requests.get(token)
        if not record or record.get("status") != "pending":
            raise ValueError("unknown or already-consumed approval token")
        if parse_iso(record["created_at"]) > now() + timedelta(minutes=5):
            record["status"] = "invalid"
            write_json(REQUESTS, requests)
            audit({"event": "invalid", "token": token, "reason": "future created_at"})
            raise ValueError("approval token creation time is in the future")
        if now() > parse_iso(record["expires_at"]):
            record["status"] = "expired"
            write_json(REQUESTS, requests)
            audit({"event": "expired", "token": token})
            raise ValueError("approval token expired")
        # Consume before the irreversible command. A crash leaves "executing"
        # for human reconciliation and can never run the token twice.
        record["status"] = "executing"
        record["execution_started_at"] = iso()
        write_json(REQUESTS, requests)

    try:
        action = record["action"]
        kind = action["kind"]
        payload = action["payload"]
        if kind == "launchd_restart":
            label = str(payload.get("label") or "")
            if not LABEL.fullmatch(label):
                raise ValueError("launchd label is not allowed")
            command = [
                "launchctl",
                "kickstart",
                "-k",
                f"gui/{os.getuid()}/{label}",
            ]
        elif kind == "github_rerun":
            repository = str(payload.get("repository") or "")
            run_id = str(payload.get("run_id") or "")
            if not valid_repository(repository) or not run_id.isdigit():
                raise ValueError("GitHub repair payload is not allowed")
            command = ["gh", "run", "rerun", run_id, "-R", repository]
        else:
            raise ValueError(f"repair kind is not executable: {kind}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        with request_lock():
            requests = read_json(REQUESTS, {})
            failed = requests[token]
            failed["status"] = "failed"
            failed["executed_at"] = iso()
            failed["error"] = f"{type(exc).__name__}: {exc}"[:1000]
            write_json(REQUESTS, requests)
        audit({
            "event": "failed",
            "token": token,
            "entity_id": record["entity_id"],
            "error": type(exc).__name__,
        })
        raise
    with request_lock():
        requests = read_json(REQUESTS, {})
        record = requests[token]
        record["status"] = "executed" if result.returncode == 0 else "failed"
        record["executed_at"] = iso()
        record["returncode"] = result.returncode
        record["result"] = (result.stdout or result.stderr or "")[:1000]
        write_json(REQUESTS, requests)
    audit(
        {
            "event": record["status"],
            "token": token,
            "entity_id": record["entity_id"],
            "returncode": result.returncode,
        }
    )
    return record


def cancel(token: str) -> Dict[str, Any]:
    with request_lock():
        requests = read_json(REQUESTS, {})
        record = requests.get(token)
        if not record or record.get("status") != "pending":
            raise ValueError("unknown or non-pending approval token")
        record["status"] = "cancelled"
        record["cancelled_at"] = iso()
        write_json(REQUESTS, requests)
    audit({
        "event": "cancelled",
        "token": token,
        "entity_id": record["entity_id"],
    })
    return record


def main():
    if len(sys.argv) < 2:
        print("usage: repair_approval.py list | approve TOKEN | cancel TOKEN")
        return 2
    if sys.argv[1] == "list":
        print(json.dumps(read_json(REQUESTS, {}), indent=2))
        return 0
    if sys.argv[1] == "approve" and len(sys.argv) == 3:
        print(json.dumps(execute(sys.argv[2].upper()), indent=2))
        return 0
    if sys.argv[1] == "cancel" and len(sys.argv) == 3:
        print(json.dumps(cancel(sys.argv[2].upper()), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
