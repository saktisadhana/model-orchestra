"""Regression checks for clean-checkout CI determinism."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "public-alpha.yml",
)


def test_feature_branch_updates_run_only_pull_request_workflows() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert re.search(r"(?m)^  push:\n    branches: \[main\]$", text), workflow
        assert re.search(
            r"(?m)^  pull_request:\n    branches: \[main\]$", text
        ), workflow


def test_standalone_resolver_check_matches_tracked_config() -> None:
    environment = os.environ.copy()
    environment["MODEL_ORCHESTRA_CONFIG"] = str(ROOT / "config.json")
    process = subprocess.run(
        [sys.executable, "tests/test_resolve.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_mcp_dependency_stays_on_fastmcp_compatible_major() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "mcp>=1.2.0,<2" in dependencies
    assert "mcp>=1.2.0,<2" in (ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines()
