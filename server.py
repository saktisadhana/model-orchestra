"""
model-orchestra: an MCP server that lets an orchestrator model delegate work
to tiny/cheap models on OpenRouter, NVIDIA, OpenCode Go, Groq, and SambaNova.

Tools exposed to the orchestrator:
  - list_workers()                          -> what workers exist
  - delegate(task, model, agent, ...)       -> run a single worker
  - compact(text, model, target_chars)      -> shrink oversized context safely
  - swarm(task, models, judge, ...)         -> parallel workers plus judge
  - pipeline(task, mode)                    -> composite recipe (draft+refine, swarm, etc.)
  - auto_delegate(task)                     -> auto-pick cheapest recipe
  - batch_delegate(tasks_json)              -> run independent tasks in parallel
  - cost_report()                           -> session token usage summary

`model` accepts either a worker alias from config.json ("flash", "k27", ...)
or a full "provider/model-id" string ("openrouter/meta-llama/llama-3.1-8b-instruct").

All providers speak the OpenAI chat-completions protocol, so one client
handles everything; only base_url + api_key change per provider.
"""

import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from dotenv import load_dotenv
from openai import OpenAI
from mcp.server.fastmcp import FastMCP

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")  # keys live in .env, never in .mcp.json
CONFIG = json.loads((ROOT / "config.json").read_text())
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
BUDGET = CONFIG.get("budget", {})
BUDGET_PRICES = BUDGET.get("pricing_per_million_tokens", {})
BUDGET_CURRENCY = BUDGET.get("currency", "IDR")
BUDGET_PROVIDER_LIMITS = BUDGET.get("provider_limits", {})
BUDGET_PROVIDER_GROUPS = BUDGET.get("provider_groups", {})
BUDGET_STATE_PATH = ROOT / BUDGET.get("state_file", ".model-orchestra-budget.json")
HOST_COMPARISON = BUDGET.get("host_comparison", {})
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
    "calls": 0,
    "by_model": {},
}
_STATE_LOCK = threading.RLock()


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


def _usage_cost(model: str, input_tokens: int, output_tokens: int,
                cached_tokens: int = 0, cache_write_tokens: int = 0) -> int:
    """Estimate IDR cost from configured per-million-token rates, rounded up.

    Cache-read input is billed at the far cheaper `cached_input` rate when the
    model declares one, so cached tokens are priced separately from fresh input.
    """
    price = BUDGET_PRICES.get(model)
    if not price:
        return 0
    # Anthropic-style usage reports cache reads and cache writes as SEPARATE
    # counters, so input_tokens already excludes both. Never subtract them.
    # Cache writes cost more than plain input; without a configured cache_write
    # rate, fall back to the input rate rather than pricing them at zero.
    cached = max(0, cached_tokens)
    written = max(0, cache_write_tokens)
    return int(
        (input_tokens * price["input"]
         + cached * price.get("cached_input", price["input"])
         + written * price.get("cache_write", price["input"])
         + output_tokens * price["output"] + 999_999)
        // 1_000_000
    )


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
            cost = int(entry["idr"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry.get("group") == group and created >= since:
            total += cost
    return total


def _budget_check(model: str, input_tokens: int, max_output_tokens: int) -> None:
    """Fail closed before a request exceeds its provider-specific time envelope."""
    group = _provider_group(model)
    if not group or group not in BUDGET_PROVIDER_LIMITS:
        return
    now = dt.datetime.now(dt.timezone.utc)
    worst_case = _usage_cost(model, input_tokens, max_output_tokens)
    limits = BUDGET_PROVIDER_LIMITS[group]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with _STATE_LOCK:
        entries = _load_budget_state()["entries"]
        windows = (
            ("monthly", int(limits.get("monthly", 0)), _spent_since(entries, group, month_start)),
            ("daily", int(limits.get("daily", 0)), _spent_since(entries, group, day_start)),
            ("5-hour", int(limits.get("five_hour", 0)), _spent_since(entries, group, now - dt.timedelta(hours=5))),
        )
        for name, limit, spent in windows:
            if limit and spent + worst_case > limit:
                raise RuntimeError(
                    f"Budget guard blocked {model}: estimated {worst_case:,} {BUDGET_CURRENCY} "
                    f"would exceed the {group} {name} limit ({spent:,}/{limit:,} {BUDGET_CURRENCY})."
                )


def _track(model: str, usage):
    """Record token usage and persist the configured-cost estimate."""
    if not usage:
        return
    inp = int(getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0)
    out = int(getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0)
    cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
    written = int(getattr(usage, "cache_write_tokens", 0) or 0)
    group = _provider_group(model)
    with _STATE_LOCK:
        SESSION_USAGE["total_input_tokens"] += inp
        SESSION_USAGE["total_output_tokens"] += out
        SESSION_USAGE["total_cached_tokens"] = (
            SESSION_USAGE.get("total_cached_tokens", 0) + cached)
        SESSION_USAGE["calls"] += 1
        # setdefault/get so a usage dict built before "cached" existed still works.
        entry = SESSION_USAGE["by_model"].setdefault(
            model, {"input": 0, "output": 0, "cached": 0, "calls": 0})
        SESSION_USAGE["total_cache_write_tokens"] = (
            SESSION_USAGE.get("total_cache_write_tokens", 0) + written)
        entry["input"] += inp
        entry["output"] += out
        entry["cached"] = entry.get("cached", 0) + cached
        entry["cache_write"] = entry.get("cache_write", 0) + written
        entry["calls"] += 1
        if group and model in BUDGET_PRICES:
            state = _load_budget_state()
            state["entries"].append({
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "group": group,
                "model": model,
                "input_tokens": inp,
                "output_tokens": out,
                "cached_tokens": cached,
                "cache_write_tokens": written,
                "idr": _usage_cost(model, inp, out, cached, written),
            })
            _save_budget_state(state)


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
# Security/CTF/forensics reasoning must never fall to cheap 8B workers. The
# routing tools (auto_delegate/pipeline) floor flagged tasks at strong models.
# Explicit delegate(model=...) is the deliberate escape hatch for mechanical
# subparts of a security task (e.g. "write a pcap parser") — left unguarded.
SECURITY_KEYWORDS = (
    "exploit", "vuln", "cve-", "cve ", "crypto", "cipher", "decrypt", "encrypt",
    "rce", "xss", "sqli", "sql injection", "payload", "forensic", "ctf", "flag{",
    # Bare "reversing" was removed: in a CODING tool it almost always means
    # reversing a string/list/word order, and it was routing trivial tasks to the
    # security floor -- measured in a 24-task batch, "reversing word order" went
    # to sol at ~6x the price and 4.4 tok/s instead of flash at 77 tok/s. Genuine
    # reverse-engineering is still caught by the explicit phrases plus
    # disassemble / shellcode / malware / obfuscat below.
    "reverse engineer", "reverse-engineer", "malware", "shellcode", "pwn", "stego",
    "privilege escalation", "privesc", "buffer overflow", " rop ", "deserializ",
    "pcap", "disassemble", "obfuscat", "hashcat",
)
STRONG_MODELS = set(TIERS.get("strong", []))
# Set enforce_security_floor:false in config.json to let cheap/Sol recipes handle
# security tasks too. Default true: a subtly-wrong exploit costs more than the tokens.
ENFORCE_SECURITY_FLOOR = CONFIG.get("enforce_security_floor", True)


# "cryptocurrency" is a payments/finance word, not a security one. Without this
# a task like "add a cryptocurrency payment button" trips the "crypto" keyword
# and gets routed to Sol at ~6x the price. Everything else stays substring-matched
# so the floor keeps failing safe (over-triggering costs money, under-triggering
# costs correctness).
_BENIGN_TERMS = ("cryptocurrencies", "cryptocurrency")


def _is_security(task: str) -> bool:
    t = task.lower()
    for benign in _BENIGN_TERMS:
        t = t.replace(benign, " ")
    return any(kw in t for kw in SECURITY_KEYWORDS)


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
    deadline: float | None = None, **kwargs: Any
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
                if is_anthropic:
                    return client.messages.create(**kwargs)
                return client.chat.completions.create(**kwargs)
            except Exception as e:
                last = e
                if _context_overflow(e):
                    raise               # input too long: no retry/rotate can fix
                if _is_transient(e):
                    pause = backoff * (2 ** i)
                    left = _remaining(deadline)
                    if left is not None and pause >= left:
                        raise           # backing off would blow the deadline
                    time.sleep(pause)
                    continue            # same key, back off and retry
                if _key_exhausted(e) and ki < len(keys) - 1:
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
    _budget_check(model, len(prompt) // 4, output_tokens)

    if is_anthropic:
        kwargs: dict[str, Any] = dict(
            model=model_id,
            max_tokens=output_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = _cacheable_system(system)
        r = _create_with_retry(provider, deadline=deadline, **kwargs)
        _track(model, _anthropic_usage(r))
        return _truncate(_anthropic_text(r.content))

    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    r = _create_with_retry(
        provider, model=model_id, messages=msgs, temperature=temperature,
        max_tokens=output_tokens, deadline=deadline,
    )
    _track(model, r.usage)
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
                       max_tokens: int | None = None) -> str:
    """Try the primary; on failure cascade through its tiered fallbacks."""
    budget = max_tokens or WORKER_MAX_TOKENS
    deadline = time.monotonic() + CASCADE_DEADLINE
    try:
        return chat(model, prompt, system, temperature, budget, deadline=deadline)
    except Exception as error:
        if _context_overflow(error):
            raise
        for fallback in _fallbacks_for(model):
            # Stop cascading once the wall clock is spent; trying a 5th model
            # with no time left just turns a failure into a much slower failure.
            if _remaining(deadline) <= 0:
                raise
            try:
                return chat(fallback, prompt, system, temperature, budget, deadline=deadline)
            except Exception as fallback_error:
                if _context_overflow(fallback_error):
                    raise
                continue
        # all failed, try primary one more time and let it raise
        return chat(model, prompt, system, temperature, budget, deadline=deadline)


# ── Agent-mode tools the worker can call ────────────────────────────────────

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file relative to the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
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


def run_tool(name: str, args: Mapping[str, Any], workspace: pathlib.Path) -> str:
    if name == "read_file":
        target = _workspace_target(workspace, str(args["path"]))
        if target is None:
            return "ERROR: path traversal blocked"
        return target.read_text(encoding="utf-8", errors="replace")[:20000]
    if name == "write_file":
        target = _workspace_target(workspace, str(args["path"]))
        if target is None:
            return "ERROR: path traversal blocked"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args["content"])
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


@mcp.tool()
def delegate(task: str, model: str = "flash", agent: bool = False,
             system: str = "", workspace: str = ".") -> str:
    """Send task to a worker. model: alias or provider/id. agent=True for tool loop."""
    provider, model_id = resolve(model)
    is_anthropic = PROVIDERS[provider].get("client") == "anthropic"
    task = _cap_input(task)
    _budget_check(model, len(task) // 4, AGENT_MAX_TOKENS if agent else WORKER_MAX_TOKENS)

    if not agent:
        if is_anthropic:
            kwargs: dict[str, Any] = dict(
                model=model_id,
                max_tokens=WORKER_MAX_TOKENS,
                messages=[{"role": "user", "content": task}],
            )
            if system:
                kwargs["system"] = _cacheable_system(system)
            r = _create_with_retry(provider, **kwargs)
            _track(model, _anthropic_usage(r))
            return _truncate(_anthropic_text(r.content) or "(empty response)")
        
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": task}]
        r = _create_with_retry(
            provider, model=model_id, messages=msgs, max_tokens=WORKER_MAX_TOKENS
        )
        _track(model, r.usage)
        return _truncate(r.choices[0].message.content or "(empty response)")

    ws = pathlib.Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    sys_prompt = (system or "You are a coding worker. Use the tools to complete the "
                  "task in the workspace, then reply with a short summary of what you did.")
                  
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
            )
            _track(model, _anthropic_usage(r))
            
            msgs.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "tool_use":
                return _truncate(
                    _anthropic_text(r.content) or "(done, no summary)"
                )
                
            tool_results = []
            for block in r.content:
                if block.type == "tool_use":
                    try:
                        out = run_tool(block.name, block.input, ws)
                    except Exception as e:
                        out = f"ERROR: {e}"
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
        )
        _track(model, r.usage)
        m = r.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        if not m.tool_calls:
            return _truncate(m.content or "(done, no summary)")
        for tc in m.tool_calls:
            try:
                out = run_tool(tc.function.name, json.loads(tc.function.arguments), ws)
            except Exception as e:  # feed errors back so the worker can recover
                out = f"ERROR: {e}"
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return f"(hit agent_max_steps={MAX_STEPS} without finishing)"


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
            ex.submit(
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
                summaries[index] = f"[chunk {index + 1} could not be summarized: {error}]"
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
# The gap between "plausible code" and "correct code" is the whole product. A
# 96% pass rate means 1 in 25 answers is silently wrong, which forces the HOST to
# verify -- burning host tokens and cancelling the saving that motivated
# delegating in the first place. Here the worker writes tests alongside the
# implementation, we execute them, and on failure the real traceback is fed back
# for a retry. The host sees only results that actually ran.

VERIFY_MAX_ATTEMPTS = int(CONFIG.get("verify_max_attempts", 3))
VERIFY_TIMEOUT = int(CONFIG.get("verify_timeout_seconds", 30))

VERIFY_PROMPT = """{task}

Return EXACTLY two Python code blocks and nothing else:

Block 1: the implementation only.
Block 2: tests for it. Use plain `assert` statements inside functions named
`test_*`. Cover edge cases: empty input, boundaries, and error conditions.
Do not import pytest. Do not call the test functions yourself.
"""

VERIFY_RETRY = """Your previous answer failed its own tests.

FAILURES:
{failures}

PREVIOUS IMPLEMENTATION:
```python
{impl}
```

Fix the implementation so every test passes. Return the same two code blocks
(implementation, then tests). If a test itself was wrong, correct it."""

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
        fails = [l.partition(" ")[2] for l in out.splitlines()
                 if l.startswith("__VERIFY_FAIL__")]
        if proc.returncode == 0 and count:
            return True, count, ""
        if not count and not fails:
            # Did not even import/run: surface the interpreter error itself.
            return False, 0, out.strip()[-800:] or "module failed to execute"
        return False, count, "\n".join(fails)[:1500]


@mcp.tool()
def delegate_verified(task: str, model: str = "flash", tests: str = "",
                      attempts: int = 0) -> str:
    """Generate code, RUN it against tests, and retry with the real failure output.

    `tests`: Python `test_*` functions using plain asserts. **Supply these.** They
    are the specification the worker cannot game.

    MEASURED WARNING -- if you omit `tests`, the worker writes its own, and that
    scored WORSE than a single plain call: 18/24 vs 23/24 correct, 5x the output
    tokens, 3.7x the wall time, and it once reported VERIFIED for code that failed
    independent checks. Self-written tests are not a correctness signal. Use this
    tool with your own tests, or use `delegate` / `pipeline` instead.
    """
    limit = max(1, min(attempts or VERIFY_MAX_ATTEMPTS, 5))
    caller_tests = tests.strip()
    if caller_tests:
        prompt = (f"{_cap_input(task)}\n\nYour implementation must satisfy these "
                  f"tests exactly. Return ONLY the implementation in one ```python "
                  f"block; do not restate the tests.\n\n```python\n{caller_tests}\n```")
    else:
        prompt = VERIFY_PROMPT.format(task=_cap_input(task))
    impl = generated_tests = ""
    last = "no attempt ran"
    for attempt in range(limit):
        answer = chat_with_failover(model, prompt, ARTIFACT_ONLY,
                                    temperature=0.2, max_tokens=WORKER_MAX_TOKENS)
        if caller_tests:
            impl = _two_blocks(answer)[0]
            generated_tests = caller_tests
        else:
            impl, generated_tests = _two_blocks(answer)
        ok, count, failures = _run_generated(impl, generated_tests)
        if ok:
            return (f"VERIFIED after {attempt + 1} attempt(s): {count} generated "
                    f"test(s) passed.\n\n```python\n{impl.strip()}\n```")
        last = failures
        if caller_tests:
            prompt = (f"{_cap_input(task)}\n\nYour previous implementation FAILED "
                      f"these tests:\n{failures}\n\nPREVIOUS:\n```python\n{impl.strip()}"
                      f"\n```\n\nFix it. The tests are correct and must not change. "
                      f"Return ONLY the corrected implementation.\n\n"
                      f"```python\n{caller_tests}\n```")
        else:
            prompt = VERIFY_RETRY.format(failures=failures or "unknown", impl=impl.strip())
    return (f"UNVERIFIED after {limit} attempt(s). Last failure:\n{last}\n\n"
            f"Best attempt (DO NOT TRUST WITHOUT REVIEW):\n```python\n{impl.strip()}\n```")


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
            futs[ex.submit(
                chat_with_failover, m, task, sys_p, temp, WORKER_MAX_TOKENS
            )] = m
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = _truncate(f.result(), SWARM_WORKER_RESPONSE)
            except Exception as e:
                results[m] = f"(worker error: {e})"

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
        return (f"[judge {judge!r} failed: {error}. Returning the single best "
                f"worker answer from {best!r}, UNMERGED and unverified.]\n\n"
                f"{usable[best]}")


@mcp.tool()
def pipeline(task: str, mode: str = "draft-refine", agent: bool = False,
             system: str = "", workspace: str = ".") -> str:
    """Run a configured draft/refine, staged, single-model, or swarm recipe."""
    pipe = PIPELINES.get(mode)
    if not pipe:
        available = ", ".join(f'"{k}"' for k in PIPELINES)
        return f"Unknown pipeline mode {mode!r}. Available: {available}"

    # CybSec floor: a security task must not touch any cheap worker. If the
    # chosen recipe includes a non-strong model, swap to the strong "security"
    # recipe (k26 -> glm). Costs up, never quality-down.
    if ENFORCE_SECURITY_FLOOR and _is_security(task) and any(
            m not in STRONG_MODELS for m in _pipe_models(pipe)):
        mode, pipe = "security", PIPELINES["security"]

    # --- Swarm modes (workers + judge) ---
    if "workers" in pipe and "judge" in pipe:
        return swarm(task=task,
                     models=",".join(pipe["workers"]),
                     judge=pipe["judge"],
                     system=system)

    # --- Single-model mode (speed-run) ---
    if "single" in pipe:
        if agent:
            return delegate(task=task, model=pipe["single"], agent=True,
                          system=system, workspace=workspace)
        return chat_with_failover(
            pipe["single"], task,
            system or "Answer directly. Include only the requested artifact.",
            max_tokens=WORKER_MAX_TOKENS,
        )

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
        return result

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
    return chat_with_failover(
        refiner, refine_prompt, ARTIFACT_ONLY, temperature=0.1,
        max_tokens=JUDGE_MAX_TOKENS
    )


# -- Task keyword routing for auto_delegate ---------------------------------

ROUTE_PRIORITY = ("code-review", "test-factory", "debug", "reasoning", "draft-refine", "speed-run")

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


@mcp.tool()
def auto_delegate(task: str, agent: bool = False, system: str = "",
                  workspace: str = ".") -> str:
    """Auto-route task to cheapest pipeline by keyword analysis."""
    task_lower = task.lower()

    scores = {route: 0 for route in TASK_ROUTES}
    for route, keywords in TASK_ROUTES.items():
        for kw in keywords:
            if kw in task_lower:
                scores[route] += 1

    best = max(
        scores,
        key=lambda route: (scores[route], -ROUTE_PRIORITY.index(route)),
    )
    best_score = scores[best]

    if best_score == 0:
        if len(task.split()) > 20:
            best = "draft-refine"
        else:
            best = "speed-run"

    # CybSec floor: never route a security/CTF/forensics task to a cheap recipe.
    if ENFORCE_SECURITY_FLOOR and _is_security(task):
        best = "security"

    return pipeline(task=task, mode=best, agent=agent,
                   system=system, workspace=workspace)


@mcp.tool()
def batch_delegate(tasks_json: str, workspace: str = ".", out_dir: str = "") -> str:
    """Run multiple tasks in ONE call. Input: JSON list of {task, model?, mode?, agent?}.

    out_dir: if set, write each artifact to a file there and return only a manifest
    instead of the code. Use this for large batches -- returned text is billed at
    HOST rates, so echoing 50 artifacts back cancels the saving that delegating
    them earned.

    Example: [{"task":"write hello.py","mode":"speed-run"},{"task":"write tests","mode":"test-factory"}]
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Expected a JSON list of task objects."
    if len(tasks) > BATCH_MAX_TASKS:
        return (f"ERROR: batch limited to {BATCH_MAX_TASKS} tasks; "
                f"received {len(tasks)}")
    if not all(isinstance(task, dict) for task in tasks):
        return "Expected every batch item to be an object."

    results = []
    # Parallelism is the whole point of batching: N independent tasks should cost
    # roughly one task's wall time, not N. A hardcoded 5 capped throughput no
    # matter how large the batch was.
    with cf.ThreadPoolExecutor(max_workers=min(len(tasks), BATCH_MAX_PARALLEL)) as ex:
        futs = {}
        for i, t in enumerate(tasks):
            task_text = t.get("task", "")
            mode = t.get("mode")
            model = t.get("model")
            system = t.get("system", "")
            agent = bool(t.get("agent", False))
            task_workspace = str(t.get("workspace", workspace))

            if mode:
                f = ex.submit(pipeline, task_text, mode, agent, system, task_workspace)
            elif model:
                f = ex.submit(delegate, task_text, model, agent, system, task_workspace)
            else:
                f = ex.submit(auto_delegate, task_text, agent, system, task_workspace)
            futs[f] = i

        indexed = {}
        for f in cf.as_completed(futs):
            idx = futs[f]
            try:
                indexed[idx] = f.result()
            except Exception as e:
                indexed[idx] = f"ERROR: {e}"

    if out_dir:
        # Manifest mode. Returning every artifact to the host defeats the point of
        # delegating: that text lands in the host's context and is billed at HOST
        # rates. Past a few dozen tasks it becomes the dominant cost. Writing to
        # disk and returning a summary keeps the return payload roughly constant
        # no matter how large the batch gets.
        target = pathlib.Path(out_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        manifest, failures = [], 0
        for i in range(len(tasks)):
            body = str(indexed.get(i, "(missing)"))
            blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", body, re.DOTALL)
            content = blocks[0] if blocks else body
            name = re.search(r"`(\w+)", tasks[i].get("task", ""))
            filename = f"{i+1:02d}_{name.group(1) if name else 'task'}.py"
            if body.startswith("ERROR:"):
                failures += 1
                manifest.append(f"  {filename:<28} FAILED - {body[:80]}")
                continue
            (target / filename).write_text(content.strip() + "\n", encoding="utf-8")
            manifest.append(f"  {filename:<28} {len(content):>6,} chars")
        head = (f"Wrote {len(tasks) - failures}/{len(tasks)} artifacts to {target}"
                f"{f' ({failures} failed)' if failures else ''}:")
        return "\n".join([head, *manifest,
                          "", "Content was NOT returned -- read the files you need."])

    for i in range(len(tasks)):
        task_text = tasks[i].get("task", "(no task)")[:60]
        result = _truncate(str(indexed.get(i, "(missing)")), BATCH_ITEM_RESPONSE)
        results.append(f"### Task {i+1}: {task_text}\n{result}")

    return _truncate("\n\n".join(results), BATCH_TOTAL_RESPONSE)


def _host_equivalent(input_tokens: int, output_tokens: int) -> str:
    """Estimate what this token volume would have cost on the host model.

    This is the number that makes delegation legible: workers are cheap, but
    without a reference point the saving is invisible. It is an EQUIVALENCE
    estimate at published list rates -- it assumes the host would have produced
    a comparable number of tokens, which is a proxy, not a measurement. It is
    not an invoice and not a claim about what was actually billed.
    """
    if not HOST_COMPARISON or not (input_tokens or output_tokens):
        return ""
    cost = (input_tokens * HOST_COMPARISON["input_per_million"]
            + output_tokens * HOST_COMPARISON["output_per_million"]) / 1_000_000
    return (f"Host-equivalent: ~{cost:,.2f} {HOST_COMPARISON['currency']} at "
            f"{HOST_COMPARISON['model']} list rates for the same token volume "
            f"(estimate, not an invoice -- assumes comparable token counts).")


@mcp.tool()
def cost_report(detail: bool = False) -> str:
    """Session token usage, plus an estimate of the host cost delegation avoided.

    `detail` is currently unused; it exists so the tool never has an empty
    argument object. See the note on list_workers (zed-industries/zed#48955).
    """
    u = SESSION_USAGE
    lines = ["Worker token usage (billed cost is not calculated):"]
    if u["calls"] == 0:
        lines.append("Calls: 0 | Tokens: 0")
    else:
        lines.append(
            f"Calls: {u['calls']} | "
            f"Tokens: {u['total_input_tokens']:,} in + {u['total_output_tokens']:,} out"
        )
        if u.get("total_cached_tokens"):
            cached = u["total_cached_tokens"]
            # Cache reads are counted separately from input_tokens, so total
            # prompt volume is the sum of the two.
            written = u.get("total_cache_write_tokens", 0)
            hit_rate = cached / max(1, u["total_input_tokens"] + cached + written)
            lines.append(
                f"Prompt cache: {cached:,} input tokens served from cache "
                f"({hit_rate:.0%} of prompt volume, billed at cached_input rate); "
                f"{written:,} tokens written to cache"
            )
        equivalent = _host_equivalent(
            u["total_input_tokens"] + u.get("total_cached_tokens", 0),
            u["total_output_tokens"])
        if equivalent:
            lines.append(equivalent)
    for model, s in sorted(u["by_model"].items(),
                            key=lambda x: x[1]["output"], reverse=True):
        cached = f"/{s['cached']:,}cached" if s.get("cached") else ""
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
