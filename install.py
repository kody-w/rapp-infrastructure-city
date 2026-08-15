#!/usr/bin/env python3
"""Install the Minecraft infrastructure-city collector as a resident service."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
HOME = Path.home()
ROOT = HOME / ".rapp" / "hub" / "minecraft" / "infrastructure-city"
RUNTIME = ROOT / "runtime"
PLIST = HOME / "Library" / "LaunchAgents" / "com.rapp.infrastructure-city.plist"
LABEL = "com.rapp.infrastructure-city"
FILES = [
    "city_collector.py",
    "city_daemon.py",
    "city_layout.py",
    "city_model.py",
    "repair_approval.py",
    "test_city.py",
    "test_collector.py",
    "test_install.py",
    "install.py",
    "install.sh",
    "com.rapp.infrastructure-city.plist.template",
]


@contextmanager
def install_lock():
    ROOT.mkdir(parents=True, exist_ok=True)
    handle = open(ROOT / ".install.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def bridge_ready():
    try:
        import urllib.request

        with urllib.request.urlopen(
            "http://127.0.0.1:25575/health",
            timeout=5,
        ) as response:
            value = json.loads(response.read())
        return "infrastructure_city" in value
    except Exception:
        return False


def install(service=True):
    missing = [name for name in FILES if not (SOURCE / name).is_file()]
    if missing:
        raise RuntimeError(f"missing runtime files: {', '.join(missing)}")
    if service and not bridge_ready():
        raise RuntimeError(
            "Minecraft bridge is not infrastructure-city aware; "
            "deploy RAPPhub PR #25 first"
        )

    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".runtime.stage-", dir=RUNTIME.parent))
    backup = None
    old_plist = PLIST.read_bytes() if PLIST.exists() else None
    target = f"gui/{os.getuid()}/{LABEL}"
    old_loaded = (
        service
        and sys.platform == "darwin"
        and subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
        ).returncode == 0
    )
    try:
        for name in FILES:
            shutil.copy2(SOURCE / name, stage / name)
            os.chmod(
                stage / name,
                0o700 if name.endswith((".py", ".sh")) else 0o600,
            )
        if RUNTIME.exists():
            backup = RUNTIME.with_name(f".runtime.backup-{os.getpid()}")
            if backup.exists():
                shutil.rmtree(backup)
            RUNTIME.rename(backup)
        stage.rename(RUNTIME)
        template = (
            RUNTIME / "com.rapp.infrastructure-city.plist.template"
        ).read_text(encoding="utf-8")
        rendered = (
            template.replace("__PYTHON__", str(Path(sys.executable).resolve()))
            .replace("__HOME__", str(HOME))
            .replace("__RUNTIME__", str(RUNTIME))
        )
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        temporary = PLIST.with_suffix(".plist.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, PLIST)

        if service and sys.platform == "darwin":
            subprocess.run(["launchctl", "bootout", target], check=False)
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)],
                check=True,
            )
            subprocess.run(["launchctl", "enable", target], check=False)
            subprocess.run(["launchctl", "kickstart", "-p", target], check=True)
        if backup:
            shutil.rmtree(backup)
    except Exception:
        if service and sys.platform == "darwin":
            subprocess.run(["launchctl", "bootout", target], check=False)
        if RUNTIME.exists():
            shutil.rmtree(RUNTIME)
        if backup and backup.exists():
            backup.rename(RUNTIME)
        if old_plist is None:
            PLIST.unlink(missing_ok=True)
        else:
            PLIST.parent.mkdir(parents=True, exist_ok=True)
            PLIST.write_bytes(old_plist)
            os.chmod(PLIST, 0o600)
        if old_loaded and PLIST.exists():
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)],
                check=False,
            )
            subprocess.run(["launchctl", "kickstart", "-p", target], check=False)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    print("Installed Rappter infrastructure city")
    print(f"  runtime: {RUNTIME}")
    print(f"  state:   {ROOT}")
    print(f"  service: {'running' if service else 'not started'}")
    print(f"  approve: python3 {RUNTIME / 'repair_approval.py'} approve TOKEN")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-service", action="store_true")
    args = parser.parse_args()
    with install_lock():
        install(service=not args.no_service)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
