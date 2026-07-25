"""Report release-readiness inventory from git status without mutating the tree."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent

GENERATED_PREFIXES = (
    ".model-orchestra-artifacts/",
    "docs/REPORT.",
    "docs/PROOF.",
    "docs/USEFULNESS_BENCHMARK.",
)
GENERATED_PATHS = {
    "tools/benchmark_baseline.json",
}
LOCAL_RUNTIME_PREFIXES = (
    ".pytest_cache/",
    ".pytest-cache/",
)
LOCAL_RUNTIME_PATHS = {
    ".model-orchestra-budget.json",
    ".model-orchestra-budget.json.tmp",
    ".model-orchestra-budget.sqlite3",
    ".model-orchestra-budget.sqlite3-shm",
    ".model-orchestra-budget.sqlite3-wal",
}
UNRELATED_PREFIXES = (
    ".obsidian/",
    ".idea/",
    ".vscode/",
)
UNRELATED_PATHS = {
    "docs/chat.md",
    "docs/test.md",
}


def _status_paths(lines: Iterable[str]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw in lines:
        if len(raw) < 4:
            continue
        status = raw[:2]
        path = raw[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"').replace("\\", "/")
        entries.append((status, path))
    return entries


def classify_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized in LOCAL_RUNTIME_PATHS or normalized.startswith(LOCAL_RUNTIME_PREFIXES):
        return "local_runtime_debris"
    if normalized in GENERATED_PATHS or normalized.startswith(GENERATED_PREFIXES):
        return "generated_reports_artifacts"
    if normalized in UNRELATED_PATHS or normalized.startswith(UNRELATED_PREFIXES):
        return "unrelated_workspace_changes"
    return "source_changes"


def inventory(root: Path = ROOT) -> dict[str, object]:
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        diagnostic = (process.stderr or process.stdout or "git status failed").strip()
        raise RuntimeError(diagnostic)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for status, path in _status_paths(process.stdout.splitlines()):
        groups[classify_path(path)].append({"status": status, "path": path})
    categories = (
        "source_changes",
        "generated_reports_artifacts",
        "local_runtime_debris",
        "unrelated_workspace_changes",
    )
    return {
        "schema_version": 1,
        "root": str(root.resolve()),
        "clean": not any(groups.values()),
        "categories": {category: groups.get(category, []) for category in categories},
    }


def render(report: dict[str, object]) -> str:
    lines = [f"Release readiness: {'clean' if report['clean'] else 'changes present'}"]
    categories = report["categories"]
    assert isinstance(categories, dict)
    for category, entries in categories.items():
        title = category.replace("_", " ").title()
        lines.append(f"\n{title} ({len(entries)})")
        lines.extend(f"  {item['status']} {item['path']}" for item in entries)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = inventory(args.root)
    except RuntimeError as error:
        print(f"ERROR: {error}")
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
