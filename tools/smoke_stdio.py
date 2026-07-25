#!/usr/bin/env python3
"""Dependency-free MCP stdio client smoke test for model-orchestra.

Speak JSON-RPC 2.0 over line-delimited stdio to an installed
``model-orchestra`` server (or any command given on the CLI) and report
PASS / FAIL for every step.

Exit code **0** only when every step passed; otherwise **1**.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

STABLE_TOOLS = [
    "batch_delegate",
    "cost_report",
    "delegate",
    "delegate_verified",
    "list_workers",
    "orchestrate_change",
    "orchestration_report",
    "route_preview",
]

PROTO_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "model-orchestra-smoke", "version": "1"}
MAX_TEXT = 400


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str) -> str:
    """Return *text* capped to ``MAX_TEXT`` characters."""
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT] + f"...[{len(text)} chars total]"


def _json_rpc(method: str, *, params: dict | None = None, id: int | None = None) -> str:
    """Build a single JSON-RPC 2.0 request or notification line."""
    msg: dict = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return json.dumps(msg)


class _StreamPump:
    """Drain a child pipe on a daemon thread.

    A blocking ``readline()`` cannot honour a deadline, and ``select()`` does
    not work on Windows pipes, so every stream gets its own thread. Draining
    also stops a chatty child from filling the pipe buffer and deadlocking.
    """

    def __init__(self, stream, *, keep_tail: bool = False) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._tail = ""
        self._keep_tail = keep_tail
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        try:
            for line in stream:
                if self._keep_tail:
                    self._tail = (self._tail + line)[-MAX_TEXT:]
                else:
                    self._queue.put(line)
        except Exception:  # noqa: BLE001 - the child may close the pipe abruptly
            pass
        finally:
            self._queue.put(None)

    def readline(self, deadline: float) -> str | None:
        """Return one line, or None on EOF or once *deadline* passes."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return self._queue.get(timeout=remaining)
        except queue.Empty:
            return None

    @property
    def tail(self) -> str:
        return self._tail


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _run(command: list[str], config: str, timeout: float, emit_json: bool) -> int:
    cmd = command + ["--config", config]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_pump = _StreamPump(proc.stdout)
    stderr_pump = _StreamPump(proc.stderr, keep_tail=True)

    report: dict = {
        "command": cmd,
        "config": config,
        "timeout": timeout,
        "steps": {},
        "tools_discovered": [],
        "all_stable_present": False,
        "shutdown_returncode": None,
        "stderr_tail": "",
        "elapsed_seconds": 0.0,
        "overall": "PASS",
    }

    def _record(name: str, ok: bool, *, detail: str = "") -> None:
        report["steps"][name] = {"status": "PASS" if ok else "FAIL"}
        if detail:
            report["steps"][name]["detail"] = _truncate(detail)
        if not ok:
            report["overall"] = "FAIL"

    t0 = time.monotonic()
    deadline = t0 + timeout

    try:
        # 1. initialize
        req = _json_rpc("initialize", params={
            "protocolVersion": PROTO_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, id=1)
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = stdout_pump.readline(deadline)
        if line is None:
            _record("initialize", False, detail="no response")
        else:
            try:
                resp = json.loads(line)
                ok = resp.get("id") == 1 and "result" in resp
                _record("initialize", ok, detail=_truncate(line))
            except json.JSONDecodeError as exc:
                _record("initialize", False, detail=f"bad JSON: {exc}")

        if report["steps"]["initialize"]["status"] != "PASS":
            # Cannot continue
            _finish(proc, report, t0, stderr_pump)
            return _exit(report, emit_json)

        # 2. notifications/initialized
        note = _json_rpc("notifications/initialized")
        proc.stdin.write(note + "\n")
        proc.stdin.flush()
        # It's a notification – no response expected; short pause only.
        time.sleep(0.05)
        _record("notifications/initialized", True)

        # 3. tools/list
        req = _json_rpc("tools/list", id=2)
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = stdout_pump.readline(deadline)
        tool_names: list[str] = []
        if line is None:
            _record("tools/list", False, detail="no response")
        else:
            try:
                resp = json.loads(line)
                ok = resp.get("id") == 2 and "result" in resp
                if ok:
                    tool_names = [t["name"] for t in resp["result"].get("tools", [])]
                _record("tools/list", ok, detail=_truncate(line))
            except json.JSONDecodeError as exc:
                _record("tools/list", False, detail=f"bad JSON: {exc}")

        report["tools_discovered"] = sorted(tool_names)
        missing_tools = [name for name in STABLE_TOOLS if name not in tool_names]
        report["all_stable_present"] = not missing_tools
        _record(
            "stable_tool_surface",
            not missing_tools,
            detail="" if not missing_tools else "missing: " + ", ".join(missing_tools),
        )

        # 4. tools/call – list_workers
        req = _json_rpc("tools/call", params={"name": "list_workers", "arguments": {}}, id=3)
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = stdout_pump.readline(deadline)
        if line is None:
            _record("tools/call/list_workers", False, detail="no response")
        else:
            try:
                resp = json.loads(line)
                ok = resp.get("id") == 3 and "result" in resp
                _record("tools/call/list_workers", ok, detail=_truncate(line))
            except json.JSONDecodeError as exc:
                _record("tools/call/list_workers", False, detail=f"bad JSON: {exc}")

        # 5. tools/call – route_preview
        req = _json_rpc("tools/call", params={
            "name": "route_preview",
            "arguments": {"task": "rename a local variable in one file"},
        }, id=4)
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = stdout_pump.readline(deadline)
        if line is None:
            _record("tools/call/route_preview", False, detail="no response")
        else:
            try:
                resp = json.loads(line)
                ok = resp.get("id") == 4 and "result" in resp
                _record("tools/call/route_preview", ok, detail=_truncate(line))
            except json.JSONDecodeError as exc:
                _record("tools/call/route_preview", False, detail=f"bad JSON: {exc}")

    except Exception as exc:  # noqa: BLE001
        report["overall"] = "FAIL"
        report["steps"]["exception"] = {"status": "FAIL", "detail": _truncate(str(exc))}

    _finish(proc, report, t0, stderr_pump)
    return _exit(report, emit_json)


def _finish(proc: subprocess.Popen, report: dict, t0: float, stderr_pump: "_StreamPump") -> None:
    """Close stdin and collect the exit code."""
    try:
        proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass

    try:
        proc.wait(timeout=report["timeout"])
        report["shutdown_returncode"] = proc.returncode
        if proc.returncode != 0:
            report["overall"] = "FAIL"
            report["steps"]["shutdown"] = {
                "status": "FAIL",
                "detail": f"returncode={proc.returncode}",
            }
        else:
            report["steps"]["shutdown"] = {"status": "PASS", "detail": "clean exit"}
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        report["shutdown_returncode"] = proc.returncode or -1
        report["overall"] = "FAIL"
        report["steps"]["shutdown"] = {
            "status": "FAIL",
            "detail": "process did not exit within timeout; terminated",
        }

    report["stderr_tail"] = _truncate(stderr_pump.tail)
    report["elapsed_seconds"] = round(time.monotonic() - t0, 3)


def _exit(report: dict, emit_json: bool) -> int:
    if emit_json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 0 if report["overall"] == "PASS" else 1


def _print_human(report: dict) -> None:
    print("=== model-orchestra stdio smoke test ===")
    print(f"command : {' '.join(report['command'])}")
    print(f"config  : {report['config']}")
    print(f"timeout : {report['timeout']}s")
    print()
    for name, info in report["steps"].items():
        tag = info["status"]
        detail = info.get("detail", "")
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {name}{suffix}")
    print()
    print(f"Tools discovered ({len(report['tools_discovered'])}): "
          f"{', '.join(report['tools_discovered'])}")
    print(f"All 8 stable tools present: {report['all_stable_present']}")
    print(f"Shutdown returncode: {report['shutdown_returncode']}")
    if report["stderr_tail"]:
        print(f"Stderr tail: {report['stderr_tail'].strip()}")
    print(f"Elapsed: {report['elapsed_seconds']}s")
    print(f"Overall: {report['overall']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke_stdio",
        description="MCP stdio client smoke test for model-orchestra",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run (e.g. model-orchestra serve)",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="path passed as --config to the server",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60,
        help="seconds before the test is aborted (default: 60)",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        default=False,
        help="emit a machine-readable JSON report to stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    command = args.command
    # argparse.REMAINDER may include an empty string when called with no
    # positional args; strip it.
    if command and command[0] in {"", "--"}:
        command = command[1:]
    if not command:
        parser.error("the command positional argument is required")
    executable = Path(command[0]).expanduser()
    if executable.exists():
        command[0] = str(executable.resolve())

    return _run(
        command=command,
        config=args.config,
        timeout=args.timeout,
        emit_json=args.emit_json,
    )


if __name__ == "__main__":
    sys.exit(main())
