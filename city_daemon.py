#!/usr/bin/env python3
"""Collect, render, and publish the live infrastructure city."""

import argparse
import fcntl
import http.client
import json
import os
import sys
import tempfile
import time
import secrets
from contextlib import contextmanager
from pathlib import Path

from city_collector import collect_all
from city_layout import build_layout
from city_model import build_snapshot

HOME = Path.home()
STATE = HOME / ".rapp" / "hub" / "minecraft" / "infrastructure-city"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 25575
BOT_ID = os.environ.get("RAPP_CITY_BOT_ID", "rapp_brainstem")
CREDENTIAL = (
    HOME / ".rapp" / "hub" / "minecraft" / "credentials" / f"{BOT_ID}.secret"
)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def active_layout():
    path = STATE / "active-layout.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") == "rapp-infrastructure-city-layout/1":
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def publish(layout):
    credential = CREDENTIAL.read_text(encoding="utf-8").strip()
    body = json.dumps(layout).encode()
    connection = http.client.HTTPConnection(
        BRIDGE_HOST,
        BRIDGE_PORT,
        timeout=300,
    )
    try:
        connection.request(
            "POST",
            "/v1/infrastructure-city/apply",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-RAPP-Minecraft-Credential": credential,
            },
        )
        response = connection.getresponse()
        detail = response.read().decode("utf-8", errors="replace")
        if response.status != 200:
            raise RuntimeError(
                f"Minecraft bridge rejected city ({response.status}): {detail[:500]}"
            )
        return json.loads(detail)
    finally:
        connection.close()


@contextmanager
def tick_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    handle = open(STATE / ".tick.lock", "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _tick(owner="kody-w", apply=True):
    raw = collect_all(owner=owner)
    snapshot = build_snapshot(raw).to_dict()
    layout = build_layout(snapshot, previous_layout=active_layout())
    generation = secrets.token_hex(12)
    snapshot["generation"] = generation
    layout["generation"] = generation
    result = publish(layout) if apply else {"dry_run": True}
    if apply:
        atomic_json(STATE / "snapshot.json", snapshot)
        atomic_json(STATE / "layout.json", layout)
        atomic_json(
            STATE / "last-run.json",
            {
                "at": snapshot["generated_at"],
                "generation": generation,
                "status": snapshot["summary"]["overall_status"],
                "summary": snapshot["summary"],
                "layout": layout["summary"],
                "bridge": result,
            },
        )
    output = {
        "status": snapshot["summary"]["overall_status"],
        "entities": snapshot["summary"]["all_entities"],
        "structures": layout["summary"]["structures"],
        "features": layout["summary"]["features"],
        "bridge": result,
    }
    print(json.dumps(output), flush=True)
    return output


def tick(owner="kody-w", apply=True):
    with tick_lock() as acquired:
        if not acquired:
            print(json.dumps({"status": "skipped", "reason": "tick already running"}))
            return
        return _tick(owner=owner, apply=apply)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="kody-w")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    if not args.loop:
        tick(args.owner, apply=not args.dry_run)
        return 0
    while True:
        try:
            tick(args.owner, apply=not args.dry_run)
        except Exception as exc:
            print(
                f"city tick failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
