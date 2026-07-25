"""
model-orchestra: an MCP server that lets an orchestrator model delegate work
to tiny/cheap models on OpenRouter, NVIDIA, OpenCode Go, Groq, and SambaNova.

Tools exposed to the orchestrator:
  - list_workers()                          -> what workers exist
  - delegate(task, model, agent, ...)       -> run a single worker
  - delegate_verified(task, tests, ...)     -> generate and test Python code
  - compact(text, model, target_chars)      -> shrink oversized context safely
  - swarm(task, models, judge, ...)         -> parallel workers plus judge
  - pipeline(task, mode)                    -> composite recipe (draft+refine, swarm, etc.)
  - auto_delegate(task)                     -> capability-safe cost-aware routing
  - route_preview(task)                     -> inspect routing without a model call
  - orchestrate_change(task, workspace)     -> K3 edit plus changed-file handoff
  - batch_delegate(tasks_json)              -> parallel work plus hashed manifest
  - orchestration_report()                  -> aggregate routing/agent telemetry
  - cost_report()                           -> session token usage summary

`model` accepts either a worker alias from config.json ("flash", "k27", ...)
or a full "provider/model-id" string ("openrouter/meta-llama/llama-3.1-8b-instruct").

All providers speak the OpenAI chat-completions protocol, so one client
handles everything; only base_url + api_key change per provider.
"""

import concurrent.futures as cf
import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from dotenv import load_dotenv
from openai import OpenAI
from mcp.server.fastmcp import FastMCP

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")  # keys live in .env, never in .mcp.json
_configured_path = os.environ.get("MODEL_ORCHESTRA_CONFIG")
CONFIG_PATH = (
    pathlib.Path(_configured_path).expanduser().resolve()
    if _configured_path else ROOT / "config.json"
)
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
PROVIDERS = CONFIG["providers"]
WORKERS = CONFIG["workers"]
PIPELINES = CONFIG.get("pipelines", {})
FALLBACK_CHAIN = CONFIG.get("fallback_chain", [])
MODEL_FALLBACKS = CONFIG.get("model_fallbacks", {})
TIERS = CONFIG.get("tiers", {})
MAX_STEPS = int(CONFIG.get("agent_max_steps", 10))
MAX_RESPONSE = int(CONFIG.get("max_response_chars", 8000))
MAX_INPUT = int(CONFIG.get("max_input_chars", 200000))
WORKER_MAX_TOKENS = int(CONFIG.get("worker_max_tokens", 2048))
AGENT_MAX_TOKENS = int(CONFIG.get("agent_max_tokens", 2048))
JUDGE_MAX_TOKENS = int(CONFIG.get("judge_max_tokens", 3072))
COMPACT_MAX_TOKENS = int(CONFIG.get("compact_max_tokens", 1200))
# Measured quality cliff: at worker/judge caps of 1024/1536 the execution-checked
# suite scored 1/6 because workers were truncated mid-function and judges then
# described the fragments instead of merging them. At 3072/4096 it scores 6/6.
# max_tokens is a CAP, not a charge, so lowering these saves almost nothing and
# silently guts output quality. Spend is bounded by _budget_check, never by
# starving generation.
_TOKEN_FLOOR = 2048
if WORKER_MAX_TOKENS < _TOKEN_FLOOR or JUDGE_MAX_TOKENS < _TOKEN_FLOOR:
    raise ValueError(
        f"worker_max_tokens/judge_max_tokens must be >= {_TOKEN_FLOOR} "
        f"(got {WORKER_MAX_TOKENS}/{JUDGE_MAX_TOKENS}); lower caps truncate "
        "codegen and drop the quality suite from 6/6 to 1/6."
    )
SWARM_MAX_WORKERS = int(CONFIG.get("swarm_max_workers", 5))
SWARM_WORKER_RESPONSE = int(CONFIG.get("swarm_worker_response_chars", 4000))
SWARM_JUDGE_INPUT = int(CONFIG.get("swarm_judge_input_chars", 16000))
STAGE_CONTEXT_CHARS = int(CONFIG.get("stage_context_chars", 8000))
BATCH_MAX_TASKS = int(CONFIG.get("batch_max_tasks", 10))
BATCH_MAX_PARALLEL = int(CONFIG.get("batch_max_parallel", 8))
BATCH_ITEM_RESPONSE = int(CONFIG.get("batch_item_response_chars", 4000))
BATCH_TOTAL_RESPONSE = int(CONFIG.get("batch_total_response_chars", 12000))
DEFAULT_BATCH_ARTIFACT_DIR = str(
    CONFIG.get("default_batch_artifact_dir", ".model-orchestra-artifacts")
)
COST_CONTROL = CONFIG.get("cost_control", {})
HOST_MODEL = str(COST_CONTROL.get("host_model", "terra"))
ECONOMIC_OBJECTIVE = str(COST_CONTROL.get("objective", "strict_savings"))
IMPLEMENTATION_MODEL = str(
    COST_CONTROL.get("implementation_model", "k3")
)
CAPABILITY_FIRST_ROUTES = frozenset(
    str(route)
    for route in COST_CONTROL.get("capability_first_routes", ["repository-edit"])
)
if "repository-edit" in PIPELINES:
    PIPELINES["repository-edit"] = {
        **PIPELINES["repository-edit"],
        "single": IMPLEMENTATION_MODEL,
    }
MINIMUM_SAVING_PERCENT = float(
    COST_CONTROL.get("minimum_saving_percent", 10.0)
)
ESTIMATED_OUTPUT_TOKENS = int(COST_CONTROL.get("estimated_output_tokens", 1536))
ESTIMATED_OUTPUT_TOKENS_BY_ROUTE = {
    str(route): max(1, int(tokens))
    for route, tokens in COST_CONTROL.get(
        "estimated_output_tokens_by_route", {}
    ).items()
}
ESTIMATED_AGENT_STEPS = int(COST_CONTROL.get("estimated_agent_steps", 2))
CHARS_PER_TOKEN = float(COST_CONTROL.get("chars_per_token", 4.0))
BUDGET = CONFIG.get("budget", {})
BUDGET_PRICES = BUDGET.get("pricing_per_million_tokens", {})
BUDGET_CURRENCY = BUDGET.get("currency", "IDR")
BUDGET_PROVIDER_LIMITS = BUDGET.get("provider_limits", {})
BUDGET_PROVIDER_GROUPS = BUDGET.get("provider_groups", {})
BUDGET_BILLING_MODES = BUDGET.get("billing_modes", {})
CONFIG_DIR = CONFIG_PATH.parent
BUDGET_STATE_PATH = CONFIG_DIR / BUDGET.get(
    "state_file", ".model-orchestra-budget.json"
)
BUDGET_DB_PATH = CONFIG_DIR / BUDGET.get(
    "database_file", ".model-orchestra-budget.sqlite3"
)
AGENT_SHELL_MODE = CONFIG.get("agent_shell_mode", "deny")
if AGENT_SHELL_MODE not in {"deny", "allowlist", "unrestricted"}:
    raise ValueError(
        "agent_shell_mode must be 'deny', 'allowlist', or 'unrestricted'"
    )

_SHELL_WRAPPERS = frozenset({
    "bash", "cmd", "powershell", "pwsh", "sh", "wsl", "zsh",
})


def _shell_executable_name(command: str) -> str:
    """Return a bare executable name and reject shell wrappers."""
    name = str(command).strip().casefold()
    if not name or "/" in name or "\\" in name:
        raise ValueError(f"agent_shell_allowlist requires bare executable names: {command!r}")
    if name.endswith((".cmd", ".bat")):
        raise ValueError(f"command wrappers are not allowed in agent_shell_allowlist: {command!r}")
    if name.endswith(".exe"):
        name = name[:-4]
    if name in _SHELL_WRAPPERS:
        raise ValueError(f"shell wrappers are not allowed in agent_shell_allowlist: {command!r}")
    return name


AGENT_SHELL_ALLOWLIST = frozenset(
    _shell_executable_name(command)
    for command in CONFIG.get("agent_shell_allowlist", [])
)
AGENT_SHELL_EXECUTABLES = {
    command: resolved
    for command in AGENT_SHELL_ALLOWLIST
    if (resolved := shutil.which(command)) is not None
    and pathlib.Path(resolved).suffix.casefold() not in {".cmd", ".bat"}
}
if AGENT_SHELL_MODE == "allowlist":
    unresolved = AGENT_SHELL_ALLOWLIST - AGENT_SHELL_EXECUTABLES.keys()
    if unresolved:
        raise ValueError(
            "agent_shell_allowlist contains executables not found at startup: "
            + ", ".join(sorted(unresolved))
        )
AGENT_SHELL_TIMEOUT = int(CONFIG.get("agent_shell_timeout_seconds", 120))
AGENT_SHELL_MAX_OUTPUT = int(CONFIG.get("agent_shell_max_output_chars", 20000))
# Char-based cap (~4 chars/token => ~50K tokens at the default).
# If a worker needs the full window, raise max_input_chars in config.json.

mcp = FastMCP("model-orchestra")

# ── Session cost tracker ────────────────────────────────────────────────────

SESSION_USAGE = {
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cached_tokens": 0,
    "total_cache_write_tokens": 0,
    "calls": 0,
    "by_model": {},
}
SESSION_ORCHESTRATION = {
    "schema_version": 1,
    "calls": 0,
    "override_count": 0,
    "total_latency_seconds": 0.0,
    "tool_steps": 0,
    "returned_chars": 0,
    "host_reingestion_cost_estimate": 0.0,
    "outcomes": {},
    "fallback_categories": {},
    "by_route": {},
    "by_model": {},
    "events": [],
}
_ORCHESTRATION_EVENT_LIMIT = 100
_STATE_LOCK = threading.RLock()
# A context-local collector lets concurrent batch items report their own usage
# without subtracting overlapping global snapshots from one another.
_USAGE_COLLECTOR: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("model_orchestra_usage_collector", default=None)
)
_EVENT_COLLECTOR: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("model_orchestra_event_collector", default=None)
)
_ORCHESTRATION_COLLECTOR: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("model_orchestra_orchestration_collector", default=None)
)


@dataclass(frozen=True)
class AnthropicUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _anthropic_usage(response: Any) -> AnthropicUsage:
    """Usage from an Anthropic-style response, including prompt-cache traffic.

    input_tokens, cache reads, and cache writes are three SEPARATE counters.
    Cache writes are billed at a premium over normal input, so they are tracked
    rather than silently ignored (otherwise enabling caching looks free).
    """
    usage = response.usage
    return AnthropicUsage(
        usage.input_tokens,
        usage.output_tokens,
        int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _pricing_alias(model: str) -> str:
    """Map a raw model spec to an equivalent priced alias when one exists."""
    if model in BUDGET_PRICES:
        return model
    spec = WORKERS.get(model, model)
    for alias, configured in WORKERS.items():
        if configured == spec and alias in BUDGET_PRICES:
            return alias
    return model


def _precise_usage_cost(model: str, input_tokens: int, output_tokens: int,
                        cached_tokens: int = 0,
                        cache_write_tokens: int = 0) -> float:
    """Estimate configured cost without per-call integer rounding."""
    price = BUDGET_PRICES.get(_pricing_alias(model))
    if not price:
        return 0.0
    cached = max(0, cached_tokens)
    written = max(0, cache_write_tokens)
    return (
        input_tokens * price["input"]
        + cached * price.get("cached_input", price["input"])
        + written * price.get("cache_write", price["input"])
        + output_tokens * price["output"]
    ) / 1_000_000


def _usage_cost(model: str, input_tokens: int, output_tokens: int,
                cached_tokens: int = 0, cache_write_tokens: int = 0) -> int:
    """Estimate configured cost, rounded up for conservative budget guards."""
    cost = _precise_usage_cost(
        model, input_tokens, output_tokens, cached_tokens, cache_write_tokens
    )
    return math.ceil(cost)


def _load_budget_state() -> dict[str, list[dict[str, object]]]:
    try:
        state = json.loads(BUDGET_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    return state if isinstance(state, dict) and isinstance(state.get("entries"), list) else {"entries": []}


def _save_budget_state(state: dict[str, list[dict[str, object]]]) -> None:
    temporary = BUDGET_STATE_PATH.with_suffix(BUDGET_STATE_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(BUDGET_STATE_PATH)


def _provider_group(model: str) -> str | None:
    provider, _ = resolve(model)
    return BUDGET_PROVIDER_GROUPS.get(provider)


def _spent_since(entries: list[dict[str, object]], group: str, since: dt.datetime) -> int:
    total = 0
    for entry in entries:
        try:
            created = dt.datetime.fromisoformat(str(entry["at"]))
            cost = int(str(entry["idr"]))
        except (KeyError, TypeError, ValueError):
            continue
        if entry.get("group") == group and created >= since:
            total += cost
    return total


@contextlib.contextmanager
def _budget_connection():
    BUDGET_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(BUDGET_DB_PATH, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_reservations (
                operation_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                group_name TEXT NOT NULL,
                model TEXT NOT NULL,
                reserved_idr INTEGER NOT NULL CHECK (reserved_idr >= 0),
                state TEXT NOT NULL CHECK (
                    state IN ('reserved', 'settled', 'pending_liability', 'void')
                ),
                actual_idr INTEGER,
                settlement_id TEXT UNIQUE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS budget_reservations_group_time "
            "ON budget_reservations(group_name, created_at)"
        )
        yield connection
    finally:
        connection.close()


def _reserved_since(connection: sqlite3.Connection, group: str,
                    since: dt.datetime) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(
            CASE WHEN state = 'settled' THEN COALESCE(actual_idr, reserved_idr)
                 WHEN state IN ('reserved', 'pending_liability') THEN reserved_idr
                 ELSE 0 END
        ), 0)
        FROM budget_reservations
        WHERE group_name = ? AND created_at >= ?
        """,
        (group, since.isoformat()),
    ).fetchone()
    return int(row[0] or 0)


def _budget_windows(now: dt.datetime) -> tuple[tuple[str, dt.datetime], ...]:
    return (
        ("monthly", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)),
        ("daily", now.replace(hour=0, minute=0, second=0, microsecond=0)),
        ("5-hour", now - dt.timedelta(hours=5)),
    )


def _budget_reserve(model: str, input_tokens: int, max_output_tokens: int) -> str:
    """Atomically reserve the configured maximum across processes."""
    group = _provider_group(model)
    if not group or group not in BUDGET_PROVIDER_LIMITS:
        return ""
    pricing_model = _pricing_alias(model)
    if pricing_model not in BUDGET_PRICES:
        raise RuntimeError(
            f"Budget guard blocked unpriced model {model!r} on budgeted provider "
            f"group {group!r}; add a pricing entry before use."
        )

    now = dt.datetime.now(dt.timezone.utc)
    reserved = _usage_cost(pricing_model, input_tokens, max_output_tokens)
    limits = BUDGET_PROVIDER_LIMITS[group]
    legacy_entries = _load_budget_state()["entries"]
    operation_id = uuid.uuid4().hex
    with _budget_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for name, since in _budget_windows(now):
            key = "five_hour" if name == "5-hour" else name
            limit = int(limits.get(key, 0))
            spent = (
                _spent_since(legacy_entries, group, since)
                + _reserved_since(connection, group, since)
            )
            if limit and spent + reserved > limit:
                connection.rollback()
                raise RuntimeError(
                    f"Budget guard blocked {model}: estimated {reserved:,} "
                    f"{BUDGET_CURRENCY} would exceed the {group} {name} limit "
                    f"({spent:,}/{limit:,} {BUDGET_CURRENCY})."
                )
        connection.execute(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, state) "
            "VALUES (?, ?, ?, ?, ?, 'reserved')",
            (operation_id, now.isoformat(), group, model, reserved),
        )
        connection.commit()
    return operation_id


def _budget_settle(operation_id: str, model: str, usage: Any,
                   settlement_id: str | None = None) -> None:
    if not operation_id:
        return
    inp, out, cached, written = _usage_components(usage)
    actual = _usage_cost(_pricing_alias(model), inp, out, cached, written)
    settlement_id = settlement_id or operation_id
    with _budget_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT operation_id FROM budget_reservations WHERE settlement_id = ?",
            (settlement_id,),
        ).fetchone()
        if existing is not None:
            connection.commit()
            return
        connection.execute(
            "UPDATE budget_reservations SET state = 'settled', actual_idr = ?, "
            "settlement_id = ? WHERE operation_id = ? AND state != 'settled'",
            (actual, settlement_id, operation_id),
        )
        connection.commit()


def _budget_pending(operation_id: str) -> None:
    if not operation_id:
        return
    with _budget_connection() as connection:
        connection.execute(
            "UPDATE budget_reservations SET state = 'pending_liability' "
            "WHERE operation_id = ? AND state = 'reserved'",
            (operation_id,),
        )


def _budget_check(model: str, input_tokens: int, max_output_tokens: int) -> None:
    """Fail closed before a request exceeds its provider-specific time envelope."""
    group = _provider_group(model)
    if not group or group not in BUDGET_PROVIDER_LIMITS:
        return
    pricing_model = _pricing_alias(model)
    if pricing_model not in BUDGET_PRICES:
        raise RuntimeError(
            f"Budget guard blocked unpriced model {model!r} on budgeted provider "
            f"group {group!r}; add a pricing entry before use."
        )
    now = dt.datetime.now(dt.timezone.utc)
    worst_case = _usage_cost(pricing_model, input_tokens, max_output_tokens)
    limits = BUDGET_PROVIDER_LIMITS[group]
    with _STATE_LOCK:
        entries = _load_budget_state()["entries"]
        with _budget_connection() as connection:
            windows = []
            for name, since in _budget_windows(now):
                key = "five_hour" if name == "5-hour" else name
                spent = (
                    _spent_since(entries, group, since)
                    + _reserved_since(connection, group, since)
                )
                windows.append((name, int(limits.get(key, 0)), spent))
        for name, limit, spent in windows:
            if limit and spent + worst_case > limit:
                raise RuntimeError(
                    f"Budget guard blocked {model}: estimated {worst_case:,} {BUDGET_CURRENCY} "
                    f"would exceed the {group} {name} limit ({spent:,}/{limit:,} {BUDGET_CURRENCY})."
                )


def _usage_snapshot() -> dict[str, Any]:
    """Return an isolated usage snapshot suitable for benchmark deltas."""
    with _STATE_LOCK:
        return json.loads(json.dumps(SESSION_USAGE))


def _usage_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Subtract two session snapshots, preserving per-model token counters."""
    fields = (
        "total_input_tokens", "total_output_tokens", "total_cached_tokens",
        "total_cache_write_tokens", "calls",
    )
    delta: dict[str, Any] = {
        field: int(after.get(field, 0)) - int(before.get(field, 0))
        for field in fields
    }
    delta["by_model"] = {}
    aliases = set(before.get("by_model", {})) | set(after.get("by_model", {}))
    for alias in aliases:
        old = before.get("by_model", {}).get(alias, {})
        new = after.get("by_model", {}).get(alias, {})
        entry = {
            field: int(new.get(field, 0)) - int(old.get(field, 0))
            for field in ("input", "output", "cached", "cache_write", "calls")
        }
        if any(entry.values()):
            delta["by_model"][alias] = entry
    return delta


def _usage_total_cost(usage: Mapping[str, Any]) -> float:
    return sum(
        _precise_usage_cost(
            alias,
            int(values.get("input", 0)),
            int(values.get("output", 0)),
            int(values.get("cached", 0)),
            int(values.get("cache_write", 0)),
        )
        for alias, values in usage.get("by_model", {}).items()
    )


def _empty_usage() -> dict[str, Any]:
    return {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cached_tokens": 0,
        "total_cache_write_tokens": 0,
        "calls": 0,
        "by_model": {},
    }


def _usage_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_components(usage: Any) -> tuple[int, int, int, int]:
    """Normalize Anthropic and OpenAI usage into fresh/cache/output counters.

    Anthropic reports cache reads separately from ``input_tokens``. OpenAI-style
    gateways commonly put cached tokens inside ``prompt_tokens`` and expose the
    subset as ``prompt_tokens_details.cached_tokens``. Normalizing here prevents
    cached input from being charged twice.
    """
    if not usage:
        return 0, 0, 0, 0
    prompt = _usage_value(usage, "prompt_tokens")
    if prompt is None:
        prompt = _usage_value(usage, "input_tokens", 0)
    output = _usage_value(usage, "completion_tokens")
    if output is None:
        output = _usage_value(usage, "output_tokens", 0)

    cached = _usage_value(usage, "cache_read_tokens")
    if cached is None:
        details = _usage_value(usage, "prompt_tokens_details")
        if details is None:
            details = _usage_value(usage, "input_tokens_details")
        cached = _usage_value(details, "cached_tokens", 0)
    written = _usage_value(usage, "cache_write_tokens")
    if written is None:
        written = _usage_value(usage, "cache_creation_input_tokens", 0)

    prompt = max(0, int(prompt or 0))
    output = max(0, int(output or 0))
    cached = max(0, int(cached or 0))
    written = max(0, int(written or 0))
    # The OpenAI field is a total prompt count; Anthropic's input field is fresh
    # input. The presence of prompt_tokens identifies the former convention.
    fresh = prompt - cached - written if _usage_value(usage, "prompt_tokens") is not None else prompt
    return max(0, fresh), output, cached, written


def _add_usage(target: dict[str, Any], model: str, inp: int, out: int,
               cached: int, written: int) -> None:
    target["total_input_tokens"] += inp
    target["total_output_tokens"] += out
    target["total_cached_tokens"] += cached
    target["total_cache_write_tokens"] += written
    target["calls"] += 1
    entry = target["by_model"].setdefault(
        model, {"input": 0, "output": 0, "cached": 0, "cache_write": 0, "calls": 0}
    )
    entry["input"] += inp
    entry["output"] += out
    entry["cached"] += cached
    entry["cache_write"] += written
    entry["calls"] += 1


def _track(model: str, usage, *, record_legacy_budget: bool = True):
    """Record normalized usage globally and in the current batch collector."""
    if not usage:
        return
    inp, out, cached, written = _usage_components(usage)
    group = _provider_group(model)
    pricing_model = _pricing_alias(model)
    with _STATE_LOCK:
        _add_usage(SESSION_USAGE, model, inp, out, cached, written)
        if record_legacy_budget and group and pricing_model in BUDGET_PRICES:
            state = _load_budget_state()
            state["entries"].append({
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "group": group,
                "model": model,
                "input_tokens": inp,
                "output_tokens": out,
                "cached_tokens": cached,
                "cache_write_tokens": written,
                "idr": _usage_cost(pricing_model, inp, out, cached, written),
            })
            _save_budget_state(state)
    collector = _USAGE_COLLECTOR.get()
    if collector is not None:
        with _STATE_LOCK:
            _add_usage(collector, model, inp, out, cached, written)


def _record_event(kind: str, **details: Any) -> None:
    collector = _EVENT_COLLECTOR.get()
    if collector is not None:
        with _STATE_LOCK:
            collector.append({"kind": kind, **details})


def _append_orchestration_event(event: Mapping[str, Any]) -> None:
    SESSION_ORCHESTRATION["events"].append(dict(event))
    del SESSION_ORCHESTRATION["events"][:-_ORCHESTRATION_EVENT_LIMIT]


def _track_orchestration(source: str, route: str | None, models: list[str],
                         outcome: str, fallback_category: str | None,
                         latency_seconds: float, tool_steps: int,
                         returned_chars: int) -> None:
    """Record bounded metadata about one routing operation, never its prompt."""
    route_name = route or "host"
    safe_models = [str(model) for model in models]
    returned = max(0, int(returned_chars))
    latency = max(0.0, float(latency_seconds))
    steps = max(0, int(tool_steps))
    returned_tokens = math.ceil(returned / max(CHARS_PER_TOKEN, 0.1))
    reingestion_cost = _precise_usage_cost(HOST_MODEL, returned_tokens, 0)
    event = {
        "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": source,
        "route": route_name,
        "models": safe_models,
        "outcome": outcome,
        "fallback_category": fallback_category,
        "latency_seconds": round(latency, 3),
        "tool_steps": steps,
        "returned_chars": returned,
        "host_reingestion_cost_estimate": round(reingestion_cost, 6),
    }
    with _STATE_LOCK:
        state = SESSION_ORCHESTRATION
        state["calls"] += 1
        state["total_latency_seconds"] = round(
            float(state["total_latency_seconds"]) + latency, 3
        )
        state["returned_chars"] += returned
        state["host_reingestion_cost_estimate"] = round(
            float(state["host_reingestion_cost_estimate"]) + reingestion_cost, 6
        )
        state["outcomes"][outcome] = state["outcomes"].get(outcome, 0) + 1
        if fallback_category:
            state["fallback_categories"][fallback_category] = (
                state["fallback_categories"].get(fallback_category, 0) + 1
            )
        route_entry = state["by_route"].setdefault(
            route_name, {"calls": 0, "tool_steps": 0, "outcomes": {}}
        )
        route_entry["calls"] += 1
        route_entry["tool_steps"] += steps
        route_entry["outcomes"][outcome] = (
            route_entry["outcomes"].get(outcome, 0) + 1
        )
        for model in safe_models:
            model_entry = state["by_model"].setdefault(
                model, {"calls": 0, "tool_steps": 0}
            )
            model_entry["calls"] += 1
        _append_orchestration_event(event)


def _record_capability_override(task_kind: str, model: str) -> None:
    event = {
        "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": "delegate",
        "kind": "capability_override",
        "task_kind": task_kind,
        "model": model,
    }
    with _STATE_LOCK:
        SESSION_ORCHESTRATION["override_count"] += 1
        _append_orchestration_event(event)
    _record_event(
        "capability_override", task_kind=task_kind, model=model
    )


def _record_economic_override(model: str) -> None:
    event = {
        "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": "delegate",
        "kind": "economic_override",
        "model": model,
    }
    with _STATE_LOCK:
        SESSION_ORCHESTRATION["override_count"] += 1
        _append_orchestration_event(event)
    _record_event("economic_override", model=model)


def _record_agent_tool_step(model: str, tool: str) -> None:
    with _STATE_LOCK:
        SESSION_ORCHESTRATION["tool_steps"] += 1
        model_entry = SESSION_ORCHESTRATION["by_model"].setdefault(
            model, {"calls": 0, "tool_steps": 0}
        )
        model_entry["tool_steps"] += 1
    collector = _ORCHESTRATION_COLLECTOR.get()
    while collector is not None:
        collector["tool_steps"] = collector.get("tool_steps", 0) + 1
        collector = collector.get("parent")
    _record_event("agent_tool_call", model=model, tool=tool)


def _orchestration_snapshot(detail: bool = False) -> dict[str, Any]:
    with _STATE_LOCK:
        snapshot = json.loads(json.dumps(SESSION_ORCHESTRATION))
    if not detail:
        snapshot.pop("events", None)
    return snapshot


def _submit_with_context(executor: cf.Executor, function, *args, **kwargs):
    """Submit work while preserving route-local usage and event accounting."""
    context = contextvars.copy_context()
    return executor.submit(context.run, function, *args, **kwargs)


# ── Routing ─────────────────────────────────────────────────────────────────

def resolve(model: str) -> tuple[str, str]:
    """Return (provider_name, model_id) from an alias or a 'provider/model' string."""
    spec = WORKERS.get(model, model)
    provider, _, model_id = spec.partition("/")  # split on FIRST slash only
    if provider not in PROVIDERS or not model_id:
        raise ValueError(
            f"Bad model {model!r} -> {spec!r}. Use an alias {list(WORKERS)} "
            f"or 'provider/model-id' with provider in {list(PROVIDERS)}."
        )
    return provider, model_id


def _keys_for(provider: str) -> list[str]:
    """Return configured API keys in deterministic failover order.

    Providers may declare ``api_key_envs`` for named ordered credentials. The
    legacy ``api_key_env`` and comma-separated ``<ENV>_FALLBACKS`` format is used
    only when none of those named variables are configured.
    """
    p = PROVIDERS[provider]
    ordered_envs = p.get("api_key_envs", [])
    keys = [os.environ.get(name, "") for name in ordered_envs]
    if not any(keys):
        primary_env = p["api_key_env"]
        primary = os.environ.get(primary_env, "")
        extra = os.environ.get(primary_env + "_FALLBACKS", "")
        keys = [primary] + [key.strip() for key in extra.split(",")]

    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def client_for(provider: str, key: str | None = None):
    p = PROVIDERS[provider]
    if key is None:
        keys = _keys_for(provider)
        if not keys:
            names = p.get("api_key_envs") or [p["api_key_env"]]
            raise RuntimeError(
                f"Missing provider credentials for {provider!r}; configure one of: "
                + ", ".join(names)
            )
        key = keys[0]
    if p.get("client") == "anthropic":
        import anthropic
        return anthropic.Anthropic(base_url=p["base_url"], api_key=key)
    return OpenAI(base_url=p["base_url"], api_key=key)


# ── CybSec quality guard ────────────────────────────────────────────────────
# Security/CTF/forensics reasoning must never fall to cheap workers. Routing
# tools floor flagged tasks at Sol. Direct delegate(model=...) calls are guarded;
# bypassing the floor requires the named, telemetry-audited override flag.
SECURITY_PATTERNS = (
    r"\b(?:write|build|create|develop|craft|run)\s+(?:an?\s+)?exploit\b",
    r"\bexploitability\b|\bexploit\b(?=.{0,40}\b(?:cve|vulnerability|server|system|target)\b)",
    r"\bvulnerabilit(?:y|ies)\b|\bvulns?\b",
    r"\bcve-\d{4}-\d{4,}\b",
    r"\bcryptograph(?:y|ic|ical)\b|\bcrypto[-\s]+(?:challenge|attack|primitive)\b",
    r"\bciphers?\b(?![-\s]+themed)|\b(?:de|en)crypt(?:ed|ing|ion|or|s)?\b",
    r"\b(?:rce|xss|sqli|csrf|ssrf|xxe|idor)\b|\bsql[-\s]+injection\b",
    r"\b(?:command|code|template|ldap|xpath)[-\s]+injection\b",
    r"\b(?:digital[-\s]+)?forensics?\b(?![-\s]+accounting)|\bctf\b|\bflag\s*\{",
    r"\breverse[-\s]+engineer(?:ed)?\b|"
    r"\breverse[-\s]+engineering\b(?![-\s]+(?:calculation|cost|estimate))",
    r"\bmalware\b|\bshellcode\b|\bpwn(?:ed|ing)?\b",
    r"\bsteganograph(?:y|ic)\b|\bstego\b",
    r"\bprivilege[-\s]+escalation\b|\bprivesc\b",
    r"\bbuffer[-\s]+overflow\b|\brop[-\s]+chain\b",
    r"\bunsafe[-\s]+deserializ(?:ation|e|ing)\b",
    r"\bpcaps?\b|\bdisassembl(?:e|ed|er|ing|y)\b",
    r"\bobfuscat(?:e|ed|ing|ion|or)\b|\bhashcat\b",
    r"\bauth(?:entication|orization)?[-\s]+bypass\b|\bpath[-\s]+traversal\b",
    r"\bcredential[-\s]+dump(?:ing)?\b|\bpenetration[-\s]+test(?:ing)?\b|\bpentest\b",
    r"\b(?:rootkit|keylogger|ransomware)\b",
    r"\bsecurity[-\s]+(?:review|audit|assessment|hardening|issues?|vulnerabilit(?:y|ies)|findings?)\b",
)
_SECURITY_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in SECURITY_PATTERNS),
    re.IGNORECASE,
)
STRONG_MODELS = set(TIERS.get("strong", []))
# Set enforce_security_floor:false in config.json to let cheap/Sol recipes handle
# security tasks too. Default true: a subtly-wrong exploit costs more than the tokens.
ENFORCE_SECURITY_FLOOR = CONFIG.get("enforce_security_floor", True)


def _is_security(task: str) -> bool:
    """Return whether a task requires the strong security capability floor.

    Lexical boundaries prevent ordinary terms such as ``cryptocurrency``,
    ``deserialize``, and ``payload`` from matching fragments of security terms.
    A labeled corpus in tests/fixtures guards both misses and false positives.
    """
    return _SECURITY_RE.search(task) is not None


def _pipe_models(pipe: dict) -> list[str]:
    """Every worker alias a pipeline recipe would touch."""
    m = list(pipe.get("workers", [])) + list(pipe.get("stages", []))
    for k in ("single", "drafter", "refiner", "judge"):
        if k in pipe:
            m.append(pipe[k])
    return m


def _truncate(text: str, limit: int = 0) -> str:
    """Truncate worker output to save orchestrator context tokens."""
    cap = limit or MAX_RESPONSE
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    marker = f"\n... [TRUNCATED {len(text) - cap:,} chars] ...\n"
    if len(marker) >= cap:
        return marker[:cap]
    available = cap - len(marker)
    head = available // 2
    tail = available - head
    return text[:head] + marker + text[-tail:]


def _safe_error(error: Any, limit: int = 500) -> str:
    """Return a bounded diagnostic with configured credentials redacted."""
    text = str(error)
    for provider in PROVIDERS.values():
        names = provider.get("api_key_envs") or [provider.get("api_key_env", "")]
        for name in names:
            secret = os.environ.get(name, "")
            if secret:
                text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(?:sk|key|token)[-_][A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    return _truncate(text, limit)


def _cap_input(text: str) -> str:
    """Keep a worker prompt under the model's context window so it never 502s
    with 'input exceeds context window'. Head+tail kept, middle dropped."""
    return _truncate(text, MAX_INPUT)


# Must exceed the time to generate worker_max_tokens on a SLOW provider, or we
# abort our own in-progress generations and retry them. Measured: a 2,563-token
# mimo completion needed ~60s+ per attempt, so the old 60s cap timed out twice
# before succeeding (181s for one logical call). Raising worker_max_tokens
# without raising this re-creates that bug.
REQUEST_TIMEOUT = int(CONFIG.get("request_timeout_seconds", 180))
# Ceiling on ONE logical call including every retry, key rotation, and model
# fallback. Without it the cascade is unbounded: 5 models x 3 retries x a 60s
# timeout can stall a single pipeline() for many minutes (measured: 196s on a
# task that generates in ~20s). A stalled call also breaks ACP/editor hosts that
# give up on long tool calls, so this is a usability fix, not just a speed one.
# MUST stay comfortably above REQUEST_TIMEOUT, or the deadline kills a single
# legitimate slow generation instead of only bounding a pathological cascade.
CASCADE_DEADLINE = int(CONFIG.get("cascade_deadline_seconds", 420))
if CASCADE_DEADLINE <= REQUEST_TIMEOUT:
    raise ValueError(
        f"cascade_deadline_seconds ({CASCADE_DEADLINE}) must exceed "
        f"request_timeout_seconds ({REQUEST_TIMEOUT}); otherwise the deadline "
        "aborts a single in-flight generation rather than bounding failover."
    )


def _remaining(deadline: float | None) -> float | None:
    """Seconds left before the deadline, or None when uncapped."""
    return None if deadline is None else deadline - time.monotonic()


def _is_transient(e: Exception) -> bool:
    """OpenCode's relay masks upstream outages as a 400 'Upstream request
    failed' / 'Console Go' error. Those are retryable; a real 400 is not."""
    m = str(e).lower()
    return ("upstream request failed" in m or "console go" in m
            or "overloaded" in m or "timeout" in m or "timed out" in m
            or " 500" in m or " 502" in m or " 503" in m or " 529" in m)


def _context_overflow(e: Exception) -> bool:
    """Input too big for the model's context window. PERMANENT — retrying or
    rotating keys never helps (every key hits the same limit), so callers must
    fail fast instead of hammering. This is why a 502 'exceeds the context
    window' would otherwise hang: _is_transient matches ' 502' and retries."""
    m = str(e).lower()
    return ("context window" in m or "exceeds the context" in m
            or "context length" in m or "context_length_exceeded" in m
            or "maximum context" in m or "input is too long" in m
            or "reduce the length" in m or "too many tokens" in m)


def _key_exhausted(e: Exception) -> bool:
    """Auth/quota/rate-limit errors that a *different* API key might survive.
    A plain 400 bad-request is the caller's fault, so it is NOT here."""
    m = str(e).lower()
    return (" 401" in m or " 402" in m or " 403" in m or " 429" in m
            or "unauthorized" in m or "quota" in m or "insufficient" in m
            or "rate limit" in m or "rate_limit" in m)


def _create_with_retry(
    provider: str, *, retries: int = 3, backoff: float = 0.8,
    deadline: float | None = None, budget_model: str = "",
    budget_input_tokens: int = 0, budget_output_tokens: int = 0,
    **kwargs: Any
) -> Any:
    """create() with backoff on transient upstream errors, then key-level failover:
    if a key keeps 503ing or is rate-limited/quota-exhausted, rotate to the next
    key from _keys_for(provider). A real 400 raises immediately (no point retrying)."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    # Bound EVERY path, not just the failover cascade. delegate() and the agent
    # loops call this directly, so without a default deadline the worst case is
    # keys x retries x REQUEST_TIMEOUT (3 x 3 x 180s = 27 minutes) with nothing
    # to stop it.
    if deadline is None:
        deadline = time.monotonic() + CASCADE_DEADLINE
    is_anthropic = PROVIDERS[provider].get("client") == "anthropic"
    keys = _keys_for(provider)
    if not keys:
        provider_config = PROVIDERS[provider]
        names = provider_config.get("api_key_envs") or [provider_config["api_key_env"]]
        raise RuntimeError(
            f"Missing provider credentials for {provider!r}; configure one of: "
            + ", ".join(names)
        )
    last: Exception | None = None
    for ki, key in enumerate(keys):
        client = client_for(provider, key)
        for i in range(retries):
            left = _remaining(deadline)
            if left is not None:
                if left <= 0:
                    raise last or TimeoutError(
                        f"cascade deadline exceeded before {provider!r} responded")
                # Never wait past the deadline on a single attempt either.
                kwargs["timeout"] = min(kwargs.get("timeout", REQUEST_TIMEOUT), left)
            try:
                _record_event(
                    "provider_attempt", provider=provider, key_index=ki,
                    attempt=i + 1,
                )
                reservation_id = _budget_reserve(
                    budget_model, budget_input_tokens, budget_output_tokens
                ) if budget_model else ""
                try:
                    if is_anthropic:
                        response = client.messages.create(**kwargs)
                        usage = _anthropic_usage(response)
                    else:
                        response = client.chat.completions.create(**kwargs)
                        usage = response.usage
                except BaseException:
                    _budget_pending(reservation_id)
                    raise
                _budget_settle(reservation_id, budget_model, usage)
                return response
            except Exception as e:
                last = e
                if _context_overflow(e):
                    raise               # input too long: no retry/rotate can fix
                if _is_transient(e):
                    if i < retries - 1:
                        pause = backoff * (2 ** i)
                        left = _remaining(deadline)
                        if left is not None and pause >= left:
                            raise       # backing off would blow the deadline
                        _record_event(
                            "provider_retry", provider=provider,
                            key_index=ki, attempt=i + 1,
                        )
                        time.sleep(pause)
                        continue        # same key, back off and retry
                    if ki < len(keys) - 1:
                        _record_event(
                            "key_rotation", provider=provider,
                            from_key_index=ki, to_key_index=ki + 1,
                        )
                    break               # retries exhausted -> next key or fail
                if _key_exhausted(e) and ki < len(keys) - 1:
                    _record_event(
                        "key_rotation", provider=provider,
                        from_key_index=ki, to_key_index=ki + 1,
                    )
                    break               # dead/limited key -> next key now
                raise                   # real error (e.g. 400) -> give up
        # transient retries exhausted on this key -> fall through to next key
    if last is None:
        raise RuntimeError(f"Provider {provider!r} did not make a completion attempt.")
    raise last


def _cacheable_system(system: str) -> list[dict[str, Any]]:
    """Send the system prompt as a cacheable block.

    Gateway input is billed at the ~10x cheaper `cached_input` rate on a cache
    hit (see budget.pricing_per_million_tokens). System prompts are the same on
    every call of a recipe, so they are the highest-value thing to cache.
    Providers ignore cache_control below their minimum cacheable size.
    """
    return [{"type": "text", "text": system,
             "cache_control": {"type": "ephemeral"}}]


def _anthropic_text(content: list[Any]) -> str:
    """Join text blocks while ignoring thinking and tool-use blocks."""
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            block_type = block.get("type")
            text = block.get("text")
        else:
            block_type = getattr(block, "type", None)
            text = getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def chat(model: str, prompt: str, system: str = "", temperature: float = 0.2,
         max_tokens: int | None = None, deadline: float | None = None) -> str:
    """One-shot text completion with a bounded generation budget."""
    provider, model_id = resolve(model)
    is_anthropic = PROVIDERS[provider].get("client") == "anthropic"
    prompt = _cap_input(prompt)
    output_tokens = max_tokens or WORKER_MAX_TOKENS
    budget_kwargs = {
        "budget_model": model,
        "budget_input_tokens": len(prompt) // 4,
        "budget_output_tokens": output_tokens,
    }
    if is_anthropic:
        kwargs: dict[str, Any] = dict(
            model=model_id,
            max_tokens=output_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = _cacheable_system(system)
        r = _create_with_retry(
            provider, deadline=deadline, **budget_kwargs, **kwargs
        )
        usage = _anthropic_usage(r)
        _track(model, usage, record_legacy_budget=False)
        return _truncate(_anthropic_text(r.content))

    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    r = _create_with_retry(
        provider, model=model_id, messages=msgs, temperature=temperature,
        max_tokens=output_tokens, deadline=deadline, **budget_kwargs,
    )
    _track(model, r.usage, record_legacy_budget=False)
    return _truncate(r.choices[0].message.content or "")


def _fallbacks_for(model: str) -> list[str]:
    """Return model failovers without weakening explicit capability floors."""
    if model in MODEL_FALLBACKS:
        return list(MODEL_FALLBACKS[model])

    order: list[str] = []
    for tier in TIERS.values():
        if model in tier:
            order += [m for m in tier if m != model]  # reliable siblings first
            break
    order += FALLBACK_CHAIN
    # dedup, drop the primary, keep order
    seen, out = {model}, []
    for m in order:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def chat_with_failover(model: str, prompt: str, system: str = "",
                       temperature: float = 0.2,
                       max_tokens: int | None = None,
                       deadline: float | None = None) -> str:
    """Try the primary; cascade only for errors another route can fix."""
    budget = max_tokens or WORKER_MAX_TOKENS
    deadline = deadline or (time.monotonic() + CASCADE_DEADLINE)
    try:
        return chat(model, prompt, system, temperature, budget, deadline=deadline)
    except Exception as error:
        # Context overflow and malformed requests are permanent for this payload.
        # Cascading those errors wastes calls and can multiply an ordinary bug into
        # a long, expensive failure. Transient upstream and key/quota failures are
        # the cases where a different provider or alias can actually help.
        if _context_overflow(error) or not (_is_transient(error) or _key_exhausted(error)):
            raise
        last_error = error
        for fallback in _fallbacks_for(model):
            # Stop cascading once the wall clock is spent; trying a 5th model
            # with no time left just turns a failure into a much slower failure.
            remaining = _remaining(deadline)
            if remaining is not None and remaining <= 0:
                raise last_error
            _record_event("model_fallback", from_model=model, to_model=fallback)
            try:
                return chat(fallback, prompt, system, temperature, budget, deadline=deadline)
            except Exception as fallback_error:
                if _context_overflow(fallback_error):
                    raise
                if not (_is_transient(fallback_error) or _key_exhausted(fallback_error)):
                    raise
                last_error = fallback_error
        # Every eligible fallback failed. Do not retry the primary: that would
        # duplicate a paid call without changing the failure cause.
        raise last_error


# ── Agent-mode tools the worker can call ────────────────────────────────────

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List a workspace directory without leaving the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative directory; default is ."}}}}},
    {"type": "function", "function": {
        "name": "find_path",
        "description": "Find workspace files with a relative glob such as **/*.py.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Relative directory; default is ."}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search bounded UTF-8 workspace text with a regular expression.",
        "parameters": {"type": "object", "properties": {
            "regex": {"type": "string"},
            "path": {"type": "string", "description": "Relative file or directory; default is ."},
            "include_pattern": {"type": "string", "description": "Relative glob; default is **/*"}},
            "required": ["regex"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a bounded UTF-8 file range relative to the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Apply exact, unique old_text/new_text replacements to a workspace file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["old_text", "new_text"]}}},
            "required": ["path", "edits"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write (overwrite) a UTF-8 text file relative to the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
]

if AGENT_SHELL_MODE != "deny":
    shell_description = (
        "Run one allowlisted executable in the workspace without shell syntax."
        if AGENT_SHELL_MODE == "allowlist"
        else "Run a shell command in the workspace. Unrestricted trusted mode is enabled."
    )
    AGENT_TOOLS.append({"type": "function", "function": {
        "name": "run_shell",
        "description": shell_description,
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}})


def _workspace_target(workspace: pathlib.Path, relative_path: str) -> pathlib.Path | None:
    """Resolve a worker path and reject traversal and symlink escapes."""
    root = workspace.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


SHELL_METACHARACTERS = ("\n", "\r", ";", "&", "|", ">", "<", "`", "$(")


def _allowlisted_argv(command: str) -> list[str] | None:
    """Parse one command for shell=False, rejecting composition and path escapes."""
    if not command.strip() or any(token in command for token in SHELL_METACHARACTERS):
        return None
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None
    if os.name == "nt":
        argv = [
            value[1:-1]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
            else value
            for value in argv
        ]
    if not argv:
        return None

    try:
        executable = _shell_executable_name(argv[0])
    except ValueError:
        return None
    resolved_executable = AGENT_SHELL_EXECUTABLES.get(executable)
    if resolved_executable is None:
        return None

    for value in argv[1:]:
        path_value = value.split("=", 1)[-1]
        normalized = path_value.replace("\\", "/")
        if ".." in normalized.split("/"):
            return None
        if pathlib.PurePosixPath(path_value).is_absolute():
            return None
        if pathlib.PureWindowsPath(path_value).is_absolute():
            return None
    argv[0] = resolved_executable
    return argv


def _run_agent_command(command: str, workspace: pathlib.Path) -> str:
    if AGENT_SHELL_MODE == "deny":
        return "ERROR: shell access is disabled by agent_shell_mode=deny"

    if AGENT_SHELL_MODE == "allowlist":
        argv = _allowlisted_argv(command)
        if argv is None:
            return "ERROR: command blocked by the agent shell allowlist"
        run_args: str | list[str] = argv
        use_shell = False
    else:
        run_args = command
        use_shell = True

    try:
        result = subprocess.run(
            run_args,
            shell=use_shell,
            cwd=workspace,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=AGENT_SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {AGENT_SHELL_TIMEOUT} seconds"
    except OSError as error:
        return f"ERROR: command failed to start: {error}"

    output = result.stdout + result.stderr
    if result.returncode:
        output = f"[exit code {result.returncode}]\n{output}"
    return _truncate(output or "(no output)", AGENT_SHELL_MAX_OUTPUT)


def _relative_glob(root: pathlib.Path, pattern: str) -> list[pathlib.Path] | None:
    """Expand a bounded relative glob while rejecting traversal and absolute patterns."""
    raw = str(pattern or "")
    if not raw or pathlib.PurePosixPath(raw).is_absolute() or pathlib.PureWindowsPath(raw).is_absolute():
        return None
    parts = raw.replace("\\", "/").split("/")
    if ".." in parts:
        return None
    matches: list[pathlib.Path] = []
    try:
        for candidate in root.glob(raw):
            if len(matches) >= 1001:
                break
            try:
                relative = str(candidate.relative_to(root))
            except ValueError:
                continue
            if _workspace_target(root, relative) is not None:
                matches.append(candidate)
    except (OSError, ValueError):
        return None
    return matches


def _agent_list_directory(path: str, workspace: pathlib.Path) -> str:
    target = _workspace_target(workspace, path or ".")
    if target is None:
        return "ERROR: path traversal blocked"
    if not target.is_dir():
        return "ERROR: not a directory"
    try:
        children = sorted(
            list(target.iterdir()),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError as error:
        return f"ERROR: directory could not be read ({error})"
    lines = [
        f"{'dir' if child.is_dir() else 'file'} {child.name}"
        for child in children[:200]
    ]
    if len(children) > 200:
        lines.append("[TRUNCATED: directory has more than 200 entries]")
    return _truncate("\n".join(lines) or "(empty directory)", 12000)


def _agent_find_path(pattern: str, path: str, workspace: pathlib.Path) -> str:
    base = _workspace_target(workspace, path or ".")
    if base is None:
        return "ERROR: path traversal blocked"
    if not base.is_dir():
        return "ERROR: not a directory"
    matches = _relative_glob(base, pattern)
    if matches is None:
        return "ERROR: pattern must be a relative workspace glob"
    root = workspace.resolve()
    rendered = []
    for candidate in sorted(matches, key=lambda item: str(item).casefold()):
        try:
            rendered.append(str(candidate.resolve().relative_to(root)))
        except ValueError:
            continue
        if len(rendered) >= 200:
            break
    suffix = "\n[TRUNCATED: more than 200 matches]" if len(matches) > 200 else ""
    return _truncate("\n".join(rendered) + suffix or "(no matches)", 12000)


def _agent_grep(regex: str, path: str, include_pattern: str, workspace: pathlib.Path) -> str:
    if len(str(regex)) > 1000:
        return "ERROR: regex is limited to 1,000 characters"
    try:
        matcher = re.compile(str(regex))
    except re.error as error:
        return f"ERROR: invalid regex ({error})"
    base = _workspace_target(workspace, path or ".")
    if base is None:
        return "ERROR: path traversal blocked"
    if base.is_file():
        candidates = [base]
    elif base.is_dir():
        matches = _relative_glob(base, include_pattern or "**/*")
        if matches is None:
            return "ERROR: include_pattern must be a relative workspace glob"
        candidates = matches
    else:
        return "ERROR: path does not exist"

    lines: list[str] = []
    root = workspace.resolve()
    for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
        try:
            if not candidate.is_file() or candidate.stat().st_size > 1_000_000:
                continue
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        try:
            relative = str(candidate.resolve().relative_to(root))
        except ValueError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if matcher.search(line):
                lines.append(f"{relative}:{number}:{_truncate(line, 300)}")
                if len(lines) >= 200:
                    return _truncate("\n".join(lines) + "\n[TRUNCATED: 200 matches]", 16000)
    return _truncate("\n".join(lines) or "(no matches)", 16000)


def run_tool(name: str, args: Mapping[str, Any], workspace: pathlib.Path) -> str:
    if name == "list_directory":
        return _agent_list_directory(str(args.get("path", ".")), workspace)
    if name == "find_path":
        return _agent_find_path(
            str(args.get("pattern", "")), str(args.get("path", ".")), workspace
        )
    if name == "grep":
        return _agent_grep(
            str(args.get("regex", "")), str(args.get("path", ".")),
            str(args.get("include_pattern", "**/*")), workspace
        )
    if name == "read_file":
        target = _workspace_target(workspace, str(args["path"]))
        if target is None:
            return "ERROR: path traversal blocked"
        if not target.is_file():
            return "ERROR: not a file"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            start = int(args.get("start_line", 1) or 1)
            end = int(args.get("end_line", 0) or 0)
        except (OSError, TypeError, ValueError):
            return "ERROR: invalid file range"
        if start < 1 or (end and end < start):
            return "ERROR: invalid file range"
        lines = text.splitlines(keepends=True)
        selected = lines[start - 1:end or None]
        rendered = "".join(
            f"{number:6d}\t{line}" for number, line in enumerate(selected, start=start)
        )
        return _truncate(rendered, 20000)
    if name == "edit_file":
        target = _workspace_target(workspace, str(args["path"]))
        if target is None:
            return "ERROR: path traversal blocked"
        if not target.is_file():
            return "ERROR: not a file"
        edits = args.get("edits")
        if not isinstance(edits, list) or not edits or len(edits) > 50:
            return "ERROR: edits must contain 1-50 replacement objects"
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "ERROR: file could not be read as UTF-8"
        for edit in edits:
            if not isinstance(edit, Mapping):
                return "ERROR: each edit must be an object"
            old = str(edit.get("old_text", ""))
            new = str(edit.get("new_text", ""))
            if not old:
                return "ERROR: old_text must not be empty"
            if content.count(old) != 1:
                return "ERROR: old_text must match exactly once"
            content = content.replace(old, new, 1)
        if len(content) > MAX_INPUT:
            return f"ERROR: file content exceeds {MAX_INPUT:,} characters"
        target.write_text(content, encoding="utf-8")
        return f"edited {args['path']} with {len(edits)} replacement(s)"
    if name == "write_file":
        target = _workspace_target(workspace, str(args["path"]))
        if target is None:
            return "ERROR: path traversal blocked"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args["content"])
        if len(content) > MAX_INPUT:
            return f"ERROR: file content exceeds {MAX_INPUT:,} characters"
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {args['path']}"
    if name == "run_shell":
        return _run_agent_command(str(args["command"]), workspace.resolve())
    return f"unknown tool {name}"


# ── MCP Tools ───────────────────────────────────────────────────────────────

# `detail` exists only to keep the argument object non-empty. Zed parses a
# no-argument MCP call's arguments as JSON, and "" is not valid JSON, so
# zero-parameter tools surface as "Tool call not found" in its agent panel
# (zed-industries/zed#48955). An optional parameter sidesteps that without
# changing behaviour. Remove once that issue is fixed upstream.
@mcp.tool()
def list_workers(detail: bool = False) -> str:
    """List workers and pipelines. detail=True also shows each alias target."""
    if detail:
        lines = ["Workers:"] + [f"  {a} -> {t}" for a, t in WORKERS.items()]
    else:
        lines = ["Workers: " + ", ".join(WORKERS)]
    lines.append("Pipelines: " + ", ".join(PIPELINES))
    lines.append("Providers: " + ", ".join(PROVIDERS))
    return "\n".join(lines)


def _model_matches(model: str, alias: str) -> bool:
    """Compare aliases and raw provider/model specs without string heuristics."""
    try:
        return resolve(model) == resolve(alias)
    except (KeyError, TypeError, ValueError):
        return str(model).casefold() == str(alias).casefold()


def _agent_result_status(result: Any) -> tuple[str, str | None]:
    text = str(result or "").strip()
    if not text or "hit agent_max_steps=" in text:
        return "unusable_output", "agent_step_exhaustion"
    if text.startswith("ERROR:"):
        return "infrastructure_failure", "worker_error"
    if text.startswith("HOST_FALLBACK:"):
        return "infrastructure_failure", "worker_fallback"
    if text.startswith("SKIP_DELEGATION:"):
        return "host_skip", "host_skip"
    return "success", None


def _delegate_impl(task: str, model: str = "flash", agent: bool = False,
                   system: str = "", workspace: str = ".") -> str:
    """Run a worker after the public capability guard has accepted the call."""
    task = _cap_input(task)
    if not agent:
        return chat_with_failover(
            model, task, system=system, max_tokens=WORKER_MAX_TOKENS
        )

    provider, model_id = resolve(model)
    is_anthropic = PROVIDERS[provider].get("client") == "anthropic"
    ws = pathlib.Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    sys_prompt = (system or "You are a coding worker. Use the tools to complete the "
                  "task in the workspace, then reply with a short summary of what you did.")
                  
    deadline = time.monotonic() + CASCADE_DEADLINE

    if is_anthropic:
        # Translate OpenAI tools to Anthropic format
        anthropic_tools: list[dict[str, Any]] = [
            {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
            for t in AGENT_TOOLS
        ]
        msgs: list[dict[str, Any]] = [{"role": "user", "content": task}]
        
        for _ in range(MAX_STEPS):
            r = _create_with_retry(
                provider,
                model=model_id,
                max_tokens=AGENT_MAX_TOKENS,
                system=_cacheable_system(sys_prompt),
                messages=msgs,
                tools=anthropic_tools,
                deadline=deadline,
                budget_model=model,
                budget_input_tokens=max(
                    1, len(json.dumps(msgs, default=str)) // 4
                ),
                budget_output_tokens=AGENT_MAX_TOKENS,
            )
            _track(model, _anthropic_usage(r), record_legacy_budget=False)
            
            msgs.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "tool_use":
                return _truncate(
                    _anthropic_text(r.content) or "(done, no summary)"
                )
                
            tool_results = []
            for block in r.content:
                if block.type == "tool_use":
                    try:
                        _record_agent_tool_step(model, block.name)
                        out = run_tool(block.name, block.input, ws)
                    except Exception as e:
                        out = f"ERROR: {_safe_error(e)}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": out
                    })
            msgs.append({"role": "user", "content": tool_results})
        return f"(hit agent_max_steps={MAX_STEPS} without finishing)"

    # OpenAI loop
    msgs = [{"role": "system", "content": sys_prompt},
            {"role": "user", "content": task}]

    for _ in range(MAX_STEPS):
        r = _create_with_retry(
            provider,
            model=model_id,
            messages=msgs,
            tools=AGENT_TOOLS,
            max_tokens=AGENT_MAX_TOKENS,
            deadline=deadline,
            budget_model=model,
            budget_input_tokens=max(
                1, len(json.dumps(msgs, default=str)) // 4
            ),
            budget_output_tokens=AGENT_MAX_TOKENS,
        )
        _track(model, r.usage, record_legacy_budget=False)
        m = r.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        if not m.tool_calls:
            return _truncate(m.content or "(done, no summary)")
        for tc in m.tool_calls:
            try:
                _record_agent_tool_step(model, tc.function.name)
                out = run_tool(tc.function.name, json.loads(tc.function.arguments), ws)
            except Exception as e:  # feed errors back so the worker can recover
                out = f"ERROR: {_safe_error(e)}"
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return f"(hit agent_max_steps={MAX_STEPS} without finishing)"


@mcp.tool()
def delegate(task: str, model: str = "flash", agent: bool = False,
             system: str = "", workspace: str = ".",
             allow_capability_override: bool = False,
             allow_economic_override: bool = False) -> str:
    """Send a task to a worker with an auditable capability guard.

    Security work requires Sol. Workspace repository implementation with
    ``agent=True`` requires K3. Mechanical stateless calls remain compatible;
    an explicit override is available for deliberate, audited subparts only.
    """
    task = _cap_input(task)
    task_kind = _task_kind(task)
    security_task = _is_security(task)
    started = time.monotonic()
    override = bool(allow_capability_override)
    if security_task and not _model_matches(model, "sol"):
        if not override:
            message = "ERROR: security tasks require the Sol model"
            _track_orchestration(
                "delegate", "security", [str(model)], "blocked", "capability_floor",
                time.monotonic() - started, 0, len(message),
            )
            return message
        _record_capability_override("security", model)
    if task_kind == "repository":
        if not agent and not override:
            message = (
                "ERROR: repository tasks require agent=True with the K3 workspace "
                "route; use orchestrate_change"
            )
            _track_orchestration(
                "delegate", "repository-edit", [str(model)], "blocked", "capability_floor",
                time.monotonic() - started, 0, len(message),
            )
            return message
        if agent and not _model_matches(model, IMPLEMENTATION_MODEL):
            if not override:
                message = (
                    f"ERROR: repository agent tasks require {IMPLEMENTATION_MODEL}; "
                    "set allow_capability_override=true only for a deliberate subtask"
                )
                _track_orchestration(
                    "delegate", "repository-edit", [str(model)], "blocked", "capability_floor",
                    time.monotonic() - started, 0, len(message),
                )
                return message
            _record_capability_override(task_kind, model)
        elif not agent and override:
            _record_capability_override(task_kind, model)
    economic = _direct_model_economics(task, model, system=system, agent=agent)
    capability_required = (
        (task_kind == "repository" and agent and _model_matches(model, IMPLEMENTATION_MODEL))
        or (task_kind == "security" and _model_matches(model, "sol"))
    )
    if not economic["eligible"] and not capability_required:
        if not allow_economic_override:
            message = (
                "ERROR: direct model route fails the configured budget objective; "
                "request an economic override (allow_economic_override=true) for a deliberate audited call"
            )
            _track_orchestration(
                "delegate", "direct-model", [str(model)], "blocked",
                "economic_policy", time.monotonic() - started, 0, len(message),
            )
            return message
        _record_economic_override(model)
    operation = {"tool_steps": 0, "parent": _ORCHESTRATION_COLLECTOR.get()}
    operation_token = _ORCHESTRATION_COLLECTOR.set(operation)
    try:
        result = _delegate_impl(
            task, model=model, agent=agent, system=system, workspace=workspace
        )
    except Exception:
        _track_orchestration(
            "delegate", "repository-edit" if task_kind == "repository" else None,
            [str(model)], "infrastructure_failure", "worker_error",
            time.monotonic() - started, operation.get("tool_steps", 0), 0,
        )
        raise
    finally:
        _ORCHESTRATION_COLLECTOR.reset(operation_token)
    outcome, fallback = _agent_result_status(result) if agent else ("success", None)
    _track_orchestration(
        "delegate", "repository-edit" if task_kind == "repository" else None,
        [str(model)], outcome, fallback, time.monotonic() - started,
        operation.get("tool_steps", 0), len(str(result)),
    )
    return result


# Judges and later pipeline stages kept returning commentary ("The user wants me
# to act as an orchestrator...") instead of the artifact, which fails every
# execution check. The instruction used to live at the end of a long user
# message, where it loses to the model's urge to narrate. As a system prompt it
# holds. Measured: this is the difference between a stage returning code and
# returning a description of code.
ARTIFACT_ONLY = (
    "You output only the requested artifact. Never write a preamble, never "
    "restate or analyse the request, never explain what you are doing, and never "
    "describe the input you were given. If the artifact is code, emit exactly one "
    "code block and nothing outside it."
)

COMPACT_CHUNK = min(int(CONFIG.get("compact_chunk_chars", 24_000)), MAX_INPUT)
# Cap fan-out so compacting a huge transcript cannot open dozens of concurrent
# connections to one provider and trip its rate limit.
COMPACT_MAX_PARALLEL = int(CONFIG.get("compact_max_parallel", 4))
COMPACT_PROMPT = (
    "Summarize part {n}/{total} of a long working context so it can be resumed later. "
    "Preserve verbatim: key decisions, file paths, function/variable names, API names, "
    "error strings, and any open TODOs. Drop chatter. Be dense.\n\n{body}"
)


def _compact(text: str, model: str = "flash", target_chars: int | None = None,
             chunk_chars: int | None = None, _depth: int = 0) -> str:
    """Map-reduce summarizer that NEVER sends more than chunk_chars in one call, so
    it can shrink a 500K-token transcript without ever tripping the model's context
    window (which hard-truncation loses the middle of, and a raw call 502s on).
    Splits -> summarizes each chunk -> recombines -> recurses until it fits."""
    target = target_chars or MAX_INPUT
    chunk = chunk_chars or COMPACT_CHUNK
    if len(text) <= target:
        return text
    if _depth >= 5:                       # pathological input: stop recursing, chop
        return _truncate(text, target)
    parts = [text[i:i + chunk] for i in range(0, len(text), chunk)]
    # Chunks are independent, so summarize them concurrently. Serially this was
    # N round trips for an N-chunk transcript (a 500K-char history is ~21 calls),
    # and compaction runs precisely when context is already in trouble. Order is
    # restored by index because the summaries must stay in transcript order.
    summaries: list[str] = [""] * len(parts)
    with cf.ThreadPoolExecutor(max_workers=min(len(parts), COMPACT_MAX_PARALLEL)) as ex:
        futures = {
            _submit_with_context(
                ex,
                chat_with_failover,
                model,
                COMPACT_PROMPT.format(n=i + 1, total=len(parts), body=p),
                "",
                0.2,
                COMPACT_MAX_TOKENS,
            ): i
            for i, p in enumerate(parts)
        }
        for future in cf.as_completed(futures):
            index = futures[future]
            try:
                summaries[index] = future.result()
            except Exception as error:
                # Losing one chunk must not abort the whole compaction; keep a
                # marker so the gap is visible rather than silently dropped.
                summaries[index] = (
                    f"[chunk {index + 1} could not be summarized: {_safe_error(error)}]"
                )
    return _compact("\n\n".join(summaries), model, target, chunk, _depth + 1)


@mcp.tool()
def compact(text: str, model: str = "flash", target_chars: int = 0) -> str:
    """Shrink an over-long context/transcript so it fits a model's window WITHOUT
    502ing on 'input exceeds context window'. Map-reduce: split into safe chunks
    (each well under the limit), summarize each, recombine, repeat. Call this before
    feeding a huge history back to Sol instead of letting compaction die.
    model: summarizer worker (flash = cheap default; pass 'orchestrator-fallback' for Sol).
    target_chars: desired max output size (0 = max_input_chars from config)."""
    return _compact(text, model=model, target_chars=target_chars or None)


# ── Closed-loop verification ────────────────────────────────────────────────
# Verification is only meaningful against caller-supplied tests. A measured
# worker-written-test experiment reduced hidden-test correctness from 23/24 to
# 18/24 while using 5.5x the output tokens, so there is deliberately no fallback
# that lets the candidate author define its own success criteria.

VERIFY_MAX_ATTEMPTS = int(CONFIG.get("verify_max_attempts", 3))
VERIFY_TIMEOUT = int(CONFIG.get("verify_timeout_seconds", 30))

# Appended to the generated module: runs every test_* and reports machine-readably.
_VERIFY_RUNNER = '''
import sys as _sys, traceback as _tb
_tests = [(_k, _v) for _k, _v in sorted(globals().items())
          if _k.startswith("test_") and callable(_v)]
_failures = []
for _name, _fn in _tests:
    try:
        _fn()
    except Exception:
        _failures.append(_name + ": " + _tb.format_exc(limit=3).strip().splitlines()[-1])
print("__VERIFY_COUNT__", len(_tests))
for _f in _failures:
    print("__VERIFY_FAIL__", _f)
_sys.exit(1 if _failures or not _tests else 0)
'''


def _two_blocks(text: str) -> tuple[str, str]:
    """Split a response into (implementation, tests) code blocks."""
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if len(blocks) >= 2:
        return blocks[0], blocks[1]
    if len(blocks) == 1:
        return blocks[0], ""
    return text, ""


def _run_generated(impl: str, tests: str) -> tuple[bool, int, str]:
    """Execute impl+tests in a separate process. Returns (passed, n_tests, failures).

    SECURITY: this runs model-generated code on this machine. It is isolated to a
    temporary directory with a wall-clock timeout and a fresh interpreter, but it
    is NOT a sandbox -- the code can reach the filesystem and network like any
    local script. Same trust model as `agent_shell_mode`; do not point it at
    untrusted task descriptions.
    """
    if not tests.strip():
        return False, 0, "worker returned no tests"
    with tempfile.TemporaryDirectory(prefix="mo_verify_") as tmp:
        path = pathlib.Path(tmp) / "candidate.py"
        path.write_text(impl + "\n\n" + tests + "\n" + _VERIFY_RUNNER, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(path)], capture_output=True, text=True,
                timeout=VERIFY_TIMEOUT, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return False, 0, f"tests exceeded {VERIFY_TIMEOUT}s (likely an infinite loop)"
        out = (proc.stdout or "") + (proc.stderr or "")
        count = 0
        for line in out.splitlines():
            if line.startswith("__VERIFY_COUNT__"):
                count = int(line.split()[1])
        fails = [line.partition(" ")[2] for line in out.splitlines()
                 if line.startswith("__VERIFY_FAIL__")]
        if proc.returncode == 0 and count:
            return True, count, ""
        if not count and not fails:
            # Did not even import/run: surface the interpreter error itself.
            return False, 0, out.strip()[-800:] or "module failed to execute"
        return False, count, "\n".join(fails)[:1500]


def _verification_models(model: str, escalate: bool) -> list[str]:
    """Return an evidence-driven verification sequence without weakening floors."""
    models = [model]
    if not escalate or model == "sol":
        return models
    # Attempt one repair with the same cheap model before paying for a stronger
    # model. The sequence is indexed by verification attempt, including the
    # initial candidate check.
    models.append(model)
    stronger = str(COST_CONTROL.get("verification_escalation_model", "k27"))
    if stronger in WORKERS and stronger in STRONG_MODELS and stronger != model:
        models.append(stronger)
    return models


def _verify_with_tests(task: str, model: str, tests: str, attempts: int = 0,
                       initial_impl: str = "", escalate: bool = False) -> str:
    """Test a candidate and use real failures to request bounded repairs.

    Escalation is opt-in: the first candidate is tested locally, then a stronger
    configured model is used only after an actual failing result. Security model
    ``sol`` never escalates to a weaker model or fallback chain.
    """
    caller_tests = tests.strip()
    if not caller_tests:
        return "ERROR: verification requires caller-supplied Python test_* functions"
    if len(caller_tests) > MAX_INPUT // 2:
        return ("ERROR: verification tests exceed the safe prompt budget; "
                f"limit is {MAX_INPUT // 2:,} characters")

    limit = max(1, min(attempts or VERIFY_MAX_ATTEMPTS, 5))
    impl = initial_impl.strip()
    prompt = (
        f"{_cap_input(task)}\n\nYour implementation must satisfy these tests exactly. "
        "Return ONLY the implementation in one ```python block; do not restate "
        f"the tests.\n\n```python\n{_cap_input(caller_tests)}\n```"
    )
    last = "no attempt ran"
    best_impl = impl
    verification_models = _verification_models(model, escalate)
    previous_model: str | None = None
    for attempt in range(limit):
        current_model = verification_models[min(attempt, len(verification_models) - 1)]
        generated = not bool(impl)
        _record_event(
            "verification_attempt", attempt=attempt + 1,
            model=current_model, generated=generated,
        )
        if previous_model is not None and current_model != previous_model:
            _record_event(
                "verification_escalation", attempt=attempt + 1,
                from_model=previous_model, to_model=current_model,
            )
        previous_model = current_model
        if not impl:
            answer = chat_with_failover(
                current_model, prompt, ARTIFACT_ONLY,
                temperature=0.2, max_tokens=WORKER_MAX_TOKENS,
            )
            impl = _two_blocks(answer)[0]
        best_impl = impl
        ok, count, failures = _run_generated(impl, caller_tests)
        if ok:
            _record_event(
                "verification_pass", attempt=attempt + 1,
                model=current_model, tests=count,
            )
            return (f"VERIFIED after {attempt + 1} attempt(s): {count} "
                    f"caller-supplied test(s) passed.\n\n"
                    f"```python\n{impl.strip()}\n```")
        last = failures
        _record_event(
            "verification_failure", attempt=attempt + 1,
            model=current_model, tests=count,
        )
        prompt = (
            f"{_cap_input(task)}\n\nYour previous implementation FAILED the "
            f"caller-supplied tests:\n{failures or 'unknown failure'}\n\n"
            f"PREVIOUS:\n```python\n{impl.strip()}\n```\n\nFix it. The tests are "
            "correct and must not change. Return ONLY the corrected implementation."
            f"\n\n```python\n{_cap_input(caller_tests)}\n```"
        )
        impl = ""
    return (f"UNVERIFIED after {limit} attempt(s). Last failure:\n{last}\n\n"
            f"Best attempt (DO NOT TRUST WITHOUT REVIEW):\n"
            f"```python\n{best_impl.strip()}\n```")


@mcp.tool()
def delegate_verified(task: str, model: str = "flash", tests: str = "",
                      attempts: int = 0, escalate: bool = False) -> str:
    """Generate Python code and verify it against caller-supplied tests.

    `tests` must define plain-assert functions named `test_*`. Verification runs
    generated code locally in a temporary directory with a timeout, but not an OS
    sandbox. Never use this tool for untrusted code or test input.
    """
    capability_error = _capability_error(task, model, agent=False)
    if capability_error:
        return capability_error
    return _verify_with_tests(task, model, tests, attempts, escalate=escalate)


@mcp.tool()
def swarm(task: str, models: str = "flash,mimo,ds-pro",
          judge: str = "k26", system: str = "") -> str:
    """Parallel workers + judge with bounded fan-out and context."""
    workers = [m.strip() for m in models.split(",") if m.strip()]
    if not workers:
        return "ERROR: swarm requires at least one worker model"
    if len(workers) > SWARM_MAX_WORKERS:
        return (f"ERROR: swarm limited to {SWARM_MAX_WORKERS} workers; "
                f"received {len(workers)}")
    task = _cap_input(task)
    # Vary system prompts for diversity (mitigates correlated failures)
    diverse_systems = [
        (system or "") + "\nFocus on correctness and edge cases.",
        (system or "") + "\nFocus on clean, readable code.",
        (system or "") + "\nFocus on performance and efficiency.",
        (system or "") + "\nFocus on robustness and error handling.",
        (system or "") + "\nFocus on simplicity and minimalism.",
    ]
    temperatures = [0.15, 0.3, 0.45, 0.2, 0.35]

    results: dict[str, str] = {}
    with cf.ThreadPoolExecutor(max_workers=len(workers)) as ex:
        futs = {}
        for i, m in enumerate(workers):
            sys_p = diverse_systems[i % len(diverse_systems)]
            temp = temperatures[i % len(temperatures)]
            futs[_submit_with_context(
                ex, chat_with_failover, m, task, sys_p, temp, WORKER_MAX_TOKENS
            )] = m
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = _truncate(f.result(), SWARM_WORKER_RESPONSE)
            except Exception as e:
                results[m] = f"(worker error: {_safe_error(e)})"

    combined = _truncate(
        "\n\n".join(f"### Worker {m}\n{out}" for m, out in results.items()),
        SWARM_JUDGE_INPUT,
    )
    judge_prompt = (
        f"You are the orchestrator. {len(workers)} workers each attempted this task:\n\n"
        f"TASK:\n{task}\n\nWORKER ANSWERS:\n{combined}\n\n"
        "Produce the single best final answer. Fix mistakes, merge the strongest "
        "parts, and drop anything wrong. Output only the final answer."
    )
    try:
        return chat_with_failover(
            judge, judge_prompt, ARTIFACT_ONLY, temperature=0.1,
            max_tokens=JUDGE_MAX_TOKENS
        )
    except Exception as error:
        # The workers already succeeded and were already paid for. Losing all of
        # that because the judge failed is strictly worse than returning the
        # longest worker answer and saying the merge did not happen.
        usable = {m: o for m, o in results.items() if not o.startswith("(worker error:")}
        if not usable:
            raise
        best = max(usable, key=lambda m: len(usable[m]))
        return (f"[judge {judge!r} failed: {_safe_error(error)}. Returning the single best "
                f"worker answer from {best!r}, UNMERGED and unverified.]\n\n"
                f"{usable[best]}")


@mcp.tool()
def pipeline(task: str, mode: str = "draft-refine", agent: bool = False,
             system: str = "", workspace: str = ".", tests: str = "",
             attempts: int = 0, escalate: bool = False) -> str:
    """Run a configured recipe and optionally verify its final Python artifact.

    `tests` must be caller-supplied plain-assert `test_*` functions. When present,
    the pipeline result is executed first and a repair model is called only after
    a real test failure. Verification is unavailable in agent mode because agent
    output may be a workspace summary rather than a self-contained artifact.
    """
    pipe = PIPELINES.get(mode)
    if not pipe:
        available = ", ".join(f'"{k}"' for k in PIPELINES)
        return f"Unknown pipeline mode {mode!r}. Available: {available}"

    # Security work belongs on the Sol-only route before validating the requested
    # implementation mode, so an explicit repository route cannot bypass the floor.
    if ENFORCE_SECURITY_FLOOR and _is_security(task) and mode != "security":
        mode, pipe = "security", PIPELINES["security"]

    if tests.strip() and agent:
        return "ERROR: pipeline verification is unavailable with agent=True"
    if mode == "repository-edit" and not agent:
        return "ERROR: repository-edit requires agent=True"
    if agent and _task_kind(task) == "repository" and mode != "repository-edit":
        return (
            "ERROR: repository implementation requires the K3 repository-edit route; "
            "use orchestrate_change or auto_delegate"
        )

    def finish(result: str, repair_model: str):
        if not tests.strip():
            return result
        return _verify_with_tests(
            task, repair_model, tests, attempts,
            initial_impl=_two_blocks(result)[0], escalate=escalate,
        )

    # --- Swarm modes (workers + judge) ---
    if "workers" in pipe and "judge" in pipe:
        result = swarm(task=task,
                       models=",".join(pipe["workers"]),
                       judge=pipe["judge"],
                       system=system)
        return finish(result, pipe["judge"])

    # --- Single-model mode (speed-run) ---
    if "single" in pipe:
        if agent:
            return delegate(task=task, model=pipe["single"], agent=True,
                          system=system, workspace=workspace)
        result = chat_with_failover(
            pipe["single"], task,
            system or "Answer directly. Include only the requested artifact.",
            max_tokens=WORKER_MAX_TOKENS,
        )
        return finish(result, pipe["single"])

    # --- Multi-stage pipeline (test-factory, deep-plan, sec-deep) ---
    if "stages" in pipe:
        stages = pipe["stages"]
        # A recipe may carry its own per-stage prompts (role-based pipelines like
        # deep-plan / sec-deep). Fall back to the test-factory prompts otherwise.
        custom = pipe.get("stage_prompts")
        stage_prompts = custom or [
            "Generate a comprehensive test skeleton for this task. Include setup, "
            "basic test cases, and structure. Output ONLY the test code.\n\nTASK:\n",

            "Review this test code. Add edge cases: empty inputs, boundary values, "
            "error scenarios, unicode, overflow. Output the COMPLETE updated test code.\n\n"
            "CURRENT TESTS:\n",

            "Review these tests for coverage gaps. Fix any issues. Ensure all edge "
            "cases are covered. Output the FINAL complete test code.\n\nTESTS:\n",
        ]
        result = task
        for i, model in enumerate(stages):
            head = stage_prompts[i] if i < len(stage_prompts) else stage_prompts[-1]
            if custom:
                # Role-based stages keep the original task visible so later stages
                # (verify / codebase-safety) judge against it, not just prior output.
                prompt = (f"{head}\n\nORIGINAL TASK:\n{_truncate(task, 6000)}\n\n"
                          f"WORK SO FAR:\n{_truncate(result, STAGE_CONTEXT_CHARS)}")
            else:
                prompt = head + _truncate(result, STAGE_CONTEXT_CHARS)
            result = chat_with_failover(
                model, prompt, system or ARTIFACT_ONLY, temperature=0.2,
                max_tokens=JUDGE_MAX_TOKENS if custom else WORKER_MAX_TOKENS,
            )
        return finish(result, stages[-1])

    # --- Draft + Refine mode (default) ---
    drafter = pipe.get("drafter", "flash")
    refiner = pipe.get("refiner", "k27")

    if agent:
        # Agent mode: let the drafter do the agentic work, then refine the result
        draft = delegate(task=task, model=drafter, agent=True,
                        system=system, workspace=workspace)
        refine_prompt = (
            "Review this worker result against the task. Return only actionable "
            "issues, or `OK`.\n\n"
            f"TASK:\n{_truncate(task, 6000)}\n\n"
            f"RESULT:\n{_truncate(draft, 4000)}"
        )
        return chat_with_failover(
            refiner, refine_prompt, temperature=0.1, max_tokens=JUDGE_MAX_TOKENS
        )

    # Text mode: draft then refine
    draft_system = system or "Return only the requested implementation or artifact."
    draft = chat_with_failover(drafter, task, draft_system, temperature=0.3)

    refine_prompt = (
        "Correct the draft only where needed. Return only the final artifact.\n\n"
        f"TASK:\n{_truncate(task, 8000)}\n\n"
        f"DRAFT:\n{_truncate(draft, 8000)}"
    )
    result = chat_with_failover(
        refiner, refine_prompt, ARTIFACT_ONLY, temperature=0.1,
        max_tokens=JUDGE_MAX_TOKENS
    )
    return finish(result, refiner)


# -- Capability-aware task routing -------------------------------------------

ROUTE_PRIORITY = (
    "code-review", "test-factory", "debug", "reasoning", "draft-refine", "speed-run",
)

TASK_ROUTES = {
    "speed-run":    ["quick", "simple", "one-liner", "convert", "format",
                     "translate", "boilerplate", "template", "regex",
                     "email address", "hello", "print", "list"],
    "debug":        ["debug", "fix", "bug", "error", "broken", "crash",
                     "traceback", "exception", "failing", "wrong output"],
    "draft-refine": ["write", "code", "function", "implement", "class",
                     "method", "script", "program", "algorithm", "create",
                     "build", "make", "add", "feature"],
    "test-factory": ["test", "unit test", "unittest", "pytest", "spec",
                     "coverage", "assert", "mock"],
    "reasoning":    ["analyze", "explain", "why", "compare", "evaluate",
                     "design", "architect", "plan", "review", "audit",
                     "reason", "think"],
    "code-review":  ["review", "pr ", "pull request", "check", "security",
                     "vulnerability", "vulnerabilities"],
}

# A filename, repository word, or edit request means a stateless text worker has
# not been given enough information to safely perform the task. The repository-edit
# route is agent-aware and selects K3; callers do not need to know that detail.
_TINY_LOCAL_RE = re.compile(
    r"(?:\b(?:fix|correct)\s+(?:a\s+)?(?:typo|spelling|grammar)\b|"
    r"\b(?:format|formatting|whitespace|indentation)[- ]only\b|"
    r"\b(?:replace|change|update)\s+(?:one\s+)?(?:obvious\s+)?"
    r"(?:string|literal|value)\b.{0,80}\b(?:in|of)\s+[\w./\\-]+\.[\w-]+\b)",
    re.IGNORECASE | re.DOTALL,
)
_REPOSITORY_RE = re.compile(
    r"(?:\b(?:repository|repo|codebase|workspace|existing project|current project|"
    r"existing code)\b|"
    r"\b(?:read|inspect|open|look at|edit|modify|refactor|change|update|remove|delete)\b"
    r".{0,80}\b[\w./\\-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|"
    r"json|yaml|yml|toml|md|sql|css|scss|html|sh|bat|ini|cfg)\b|"
    r"\b(?:fix|debug|repair|implement|add|change|update|remove|refactor)\b.{0,80}"
    r"\b[\w./\\-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|"
    r"json|yaml|yml|toml|md|sql|css|scss|html|sh|bat|ini|cfg)\b|"
    r"\b(?:implement|build|add|develop|refactor)\b.{0,80}\b(?:feature|panel|component|"
    r"endpoint|module|service|integration|workflow|behavior|settings|screen|page|route)\b)",
    re.IGNORECASE | re.DOTALL,
)
_JUDGMENT_RE = re.compile(
    r"\b(?:architect(?:ure)?|design|analy[sz]e|explain|compare|evaluate|trade[- ]?offs?|"
    r"code review|review|pull request|pr review|plan|audit|recommend|strategy|why)\b",
    re.IGNORECASE,
)


def _keyword_route(task: str) -> str:
    task_lower = task.lower()
    scores = {route: 0 for route in TASK_ROUTES}
    for route, keywords in TASK_ROUTES.items():
        scores[route] = sum(keyword in task_lower for keyword in keywords)
    best = max(
        scores,
        key=lambda route: (scores[route], -ROUTE_PRIORITY.index(route)),
    )
    if scores[best] == 0:
        return "draft-refine" if len(task.split()) > 20 else "speed-run"
    return best


def _task_kind(task: str) -> str:
    """Classify capability before applying any price heuristic.

    A mixed review/edit request is repository work when it names an edit target;
    pure review and architecture requests remain host judgment.
    """
    if ENFORCE_SECURITY_FLOOR and _is_security(task):
        return "security"
    if _TINY_LOCAL_RE.search(task):
        return "local"
    repository_match = _REPOSITORY_RE.search(task)
    edit_intent = re.search(
        r"\b(?:fix|debug|repair|implement|add|change|update|remove|delete|"
        r"refactor|modify|edit|write|create|build|develop)\b",
        task,
        re.IGNORECASE,
    )
    target = re.search(r"\b[\w./\\-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|"
                       r"php|cs|cpp|c|h|json|yaml|yml|toml|md|sql|css|"
                       r"scss|html|sh|bat|ini|cfg)\b", task,
                       re.IGNORECASE)
    if repository_match and (edit_intent or target):
        return "repository"
    if _JUDGMENT_RE.search(task):
        return "judgment"
    if repository_match:
        return "repository"
    return "mechanical"


def _capability_error(task: str, model: str, agent: bool = False) -> str | None:
    """Return a public execution error when a requested route violates a floor."""
    kind = _task_kind(task)
    if kind == "security" and not _model_matches(model, "sol"):
        return "ERROR: security tasks require the Sol model"
    if kind == "repository":
        if not agent:
            return (
                "ERROR: repository tasks require agent=True with the K3 workspace "
                "route; use orchestrate_change"
            )
        if not _model_matches(model, IMPLEMENTATION_MODEL):
            return f"ERROR: repository agent tasks require {IMPLEMENTATION_MODEL}"
    return None


def _route_candidates(task: str, kind: str, agent: bool) -> list[str]:
    if kind == "security":
        return ["security"]
    if kind in {"local", "judgment"}:
        return []
    if kind == "repository":
        return ["repository-edit"]

    keyword = _keyword_route(task)
    # Preserve task semantics when the route is a specialist recipe. A test
    # factory must not silently become a generic code generator just because it
    # is cheaper. General codegen is the one capability family with a safe cheap
    # alternative: draft-refine -> speed-run.
    if keyword in {"debug", "test-factory"}:
        return [keyword]
    if keyword in {"draft-refine", "speed-run"}:
        return list(dict.fromkeys([keyword, "speed-run", "draft-refine"]))
    return [keyword]


def _estimate_output_tokens(mode: str) -> int:
    return max(1, ESTIMATED_OUTPUT_TOKENS_BY_ROUTE.get(mode, ESTIMATED_OUTPUT_TOKENS))


def _estimate_pipeline_cost(mode: str, task: str, system: str = "",
                            agent: bool = False, tests: str = "") -> dict[str, Any]:
    """Estimate recipe cost against direct generation by the configured host.

    This is a decision aid, not an invoice. It includes a small retry reserve so
    a route does not look profitable only because its first provider attempt is
    assumed to succeed forever.
    """
    pipe = PIPELINES[mode]
    prompt_tokens = max(
        1, int((len(task) + len(system)) / max(CHARS_PER_TOKEN, 0.1)) + 1
    )
    output_tokens = _estimate_output_tokens(mode)
    calls: list[tuple[str, int, int]] = []

    if "single" in pipe:
        steps = max(1, ESTIMATED_AGENT_STEPS) if agent else 1
        for step in range(steps):
            calls.append((
                pipe["single"], prompt_tokens + step * output_tokens, output_tokens
            ))
    elif "workers" in pipe and "judge" in pipe:
        for model in pipe["workers"]:
            calls.append((model, prompt_tokens, output_tokens))
        judge_input = prompt_tokens + output_tokens * len(pipe["workers"])
        calls.append((pipe["judge"], judge_input, output_tokens))
    elif "stages" in pipe:
        stage_input = prompt_tokens
        for model in pipe["stages"]:
            calls.append((model, stage_input, output_tokens))
            stage_input = prompt_tokens + output_tokens
    else:
        drafter = pipe.get("drafter", "flash")
        refiner = pipe.get("refiner", "k27")
        calls.extend([
            (drafter, prompt_tokens, output_tokens),
            (refiner, prompt_tokens + output_tokens, output_tokens),
        ])

    worker_cost = sum(
        _precise_usage_cost(model, inp, out) for model, inp, out in calls
    )
    # Agent workers return a compact summary to the host even though their model
    # internally spends several turns. This avoids charging the full internal
    # artifact as host re-ingestion in the route preview.
    returned_tokens = max(128, output_tokens // 4) if agent else output_tokens
    host_reingestion_cost = _precise_usage_cost(HOST_MODEL, returned_tokens, 0)
    direct_host_cost = _precise_usage_cost(
        HOST_MODEL, prompt_tokens, output_tokens
    )
    fallback_reserve_percent = float(COST_CONTROL.get("fallback_reserve_percent", 5.0))
    fallback_reserve = worker_cost * max(0.0, fallback_reserve_percent) / 100.0
    end_to_end_cost = worker_cost + fallback_reserve + host_reingestion_cost
    saving = direct_host_cost - end_to_end_cost
    saving_percent = saving / direct_host_cost * 100 if direct_host_cost else 0.0
    verification_reserve = 0.0
    if tests.strip():
        verification_reserve = worker_cost * float(
            COST_CONTROL.get("verification_retry_reserve_percent", 20.0)
        ) / 100.0
        end_to_end_cost += verification_reserve
        saving = direct_host_cost - end_to_end_cost
        saving_percent = saving / direct_host_cost * 100 if direct_host_cost else 0.0
    return {
        "mode": mode,
        "calls": calls,
        "models": _pipe_models(pipe),
        "prompt_tokens": prompt_tokens,
        "estimated_output_tokens": output_tokens,
        "maximum_output_tokens": (
            AGENT_MAX_TOKENS if agent else WORKER_MAX_TOKENS
        ),
        "returned_output_tokens": returned_tokens,
        "worker_cost": worker_cost,
        "fallback_reserve_cost": fallback_reserve,
        "verification_reserve_cost": verification_reserve,
        "host_reingestion_cost": host_reingestion_cost,
        "end_to_end_cost": end_to_end_cost,
        "direct_host_cost": direct_host_cost,
        "saving": saving,
        "saving_percent": saving_percent,
        "currency": BUDGET_CURRENCY,
    }


def _route_billing(mode: str) -> tuple[str, bool]:
    """Return a common billing mode and whether metered overage is possible."""
    groups = {
        group
        for model in _pipe_models(PIPELINES[mode])
        if (group := _provider_group(model)) is not None
    }
    settings = [BUDGET_BILLING_MODES.get(group, {}) for group in groups]
    modes = {str(item.get("mode", "unknown")) for item in settings}
    billing_mode = next(iter(modes)) if len(modes) == 1 else "mixed"
    overage = any(bool(item.get("metered_overage_enabled", False)) for item in settings)
    return billing_mode, overage


def _direct_model_economics(task: str, model: str, system: str = "",
                            agent: bool = False) -> dict[str, Any]:
    prompt_tokens = max(
        1, int((len(task) + len(system)) / max(CHARS_PER_TOKEN, 0.1)) + 1
    )
    output_tokens = AGENT_MAX_TOKENS if agent else WORKER_MAX_TOKENS
    worker_cost = _precise_usage_cost(model, prompt_tokens, output_tokens)
    returned_tokens = max(128, output_tokens // 4) if agent else output_tokens
    end_to_end = worker_cost + _precise_usage_cost(
        HOST_MODEL, returned_tokens, 0
    )
    direct = _precise_usage_cost(HOST_MODEL, prompt_tokens, output_tokens)
    saving_percent = (direct - end_to_end) / direct * 100 if direct else 0.0
    group = _provider_group(model)
    billing = BUDGET_BILLING_MODES.get(group or "", {})
    billing_mode = str(billing.get("mode", "unknown"))
    metered_overage = bool(billing.get("metered_overage_enabled", False))
    preserves_strong_pool = (
        ECONOMIC_OBJECTIVE == "preserve_strong_model"
        and billing_mode == "subscription"
        and not metered_overage
    )
    clears_savings = saving_percent >= MINIMUM_SAVING_PERCENT
    return {
        "eligible": preserves_strong_pool or clears_savings,
        "billing_mode": billing_mode,
        "metered_overage_enabled": metered_overage,
        "estimated_cost": end_to_end,
        "direct_host_cost": direct,
        "saving_percent": saving_percent,
    }


def _parse_cost_cap(value: Any) -> float:
    try:
        cap = float(value or 0.0)
    except (TypeError, ValueError) as error:
        raise ValueError("max_cost_idr must be a non-negative finite number") from error
    if not math.isfinite(cap) or cap < 0:
        raise ValueError("max_cost_idr must be a non-negative finite number")
    return cap


@dataclass(frozen=True)
class RouteDecision:
    schema_version: int
    task_kind: str
    capability: str
    keyword_route: str
    selected_route: str | None
    selected_models: tuple[str, ...]
    eligible_routes: tuple[str, ...]
    workspace_required: bool
    host_action_required: bool
    host_confirmation_required: bool
    host_action: str
    fallback_policy: str
    verification_plan: tuple[str, ...]
    inferred_agent: bool
    effective_agent: bool
    economics: Mapping[str, Any]
    reason: str
    capability_floor: str
    host_model: str
    max_cost_idr: float | None

    def as_dict(self) -> dict[str, Any]:
        economics = dict(self.economics)
        return {
            "schema_version": self.schema_version,
            "task_kind": self.task_kind,
            "capability": self.capability,
            "keyword_route": self.keyword_route,
            "selected_route": self.selected_route,
            "selected_models": list(self.selected_models),
            "eligible_routes": list(self.eligible_routes),
            "workspace_required": self.workspace_required,
            "host_action_required": self.host_action_required,
            "host_confirmation_required": self.host_confirmation_required,
            "host_action": self.host_action,
            "fallback_policy": self.fallback_policy,
            "verification_plan": list(self.verification_plan),
            "inferred_agent": self.inferred_agent,
            "economics": economics,
            "reason": self.reason,
            # Compatibility aliases retained for callers of schema version 1.
            "estimates": economics.get("estimates", {}),
            "estimated_cost": economics.get("estimated_cost"),
            "direct_host_cost": economics.get("direct_host_cost"),
            "saving_percent": economics.get("saving_percent"),
            "currency": economics.get("currency"),
            "host_model": self.host_model,
            "capability_floor": self.capability_floor,
            "requires_agent": self.workspace_required,
            "agent": self.effective_agent,
            "max_cost_idr": self.max_cost_idr,
            "skipped": self.selected_route is None,
        }


def _route_plan(task: str, agent: bool = False, system: str = "",
                tests: str = "", max_cost_idr: float = 0.0) -> dict[str, Any]:
    kind = _task_kind(task)
    keyword = _keyword_route(task)
    candidates = _route_candidates(task, kind, agent)
    effective_agent = kind == "repository" or bool(agent)
    estimates = {
        mode: _estimate_pipeline_cost(mode, task, system, effective_agent, tests)
        for mode in candidates
        if mode in PIPELINES
    }
    primary = estimates.get(keyword) or (next(iter(estimates.values()), None))
    cap = max_cost_idr if max_cost_idr > 0 else None
    selected: str | None = None
    reason = ""
    capability_floor = {
        "security": "strong-only",
        "repository": "workspace-agent-k3",
        "local": "host-local",
        "judgment": "host-judgment",
        "mechanical": "bounded-mechanical",
    }[kind]

    if kind == "security":
        selected = "security"
        reason = "Security capability floor: Sol is required and has no downgrade fallback."
        security_estimate = estimates.get("security", {})
        security_cost = security_estimate.get("end_to_end_cost")
        if cap is not None and (
            security_cost is None or float(security_cost) > cap
        ):
            selected = None
            reason = (
                f"Security route exceeds the explicit {cap:.3f} {BUDGET_CURRENCY} cap; "
                "do not substitute a weaker model."
            )
    elif kind == "judgment":
        reason = "Judgment-heavy work stays with the host; delegation would add an unchecked opinion."
    elif kind == "local":
        reason = "Genuinely tiny local edits stay with the host; delegation overhead is not justified."
    elif kind == "repository":
        repository_estimate = estimates.get("repository-edit", {})
        repository_cost = repository_estimate.get("end_to_end_cost")
        within_cap = cap is None or (
            repository_cost is not None and float(repository_cost) <= cap
        )
        if "repository-edit" in CAPABILITY_FIRST_ROUTES and within_cap:
            selected = "repository-edit"
            reason = (
                "Substantial repository implementation is capability-first: use the "
                f"workspace agent with {IMPLEMENTATION_MODEL}; the host reviews and verifies."
            )
        else:
            selected = None
            reason = (
                "Repository implementation is capability-eligible but blocked by the "
                "explicit cost cap; do not downgrade to a stateless worker."
            )
    elif not candidates:
        reason = "No capability-eligible delegation route is available."
    else:
        ordered_candidates = list(candidates)
        if ECONOMIC_OBJECTIVE == "preserve_strong_model":
            prepaid_candidates = [
                mode for mode in ordered_candidates
                if _route_billing(mode) == ("subscription", False)
            ]
            if len(prepaid_candidates) == len(ordered_candidates):
                configured_order = {
                    mode: index for index, mode in enumerate(ordered_candidates)
                }
                ordered_candidates.sort(
                    key=lambda mode: (
                        float(estimates.get(mode, {}).get("end_to_end_cost", math.inf)),
                        0 if "single" in PIPELINES[mode] else 1,
                        configured_order[mode],
                    )
                )
        for mode in ordered_candidates:
            estimate = estimates.get(mode, {})
            estimated_cost = estimate.get("end_to_end_cost")
            saving_percent = estimate.get("saving_percent")
            billing_mode, metered_overage = _route_billing(mode)
            # A cap is a hard upper bound. If a custom estimator cannot provide a
            # cost, fail closed under a cap; normal config estimates always include it.
            within_cap = cap is None or (
                estimated_cost is not None and float(estimated_cost) <= cap
            )
            preserves_strong_pool = (
                ECONOMIC_OBJECTIVE == "preserve_strong_model"
                and billing_mode == "subscription"
                and not metered_overage
            )
            clears_savings = (
                saving_percent is not None
                and float(saving_percent) >= MINIMUM_SAVING_PERCENT
            )
            if within_cap and (preserves_strong_pool or clears_savings):
                selected = mode
                if preserves_strong_pool:
                    reason = (
                        "Eligible prepaid route preserves strong-model capacity; "
                        "quota-equivalent usage is reported separately from cash."
                    )
                elif mode != candidates[0]:
                    reason = (
                        f"{candidates[0]!r} misses the saving floor; {mode!r} is the "
                        "cheapest capability-equivalent route that clears it."
                    )
                else:
                    reason = "Capability-eligible route clears the configured end-to-end saving floor."
                break
        if selected is None:
            reason = (
                "Every capability-eligible route misses the configured saving floor"
                + (f" or explicit {cap:.3f} {BUDGET_CURRENCY} cap." if cap is not None else ".")
            )

    chosen = estimates.get(selected) if selected else primary
    chosen_billing_mode = "unavailable"
    chosen_overage = False
    if selected:
        chosen_billing_mode, chosen_overage = _route_billing(selected)
    quota_equivalent = chosen.get("end_to_end_cost") if chosen else None
    direct_capacity = chosen.get("direct_host_cost") if chosen else None
    incremental_cash = (
        0.0
        if chosen_billing_mode == "subscription" and not chosen_overage
        else None
    )
    capabilities = {
        "security": "security-analysis",
        "repository": "repository-implementation",
        "local": "tiny-local-edit",
        "judgment": "host-judgment",
        "mechanical": "stateless-generation",
    }
    if kind == "repository":
        host_action = (
            "Inspect the changed-file manifest and diff, then run deterministic "
            "repository checks before accepting the K3 changes."
        )
        fallback_policy = "No retry or model downgrade; continue locally once after a recorded K3 failure."
        verification_plan = (
            "Inspect every changed path and the repository diff.",
            "Run checks for the changed surface, then broaden when risk requires it.",
            "Treat the worker summary as a handoff, not proof of correctness.",
        )
    elif kind in {"local", "judgment"}:
        host_action = "Handle this task directly on the host."
        fallback_policy = "Host-only route; no worker fallback is eligible."
        verification_plan = ("Apply normal host review and validation for the task.",)
    elif kind == "security":
        host_action = "Review the Sol result and validate security-sensitive claims before acceptance."
        fallback_policy = "Sol-only; never downgrade security work to another model."
        verification_plan = ("Perform host review and use task-specific security validation.",)
    else:
        host_action = "Review the returned artifact before using it."
        fallback_policy = "Use only capability-equivalent configured failover; otherwise return control to the host."
        verification_plan = ("Run caller-supplied tests or deterministic checks when behavior changes.",)

    decision = RouteDecision(
        schema_version=2,
        task_kind=kind,
        capability=capabilities[kind],
        keyword_route=keyword,
        selected_route=selected,
        selected_models=tuple(
            _pipe_models(PIPELINES[selected]) if selected else []
        ),
        eligible_routes=tuple(candidates),
        workspace_required=kind == "repository",
        host_action_required=True,
        host_confirmation_required=kind == "repository",
        host_action=host_action,
        fallback_policy=fallback_policy,
        verification_plan=verification_plan,
        inferred_agent=kind == "repository" and not bool(agent),
        effective_agent=effective_agent,
        economics={
            "estimates": estimates,
            "objective": ECONOMIC_OBJECTIVE,
            "billing_mode": chosen_billing_mode,
            "metered_overage_enabled": chosen_overage,
            "incremental_cash": incremental_cash,
            "quota_equivalent": quota_equivalent,
            "strong_model_capacity_preserved": direct_capacity,
            "estimated_cost": chosen.get("end_to_end_cost") if chosen else None,
            "direct_host_cost": chosen.get("direct_host_cost") if chosen else None,
            "saving_percent": chosen.get("saving_percent") if chosen else None,
            "currency": BUDGET_CURRENCY,
            "max_cost_idr": cap,
            "minimum_saving_percent": MINIMUM_SAVING_PERCENT,
        },
        reason=reason,
        capability_floor=capability_floor,
        host_model=HOST_MODEL,
        max_cost_idr=cap,
    )
    return decision.as_dict()


_ORCHESTRATION_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "node_modules",
    ".model-orchestra-artifacts", ".zed", ".obsidian",
})
_ORCHESTRATION_EXCLUDED_NAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials.json", "secrets.json", "id_rsa", "id_ed25519",
})
_ORCHESTRATION_MAX_FILE_BYTES = 2_000_000
_ORCHESTRATION_MAX_FILES = 4_000


def _workspace_snapshot(workspace: pathlib.Path) -> dict[str, Any]:
    """Hash bounded workspace metadata without reading contents into a response."""
    root = workspace.resolve()
    files: list[dict[str, Any]] = []
    skipped = 0
    if not root.exists() or not root.is_dir():
        return {"files": files, "skipped": 0}
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = pathlib.Path(current)
        directories[:] = sorted(
            name for name in directories
            if name not in _ORCHESTRATION_EXCLUDED_DIRS
            and not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            path = current_path / name
            if name in _ORCHESTRATION_EXCLUDED_NAMES or name.endswith((".pem", ".key")):
                skipped += 1
                continue
            if path.is_symlink():
                skipped += 1
                continue
            try:
                relative = path.relative_to(root).as_posix()
                size = path.stat().st_size
                if size > _ORCHESTRATION_MAX_FILE_BYTES:
                    skipped += 1
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                files.append({"path": relative, "sha256": digest.hexdigest(), "bytes": size})
                if len(files) >= _ORCHESTRATION_MAX_FILES:
                    skipped += 1
                    break
            except (OSError, ValueError):
                skipped += 1
        if len(files) >= _ORCHESTRATION_MAX_FILES:
            break
    files.sort(key=lambda item: item["path"])
    return {"files": files, "skipped": skipped}


def _workspace_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = {item["path"]: item for item in before.get("files", [])}
    new = {item["path"]: item for item in after.get("files", [])}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(old) | set(new)):
        previous = old.get(path)
        current = new.get(path)
        if previous == current:
            continue
        changes.append({
            "path": path,
            "change": "added" if previous is None else "deleted" if current is None else "modified",
            "before": previous,
            "after": current,
        })
    return changes


def _bounded_orchestration_json(payload: Mapping[str, Any]) -> str:
    """Keep orchestration handoffs valid JSON and below the MCP response cap."""
    candidate = dict(payload)
    files = list(candidate.get("changed_files", []))
    summary = str(candidate.get("worker_summary", ""))
    for limit in (1000, 250, 100, 25, 0):
        candidate["worker_summary"] = _truncate(summary, 1600 if limit >= 100 else 800)
        candidate["changed_files"] = files[:limit]
        if limit < len(files):
            candidate["changed_files_truncated"] = len(files) - limit
        else:
            candidate.pop("changed_files_truncated", None)
        rendered = json.dumps(candidate, indent=2, sort_keys=True)
        if len(rendered) <= MAX_RESPONSE:
            return rendered
    # The fields below are deliberately compact but preserve the handoff
    # contract, including a route decision and an explicitly truncated manifest.
    route_decision = candidate.get("route_decision", {})
    if isinstance(route_decision, Mapping):
        route_decision = dict(route_decision)
        route_decision.pop("estimates", None)
        economics = route_decision.get("economics")
        if isinstance(economics, Mapping):
            route_decision["economics"] = {
                key: value for key, value in economics.items()
                if key != "estimates"
            }
    minimal = {
        "schema_version": candidate.get("schema_version", 1),
        "route_decision": route_decision,
        "status": candidate.get("status"),
        "fallback_category": candidate.get("fallback_category"),
        "worker_summary": _truncate(summary, 400),
        "changed_files": files[:1],
        "changed_files_truncated": max(0, len(files) - 1),
        "elapsed_seconds": candidate.get("elapsed_seconds"),
        "tool_steps": candidate.get("tool_steps", 0),
        "host_verification_handoff": candidate.get("host_verification_handoff", {}),
    }
    rendered = json.dumps(minimal, separators=(",", ":"), sort_keys=True)
    if len(rendered) <= MAX_RESPONSE:
        return rendered
    # Route identifiers and host handoff text are still more useful than a
    # malformed response; retain the stable scalar decision fields if needed.
    compact_decision = {
        key: route_decision.get(key)
        for key in (
            "schema_version", "task_kind", "capability", "selected_route",
            "selected_models", "workspace_required", "host_action_required",
            "host_confirmation_required", "inferred_agent", "reason",
        )
        if key in route_decision
    }
    minimal["route_decision"] = compact_decision
    minimal["changed_files"] = []
    minimal["changed_files_truncated"] = len(files)
    minimal["worker_summary"] = _truncate(summary, 120)
    minimal["host_verification_handoff"] = {
        "required": True,
        "mcp_limitation": "MCP invocation remains advisory to the host.",
    }
    return json.dumps(minimal, separators=(",", ":"), sort_keys=True)


@mcp.tool()
def orchestrate_change(task: str, workspace: str = ".", system: str = "",
                       max_cost_idr: float = 0.0) -> str:
    """Run one substantial repository change through the K3 workspace agent.

    The response is a bounded metadata handoff. It contains hashes and sizes for
    changed files, never their contents. K3 is invoked at most once and is never
    silently replaced by a cheaper worker; the host owns diff review and tests.
    """
    started = time.monotonic()
    plan: dict[str, Any] = {}
    before: dict[str, Any] = {"files": [], "skipped": 0}
    after: dict[str, Any] = before
    result_text = ""
    status = "host_skip"
    fallback_category: str | None = "host_only"
    workspace_root: pathlib.Path | None = None
    operation = {"tool_steps": 0, "parent": _ORCHESTRATION_COLLECTOR.get()}
    try:
        cap = _parse_cost_cap(max_cost_idr)
        plan = _route_plan(task, agent=True, system=system, max_cost_idr=cap)
        task_kind = plan.get("task_kind")
        if task_kind != "repository":
            fallback_category = "capability_mismatch"
        elif plan.get("selected_route") is None:
            if cap > 0:
                status = "explicit_cost_cap"
                fallback_category = "explicit_cost_cap"
            else:
                fallback_category = "route_unavailable"
        elif plan.get("selected_route") != "repository-edit":
            fallback_category = "route_mismatch"
        elif plan.get("selected_models") != [IMPLEMENTATION_MODEL]:
            fallback_category = "model_mismatch"
        else:
            workspace_root = pathlib.Path(workspace).resolve()
            if workspace_root.exists() and not workspace_root.is_dir():
                raise NotADirectoryError(f"workspace is not a directory: {workspace}")
            workspace_root.mkdir(parents=True, exist_ok=True)
            before = _workspace_snapshot(workspace_root)
            operation_token = _ORCHESTRATION_COLLECTOR.set(operation)
            try:
                result = pipeline(
                    task=task, mode="repository-edit", agent=True,
                    system=system, workspace=str(workspace_root),
                )
            finally:
                _ORCHESTRATION_COLLECTOR.reset(operation_token)
            result_text = str(result or "")
            status, fallback_category = _agent_result_status(result_text)
            if status == "success":
                fallback_category = None
            after = _workspace_snapshot(workspace_root)
    except ValueError as error:
        status = "host_skip"
        fallback_category = "invalid_input"
        result_text = f"ERROR: {_safe_error(error)}"
    except Exception as error:
        status = "infrastructure_failure"
        fallback_category = "worker_error"
        result_text = f"ERROR: {_safe_error(error)}"
        if workspace_root is not None:
            try:
                after = _workspace_snapshot(workspace_root)
            except Exception:
                after = before

    changed_files = _workspace_diff(before, after)
    if status == "success" and not changed_files:
        status = "unusable_output"
        fallback_category = "no_workspace_changes"
    elapsed = round(time.monotonic() - started, 3)
    selected_models = [str(model) for model in plan.get("selected_models", [])]
    _track_orchestration(
        "orchestrate_change", plan.get("selected_route"), selected_models,
        status, fallback_category, elapsed, operation.get("tool_steps", 0),
        len(result_text),
    )
    decision = dict(plan) if plan else {
        "schema_version": 2,
        "task_kind": "unknown",
        "selected_route": None,
        "selected_models": [],
        "reason": "Route decision could not be evaluated.",
    }
    # Per-route estimate tables are available from route_preview. Keeping both
    # copies here can crowd the changed-file manifest out of the bounded MCP
    # response, so the handoff retains only the selected-route economics.
    decision["estimates"] = {}
    decision["economics"] = {
        key: value
        for key, value in dict(decision.get("economics", {})).items()
        if key != "estimates"
    }
    payload = {
        "schema_version": 1,
        "route_decision": decision,
        "status": status,
        "fallback_category": fallback_category,
        "worker_summary": _safe_error(result_text, 1600) if result_text else "",
        "changed_files": changed_files,
        "workspace_snapshot": {
            "before_skipped": int(before.get("skipped", 0)),
            "after_skipped": int(after.get("skipped", 0)),
            "max_file_bytes": _ORCHESTRATION_MAX_FILE_BYTES,
            "max_files": _ORCHESTRATION_MAX_FILES,
        },
        "elapsed_seconds": elapsed,
        "tool_steps": operation.get("tool_steps", 0),
        "host_verification_handoff": {
            "required": True,
            "action": decision.get(
                "host_action",
                "Inspect the changed-file manifest and diff before accepting changes.",
            ),
            "verification_plan": decision.get("verification_plan", []),
            "mcp_limitation": (
                "MCP can report this handoff but cannot force the host to invoke a "
                "tool or accept a change."
            ),
        },
    }
    return _bounded_orchestration_json(payload)


@mcp.tool()
def orchestration_report(detail: bool = False) -> str:
    """Return aggregate routing and agent telemetry without prompts or file contents."""
    report = _orchestration_snapshot(detail=detail)
    report["limitations"] = (
        "Metadata only: counts, routes, models, timing, tool steps, and estimated "
        "host re-ingestion. Prompts, file contents, credentials, and raw errors are "
        "not retained. MCP invocation remains advisory to the host."
    )
    events = list(report.get("events", []))
    for limit in (100, 50, 25, 10, 0):
        if "events" in report:
            report["events"] = events[-limit:] if limit else []
            report["events_truncated"] = max(0, len(events) - limit)
        rendered = json.dumps(report, indent=2, sort_keys=True)
        if len(rendered) <= MAX_RESPONSE:
            return rendered
    minimal = {
        "schema_version": report.get("schema_version", 1),
        "calls": report.get("calls", 0),
        "override_count": report.get("override_count", 0),
        "total_latency_seconds": report.get("total_latency_seconds", 0.0),
        "tool_steps": report.get("tool_steps", 0),
        "returned_chars": report.get("returned_chars", 0),
        "host_reingestion_cost_estimate": report.get(
            "host_reingestion_cost_estimate", 0.0
        ),
        "limitations": report["limitations"],
    }
    return json.dumps(minimal, separators=(",", ":"), sort_keys=True)


@mcp.tool()
def route_preview(task: str, agent: bool = False, system: str = "",
                  tests: str = "", max_cost_idr: float = 0.0) -> str:
    """Explain the capability-safe route and its configured cost estimate.

    This performs no model calls. Use it before an expensive or ambiguous
    delegation; auto_delegate uses the same planner and therefore cannot silently
    choose a cheaper route outside the task's capability class.
    """
    try:
        cap = _parse_cost_cap(max_cost_idr)
    except ValueError as error:
        return f"ERROR: {error}"
    return json.dumps(_route_plan(task, agent, system, tests, cap), indent=2, sort_keys=True)


@mcp.tool()
def auto_delegate(task: str, agent: bool = False, system: str = "",
                  workspace: str = ".", tests: str = "", attempts: int = 0,
                  max_cost_idr: float = 0.0, escalate: bool = False) -> str:
    """Auto-route using capability eligibility before end-to-end economics."""
    started = time.monotonic()
    try:
        cap = _parse_cost_cap(max_cost_idr)
    except ValueError as error:
        result = f"ERROR: {error}"
        _track_orchestration(
            "auto_delegate", None, [], "host_skip", "invalid_input",
            time.monotonic() - started, 0, len(result),
        )
        return result
    plan = _route_plan(task, agent, system, tests, cap)
    best = plan["selected_route"]
    models = [str(model) for model in plan.get("selected_models", [])]
    if best is None:
        estimate = plan.get("estimated_cost")
        direct = plan.get("direct_host_cost")
        economics = (
            f" Estimated {estimate:.3f} {BUDGET_CURRENCY} versus "
            f"{direct:.3f} {BUDGET_CURRENCY} direct "
            f"({plan.get('saving_percent', 0.0):.1f}% saving)."
            if estimate is not None and direct is not None else ""
        )
        result = f"SKIP_DELEGATION: {plan['reason']}{economics}"
        fallback = (
            "explicit_cost_cap"
            if cap > 0 and "explicit" in str(plan.get("reason", "")).casefold()
            else "host_skip"
        )
        _track_orchestration(
            "auto_delegate", None, models, "host_skip", fallback,
            time.monotonic() - started, 0, len(result),
        )
        return result

    effective_agent = bool(plan.get("agent", agent))
    operation = {"tool_steps": 0, "parent": _ORCHESTRATION_COLLECTOR.get()}
    operation_token = _ORCHESTRATION_COLLECTOR.set(operation)
    try:
        result = pipeline(task=task, mode=best, agent=effective_agent,
                          system=system, workspace=workspace, tests=tests,
                          attempts=attempts, escalate=escalate)
    except Exception as error:
        if plan.get("task_kind") == "repository" and effective_agent:
            result = (
                "HOST_FALLBACK: K3 delegation failed; continue locally once. "
                f"Diagnostic: {_safe_error(error)}"
            )
            _track_orchestration(
                "auto_delegate", best, models, "infrastructure_failure",
                "worker_error", time.monotonic() - started,
                operation.get("tool_steps", 0), len(result),
            )
            return result
        _track_orchestration(
            "auto_delegate", best, models, "infrastructure_failure",
            "worker_error", time.monotonic() - started,
            operation.get("tool_steps", 0), 0,
        )
        raise
    finally:
        _ORCHESTRATION_COLLECTOR.reset(operation_token)
    result_text = str(result).strip()
    if plan.get("task_kind") == "repository" and effective_agent:
        outcome, fallback = _agent_result_status(result_text)
        if outcome != "success":
            if outcome == "infrastructure_failure" and result_text.startswith("HOST_FALLBACK:"):
                fallback_result = result_text
            elif outcome == "host_skip" and result_text.startswith("SKIP_DELEGATION:"):
                fallback_result = result_text
            else:
                fallback_result = (
                    "HOST_FALLBACK: K3 delegation returned no usable result; "
                    "continue locally once."
                )
            _track_orchestration(
                "auto_delegate", best, models, outcome, fallback,
                time.monotonic() - started,
                operation.get("tool_steps", 0), len(fallback_result),
            )
            return fallback_result
    _track_orchestration(
        "auto_delegate", best, models, "success", None,
        time.monotonic() - started, operation.get("tool_steps", 0),
        len(result_text),
    )
    return result


@mcp.tool()
def batch_delegate(tasks_json: str, workspace: str = ".", out_dir: str = "",
                   inline: bool = False, overwrite: bool = False) -> str:
    """Run independent tasks concurrently and return a disk manifest by default.

    Input is a JSON list of {task, model?, mode?, agent?, tests?, attempts?}.
    Artifacts are written beneath `workspace`; set `inline=True` only when the host
    genuinely needs every result in context. `out_dir` selects a workspace-contained
    artifact directory. Existing explicit artifacts are never overwritten unless
    `overwrite=True`.
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Expected a JSON list of task objects."
    if not isinstance(inline, bool) or not isinstance(overwrite, bool):
        return "ERROR: inline and overwrite must be booleans"
    if len(tasks) > BATCH_MAX_TASKS:
        return (f"ERROR: batch limited to {BATCH_MAX_TASKS} tasks; "
                f"received {len(tasks)}")
    if not all(isinstance(task, dict) for task in tasks):
        return "Expected every batch item to be an object."
    workspace_root = pathlib.Path(workspace).resolve()
    task_workspaces: dict[int, str] = {}
    valid_modes = set(PIPELINES)
    for index, item in enumerate(tasks, start=1):
        if not isinstance(item.get("task", ""), str):
            return f"Invalid batch item {index}: task must be a string"
        for field in ("model", "mode", "system", "workspace"):
            if item.get(field) is not None and not isinstance(item.get(field), str):
                return f"Invalid batch item {index}: {field} must be a string"
        if item.get("model"):
            try:
                resolve(item["model"])
            except ValueError as error:
                return f"Invalid batch item {index}: {_safe_error(error)}"
            capability_error = _capability_error(
                item.get("task", ""), item["model"], bool(item.get("agent", False))
            )
            if capability_error:
                return f"Invalid batch item {index}: {capability_error[7:]}"
            group = _provider_group(item["model"])
            if (
                group in BUDGET_PROVIDER_LIMITS
                and _pricing_alias(item["model"]) not in BUDGET_PRICES
            ):
                return (
                    f"Invalid batch item {index}: unpriced model on budgeted "
                    f"provider group {group!r}"
                )
        if item.get("mode") and item["mode"] not in valid_modes:
            return f"Invalid batch item {index}: unknown mode {item['mode']!r}"
        for field in ("agent", "escalate"):
            if field in item and not isinstance(item[field], bool):
                return f"Invalid batch item {index}: {field} must be a boolean"
        item_workspace = item.get("workspace", ".")
        resolved_workspace = _workspace_target(workspace_root, item_workspace)
        if resolved_workspace is None:
            return f"Invalid batch item {index}: workspace must stay inside batch workspace"
        task_workspaces[index] = str(resolved_workspace)
        if item.get("mode") == "repository-edit" and not item.get("agent", False):
            return f"Invalid batch item {index}: repository-edit requires agent=true"
        if item.get("tests") and (
            item.get("agent", False)
            or (not item.get("mode") and not item.get("model")
                and _task_kind(item.get("task", "")) == "repository")
        ):
            return f"Invalid batch item {index}: verification is unavailable with agent=true"
        if item.get("tests") is not None and not isinstance(item.get("tests"), str):
            return f"Invalid batch item {index}: tests must be a string"
        try:
            attempts = int(item.get("attempts", 0))
        except (TypeError, ValueError):
            return f"Invalid batch item {index}: attempts must be an integer"
        if isinstance(item.get("attempts", 0), bool) or not 0 <= attempts <= 5:
            return f"Invalid batch item {index}: attempts must be between 0 and 5"
        if item.get("max_cost_idr") is not None:
            try:
                cap = float(item["max_cost_idr"])
            except (TypeError, ValueError):
                return f"Invalid batch item {index}: max_cost_idr must be non-negative"
            if not math.isfinite(cap) or cap < 0:
                return f"Invalid batch item {index}: max_cost_idr must be non-negative"

    target: pathlib.Path | None = None
    explicit_target = bool(out_dir)
    if not inline:
        workspace_root.mkdir(parents=True, exist_ok=True)
        if explicit_target:
            target = _workspace_target(workspace_root, out_dir)
        else:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = _workspace_target(
                workspace_root, f"{DEFAULT_BATCH_ARTIFACT_DIR}/batch-{stamp}"
            )
        if target is None:
            return "ERROR: batch out_dir must stay inside workspace"
        if target.exists() and not target.is_dir():
            return "ERROR: batch out_dir is not a directory"
        if explicit_target and target.exists() and not overwrite:
            language_suffixes = tuple({
                ".py", ".js", ".ts", ".json", ".yaml", ".toml", ".rs", ".go",
                ".sh", ".txt",
            })
            possible_names = {"manifest.json"}
            for index, item in enumerate(tasks, start=1):
                requested = re.search(
                    r"`([^`/\\]+\.[A-Za-z0-9]{1,8})`", item.get("task", "")
                )
                if requested:
                    bases = {pathlib.Path(requested.group(1)).name}
                else:
                    bases = {"task" + suffix for suffix in language_suffixes}
                possible_names.update(f"{index:02d}_{base}" for base in bases)
            collisions = sorted(
                name for name in possible_names if (target / name).exists()
            )
            if collisions:
                return (
                    "ERROR: explicit batch out_dir contains artifact(s) that would be "
                    "overwritten; pass overwrite=True: " + ", ".join(collisions[:8])
                )

        # Reserve the destination before model calls. Default paths are unique by
        # construction; explicit paths may be empty but are still checked above.
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            if not target.is_dir():
                return "ERROR: batch out_dir is not a directory"

    # Compute route metadata before any worker call. This makes the manifest a
    # record of the decision that was made, not a guess reconstructed afterward.
    plans: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(tasks, start=1):
        task_text = item.get("task", "")
        system = item.get("system", "") or ""
        tests = item.get("tests") or ""
        agent = bool(item.get("agent", False))
        cap = float(item.get("max_cost_idr") or 0.0)
        mode = item.get("mode")
        model = item.get("model")
        if mode:
            pipe = PIPELINES[mode]
            effective_mode = mode
            if ENFORCE_SECURITY_FLOOR and _is_security(task_text) and effective_mode != "security":
                effective_mode = "security"
                pipe = PIPELINES[effective_mode]
            estimate = _estimate_pipeline_cost(
                effective_mode, task_text, system, agent, tests
            )
            plan = {
                "task_kind": _task_kind(task_text),
                "requested_route": mode,
                "selected_route": effective_mode,
                "selected_models": _pipe_models(pipe),
                "eligible_routes": [effective_mode],
                "estimated_cost": estimate.get("end_to_end_cost"),
                "direct_host_cost": estimate.get("direct_host_cost"),
                "saving_percent": estimate.get("saving_percent"),
                "agent": agent,
                "reason": "Explicit pipeline requested.",
            }
        elif model:
            prompt_tokens = max(
                1, int((len(task_text) + len(system)) / max(CHARS_PER_TOKEN, 0.1)) + 1
            )
            output_tokens = _estimate_output_tokens("speed-run")
            worker_cost = _precise_usage_cost(model, prompt_tokens, output_tokens)
            direct_cost = _precise_usage_cost(HOST_MODEL, prompt_tokens, output_tokens)
            end_to_end = worker_cost + _precise_usage_cost(
                HOST_MODEL, output_tokens if not agent else max(128, output_tokens // 4), 0
            )
            plan = {
                "task_kind": _task_kind(task_text),
                "requested_route": "direct-model",
                "selected_route": "direct-model",
                "selected_models": [model],
                "eligible_routes": ["direct-model"],
                "estimated_cost": end_to_end,
                "direct_host_cost": direct_cost,
                "saving_percent": ((direct_cost - end_to_end) / direct_cost * 100
                                   if direct_cost else 0.0),
                "agent": agent,
                "reason": "Explicit model requested.",
            }
        else:
            plan = _route_plan(task_text, agent, system, tests, cap)
            plan = {**plan, "requested_route": "auto"}
        estimated_cost = plan.get("estimated_cost")
        if cap > 0 and (
            estimated_cost is None or float(estimated_cost) > cap
        ):
            return (
                f"Invalid batch item {index}: estimated cost "
                f"{estimated_cost if estimated_cost is not None else 'unavailable'} "
                f"exceeds explicit {cap:.6f} {BUDGET_CURRENCY} cap"
            )
        plans[index] = plan

    def run_item(index: int, item: dict[str, Any]) -> dict[str, Any]:
        collector = _empty_usage()
        events: list[dict[str, Any]] = []
        usage_token = _USAGE_COLLECTOR.set(collector)
        event_token = _EVENT_COLLECTOR.set(events)
        started = time.monotonic()
        task_text = item.get("task", "")
        mode = item.get("mode")
        model = item.get("model")
        system = item.get("system", "")
        agent = bool(plans[index].get("agent", item.get("agent", False)))
        tests = item.get("tests") or ""
        attempts = int(item.get("attempts", 0))
        task_workspace = task_workspaces[index]
        escalate = bool(item.get("escalate", False))
        try:
            if mode:
                result = pipeline(
                    task_text, mode=plans[index].get("selected_route", mode),
                    agent=agent, system=system, workspace=task_workspace,
                    tests=tests, attempts=attempts, escalate=escalate,
                )
            elif model and tests:
                if agent:
                    result = "ERROR: batch verification is unavailable with agent=true"
                elif escalate:
                    result = _verify_with_tests(
                        task_text, model, tests, attempts, escalate=True
                    )
                else:
                    result = _verify_with_tests(task_text, model, tests, attempts)
            elif model:
                result = delegate(
                    task_text, model=model, agent=agent, system=system,
                    workspace=task_workspace,
                )
            else:
                result = auto_delegate(
                    task_text, agent=agent, system=system, workspace=task_workspace,
                    tests=tests, attempts=attempts,
                    max_cost_idr=float(item.get("max_cost_idr") or 0),
                    escalate=escalate,
                )
            text = str(result)
            if text.startswith("ERROR:"):
                status = "error"
            elif text.startswith("HOST_FALLBACK:"):
                status = "fallback"
            elif text.startswith("SKIP_DELEGATION:"):
                status = "skipped"
            else:
                status = "success"
            error = _safe_error(text) if status != "success" else ""
            plan = plans[index]
            return {
                "index": index,
                "task": _truncate(task_text, 240),
                "status": status,
                "result": text,
                "error": error,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": collector,
                "events": events,
                "route": plan.get("selected_route"),
                "model": model,
                "agent": agent,
                "plan": plan,
            }
        except Exception as error:
            plan = plans[index]
            return {
                "index": index,
                "task": _truncate(task_text, 240),
                "status": "error",
                "result": f"ERROR: {_safe_error(error)}",
                "error": _safe_error(error),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": collector,
                "events": events,
                "route": plan.get("selected_route"),
                "model": model,
                "agent": agent,
                "plan": plan,
            }
        finally:
            _EVENT_COLLECTOR.reset(event_token)
            _USAGE_COLLECTOR.reset(usage_token)

    # Parallelism is the whole point of batching: independent tasks should cost
    # roughly one task's wall time, not N serial round trips. Context-local usage
    # collectors keep per-item accounting correct even when calls overlap.
    with cf.ThreadPoolExecutor(max_workers=min(len(tasks), BATCH_MAX_PARALLEL)) as ex:
        futures = {
            _submit_with_context(ex, run_item, index, item): index
            for index, item in enumerate(tasks, start=1)
        }
        indexed: dict[int, dict[str, Any]] = {}
        for future in cf.as_completed(futures):
            index = futures[future]
            try:
                indexed[index] = future.result()
            except Exception as error:
                indexed[index] = {
                    "index": index, "task": _truncate(tasks[index - 1].get("task", ""), 240),
                    "status": "error", "result": f"ERROR: {_safe_error(error)}",
                    "error": _safe_error(error), "elapsed_seconds": 0.0,
                    "usage": _empty_usage(), "route": plans[index].get("selected_route"),
                    "model": tasks[index - 1].get("model"),
                    "agent": bool(plans[index].get(
                        "agent", tasks[index - 1].get("agent", False)
                    )),
                    "plan": plans[index],
                }

    if inline:
        rendered = []
        for index in range(1, len(tasks) + 1):
            record = indexed[index]
            task_text = tasks[index - 1].get("task", "(no task)")[:60]
            result = _truncate(record["result"], BATCH_ITEM_RESPONSE)
            rendered.append(f"### Task {index}: {task_text}\n{result}")
        return _truncate("\n\n".join(rendered), BATCH_TOTAL_RESPONSE)

    assert target is not None and workspace_root is not None
    failures = 0
    manifest_items: list[dict[str, Any]] = []
    language_extensions = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "json": ".json", "yaml": ".yaml", "toml": ".toml",
        "rust": ".rs", "go": ".go", "shell": ".sh", "bash": ".sh",
    }
    for index in range(1, len(tasks) + 1):
        record = indexed[index]
        body = str(record["result"])
        blocks = re.findall(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", body, re.DOTALL)
        language, content = blocks[0] if blocks else ("", body)
        requested = re.search(
            r"`([^`/\\]+\.[A-Za-z0-9]{1,8})`", tasks[index - 1].get("task", "")
        )
        if requested:
            base = pathlib.Path(requested.group(1)).name
        else:
            extension = language_extensions.get(language.casefold(), ".txt")
            base = f"task{extension}"
        safe_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
        filename = f"{index:02d}_{safe_base or 'task.txt'}"
        item: dict[str, Any] = {
            "index": index,
            "task": record["task"],
            "status": record["status"],
            "route": record["route"],
            "requested_route": record["plan"].get("requested_route"),
            "task_kind": record["plan"].get("task_kind"),
            "selected_models": record["plan"].get("selected_models", []),
            "model": record["model"],
            "actual_models": sorted(record["usage"].get("by_model", {})),
            "agent": record["agent"],
            "elapsed_seconds": record["elapsed_seconds"],
            "usage": record["usage"],
            "events": record.get("events", []),
            "event_counts": {
                kind: sum(event.get("kind") == kind for event in record.get("events", []))
                for kind in sorted({
                    event.get("kind") for event in record.get("events", [])
                })
                if kind
            },
            "worker_cost_idr": round(_usage_total_cost(record["usage"]), 6),
            "estimated_cost_idr": record["plan"].get("estimated_cost"),
            "direct_host_cost_idr": record["plan"].get("direct_host_cost"),
            "estimated_saving_percent": record["plan"].get("saving_percent"),
            "returned_chars": len(body),
            "artifact": None,
            "error": record["error"],
        }
        if record["status"] == "success":
            artifact_path = _workspace_target(target, filename)
            if artifact_path is None:
                item["status"] = "error"
                item["error"] = "ERROR: artifact path escaped output directory"
                failures += 1
            else:
                data = (content.strip() + "\n").encode("utf-8")
                try:
                    if overwrite:
                        artifact_path.write_bytes(data)
                    else:
                        with artifact_path.open("xb") as handle:
                            handle.write(data)
                except FileExistsError:
                    item["status"] = "error"
                    item["error"] = (
                        "ERROR: artifact appeared during the batch; overwrite was disabled"
                    )
                    failures += 1
                    manifest_items.append(item)
                    continue
                except OSError as error:
                    item["status"] = "error"
                    item["error"] = f"ERROR: {_safe_error(error)}"
                    failures += 1
                    manifest_items.append(item)
                    continue
                item["artifact"] = {
                    "path": filename,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
        else:
            failures += 1
        manifest_items.append(item)

    manifest_path = _workspace_target(target, "manifest.json")
    if manifest_path is None:
        return "ERROR: manifest path escaped output directory"
    manifest_data = {
        "schema_version": 2,
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "workspace": str(workspace_root),
        "artifact_directory": str(target),
        "overwrite": bool(overwrite),
        "task_count": len(tasks),
        "success_count": len(tasks) - failures,
        "failure_count": failures,
        "items": manifest_items,
    }
    manifest_bytes = (json.dumps(manifest_data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        if overwrite:
            manifest_path.write_bytes(manifest_bytes)
        else:
            with manifest_path.open("xb") as handle:
                handle.write(manifest_bytes)
    except FileExistsError:
        return "ERROR: manifest appeared during the batch; overwrite was disabled"
    except OSError as error:
        return f"ERROR: could not write manifest ({_safe_error(error)})"
    lines = [
        f"Wrote {len(tasks) - failures}/{len(tasks)} artifacts to {target}"
        f"{f' ({failures} skipped/failed)' if failures else ''}.",
        f"Manifest: {manifest_path}",
    ]
    lines.extend(
        f"  {item['index']:02d} {item['status']:<7} "
        f"{item['artifact']['path'] if item['artifact'] else '-':<28} "
        f"{item['worker_cost_idr']:.3f} {BUDGET_CURRENCY}"
        for item in manifest_items
    )
    return "\n".join([*lines, "", "Content was NOT returned; read only the files needed."])


def _end_to_end_cost(usage: Mapping[str, Any],
                     returned_output_tokens: int | None = None) -> dict[str, float]:
    """Price measured worker usage plus Terra re-ingestion versus direct Terra."""
    inp = int(usage.get("total_input_tokens", 0))
    cached = int(usage.get("total_cached_tokens", 0))
    out = int(usage.get("total_output_tokens", 0))
    returned = out if returned_output_tokens is None else max(0, returned_output_tokens)
    worker_cost = _usage_total_cost(usage)
    host_reingestion = _precise_usage_cost(HOST_MODEL, returned, 0)
    written = int(usage.get("total_cache_write_tokens", 0))
    direct_host = _precise_usage_cost(HOST_MODEL, inp + cached + written, out)
    end_to_end = worker_cost + host_reingestion
    saving = direct_host - end_to_end
    return {
        "worker_cost": worker_cost,
        "host_reingestion_cost": host_reingestion,
        "end_to_end_cost": end_to_end,
        "direct_host_cost": direct_host,
        "saving": saving,
        "saving_percent": saving / direct_host * 100 if direct_host else 0.0,
    }


@mcp.tool()
def cost_report(detail: bool = False) -> str:
    """Session token usage and configured end-to-end cost estimates.

    `detail` is currently unused; it exists so the tool never has an empty
    argument object. See the note on list_workers (zed-industries/zed#48955).
    """
    u = _usage_snapshot()
    lines = [f"Worker token usage and configured {BUDGET_CURRENCY} estimates:"]
    if u["calls"] == 0:
        lines.append("Calls: 0 | Tokens: 0")
    else:
        lines.append(
            f"Calls: {u['calls']} | "
            f"Tokens: {u['total_input_tokens']:,} in + {u['total_output_tokens']:,} out"
        )
        if u.get("total_cached_tokens"):
            cached = u["total_cached_tokens"]
            written = u.get("total_cache_write_tokens", 0)
            hit_rate = cached / max(1, u["total_input_tokens"] + cached + written)
            lines.append(
                f"Prompt cache: {cached:,} input tokens served from cache "
                f"({hit_rate:.0%} of prompt volume, billed at cached_input rate); "
                f"{written:,} tokens written to cache"
            )
        costs = _end_to_end_cost(u)
        lines.extend([
            f"Measured worker estimate: {costs['worker_cost']:,.3f} {BUDGET_CURRENCY}",
            f"Host re-ingestion ceiling: {costs['host_reingestion_cost']:,.3f} "
            f"{BUDGET_CURRENCY} (conservatively assumes every worker output token "
            "was returned to the host)",
            f"End-to-end ceiling: {costs['end_to_end_cost']:,.3f} {BUDGET_CURRENCY}",
            f"Direct {HOST_MODEL} equivalent: {costs['direct_host_cost']:,.3f} "
            f"{BUDGET_CURRENCY}",
            f"Estimated net saving: {costs['saving']:,.3f} {BUDGET_CURRENCY} "
            f"({costs['saving_percent']:.1f}%)",
            "Configured estimates, not provider invoices; direct-host token counts "
            "are an equivalence assumption.",
        ])
    for model, s in sorted(u["by_model"].items(),
                            key=lambda x: x[1]["output"], reverse=True):
        cached = f"/{s.get('cached', 0):,}cached" if s.get("cached") else ""
        lines.append(
            f"  {model}: {s['calls']}x, {s['input']:,}in{cached}/{s['output']:,}out")
    if BUDGET_PROVIDER_LIMITS:
        entries = _load_budget_state()["entries"]
        now = dt.datetime.now(dt.timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        lines.append("Configured budget estimates:")
        for group, limits in sorted(BUDGET_PROVIDER_LIMITS.items()):
            spent = _spent_since(entries, group, month_start)
            lines.append(f"  {group}: {spent:,}/{int(limits['monthly']):,} {BUDGET_CURRENCY} this month")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
