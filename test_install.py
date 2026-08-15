#!/usr/bin/env python3
"""Installer rollback test when service restart fails."""

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap

root = pathlib.Path(__file__).resolve().parent
home = pathlib.Path(tempfile.mkdtemp(prefix="city-install-rollback-"))
script = textwrap.dedent(
    f"""
    import importlib.util
    import subprocess

    spec = importlib.util.spec_from_file_location(
        "installer", {str(root / "install.py")!r}
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install(service=False)
    (module.RUNTIME / "old-marker").write_text("old")
    module.PLIST.parent.mkdir(parents=True, exist_ok=True)
    module.PLIST.write_text("old plist")
    module.sys.platform = "darwin"
    module.bridge_ready = lambda: True
    failed = False
    calls = []
    def fake_run(command, **kwargs):
        global failed
        calls.append(command)
        if command[1] == "print":
            return subprocess.CompletedProcess(
                command, 0, stdout="loaded", stderr=""
            )
        if command[1] == "bootstrap" and kwargs.get("check") and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    module.subprocess.run = fake_run
    try:
        module.install(service=True)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("expected bootstrap failure")
    assert (module.RUNTIME / "old-marker").read_text() == "old"
    assert module.PLIST.read_text() == "old plist"
    assert sum(command[1] == "bootstrap" for command in calls) == 2
    print("city installer service rollback passed")
    """
)
result = subprocess.run(
    [sys.executable, "-c", script],
    capture_output=True,
    text=True,
    env={**os.environ, "HOME": str(home)},
)
assert result.returncode == 0, result.stderr
print(result.stdout.strip())
