"""Canonical deterministic release check for Model Orchestra."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> int:
    rendered = " ".join(command)
    print(f"==> {rendered}")
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.endswith("_API_KEY") or name in {
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "GROQ_API_KEY",
            "SAMBANOVA_API_KEY",
        }:
            environment.pop(name, None)
    process = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    return process.returncode


def main() -> int:
    checks = [
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "tools/usefulness_benchmark.py", "--check"],
        [sys.executable, "tools/benchmark.py", "--check-baseline"],
        [sys.executable, "tests/test_resolve.py"],
        [sys.executable, "tools/snapshot_tool_schemas.py"],
        [sys.executable, "-m", "compileall", "-q", "server.py", "model_orchestra", "tools", "tests"],
    ]
    for command in checks:
        code = run(command)
        if code:
            print(f"FAILED ({code}): {' '.join(command)}")
            return code
    print("All deterministic release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
