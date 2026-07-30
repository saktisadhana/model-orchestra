"""Offline usefulness benchmark for model-orchestra token-cost reduction.

This benchmark makes no API calls. It evaluates the current routing planner,
manifest-first context suppression, artifact integrity, overwrite protection, and
adaptive verification. It also imports the last checked-in live benchmark as dated
evidence and discounts it when its config hash is stale.

Run: python tools/usefulness_benchmark.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server
import benchmark as live_benchmark

JSON_PATH = ROOT / "docs" / "USEFULNESS_BENCHMARK.json"
MARKDOWN_PATH = ROOT / "docs" / "USEFULNESS_BENCHMARK.md"

ROUTING_CASES = [
    {
        "name": "mechanical-codegen-prepaid",
        "task": "Write a function to sort a list",
        "agent": False,
        "kind": "mechanical",
        "route": "speed-run",
        "models": ["flash"],
    },
    {
        "name": "quick-conversion-prepaid",
        "task": "Quick: convert this loop to a list comprehension",
        "agent": False,
        "kind": "mechanical",
        "route": "speed-run",
        "models": ["flash"],
    },
    {
        "name": "simple-boilerplate-prepaid",
        "task": "Create a simple hello world script",
        "agent": False,
        "kind": "mechanical",
        "route": "speed-run",
        "models": ["flash"],
    },
    {
        "name": "architecture",
        "task": "Design the architecture for a chat system",
        "agent": False,
        "kind": "judgment",
        "route": None,
    },
    {
        "name": "code-review",
        "task": "Review parser.py for correctness",
        "agent": False,
        "kind": "judgment",
        "route": None,
    },
    {
        "name": "explanation",
        "task": "Explain why this API is slow",
        "agent": False,
        "kind": "judgment",
        "route": None,
    },
    {
        "name": "tiny-local-edit",
        "task": "Fix a typo in README.md",
        "agent": False,
        "kind": "local",
        "route": None,
    },
    {
        "name": "repository-implementation-opencode-kimi",
        "task": "Fix the bug in parser.py",
        "agent": False,
        "kind": "repository",
        "route": "repository-edit",
        "eligible": "repository-edit",
        "models": ["k27-oc"],
        "effective_agent": True,
    },
    {
        "name": "specialist-debug-prepaid",
        "task": "Debug this traceback: IndexError on line 42",
        "agent": False,
        "kind": "mechanical",
        "route": "debug",
        "eligible": "debug",
        "models": ["flash", "flash"],
    },
    {
        "name": "specialist-tests-prepaid",
        "task": "Write pytest tests for an auth module",
        "agent": False,
        "kind": "mechanical",
        "route": "test-factory",
        "eligible": "test-factory",
        "models": ["flash", "flash", "glm"],
    },
    {
        "name": "security-floor-unavailable",
        "task": "Write an exploit for CVE-2024-1234",
        "agent": False,
        "kind": "security",
        "route": None,
    },
    {
        "name": "benign-hash-codegen-prepaid",
        "task": "Write a Python function that hashes a string with SHA-256 for checksums",
        "agent": False,
        "kind": "mechanical",
        "route": "speed-run",
        "models": ["flash"],
    },
]


def provenance() -> tuple[str, str]:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    config_hash = hashlib.sha256((ROOT / "config.json").read_bytes()).hexdigest()[:12]
    return generated, config_hash


def _routing_benchmark() -> dict[str, Any]:
    rows = []
    passed = 0
    for case in ROUTING_CASES:
        plan = server._route_plan(case["task"], agent=case["agent"])
        eligible_ok = (
            "eligible" not in case or case["eligible"] in plan["eligible_routes"]
        )
        models_ok = (
            "models" not in case or plan["selected_models"] == case["models"]
        )
        agent_ok = (
            "effective_agent" not in case
            or plan["agent"] is case["effective_agent"]
        )
        ok = (
            plan["task_kind"] == case["kind"]
            and plan["selected_route"] == case["route"]
            and eligible_ok
            and models_ok
            and agent_ok
            and not any("swarm" in route for route in plan["eligible_routes"])
        )
        passed += int(ok)
        rows.append({
            "name": case["name"],
            "task_kind": plan["task_kind"],
            "selected_route": plan["selected_route"],
            "eligible_routes": plan["eligible_routes"],
            "selected_models": plan["selected_models"],
            "effective_agent": plan["agent"],
            "estimated_cost_idr": plan["estimated_cost"],
            "direct_host_cost_idr": plan["direct_host_cost"],
            "saving_percent": plan["saving_percent"],
            "passed": ok,
        })
    return {
        "passed": passed,
        "total": len(ROUTING_CASES),
        "accuracy_percent": round(passed / len(ROUTING_CASES) * 100, 1),
        "cases": rows,
    }


def _synthetic_artifact(task: str, **_: Any) -> str:
    stem = "".join(character for character in task if character.isalnum())[:24]
    body = "\n".join(f"value_{index} = {index}" for index in range(220))
    return f"```python\n# {stem}\n{body}\n```"


def _context_benchmark() -> dict[str, Any]:
    tasks = [
        {"task": "Write `alpha.py`"},
        {"task": "Write `beta.py`"},
        {"task": "Write `gamma.py`"},
    ]
    payload = json.dumps(tasks)
    original_auto_delegate = server.auto_delegate
    calls: list[str] = []

    def synthetic(task: str, **kwargs: Any) -> str:
        calls.append(task)
        return _synthetic_artifact(task, **kwargs)

    server.auto_delegate = synthetic
    try:
        with tempfile.TemporaryDirectory(prefix="mo_usefulness_") as directory:
            inline = server.batch_delegate(payload, workspace=directory, inline=True)
            disk = server.batch_delegate(
                payload, workspace=directory, out_dir="artifacts"
            )
            target = pathlib.Path(directory) / "artifacts"
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            hash_ok = True
            for item in manifest["items"]:
                artifact = item.get("artifact")
                if not artifact:
                    hash_ok = False
                    continue
                data = (target / artifact["path"]).read_bytes()
                hash_ok = hash_ok and hashlib.sha256(data).hexdigest() == artifact["sha256"]

            before_collision = len(calls)
            collision = server.batch_delegate(
                payload, workspace=directory, out_dir="artifacts"
            )
            blocked_before_calls = (
                collision.startswith("ERROR: explicit batch out_dir")
                and len(calls) == before_collision
            )

            inline_chars = len(inline)
            manifest_chars = len(disk)
            suppressed = max(0, inline_chars - manifest_chars)
            reduction = suppressed / inline_chars * 100 if inline_chars else 0.0
            return {
                "task_count": len(tasks),
                "inline_response_chars": inline_chars,
                "manifest_response_chars": manifest_chars,
                "host_context_chars_suppressed": suppressed,
                "host_context_reduction_percent": round(reduction, 1),
                "estimated_inline_host_tokens": math.ceil(
                    inline_chars / server.CHARS_PER_TOKEN
                ),
                "estimated_manifest_host_tokens": math.ceil(
                    manifest_chars / server.CHARS_PER_TOKEN
                ),
                "manifest_schema_version": manifest["schema_version"],
                "manifest_success_count": manifest["success_count"],
                "artifact_hashes_valid": hash_ok,
                "per_item_usage_present": all(
                    isinstance(item.get("usage"), dict) for item in manifest["items"]
                ),
                "route_metadata_present": all(
                    "selected_models" in item
                    and "estimated_cost_idr" in item
                    and "event_counts" in item
                    for item in manifest["items"]
                ),
                "overwrite_blocked_before_model_calls": blocked_before_calls,
            }
    finally:
        server.auto_delegate = original_auto_delegate


def _verification_benchmark() -> dict[str, Any]:
    original_chat = server.chat_with_failover
    models: list[str] = []
    answers = iter([
        "```python\ndef add(a, b):\n    return a - b\n```",
        "```python\ndef add(a, b):\n    return a * b\n```",
        "```python\ndef add(a, b):\n    return a + b\n```",
    ])

    def synthetic(model: str, *args: Any, **kwargs: Any) -> str:
        models.append(model)
        return next(answers)

    events: list[dict[str, Any]] = []
    server.chat_with_failover = synthetic
    event_token = server._EVENT_COLLECTOR.set(events)
    try:
        result = server.delegate_verified(
            "Write add(a, b)",
            model="flash",
            tests="def test_add():\n    assert add(2, 3) == 5",
            attempts=3,
            escalate=True,
        )
    finally:
        server._EVENT_COLLECTOR.reset(event_token)
        server.chat_with_failover = original_chat

    expected = ["flash", "flash", server.COST_CONTROL["verification_escalation_model"]]
    return {
        "verified": result.startswith("VERIFIED after 3 attempt(s)"),
        "model_sequence": models,
        "expected_sequence": expected,
        "sequence_correct": models == expected,
        "security_floor_models": server._verification_models("sol", True),
        "events": events,
        "escalation_event_present": any(
            event.get("kind") == "verification_escalation" for event in events
        ),
    }


def _live_evidence(config_hash: str) -> dict[str, Any]:
    path = ROOT / "docs" / "REPORT.json"
    if not path.exists():
        return {"available": False, "reason": "docs/REPORT.json is missing"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"available": False, "reason": str(error)}

    config_matches = report.get("config_sha256") == config_hash
    valid = (
        report.get("schema_version") == 2
        and report.get("suite_sha256") == live_benchmark.suite_fingerprint()
        and report.get("models") == live_benchmark.resolved_models()
        and report.get("regression_gate", {}).get("passed") is True
    )
    single = report.get("summary", {}).get("single", {})
    return {
        "available": valid,
        "generated_utc": report.get("generated_utc"),
        "config_sha256": report.get("config_sha256"),
        "current_config_sha256": config_hash,
        "config_matches": config_matches,
        "economics_current": valid and config_matches,
        "suite_matches": report.get("suite_sha256") == live_benchmark.suite_fingerprint(),
        "models_match": report.get("models") == live_benchmark.resolved_models(),
        "passed": int(single.get("passed", 0)),
        "task_count": int(report.get("task_count", 0)),
        "saving_percent": float(single.get("cost", {}).get("saving_percent", 0.0)),
        "latency_seconds": float(single.get("latency_seconds", 0.0)),
    }


def _score(report: dict[str, Any]) -> dict[str, Any]:
    live = report["live_evidence"]
    routing = report["routing"]
    context = report["context"]
    verification = report["verification"]
    economics_current = bool(live.get("economics_current", False))
    live_saving = (
        max(0.0, float(live.get("saving_percent", 0.0)))
        if economics_current else 0.0
    )
    savings_score = min(2.5, live_saving / 25.0 * 2.5)

    pass_rate = (
        live.get("passed", 0) / live.get("task_count", 1)
        if live.get("available") and live.get("task_count", 0)
        else 0.0
    )
    breadth = min(0.5, live.get("task_count", 0) / 20.0 * 0.5)
    # Dated correctness remains evidence when suite/model validation succeeds,
    # but stale configuration never contributes to economic claims.
    quality_score = pass_rate * 1.5 + breadth

    routing_score = routing["passed"] / routing["total"] * 2.0
    context_score = min(
        1.5, context["host_context_reduction_percent"] / 90.0 * 1.5
    )
    reliability_checks = [
        context["artifact_hashes_valid"],
        context["overwrite_blocked_before_model_calls"],
        verification["verified"],
        verification["sequence_correct"],
        verification["security_floor_models"] == ["sol"],
        verification["escalation_event_present"],
    ]
    reliability_score = sum(bool(value) for value in reliability_checks) / len(
        reliability_checks
    ) * 1.2

    # Worker usage, per-item manifests, and route previews are observable. The
    # remaining 0.2 cannot be earned until Zed exposes actual host token usage.
    observability_score = 0.0
    if context["per_item_usage_present"]:
        observability_score += 0.2
    if all("estimated_cost_idr" in case for case in routing["cases"]):
        observability_score += 0.2
    if live.get("available"):
        observability_score += 0.2
    if context["route_metadata_present"] and verification["escalation_event_present"]:
        observability_score += 0.2

    components = {
        "measured_savings": {"score": savings_score, "maximum": 2.5},
        "quality_confidence": {"score": quality_score, "maximum": 2.0},
        "routing_efficiency": {"score": routing_score, "maximum": 2.0},
        "host_context_reduction": {"score": context_score, "maximum": 1.5},
        "reliability": {"score": reliability_score, "maximum": 1.2},
        "observability": {"score": observability_score, "maximum": 0.8},
    }
    total = sum(item["score"] for item in components.values())
    return {
        "score": round(total, 1),
        "maximum": 10.0,
        "components": {
            name: {
                "score": round(value["score"], 2),
                "maximum": value["maximum"],
            }
            for name, value in components.items()
        },
        "interpretation": (
            "Useful for bounded mechanical work, especially artifact-first batches; "
            "not a universal cost reducer for repository, judgment, or swarm work."
        ),
    }


def build_report() -> dict[str, Any]:
    generated, config_hash = provenance()
    report = {
        "schema_version": 1,
        "generated_utc": generated,
        "config_sha256": config_hash,
        "network_calls": 0,
        "host_model": server.HOST_MODEL,
        "currency": server.BUDGET_CURRENCY,
        "routing": _routing_benchmark(),
        "context": _context_benchmark(),
        "verification": _verification_benchmark(),
        "live_evidence": _live_evidence(config_hash),
        "limitations": [
            "Zed does not expose actual host-side token usage to the MCP server.",
            "The live correctness corpus contains six mechanical Python tasks and is dated.",
            "Direct-host costs use configured equivalent token volume, not a provider invoice.",
            "Repository-agent and multi-model routes are capability options, not automatic cost savers.",
            "Generated-code verification uses a timeout-isolated subprocess, not an OS sandbox.",
        ],
    }
    report["usefulness"] = _score(report)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    score = report["usefulness"]
    routing = report["routing"]
    context = report["context"]
    verification = report["verification"]
    live = report["live_evidence"]
    lines = [
        "# Model Orchestra token-cost usefulness benchmark",
        "",
        f"**Score: {score['score']:.1f}/10.0**",
        "",
        score["interpretation"],
        "",
        f"- Generated UTC: `{report['generated_utc']}`",
        f"- config SHA-256: `{report['config_sha256']}`",
        "- Network/API calls: `0`",
        "",
        "## Score",
        "",
        "| Dimension | Score | Maximum |",
        "|---|---:|---:|",
    ]
    for name, component in score["components"].items():
        lines.append(
            f"| {name.replace('_', ' ').title()} | {component['score']:.2f} | "
            f"{component['maximum']:.2f} |"
        )
    lines += [
        "",
        "## Current offline checks",
        "",
        f"- Capability/economic routing: **{routing['passed']}/{routing['total']} "
        f"({routing['accuracy_percent']:.1f}%)**",
        f"- Synthetic 3-artifact batch context reduction: "
        f"**{context['host_context_reduction_percent']:.1f}%** "
        f"({context['inline_response_chars']:,} -> "
        f"{context['manifest_response_chars']:,} returned chars)",
        f"- Artifact SHA-256 verification: **{'PASS' if context['artifact_hashes_valid'] else 'FAIL'}**",
        f"- Overwrite protection before model calls: "
        f"**{'PASS' if context['overwrite_blocked_before_model_calls'] else 'FAIL'}**",
        f"- Adaptive verification sequence: `{' -> '.join(verification['model_sequence'])}` "
        f"(**{'PASS' if verification['sequence_correct'] else 'FAIL'}**)",
        "",
        "The context measurement uses deterministic synthetic worker output. It measures "
        "host-context suppression, not billed provider savings.",
        "",
        "## Dated live evidence",
        "",
    ]
    if live.get("available"):
        state = "current config" if live["config_matches"] else "stale config hash; discounted in score"
        lines += [
            f"- Generated: `{live['generated_utc']}` ({state})",
            f"- Single Flash correctness: **{live['passed']}/{live['task_count']}**",
            f"- Single Flash end-to-end saving vs Terra equivalent: "
            f"**{live['saving_percent']:.1f}%**",
            f"- Total latency: **{live['latency_seconds']:.1f}s**",
        ]
    else:
        lines.append(f"Unavailable: {live.get('reason', 'report failed validation')}")
    lines += [
        "",
        "## Why this is not 10/10",
        "",
    ]
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    lines += [
        "",
        "The highest-value remaining benchmark is a fresh, broader A/B run with actual "
        "Zed host token telemetry. Until Zed exposes that telemetry, universal savings "
        "claims are not defensible.",
        "",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="run the offline checks without writing report files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    routing_ok = report["routing"]["passed"] == report["routing"]["total"]
    context = report["context"]
    verification = report["verification"]
    checks_ok = all([
        routing_ok,
        context["artifact_hashes_valid"],
        context["overwrite_blocked_before_model_calls"],
        verification["verified"],
        verification["sequence_correct"],
    ])
    if not args.check:
        JSON_PATH.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(
        f"token-cost usefulness: {report['usefulness']['score']:.1f}/10; "
        f"routing {report['routing']['passed']}/{report['routing']['total']}; "
        f"context -{context['host_context_reduction_percent']:.1f}%"
    )
    return 0 if checks_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
