"""Offline regression checks for orchestration safety controls."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT), str(_ROOT / "tools")]

import benchmark
import configure_zed_profile
import server
import setup_model_orchestra
import usefulness_benchmark
import release_readiness
import model_orchestra.cli as public_cli
import model_orchestra.config as public_config


ROOT = _ROOT
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{20,}")


def test_gateway_key_order_and_legacy_fallback() -> None:
    provider = "68886868"
    names = server.PROVIDERS[provider]["api_key_envs"]
    original = {name: server.os.environ.get(name) for name in names}
    legacy_name = server.PROVIDERS[provider]["api_key_env"]
    legacy = server.os.environ.get(legacy_name)
    legacy_fallbacks = server.os.environ.get(legacy_name + "_FALLBACKS")
    try:
        for name, value in zip(names, ("lite-one", "lite-two", "pro")):
            server.os.environ[name] = value
        server.os.environ[legacy_name] = "legacy"
        server.os.environ[legacy_name + "_FALLBACKS"] = "legacy-two"
        assert server._keys_for(provider) == ["lite-one", "lite-two", "pro"]

        for name in names:
            server.os.environ.pop(name, None)
        assert server._keys_for(provider) == ["legacy", "legacy-two"]
    finally:
        for name, value in original.items():
            if value is None:
                server.os.environ.pop(name, None)
            else:
                server.os.environ[name] = value
        if legacy is None:
            server.os.environ.pop(legacy_name, None)
        else:
            server.os.environ[legacy_name] = legacy
        if legacy_fallbacks is None:
            server.os.environ.pop(legacy_name + "_FALLBACKS", None)
        else:
            server.os.environ[legacy_name + "_FALLBACKS"] = legacy_fallbacks


def test_context_overflow_stops_key_rotation() -> None:
    calls: list[str] = []
    original_keys_for = server._keys_for
    original_client_for = server.client_for

    class Completions:
        def create(self, **kwargs):
            calls.append("create")
            raise RuntimeError("status_code=502: input exceeds the context window")

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": Completions()})()

    server._keys_for = lambda provider: ["first", "second"]
    server.client_for = lambda provider, key=None: Client()
    try:
        try:
            server._create_with_retry(
                "opencode-go", retries=3, backoff=0, model="test", messages=[]
            )
        except RuntimeError as error:
            assert server._context_overflow(error)
        else:
            raise AssertionError("context overflow should propagate")
    finally:
        server._keys_for = original_keys_for
        server.client_for = original_client_for

    assert calls == ["create"], "context overflow retried or rotated API keys"


def test_context_overflow_stops_model_failover() -> None:
    calls: list[str] = []
    original_chat = server.chat

    def overflowing_chat(model, prompt, system="", temperature=0.2, max_tokens=None,
                         deadline=None):
        calls.append(model)
        raise RuntimeError("maximum context length exceeded")

    server.chat = overflowing_chat
    try:
        try:
            server.chat_with_failover("flash", "oversized prompt")
        except RuntimeError as error:
            assert server._context_overflow(error)
        else:
            raise AssertionError("context overflow should propagate")
    finally:
        server.chat = original_chat

    assert calls == ["flash"], "context overflow fell through to another model"


def test_failover_preserves_output_budget() -> None:
    calls: list[tuple[str, int | None]] = []
    original_chat = server.chat

    def failing_primary(model, prompt, system="", temperature=0.2, max_tokens=None,
                        deadline=None):
        calls.append((model, max_tokens))
        if model == "flash":
            raise RuntimeError("temporary upstream timeout")
        return "fallback result"

    server.chat = failing_primary
    try:
        result = server.chat_with_failover("flash", "small task", max_tokens=321)
    finally:
        server.chat = original_chat

    assert result == "fallback result"
    assert calls[0] == ("flash", 321)
    assert calls[1][1] == 321, "fallback changed the requested token budget"


def test_configured_token_budgets_are_bounded() -> None:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert 1 <= config["agent_max_steps"] <= 20
    assert 1 <= config["worker_max_tokens"] <= 4096
    assert 1 <= config["agent_max_tokens"] <= 4096
    assert 1 <= config["judge_max_tokens"] <= 8192
    assert 1 <= config["compact_max_tokens"] <= 4096
    assert config["max_response_chars"] <= 12000
    assert 1 <= config["swarm_max_workers"] <= 8
    assert 1 <= config["batch_max_tasks"] <= 20
    assert config["batch_total_response_chars"] >= config["batch_item_response_chars"]


def test_compaction_uses_safe_chunk_default_and_failover() -> None:
    assert server.COMPACT_CHUNK == 24000
    original = server.chat_with_failover
    calls: list[str] = []
    server.chat_with_failover = lambda *args, **kwargs: calls.append(args[0]) or "summary"
    try:
        result = server._compact("x" * 30, target_chars=10)
    finally:
        server.chat_with_failover = original
    assert result == "summary"
    assert calls == ["flash"]


def test_auto_delegate_tie_breaking_is_deterministic() -> None:
    original_pipeline = server.pipeline
    original_estimate = server._estimate_pipeline_cost
    calls: list[str] = []
    server.pipeline = lambda task, mode, **kwargs: calls.append(mode) or mode
    server._estimate_pipeline_cost = lambda *args, **kwargs: {
        "saving_percent": 50.0,
        "end_to_end_cost": 1.0,
        "direct_host_cost": 2.0,
    }
    try:
        result = server.auto_delegate("write a function")
    finally:
        server.pipeline = original_pipeline
        server._estimate_pipeline_cost = original_estimate
    assert result == "speed-run"
    assert calls == ["speed-run"]


def test_judgment_requests_stay_with_host() -> None:
    for task in (
        "review code",
        "Review this implementation for correctness",
        "Design the architecture for a chat system",
    ):
        plan = server._route_plan(task)
        assert plan["task_kind"] == "judgment"
        assert plan["selected_route"] is None
        assert plan["capability_floor"] == "host-judgment"


def test_repository_edits_use_k3_without_an_agent_hint() -> None:
    plan = server._route_plan("Fix the bug in parser.py", agent=False)
    assert plan["task_kind"] == "repository"
    assert plan["eligible_routes"] == ["repository-edit"]
    assert plan["selected_route"] == "repository-edit"
    assert plan["selected_models"] == ["k3"]
    assert plan["requires_agent"] is True
    assert plan["agent"] is True
    assert "capability-first" in plan["reason"]

    tiny = server._route_plan("Fix a typo in README.md")
    assert tiny["task_kind"] == "local"
    assert tiny["selected_route"] is None
    assert tiny["capability_floor"] == "host-local"

    # An explicit cap can block K3, but routing never downgrades a workspace edit
    # into stateless generation.
    capped = server._route_plan("Fix the bug in parser.py", max_cost_idr=0.01)
    assert capped["selected_route"] is None
    assert capped["agent"] is True
    assert "explicit cost cap" in capped["reason"]

    assert server.PIPELINES["repository-edit"]["single"] == server.IMPLEMENTATION_MODEL == "k3"
    assert server.pipeline("Fix parser.py", mode="repository-edit", agent=False) == (
        "ERROR: repository-edit requires agent=True"
    )
    assert server.pipeline(
        "Fix parser.py", mode="draft-refine", agent=True
    ).startswith("ERROR: repository implementation requires the K3")


def test_auto_delegate_infers_k3_agent_mode() -> None:
    original_pipeline = server.pipeline
    calls: list[tuple[str, bool, str]] = []
    server.pipeline = lambda task, mode, agent=False, **kwargs: (
        calls.append((mode, agent, kwargs["workspace"])) or "implemented"
    )
    try:
        result = server.auto_delegate(
            "Implement a settings panel",
            workspace="workspace-k3",
        )
    finally:
        server.pipeline = original_pipeline
    assert result == "implemented"
    assert calls == [("repository-edit", True, "workspace-k3")]


def test_k3_agent_reads_edits_and_returns_summary_with_mocked_calls() -> None:
    original_create = server._create_with_retry
    original_track = server._track
    original_budget_check = server._budget_check
    responses = iter([
        SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(
                type="tool_use", id="tool-1", name="read_file",
                input={"path": "src/result.py"},
            )],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ),
        SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(
                type="tool_use", id="tool-2", name="edit_file",
                input={"path": "src/result.py", "edits": [{
                    "old_text": "value = 41", "new_text": "value = 42"
                }]},
            )],
            usage=SimpleNamespace(
                input_tokens=12, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Read and updated src/result.py")],
            usage=SimpleNamespace(
                input_tokens=14, output_tokens=4,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ),
    ])

    def fake_create(*args, **kwargs):
        return next(responses)

    def fake_track(*args, **kwargs):
        return None

    def fake_budget_check(*args, **kwargs):
        return None

    server._create_with_retry = fake_create
    server._track = fake_track
    server._budget_check = fake_budget_check
    try:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "src" / "result.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"value = 41\n")
            result = server.delegate(
                "Implement the result module in the existing project",
                model="k3", agent=True, workspace=directory,
            )
            assert target.read_text(encoding="utf-8") == "value = 42\n"
    finally:
        server._create_with_retry = original_create
        server._track = original_track
        server._budget_check = original_budget_check
    assert result == "Read and updated src/result.py"


def test_orchestrate_change_returns_manifest_and_host_handoff() -> None:
    original_pipeline = server.pipeline

    def synthetic_pipeline(*args, **kwargs):
        workspace = Path(kwargs["workspace"])
        target = workspace / "src" / "changed.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"changed = True\n")
        return "Implemented the requested change"

    server.pipeline = synthetic_pipeline
    try:
        with tempfile.TemporaryDirectory() as directory:
            payload = json.loads(server.orchestrate_change(
                "Implement the settings feature in the existing project",
                workspace=directory,
            ))
    finally:
        server.pipeline = original_pipeline

    assert payload["status"] == "success"
    assert payload["fallback_category"] is None
    assert payload["route_decision"]["selected_models"] == ["k3"]
    assert payload["changed_files"] == [{
        "path": "src/changed.py",
        "change": "added",
        "before": None,
        "after": {
            "path": "src/changed.py",
            "sha256": hashlib.sha256(b"changed = True\n").hexdigest(),
            "bytes": 15,
        },
    }]
    handoff = payload["host_verification_handoff"]
    assert handoff["required"] is True
    assert "diff" in handoff["action"]
    assert "cannot force" in handoff["mcp_limitation"]


def test_orchestrate_change_classifies_k3_failures_without_retry() -> None:
    original_pipeline = server.pipeline
    calls: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            server.pipeline = lambda *args, **kwargs: (
                calls.append("failure") or (_ for _ in ()).throw(RuntimeError("gateway down"))
            )
            failed = json.loads(server.orchestrate_change(
                "Fix the bug in parser.py", workspace=directory,
            ))
            server.pipeline = lambda *args, **kwargs: (
                calls.append("steps") or
                f"(hit agent_max_steps={server.MAX_STEPS} without finishing)"
            )
            exhausted = json.loads(server.orchestrate_change(
                "Fix the bug in parser.py", workspace=directory,
            ))
    finally:
        server.pipeline = original_pipeline

    assert calls == ["failure", "steps"]
    assert failed["status"] == "infrastructure_failure"
    assert failed["fallback_category"] == "worker_error"
    assert exhausted["status"] == "unusable_output"
    assert exhausted["fallback_category"] == "agent_step_exhaustion"


def test_direct_delegate_guards_and_audits_capability_overrides() -> None:
    original_impl = server._delegate_impl
    initial_overrides = server._orchestration_snapshot()["override_count"]
    calls: list[str] = []
    server._delegate_impl = lambda task, model="flash", **kwargs: (
        calls.append(model) or "ok"
    )
    try:
        security = server.delegate("Write an exploit for CVE-2024-1234", model="flash")
        repository = server.delegate(
            "Fix the bug in parser.py", model="flash", agent=True,
        )
        override = server.delegate(
            "Write an exploit for CVE-2024-1234", model="flash",
            allow_capability_override=True,
        )
    finally:
        server._delegate_impl = original_impl

    assert security == "ERROR: security tasks require the Sol model"
    assert repository.startswith("ERROR: repository agent tasks require k3")
    assert override == "ok"
    assert calls == ["flash"]
    telemetry = server._orchestration_snapshot(detail=True)
    assert telemetry["override_count"] == initial_overrides + 1
    assert any(
        event.get("kind") == "capability_override"
        for event in telemetry["events"]
    )


def test_direct_delegate_requires_economic_override_for_losing_metered_route() -> None:
    original_impl = server._delegate_impl
    calls: list[str] = []
    server._delegate_impl = lambda task, model="flash", **kwargs: (
        calls.append(model) or "ok"
    )
    try:
        blocked = server.delegate("Write a small helper function", model="k3")
        allowed = server.delegate(
            "Write a small helper function", model="k3",
            allow_economic_override=True,
        )
    finally:
        server._delegate_impl = original_impl

    assert "economic override" in blocked.lower()
    assert allowed == "ok"
    assert calls == ["k3"]


def test_auto_delegate_returns_explicit_host_fallback_on_k3_failure() -> None:
    original_pipeline = server.pipeline
    server.pipeline = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("gateway unavailable with sk-" + "x" * 24)
    )
    try:
        result = server.auto_delegate("Fix the bug in parser.py")
    finally:
        server.pipeline = original_pipeline
    assert result.startswith("HOST_FALLBACK: K3 delegation failed")
    assert "secret-token" not in result


def test_auto_delegate_does_not_mark_worker_fallback_as_success() -> None:
    original_pipeline = server.pipeline
    server.pipeline = lambda *args, **kwargs: (
        "HOST_FALLBACK: worker returned control to the host"
    )
    try:
        result = server.auto_delegate("Fix the bug in parser.py")
    finally:
        server.pipeline = original_pipeline
    assert result.startswith("HOST_FALLBACK:")
    report = json.loads(server.orchestration_report())
    assert report["outcomes"].get("infrastructure_failure", 0) >= 1


def test_security_never_uses_k3_repository_route() -> None:
    plan = server._route_plan("Implement an exploit for CVE-2024-1234")
    assert plan["task_kind"] == "security"
    assert plan["selected_route"] == "security"
    assert plan["selected_models"] == ["sol"]
    assert plan["selected_models"] != ["k3"]


def test_route_preview_is_local_and_structured() -> None:
    original_chat = server.chat_with_failover
    server.chat_with_failover = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("route preview called a model")
    )
    try:
        preview = json.loads(server.route_preview("Write a function to sort a list"))
    finally:
        server.chat_with_failover = original_chat
    assert preview["schema_version"] == 2
    assert preview["task_kind"] == "mechanical"
    assert preview["capability"] == "stateless-generation"
    assert "speed-run" in preview["eligible_routes"]
    assert preview["host_model"] == server.HOST_MODEL
    required = {
        "workspace_required", "host_action_required",
        "host_confirmation_required", "host_action", "fallback_policy",
        "verification_plan", "inferred_agent", "economics", "reason",
    }
    assert required <= preview.keys()
    assert preview["agent"] is preview["requires_agent"] is False
    assert server.route_preview("task", max_cost_idr=float("nan")).startswith("ERROR:")
    assert server.auto_delegate("task", max_cost_idr=float("inf")).startswith("ERROR:")


def test_route_preview_exposes_pool_economics_and_output_bounds() -> None:
    preview = json.loads(server.route_preview("Write a function to sort a list"))
    economics = preview["economics"]
    assert economics["objective"] == "preserve_strong_model"
    assert economics["billing_mode"] == "subscription"
    assert economics["incremental_cash"] == 0.0
    assert economics["quota_equivalent"] is not None
    assert economics["strong_model_capacity_preserved"] is not None
    estimate = economics["estimates"]["speed-run"]
    assert estimate["estimated_output_tokens"] == 1536
    assert estimate["maximum_output_tokens"] == server.WORKER_MAX_TOKENS
    assert preview["selected_route"] == "speed-run"


def test_repository_review_stays_with_host_judgment() -> None:
    prompts = [
        "Analyze this repository and explain its architecture",
        "Review the current codebase for maintainability risks",
        "Audit this project and recommend a release plan",
    ]
    for prompt in prompts:
        plan = server._route_plan(prompt)
        assert plan["task_kind"] == "judgment", prompt
        assert plan["selected_route"] is None, prompt


def test_cache_usage_normalizes_openai_totals_once() -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 40},
    }
    assert server._usage_components(usage) == (60, 7, 40, 0)


def test_permanent_failover_error_does_not_rotate_or_retry() -> None:
    original_chat = server.chat
    calls: list[str] = []

    def bad_request(model, *args, **kwargs):
        calls.append(model)
        raise RuntimeError("status_code=400 invalid request")

    server.chat = bad_request
    try:
        try:
            server.chat_with_failover("flash", "bad")
        except RuntimeError as error:
            assert "400" in str(error)
        else:
            raise AssertionError("permanent error was swallowed")
    finally:
        server.chat = original_chat
    assert calls == ["flash"]


def test_auto_delegate_cost_control_downgrades_or_skips() -> None:
    original_pipeline = server.pipeline
    original_estimate = server._estimate_pipeline_cost
    calls: list[str] = []
    server.pipeline = lambda task, mode, **kwargs: calls.append(mode) or mode

    def estimate(mode, *args, **kwargs):
        saving = 20.0 if mode == "speed-run" else -50.0
        return {
            "saving_percent": saving,
            "end_to_end_cost": 10.0,
            "direct_host_cost": 5.0,
        }

    server._estimate_pipeline_cost = estimate
    try:
        mechanical = server.auto_delegate("Implement a payment processing function")
        judgment = server.auto_delegate("Design the architecture for a chat system")
        security = server.auto_delegate("Write an exploit for CVE-2024-1234")
    finally:
        server.pipeline = original_pipeline
        server._estimate_pipeline_cost = original_estimate

    assert mechanical == "speed-run"
    assert judgment.startswith("SKIP_DELEGATION:")
    assert security == "security"
    assert calls == ["speed-run", "security"]


def test_batch_delegate_forwards_workspace_and_agent_flag() -> None:
    original_delegate = server.delegate
    calls: list[tuple[str, bool, str]] = []
    server.delegate = lambda task, model, agent=False, system="", workspace=".": calls.append((model, agent, workspace)) or "ok"
    try:
        result = server.batch_delegate(
            '[{"task":"one","model":"flash","agent":true}]',
            workspace="workspace-a", inline=True,
        )
    finally:
        server.delegate = original_delegate
    assert "ok" in result
    assert calls == [("flash", True, str(Path("workspace-a").resolve()))]


def test_batch_delegate_auto_repository_infers_k3_agent_mode() -> None:
    original_auto_delegate = server.auto_delegate
    calls: list[tuple[str, bool, str]] = []

    def synthetic(task: str, agent: bool = False, workspace: str = ".", **kwargs):
        calls.append((task, agent, workspace))
        return "ok"

    server.auto_delegate = synthetic
    try:
        with tempfile.TemporaryDirectory() as directory:
            server.batch_delegate(
                '[{"task":"Fix the bug in parser.py"}]',
                workspace=directory,
                out_dir="artifacts",
            )
            manifest = json.loads(
                (Path(directory) / "artifacts" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
    finally:
        server.auto_delegate = original_auto_delegate

    assert calls == [(
        "Fix the bug in parser.py", True, str(Path(directory).resolve())
    )]
    item = manifest["items"][0]
    assert item["route"] == "repository-edit"
    assert item["selected_models"] == ["k3"]
    assert item["agent"] is True


def test_batch_delegate_propagates_verification_and_validates_attempts() -> None:
    original_verify = server._verify_with_tests
    calls: list[tuple[str, str, str, int]] = []
    server._verify_with_tests = lambda task, model, tests, attempts=0, initial_impl="": (
        calls.append((task, model, tests, attempts)) or "VERIFIED synthetic"
    )
    try:
        result = server.batch_delegate(json.dumps([{
            "task": "write add",
            "model": "flash",
            "tests": "def test_add(): assert add(1, 2) == 3",
            "attempts": 2,
        }]), inline=True)
        invalid = server.batch_delegate('[{"task":"write add","attempts":"many"}]')
    finally:
        server._verify_with_tests = original_verify

    assert "VERIFIED synthetic" in result
    assert calls == [(
        "write add", "flash", "def test_add(): assert add(1, 2) == 3", 2
    )]
    assert invalid == "Invalid batch item 1: attempts must be an integer"


def test_explicit_batch_cost_cap_blocks_before_worker_call() -> None:
    original_delegate = server.delegate
    original_pipeline = server.pipeline
    calls: list[str] = []
    server.delegate = lambda *args, **kwargs: calls.append("delegate") or "bad"
    server.pipeline = lambda *args, **kwargs: calls.append("pipeline") or "bad"
    try:
        direct = server.batch_delegate(json.dumps([{
            "task": "write add",
            "model": "flash",
            "max_cost_idr": 0.000001,
        }]), inline=True)
        recipe = server.batch_delegate(json.dumps([{
            "task": "write add",
            "mode": "speed-run",
            "max_cost_idr": 0.000001,
        }]), inline=True)
    finally:
        server.delegate = original_delegate
        server.pipeline = original_pipeline
    assert "exceeds explicit" in direct.lower()
    assert "exceeds explicit" in recipe.lower()
    assert calls == []


def test_explicit_batch_verification_enforces_capability_floor() -> None:
    original_verify = server._verify_with_tests
    calls: list[str] = []
    server._verify_with_tests = lambda *args, **kwargs: calls.append("verify") or "bad"
    try:
        result = server.batch_delegate(json.dumps([{
            "task": "Analyze an authentication bypass exploit",
            "model": "flash",
            "tests": "def test_result(): assert result",
        }]), inline=True)
    finally:
        server._verify_with_tests = original_verify

    assert "security" in result.lower() and "sol" in result.lower()
    assert calls == []


def test_security_routing_matches_labeled_corpus() -> None:
    corpus = json.loads(
        (ROOT / "tests" / "fixtures" / "security_routing.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(corpus["security"]) >= 15
    assert len(corpus["benign"]) >= 15
    missed = [task for task in corpus["security"] if not server._is_security(task)]
    false_positives = [task for task in corpus["benign"] if server._is_security(task)]
    assert not missed, f"security routing misses: {missed}"
    assert not false_positives, f"security routing false positives: {false_positives}"


def test_delegate_verified_requires_caller_tests_without_model_call() -> None:
    original_chat = server.chat_with_failover
    calls: list[str] = []
    server.chat_with_failover = lambda *args, **kwargs: calls.append("called") or ""
    try:
        result = server.delegate_verified("write add(a, b)")
    finally:
        server.chat_with_failover = original_chat
    assert result.startswith("ERROR: verification requires caller-supplied")
    assert calls == []


def test_delegate_verified_enforces_security_and_repository_floors() -> None:
    original_verify = server._verify_with_tests
    calls: list[str] = []
    server._verify_with_tests = lambda *args, **kwargs: calls.append("verify") or "bad"
    tests = "def test_result(): assert result"
    try:
        security = server.delegate_verified(
            "Analyze an authentication bypass exploit", model="flash", tests=tests
        )
        repository = server.delegate_verified(
            "Fix the bug in parser.py", model="flash", tests=tests
        )
    finally:
        server._verify_with_tests = original_verify
    assert "require" in security.lower() and "sol" in security.lower()
    assert "repository" in repository.lower() and "agent" in repository.lower()
    assert calls == []


def test_pipeline_verifies_final_candidate_before_repair() -> None:
    original_chat = server.chat_with_failover
    calls: list[str] = []

    def passing_chat(*args, **kwargs):
        calls.append(args[0])
        return "```python\ndef add(a, b):\n    return a + b\n```"

    server.chat_with_failover = passing_chat
    tests = "def test_add():\n    assert add(2, 3) == 5"
    try:
        result = server.pipeline(
            "Write add(a, b)", mode="speed-run", tests=tests, attempts=3
        )
    finally:
        server.chat_with_failover = original_chat

    assert result.startswith("VERIFIED after 1 attempt(s)")
    assert calls == ["flash"], "passing pipeline result triggered an unnecessary repair"


def test_pipeline_repairs_only_after_real_test_failure() -> None:
    original_chat = server.chat_with_failover
    answers = iter([
        "```python\ndef add(a, b):\n    return a - b\n```",
        "```python\ndef add(a, b):\n    return a + b\n```",
    ])
    prompts: list[str] = []

    def repairing_chat(model, prompt, *args, **kwargs):
        prompts.append(prompt)
        return next(answers)

    server.chat_with_failover = repairing_chat
    tests = "def test_add():\n    assert add(2, 3) == 5"
    try:
        result = server.pipeline(
            "Write add(a, b)", mode="speed-run", tests=tests, attempts=2
        )
    finally:
        server.chat_with_failover = original_chat

    assert result.startswith("VERIFIED after 2 attempt(s)")
    assert len(prompts) == 2
    assert "FAILED the caller-supplied tests" in prompts[1]
    assert "test_add: AssertionError" in prompts[1]


def test_pipeline_rejects_agent_verification() -> None:
    result = server.pipeline(
        "edit add.py", mode="speed-run", agent=True,
        tests="def test_add():\n    assert add(1, 2) == 3",
    )
    assert result == "ERROR: pipeline verification is unavailable with agent=True"


def test_composite_outputs_are_bounded() -> None:
    original_auto_delegate = server.auto_delegate
    original_batch_total = server.BATCH_TOTAL_RESPONSE
    original_batch_item = server.BATCH_ITEM_RESPONSE
    server.auto_delegate = lambda *args, **kwargs: "x" * 500
    server.BATCH_TOTAL_RESPONSE = 700
    server.BATCH_ITEM_RESPONSE = 500
    try:
        result = server.batch_delegate(
            '[{"task":"one"},{"task":"two"}]', inline=True
        )
    finally:
        server.auto_delegate = original_auto_delegate
        server.BATCH_TOTAL_RESPONSE = original_batch_total
        server.BATCH_ITEM_RESPONSE = original_batch_item

    assert len(result) <= 700
    assert "TRUNCATED" in result


def test_batch_delegate_defaults_to_workspace_manifest() -> None:
    original_auto_delegate = server.auto_delegate
    server.auto_delegate = lambda *args, **kwargs: "```python\ndef add(a, b):\n    return a + b\n```"
    try:
        with tempfile.TemporaryDirectory() as directory:
            result = server.batch_delegate(
                '[{"task":"write `add.py`"}]', workspace=directory
            )
            assert "Content was NOT returned" in result
            artifacts = list(Path(directory).rglob("01_add.py"))
            assert len(artifacts) == 1
            assert "def add" in artifacts[0].read_text(encoding="utf-8")
            escaped = server.batch_delegate(
                '[{"task":"write add"}]', workspace=directory,
                out_dir="../outside",
            )
            assert escaped == "ERROR: batch out_dir must stay inside workspace"
    finally:
        server.auto_delegate = original_auto_delegate


def test_fanout_limits_stop_before_worker_calls() -> None:
    original_chat = server.chat_with_failover
    original_auto_delegate = server.auto_delegate
    calls: list[str] = []
    server.chat_with_failover = lambda *args, **kwargs: calls.append("swarm") or "bad"
    server.auto_delegate = lambda *args, **kwargs: calls.append("batch") or "bad"
    try:
        models = ",".join(["flash"] * (server.SWARM_MAX_WORKERS + 1))
        swarm_result = server.swarm("task", models=models)
        tasks = [{"task": str(index)} for index in range(server.BATCH_MAX_TASKS + 1)]
        batch_result = server.batch_delegate(json.dumps(tasks))
    finally:
        server.chat_with_failover = original_chat
        server.auto_delegate = original_auto_delegate

    assert swarm_result.startswith("ERROR: swarm limited")
    assert batch_result.startswith("ERROR: batch limited")
    assert calls == []


def test_host_policies_reject_forced_delegation() -> None:
    for filename in ("AGENTS.md", "CLAUDE.md"):
        text = (ROOT / filename).read_text(encoding="utf-8").lower()
        normalized = " ".join(text.split())
        assert "delegate everything" not in normalized
        assert "do not delegate when" in normalized or "delegation would" in normalized
        assert "substantial" in normalized
        assert "k3" in normalized
        assert "agent=true" in normalized
        assert "tiny" in normalized
        assert "fallback" in normalized


def test_anthropic_text_skips_thinking_and_tool_blocks() -> None:
    content = [
        SimpleNamespace(type="thinking", thinking="internal reasoning"),
        SimpleNamespace(type="text", text="first"),
        {"type": "tool_use", "name": "read_file"},
        {"type": "text", "text": "second"},
    ]
    assert server._anthropic_text(content) == "first\nsecond"


def test_gateway_models_and_security_floor() -> None:
    security = server.PIPELINES["security"]
    assert security == {
        "description": security["description"],
        "single": "sol",
    }
    assert server.resolve("terra") == ("68886868", "gpt-5.6-terra")
    assert server.resolve("sol") == ("68886868", "gpt-5.6-sol")
    assert server.resolve("k27") == ("kimi-gw", "kimi-k2.7-code")
    assert server.resolve("k3") == ("kimi-gw", "kimi-k3")
    assert server.resolve("grok") == ("grok-gw", "grok-4.5")
    assert server._fallbacks_for("sol") == []
    assert {"terra", "sol"} <= server.STRONG_MODELS


def test_zed_gateway_profiles_are_secret_free() -> None:
    assert configure_zed_profile.PROFILE["default_model"] == {
        "provider": "c-lite-1",
        "model": "gpt-5.6-terra",
    }
    assert list(configure_zed_profile.GATEWAY_PROVIDERS) == [
        "c-lite-1", "c-lite-2", "c-pro"
    ]
    rendered = json.dumps(configure_zed_profile.GATEWAY_PROVIDERS)
    assert "api_key" not in rendered.lower()
    for config in configure_zed_profile.GATEWAY_PROVIDERS.values():
        assert [model["name"] for model in config["available_models"]] == [
            "gpt-5.6-terra", "gpt-5.6-sol"
        ]


def test_setup_preserves_env_and_replaces_selected_values() -> None:
    original = "# keep this comment\nUNRELATED=value\nC_LITE_1_API_KEY=old\n"
    rendered = setup_model_orchestra._upsert_env(
        original,
        {"C_LITE_1_API_KEY": "replacement", "C_LITE_2_API_KEY": "second"},
    )
    assert "# keep this comment" in rendered
    assert "UNRELATED=value" in rendered
    assert "C_LITE_1_API_KEY=replacement" in rendered
    assert "C_LITE_1_API_KEY=old" not in rendered
    assert "C_LITE_2_API_KEY=second" in rendered


def test_setup_blank_input_preserves_existing_values() -> None:
    original_path = setup_model_orchestra.ENV_PATH
    original_value = setup_model_orchestra.os.environ.pop("C_LITE_1_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("C_LITE_1_API_KEY=existing\n", encoding="utf-8")
            setup_model_orchestra.ENV_PATH = path
            responses = iter(["", "", ""])
            updates = setup_model_orchestra.collect_credentials(
                include_workers=False, input_fn=lambda _: next(responses)
            )
            assert updates == {}
            assert path.read_text(encoding="utf-8") == "C_LITE_1_API_KEY=existing\n"
    finally:
        setup_model_orchestra.ENV_PATH = original_path
        if original_value is not None:
            setup_model_orchestra.os.environ["C_LITE_1_API_KEY"] = original_value


def test_setup_accepts_primary_key_from_environment() -> None:
    original_path = setup_model_orchestra.ENV_PATH
    original_value = setup_model_orchestra.os.environ.get("C_LITE_1_API_KEY")
    try:
        with tempfile.TemporaryDirectory() as directory:
            setup_model_orchestra.ENV_PATH = Path(directory) / ".env"
            setup_model_orchestra.os.environ["C_LITE_1_API_KEY"] = "environment-secret"
            responses = iter(["", "", ""])
            assert setup_model_orchestra.collect_credentials(
                include_workers=False, input_fn=lambda _: next(responses)
            ) == {}
    finally:
        setup_model_orchestra.ENV_PATH = original_path
        if original_value is None:
            setup_model_orchestra.os.environ.pop("C_LITE_1_API_KEY", None)
        else:
            setup_model_orchestra.os.environ["C_LITE_1_API_KEY"] = original_value


def test_setup_requires_primary_key_and_rejects_multiline_values() -> None:
    original_path = setup_model_orchestra.ENV_PATH
    original_value = setup_model_orchestra.os.environ.pop("C_LITE_1_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as directory:
            setup_model_orchestra.ENV_PATH = Path(directory) / ".env"
            responses = iter(["", "", ""])
            try:
                setup_model_orchestra.collect_credentials(
                    include_workers=False, input_fn=lambda _: next(responses)
                )
            except setup_model_orchestra.SetupError:
                pass
            else:
                raise AssertionError("missing primary gateway key was accepted")

            try:
                setup_model_orchestra._prompt_secret(
                    "provider", "C_LITE_1_API_KEY", set(), lambda _: "bad\nINJECTED=yes"
                )
            except setup_model_orchestra.SetupError:
                pass
            else:
                raise AssertionError("multiline credential was accepted")
    finally:
        setup_model_orchestra.ENV_PATH = original_path
        if original_value is not None:
            setup_model_orchestra.os.environ["C_LITE_1_API_KEY"] = original_value


def test_setup_output_never_prints_secret_values() -> None:
    original_path = setup_model_orchestra.ENV_PATH
    original_value = setup_model_orchestra.os.environ.pop("C_LITE_1_API_KEY", None)
    output = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as directory:
            setup_model_orchestra.ENV_PATH = Path(directory) / ".env"
            responses = iter(["test-primary-secret", "test-secondary-secret", "test-pro-secret"])
            with contextlib.redirect_stdout(output):
                setup_model_orchestra.run(
                    include_workers=False,
                    install_profile=False,
                    input_fn=lambda _: next(responses),
                )
    finally:
        setup_model_orchestra.ENV_PATH = original_path
        if original_value is not None:
            setup_model_orchestra.os.environ["C_LITE_1_API_KEY"] = original_value

    rendered = output.getvalue()
    for secret in ("test-primary-secret", "test-secondary-secret", "test-pro-secret"):
        assert secret not in rendered


def test_profile_install_is_idempotent_for_new_settings_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / "nested" / "settings.json"
        assert configure_zed_profile.install(settings, backup=True)
        assert not configure_zed_profile.install(settings, backup=True)
        rendered = json.loads(settings.read_text(encoding="utf-8"))
        assert rendered["agent"]["profiles"][configure_zed_profile.PROFILE_ID] == configure_zed_profile.PROFILE
        assert not settings.with_suffix(".json.model-orchestra.bak").exists()


def test_profile_matches_server_tools() -> None:
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    exported = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
    }
    configured = set(
        configure_zed_profile.PROFILE["context_servers"]["model-orchestra"]["tools"]
    )
    assert configured == exported
    assert len(configured) == 12
    assert "compact" in configured
    assert "route_preview" in configured
    assert "orchestrate_change" in configured
    assert "orchestration_report" in configured


def test_release_readiness_classifies_without_mutating() -> None:
    assert release_readiness.classify_path("docs/REPORT.json") == "generated_reports_artifacts"
    assert release_readiness.classify_path("tools/benchmark_baseline.json") == "generated_reports_artifacts"
    assert release_readiness.classify_path(".obsidian/workspace.json") == "unrelated_workspace_changes"
    assert release_readiness.classify_path("server.py") == "source_changes"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        initialized = subprocess.run(
            ["git", "init", "--quiet"], cwd=root, capture_output=True, text=True
        )
        assert initialized.returncode == 0
        (root / "server.py").write_text("pass\n", encoding="utf-8")
        before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        report = release_readiness.inventory(root)
        after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        assert report["clean"] is False
        assert report["categories"]["source_changes"] == [{
            "status": "??", "path": "server.py"
        }]
        assert before == after


def test_orchestration_report_is_metadata_only_and_bounded() -> None:
    report = json.loads(server.orchestration_report())
    assert len(server.orchestration_report()) <= server.MAX_RESPONSE
    assert "events" not in report
    rendered = server.orchestration_report(detail=True)
    detailed = json.loads(rendered)
    assert "prompt" not in detailed
    assert "file_contents" not in detailed
    assert "raw_errors" not in detailed
    assert "limitations" in report


def test_requirements_are_utf8_text() -> None:
    raw = (ROOT / "requirements.txt").read_bytes()
    text = raw.decode("utf-8")
    assert "\x00" not in text
    requirements = [
        line for line in text.splitlines() if line and not line.startswith("#")
    ]
    assert requirements
    assert all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:[<>=!~].+)?", item)
        for item in requirements
    )


def test_documentation_contains_no_api_keys() -> None:
    for path in ROOT.rglob("*.md"):
        if path.name == "chat.md":
            # Historical transcript intentionally remains an audit artifact and
            # is excluded from the repository's publishable documentation scan.
            continue
        text = path.read_text(encoding="utf-8")
        assert not SECRET_PATTERN.search(text), (
            f"possible API key remains in {path.name}"
        )


def test_worker_paths_stay_in_workspace() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "project"
        sibling = root / "project-secret"
        workspace.mkdir()
        sibling.mkdir()
        (workspace / "src").mkdir()
        (workspace / "src" / "main.py").write_text("needle = 1\n", encoding="utf-8")
        (sibling / "secret.txt").write_text("private", encoding="utf-8")

        result = server.run_tool(
            "read_file", {"path": "../project-secret/secret.txt"}, workspace
        )
        assert result == "ERROR: path traversal blocked"
        listing = server.run_tool("list_directory", {"path": "."}, workspace)
        assert "dir src" in listing
        found = server.run_tool("find_path", {"pattern": "**/*.py"}, workspace)
        assert "src" in found and "main.py" in found
        ranged = server.run_tool(
            "read_file", {"path": "src/main.py", "start_line": 1, "end_line": 1}, workspace
        )
        assert "1\tneedle = 1" in ranged
        edited = server.run_tool(
            "edit_file", {"path": "src/main.py", "edits": [{
                "old_text": "needle = 1", "new_text": "needle = 2"
            }]}, workspace
        )
        assert edited.startswith("edited")
        assert "needle = 2" in (workspace / "src" / "main.py").read_text(encoding="utf-8")
        ambiguous = server.run_tool(
            "edit_file", {"path": "src/main.py", "edits": [{
                "old_text": "missing", "new_text": "x"
            }]}, workspace
        )
        assert ambiguous == "ERROR: old_text must match exactly once"
        matches = server.run_tool(
            "grep", {"regex": "needle", "include_pattern": "**/*.py"}, workspace
        )
        assert "src/main.py:1:needle = 2" in matches or "src\\main.py:1:needle = 2" in matches
        assert server.run_tool("find_path", {"pattern": "../**/*"}, workspace).startswith(
            "ERROR: pattern"
        )


def test_error_diagnostics_redact_configured_credentials() -> None:
    name = server.PROVIDERS["68886868"]["api_key_envs"][0]
    original = server.os.environ.get(name)
    server.os.environ[name] = "sensitive-test-value"
    try:
        rendered = server._safe_error(RuntimeError("upstream sensitive-test-value"))
    finally:
        if original is None:
            server.os.environ.pop(name, None)
        else:
            server.os.environ[name] = original
    assert "sensitive-test-value" not in rendered
    assert "REDACTED" in rendered


def test_batch_manifest_records_route_and_integrity_metadata() -> None:
    original_auto_delegate = server.auto_delegate
    server.auto_delegate = lambda *args, **kwargs: "```python\nvalue = 1\n```"
    try:
        with tempfile.TemporaryDirectory() as directory:
            server.batch_delegate(
                '[{"task":"write `result.py`"}]', workspace=directory,
                out_dir="artifacts",
            )
            manifest_path = Path(directory) / "artifacts" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            item = manifest["items"][0]
            assert manifest["schema_version"] == 2
            assert item["requested_route"] == "auto"
            assert item["selected_models"] == ["flash"]
            assert item["route"] == "speed-run"
            assert item["artifact"]["sha256"]
            assert "worker_cost_idr" in item
            assert "estimated_cost_idr" in item
            assert "event_counts" in item
            assert "events" in item
    finally:
        server.auto_delegate = original_auto_delegate


def test_shell_is_disabled_by_default() -> None:
    advertised = {tool["function"]["name"] for tool in server.AGENT_TOOLS}
    assert "run_shell" not in advertised
    with tempfile.TemporaryDirectory() as directory:
        result = server.run_tool("run_shell", {"command": "echo blocked"}, Path(directory))
    assert result == "ERROR: shell access is disabled by agent_shell_mode=deny"


def test_allowlist_rejects_shell_wrappers() -> None:
    for command in ("cmd", "powershell.exe", "bash", "tool.cmd", "tool.bat"):
        try:
            server._shell_executable_name(command)
        except ValueError:
            continue
        raise AssertionError(f"shell wrapper was accepted: {command}")


def test_allowlist_uses_shell_false_and_runs_allowed_command() -> None:
    original_mode = server.AGENT_SHELL_MODE
    original_allowlist = server.AGENT_SHELL_ALLOWLIST
    original_executables = server.AGENT_SHELL_EXECUTABLES
    server.AGENT_SHELL_MODE = "allowlist"
    server.AGENT_SHELL_ALLOWLIST = frozenset({"python"})
    server.AGENT_SHELL_EXECUTABLES = {"python": "python"}
    try:
        with tempfile.TemporaryDirectory() as directory:
            result = server.run_tool(
                "run_shell", {"command": 'python -c "print(123)"'}, Path(directory)
            )
        assert "123" in result
    finally:
        server.AGENT_SHELL_MODE = original_mode
        server.AGENT_SHELL_ALLOWLIST = original_allowlist
        server.AGENT_SHELL_EXECUTABLES = original_executables


def test_allowlist_rejects_shell_syntax_and_path_escape() -> None:
    original_mode = server.AGENT_SHELL_MODE
    original_allowlist = server.AGENT_SHELL_ALLOWLIST
    original_executables = server.AGENT_SHELL_EXECUTABLES
    server.AGENT_SHELL_MODE = "allowlist"
    server.AGENT_SHELL_ALLOWLIST = frozenset({"echo", "python"})
    server.AGENT_SHELL_EXECUTABLES = {"echo": "echo", "python": "python"}
    try:
        blocked = (
            "echo ok; echo no",
            "echo ok && echo no",
            "echo ok | echo no",
            "echo ok > output.txt",
            "python -c \"print('$(bad)')\"",
            "python ../outside.py",
        )
        for command in blocked:
            assert server._allowlisted_argv(command) is None
    finally:
        server.AGENT_SHELL_MODE = original_mode
        server.AGENT_SHELL_ALLOWLIST = original_allowlist
        server.AGENT_SHELL_EXECUTABLES = original_executables


def test_unrestricted_mode_is_explicit() -> None:
    original_mode = server.AGENT_SHELL_MODE
    original_run = server.subprocess.run
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    server.AGENT_SHELL_MODE = "unrestricted"
    server.subprocess.run = fake_run
    try:
        with tempfile.TemporaryDirectory() as directory:
            assert server.run_tool("run_shell", {"command": "echo trusted"}, Path(directory)) == "ok"
    finally:
        server.AGENT_SHELL_MODE = original_mode
        server.subprocess.run = original_run

    assert calls and calls[0][1]["shell"] is True


def test_shell_timeout_and_output_are_bounded() -> None:
    original_mode = server.AGENT_SHELL_MODE
    original_timeout = server.AGENT_SHELL_TIMEOUT
    original_max_output = server.AGENT_SHELL_MAX_OUTPUT
    original_run = server.subprocess.run
    original_allowlist = server.AGENT_SHELL_ALLOWLIST
    original_executables = server.AGENT_SHELL_EXECUTABLES

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    server.AGENT_SHELL_MODE = "allowlist"
    server.AGENT_SHELL_ALLOWLIST = frozenset({"echo"})
    server.AGENT_SHELL_EXECUTABLES = {"echo": "echo"}
    server.AGENT_SHELL_TIMEOUT = 7
    server.subprocess.run = timeout_run
    try:
        with tempfile.TemporaryDirectory() as directory:
            result = server.run_tool("run_shell", {"command": "echo slow"}, Path(directory))
        assert result == "ERROR: command timed out after 7 seconds"
    finally:
        server.AGENT_SHELL_MODE = original_mode
        server.AGENT_SHELL_TIMEOUT = original_timeout
        server.AGENT_SHELL_MAX_OUTPUT = original_max_output
        server.AGENT_SHELL_ALLOWLIST = original_allowlist
        server.AGENT_SHELL_EXECUTABLES = original_executables
        server.subprocess.run = original_run

    original_mode = server.AGENT_SHELL_MODE
    original_max_output = server.AGENT_SHELL_MAX_OUTPUT
    server.AGENT_SHELL_MODE = "allowlist"
    server.AGENT_SHELL_ALLOWLIST = frozenset({"echo"})
    server.AGENT_SHELL_EXECUTABLES = {"echo": "echo"}
    server.AGENT_SHELL_MAX_OUTPUT = 20
    server.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
        stdout="x" * 100, stderr="", returncode=0
    )
    try:
        with tempfile.TemporaryDirectory() as directory:
            result = server.run_tool("run_shell", {"command": "echo output"}, Path(directory))
        assert "TRUNCATED" in result
        assert len(result) <= 20
    finally:
        server.AGENT_SHELL_MODE = original_mode
        server.AGENT_SHELL_MAX_OUTPUT = original_max_output
        server.AGENT_SHELL_ALLOWLIST = original_allowlist
        server.AGENT_SHELL_EXECUTABLES = original_executables
        server.subprocess.run = original_run


def test_provider_budget_envelopes_are_split() -> None:
    limits = server.BUDGET_PROVIDER_LIMITS
    assert limits["opencode-go"]["monthly"] == 185000
    assert limits["chicken-farm"]["monthly"] == 155000
    assert sum(item["monthly"] for item in limits.values()) == 340000
    assert server._provider_group("flash") == "opencode-go"
    assert server._provider_group("sol") == "chicken-farm"
    assert server._provider_group("grok") == "chicken-farm"


def test_budget_guard_rejects_unpriced_raw_models() -> None:
    priced_raw = server.WORKERS["flash"]
    assert server._pricing_alias(priced_raw) == "flash"
    server._budget_check(priced_raw, 1, 1)
    try:
        server._budget_check("opencode-go/unpriced-model", 1, 1)
    except RuntimeError as error:
        assert "unpriced model" in str(error)
    else:
        raise AssertionError("unpriced model bypassed the budget guard")


def test_budget_guard_is_provider_scoped() -> None:
    original_path = server.BUDGET_STATE_PATH
    try:
        with tempfile.TemporaryDirectory() as directory:
            server.BUDGET_STATE_PATH = Path(directory) / "ledger.json"
            server._save_budget_state({"entries": [{
                "at": server.dt.datetime.now(server.dt.timezone.utc).isoformat(),
                "group": "chicken-farm",
                "idr": server.BUDGET_PROVIDER_LIMITS["chicken-farm"]["monthly"],
            }]})
            try:
                server._budget_check("sol", 1, 1)
            except RuntimeError as error:
                assert "chicken-farm monthly" in str(error)
            else:
                raise AssertionError("exhausted chicken-farm budget was accepted")
            server._budget_check("flash", 1, 1)
    finally:
        server.BUDGET_STATE_PATH = original_path


def test_budget_reservations_are_atomic_and_idempotent() -> None:
    original_db = server.BUDGET_DB_PATH
    original_limits = server.BUDGET_PROVIDER_LIMITS
    try:
        with tempfile.TemporaryDirectory() as directory:
            server.BUDGET_DB_PATH = Path(directory) / "budget.sqlite3"
            server.BUDGET_PROVIDER_LIMITS = {
                "opencode-go": {"monthly": 1, "daily": 1, "five_hour": 1}
            }
            barrier = threading.Barrier(2)
            results: list[str] = []

            def reserve() -> None:
                barrier.wait()
                try:
                    results.append(server._budget_reserve("flash", 1, 1))
                except RuntimeError:
                    results.append("blocked")

            workers = [threading.Thread(target=reserve) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            reservation_ids = [value for value in results if value != "blocked"]
            assert len(reservation_ids) == 1
            assert results.count("blocked") == 1

            reservation_id = reservation_ids[0]
            server._budget_settle(
                reservation_id, "flash", {"prompt_tokens": 1, "completion_tokens": 1},
                settlement_id="settlement-1",
            )
            server._budget_settle(
                reservation_id, "flash", {"prompt_tokens": 1, "completion_tokens": 1},
                settlement_id="settlement-1",
            )
            with contextlib.closing(sqlite3.connect(server.BUDGET_DB_PATH)) as connection:
                row = connection.execute(
                    "SELECT state, actual_idr, settlement_id FROM budget_reservations "
                    "WHERE operation_id = ?", (reservation_id,)
                ).fetchone()
            assert row == ("settled", 1, "settlement-1")
    finally:
        server.BUDGET_DB_PATH = original_db
        server.BUDGET_PROVIDER_LIMITS = original_limits


def test_uncertain_provider_failure_keeps_pending_liability() -> None:
    original_db = server.BUDGET_DB_PATH
    original_keys = server._keys_for
    original_client = server.client_for

    class FailingCompletions:
        def create(self, **kwargs):
            raise TimeoutError("provider outcome unknown")

    class FailingClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    try:
        with tempfile.TemporaryDirectory() as directory:
            server.BUDGET_DB_PATH = Path(directory) / "budget.sqlite3"
            server._keys_for = lambda provider: ["test-key"]
            server.client_for = lambda provider, key=None: FailingClient()
            try:
                server.chat(
                    "flash", "bounded task", max_tokens=1,
                    deadline=server.time.monotonic() + 1,
                )
            except TimeoutError:
                pass
            else:
                raise AssertionError("provider failure was swallowed")
            with contextlib.closing(sqlite3.connect(server.BUDGET_DB_PATH)) as connection:
                states = connection.execute(
                    "SELECT state FROM budget_reservations"
                ).fetchall()
            assert states == [("pending_liability",)]
    finally:
        server.BUDGET_DB_PATH = original_db
        server._keys_for = original_keys
        server.client_for = original_client


def test_usage_report_shows_end_to_end_estimate_without_invoice_claim() -> None:
    original_usage = server.SESSION_USAGE
    server.SESSION_USAGE = {
        "total_input_tokens": 300,
        "total_output_tokens": 500,
        "total_cached_tokens": 0,
        "total_cache_write_tokens": 0,
        "calls": 1,
        "by_model": {"flash": {
            "input": 300, "output": 500, "cached": 0,
            "cache_write": 0, "calls": 1,
        }},
    }
    try:
        report = server.cost_report()
    finally:
        server.SESSION_USAGE = original_usage
    assert "worker token usage" in report.lower()
    assert "host re-ingestion" in report.lower()
    assert "direct terra equivalent" in report.lower()
    assert "not provider invoices" in report.lower()


def test_precise_cost_does_not_round_every_call_up() -> None:
    precise = server._precise_usage_cost("terra", 1, 1)
    guarded = server._usage_cost("terra", 1, 1)
    assert 0 < precise < 1
    assert guarded == 1


def test_benchmark_baseline_matches_suite_and_models() -> None:
    baseline = benchmark.load_baseline(benchmark.BASELINE_PATH)
    assert baseline["minimum_passed"] == {"single": 5, "swarm": 5}
    assert baseline["minimum_saving_percent"] == {"single": 10.0}
    assert benchmark.main(["--check-baseline"]) == 0


def test_benchmark_attempt_distinguishes_code_and_infrastructure_failures() -> None:
    original_chat = benchmark.chat
    original_passes = benchmark.passes
    name = server.PROVIDERS["68886868"]["api_key_envs"][0]
    original_secret = server.os.environ.get(name)
    try:
        benchmark.chat = lambda *args, **kwargs: "```python\ndef add(a, b): return a - b\n```"
        benchmark.passes = lambda code, tests: False
        ok, _, output = benchmark.attempt("flash", "write add", "assert add(2, 3) == 5")
        assert not ok
        assert "def add" in output

        server.os.environ[name] = "benchmark-sensitive-value"
        benchmark.chat = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("upstream benchmark-sensitive-value")
        )
        try:
            benchmark.attempt("flash", "write add", "")
        except benchmark.BenchmarkInfrastructureError as error:
            rendered = str(error)
            assert "benchmark-sensitive-value" not in rendered
            assert "REDACTED" in rendered
        else:
            raise AssertionError("provider failure was counted as incorrect code")
    finally:
        benchmark.chat = original_chat
        benchmark.passes = original_passes
        if original_secret is None:
            server.os.environ.pop(name, None)
        else:
            server.os.environ[name] = original_secret


def test_benchmark_swarm_rejects_partial_infrastructure_results() -> None:
    original_attempt = benchmark.attempt

    def mixed_attempt(model, desc, test):
        if model == "mimo":
            raise benchmark.BenchmarkInfrastructureError("mimo unavailable")
        return True, 0.01, "working code"

    benchmark.attempt = mixed_attempt
    try:
        try:
            benchmark.run_after("task", "tests")
        except benchmark.BenchmarkInfrastructureError as error:
            assert "mimo unavailable" in str(error)
        else:
            raise AssertionError("partial swarm result was accepted")
    finally:
        benchmark.attempt = original_attempt


def test_benchmark_infrastructure_abort_preserves_reports() -> None:
    original_run_before = benchmark.run_before
    original_json_path = benchmark.JSON_REPORT_PATH
    original_markdown_path = benchmark.MARKDOWN_REPORT_PATH
    stderr = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "REPORT.json"
            markdown_path = root / "REPORT.md"
            json_path.write_text("previous json\n", encoding="utf-8")
            markdown_path.write_text("previous markdown\n", encoding="utf-8")
            benchmark.JSON_REPORT_PATH = json_path
            benchmark.MARKDOWN_REPORT_PATH = markdown_path
            benchmark.run_before = lambda *args, **kwargs: (_ for _ in ()).throw(
                benchmark.BenchmarkInfrastructureError("provider unavailable")
            )
            with contextlib.redirect_stderr(stderr):
                assert benchmark.main([]) == 3
            assert json_path.read_text(encoding="utf-8") == "previous json\n"
            assert markdown_path.read_text(encoding="utf-8") == "previous markdown\n"
    finally:
        benchmark.run_before = original_run_before
        benchmark.JSON_REPORT_PATH = original_json_path
        benchmark.MARKDOWN_REPORT_PATH = original_markdown_path

    rendered = stderr.getvalue()
    assert "INFRASTRUCTURE ERROR" in rendered
    assert "existing reports were not changed" in rendered


def test_benchmark_regression_gate_rejects_correctness_drop() -> None:
    report = {
        "summary": {
            "single": {"passed": 4},
            "swarm": {"passed": 5},
        }
    }
    baseline = {
        "minimum_passed": {"single": 5, "swarm": 5},
        "minimum_saving_percent": {},
    }
    failures = benchmark.regression_failures(report, baseline)
    assert failures == [
        "single correctness regressed: 4/6 < baseline minimum 5/6"
    ]


def test_benchmark_regression_gate_rejects_cost_drop() -> None:
    report = {
        "summary": {
            "single": {"passed": 6, "cost": {"saving_percent": 4.0}},
            "swarm": {"passed": 6, "cost": {"saving_percent": -100.0}},
        }
    }
    baseline = {
        "minimum_passed": {"single": 5, "swarm": 5},
        "minimum_saving_percent": {"single": 10.0},
    }
    assert benchmark.regression_failures(report, baseline) == [
        "single cost saving regressed: 4.0% < baseline minimum 10.0%"
    ]


def test_benchmark_scripts_record_provenance() -> None:
    benchmark = (ROOT / "tools" / "benchmark.py").read_text(encoding="utf-8")
    proof = (ROOT / "tools" / "proof.py").read_text(encoding="utf-8")
    assert "Generated UTC" in benchmark
    assert "config SHA-256" in benchmark
    assert "Generated UTC" in proof
    assert "config SHA-256" in proof


def test_usefulness_benchmark_is_offline_and_bounded() -> None:
    report = usefulness_benchmark.build_report()
    assert report["network_calls"] == 0
    assert 0.0 <= report["usefulness"]["score"] <= 10.0
    assert report["routing"]["passed"] == report["routing"]["total"]
    assert report["context"]["artifact_hashes_valid"]
    assert report["verification"]["sequence_correct"]


def test_stale_live_economics_earns_zero_savings_credit() -> None:
    report = usefulness_benchmark.build_report()
    report["live_evidence"] = {
        "available": True,
        "config_matches": False,
        "economics_current": False,
        "passed": 6,
        "task_count": 6,
        "saving_percent": 99.0,
    }
    score = usefulness_benchmark._score(report)
    assert score["components"]["measured_savings"]["score"] == 0.0


def test_release_readiness_classifies_local_runtime_debris() -> None:
    for path in (
        ".pytest_cache/v/cache/nodeids",
        ".pytest-cache/state",
        ".model-orchestra-budget.sqlite3",
        ".model-orchestra-budget.sqlite3-wal",
    ):
        assert release_readiness.classify_path(path) == "local_runtime_debris"


def test_validate_config_enforces_every_schema_required_field() -> None:
    """Each field the JSON schema marks required must fail validation when broken."""
    import copy

    base = public_config.load_config(public_config.example_config_path())
    public_config.validate_config(base)

    mutations = {
        "budget.currency": lambda c: c["budget"].pop("currency"),
        "budget.billing_modes": lambda c: c["budget"].pop("billing_modes"),
        "cost_control.objective": lambda c: c["cost_control"].update(objective="cheapest"),
        "cost_control.host_model": lambda c: c["cost_control"].update(host_model="absent"),
        "provider.client": lambda c: c["providers"]["anthropic-compatible"].update(client="Anthropic"),
        "provider.api_key_env": lambda c: c["providers"]["openai-compatible"].update(api_key_env="lower_case"),
        "pipeline.route": lambda c: c["pipelines"].update(broken="not-an-object"),
    }
    for label, mutate in mutations.items():
        candidate = copy.deepcopy(base)
        mutate(candidate)
        try:
            public_config.validate_config(candidate)
        except ValueError:
            continue
        raise AssertionError(f"validate_config accepted an invalid config: {label}")


def test_validate_config_allows_underscore_comment_keys_in_pipelines() -> None:
    """Underscore keys are the inline-comment convention, not routes to validate."""
    config = public_config.load_config(public_config.example_config_path())
    config["pipelines"]["_routing_note"] = "prefer the cheapest eligible worker"
    public_config.validate_config(config)


def test_public_config_assets_are_neutral_and_valid() -> None:
    example = public_config.example_config_path()
    schema = public_config.schema_path()
    loaded = public_config.load_config(example)
    public_config.validate_config(loaded)
    rendered = example.read_text(encoding="utf-8")
    assert schema.is_file()
    assert "68886868" not in rendered
    assert "C_LITE" not in rendered
    assert "Sakti" not in rendered
    assert loaded["schema_version"] == 1
    configured_workers = set(loaded["workers"])
    for pipeline in loaded["pipelines"].values():
        references = [
            pipeline.get(key)
            for key in ("single", "drafter", "refiner", "judge")
            if pipeline.get(key)
        ]
        references.extend(pipeline.get("stages", []))
        assert set(references) <= configured_workers


def test_config_resolution_prefers_explicit_then_environment(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "explicit.json"
    environment = tmp_path / "environment.json"
    explicit.write_text("{}", encoding="utf-8")
    environment.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MODEL_ORCHESTRA_CONFIG", str(environment))
    assert public_config.resolve_config_path(explicit) == explicit.resolve()
    assert public_config.resolve_config_path() == environment.resolve()


def test_public_cli_check_and_doctor_are_secret_free(capsys) -> None:
    config = public_config.example_config_path()
    assert public_cli.main(["check", "--config", str(config)]) == 0
    assert public_cli.main(["doctor", "--config", str(config)]) == 1
    output = capsys.readouterr().out
    assert "Configuration: valid" in output
    assert "missing credential" in output.lower()
    assert "api key" not in output.lower()


def test_public_cli_benchmark_requires_live_flag(capsys) -> None:
    assert public_cli.main(["benchmark"]) == 2
    assert "--live" in capsys.readouterr().out


def test_public_config_route_preview_uses_only_declared_workers() -> None:
    script = """
import json
import server
plan = server._route_plan('Write a small helper function')
print(json.dumps(plan))
"""
    environment = os.environ.copy()
    environment["MODEL_ORCHESTRA_CONFIG"] = str(public_config.example_config_path())
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    plan = json.loads(completed.stdout)
    declared = set(public_config.load_config(public_config.example_config_path())["workers"])
    used = {
        call[0]
        for estimate in plan["estimates"].values()
        for call in estimate["calls"]
    }
    assert used <= declared
    assert plan["selected_models"]


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"ok - {len(tests)} safety checks passed")
