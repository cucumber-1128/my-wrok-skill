"""Checked subprocess boundaries for hc and user-owned event-source commands."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

from sa_state import StateError, safe_key


class TransportError(Exception):
    def __init__(self, message, *, kind="transport", code=None, uncertain=False):
        super().__init__(message)
        self.kind, self.code, self.uncertain = kind, code, uncertain


def terminal_state(info):
    if (not isinstance(info, dict) or not isinstance(info.get("state"), str) or
            info["state"] not in {"live", "finished"} or
            info["state"] == "live" and
            (not isinstance(info.get("address"), str) or not info["address"])):
        raise TransportError("terminal observation lacks an explicit state/address",
                             kind="invalid_response", uncertain=True)
    return info["state"]


def run_process(argv, *, timeout=60, cwd=None):
    if not argv or timeout <= 0 or timeout > 600:
        raise StateError("command and timeout (0,600] required")
    options = {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "text": True, "encoding": "utf-8", "errors": "replace", "cwd": cwd,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen([str(arg) for arg in argv], **options)
    except OSError as exc:
        raise TransportError(str(exc), kind="spawn_failed", uncertain=False) from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Only this freshly created command tree is ours; never target the core.
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.communicate()
        raise TransportError("command timed out; outcome requires reconciliation",
                             kind="timeout", uncertain=True) from exc
    if len(stdout) + len(stderr) > 8 * 1024 * 1024:
        raise TransportError("command output exceeds 8 MiB", kind="output_limit", uncertain=True)
    return process.returncode, stdout, stderr


class Hc:
    def __init__(self, command=None):
        self.command = command or [shutil.which("hc") or "hc"]

    def call(self, args, timeout=60):
        args = list(args)
        args.insert(1, "--json")
        code, stdout, stderr = run_process(self.command + args, timeout=timeout)
        value = None
        for text in (stdout, stderr):
            if text.strip():
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "error" in parsed:
                        error = parsed["error"]
                        if not isinstance(error, dict):
                            raise TransportError(str(error), kind="invalid_response", code=code, uncertain=True)
                        raise TransportError(
                            error.get("message", str(error)), kind=error.get("kind", "transport"),
                            code=code, uncertain=error.get("kind") not in {
                                "blocked", "not_found", "finished", "status_mismatch",
                                "invalid_params", "name_taken", "spawn_failed",
                            },
                        )
                    if text is stdout:
                        value = parsed
                except json.JSONDecodeError:
                    pass
        if code:
            raise TransportError((stderr or stdout or f"hc exit {code}")[:2000],
                                 code=code, uncertain=True)
        if not isinstance(value, dict):
            raise TransportError("hc returned invalid JSON", kind="invalid_response", uncertain=True)
        if args[0] == "get":
            terminal_state(value)
        return value


def run_source(source, state_root):
    timeout = float(source.get("timeout_sec", 30))
    argv = source.get("argv")
    if argv is not None:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise StateError("source argv must be a nonempty string array")
    else:
        command = source.get("command")
        if not isinstance(command, str) or not command.strip():
            raise StateError("source needs argv or a user-owned PowerShell command")
        command_dir = Path(state_root) / "sources"
        command_dir.mkdir(exist_ok=True)
        command_file = command_dir / f"{safe_key(source['name'])}.ps1"
        command_file.write_text(command, encoding="utf-8")
        argv = [
            shutil.which("pwsh") or "pwsh", "-NoProfile", "-NonInteractive",
            "-File", str(Path(__file__).with_name("sa-source-runner.ps1")),
            "-CommandFile", str(command_file),
        ]
    code, stdout, stderr = run_process(argv, timeout=timeout, cwd=source.get("cwd"))
    if code:
        raise StateError(f"source command exit {code}: {(stderr or stdout)[:1000]}")
    return stdout
