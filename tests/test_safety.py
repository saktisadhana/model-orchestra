"""Offline regression checks for orchestration safety controls."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import sys
_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT), str(_ROOT / "tools")]

import configure_zed_profile
import server
import setup_model_orchestra


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
    calls: list[str] = []
    server.pipeline = lambda task, mode, **kwargs: calls.append(mode) or mode
    try:
        result = server.auto_delegate("review code")
    finally:
        server.pipeline = original_pipeline
    assert result == "code-review"
    assert calls == ["code-review"]


def test_batch_delegate_forwards_workspace_and_agent_flag() -> None:
    original_delegate = server.delegate
    calls: list[tuple[str, bool, str]] = []
    server.delegate = lambda task, model, agent=False, system="", workspace=".": calls.append((model, agent, workspace)) or "ok"
    try:
        result = server.batch_delegate('[{"task":"one","model":"flash","agent":true}]', workspace="workspace-a")
    finally:
        server.delegate = original_delegate
    assert "ok" in result
    assert calls == [("flash", True, "workspace-a")]


def test_composite_outputs_are_bounded() -> None:
    original_auto_delegate = server.auto_delegate
    original_batch_total = server.BATCH_TOTAL_RESPONSE
    original_batch_item = server.BATCH_ITEM_RESPONSE
    server.auto_delegate = lambda *args, **kwargs: "x" * 500
    server.BATCH_TOTAL_RESPONSE = 700
    server.BATCH_ITEM_RESPONSE = 500
    try:
        result = server.batch_delegate('[{"task":"one"},{"task":"two"}]')
    finally:
        server.auto_delegate = original_auto_delegate
        server.BATCH_TOTAL_RESPONSE = original_batch_total
        server.BATCH_ITEM_RESPONSE = original_batch_item

    assert len(result) <= 700
    assert "TRUNCATED" in result


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
    assert "compact" in configured


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
        (sibling / "secret.txt").write_text("private", encoding="utf-8")

        result = server.run_tool(
            "read_file", {"path": "../project-secret/secret.txt"}, workspace
        )
        assert result == "ERROR: path traversal blocked"


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


def test_usage_report_does_not_claim_billed_cost() -> None:
    original_usage = server.SESSION_USAGE
    server.SESSION_USAGE = {
        "total_input_tokens": 3,
        "total_output_tokens": 5,
        "calls": 1,
        "by_model": {"flash": {"input": 3, "output": 5, "calls": 1}},
    }
    try:
        report = server.cost_report()
    finally:
        server.SESSION_USAGE = original_usage
    assert "token usage" in report.lower()
    assert "billed cost is not calculated" in report.lower()


def test_benchmark_scripts_record_provenance() -> None:
    benchmark = (ROOT / "tools" / "benchmark.py").read_text(encoding="utf-8")
    proof = (ROOT / "tools" / "proof.py").read_text(encoding="utf-8")
    assert "Generated UTC" in benchmark
    assert "config SHA-256" in benchmark
    assert "Generated UTC" in proof
    assert "config SHA-256" in proof


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
