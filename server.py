"""
model-orchestra: an MCP server that lets a supervisor model (Opus 4.8 in Claude
Code) delegate work to tiny/cheap models on OpenRouter, NVIDIA, OpenCode Go,
Groq, and SambaNova.

Tools exposed to the supervisor:
  - list_workers()                          -> what workers exist
  - delegate(task, model, agent, ...)       -> run a single worker
  - pipeline(task, mode)                    -> composite recipe (draft+refine, swarm, etc.)
  - auto_delegate(task)                     -> auto-pick cheapest recipe
  - cost_report()                           -> session token/cost summary

`model` accepts either a worker alias from config.json ("flash", "k27", ...)
or a full "provider/model-id" string ("openrouter/meta-llama/llama-3.1-8b-instruct").

All providers speak the OpenAI chat-completions protocol, so one client
handles everything; only base_url + api_key change per provider.
"""

import concurrent.futures as cf
import json
import os
import pathlib
import subprocess
import time

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
TIERS = CONFIG.get("tiers", {})
MAX_STEPS = CONFIG.get("agent_max_steps", 15)
MAX_RESPONSE = CONFIG.get("max_response_chars", 12000)  # truncate worker output

mcp = FastMCP("model-orchestra")

# ── Session cost tracker ────────────────────────────────────────────────────

SESSION_USAGE = {
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "calls": 0,
    "by_model": {},
}


def _track(model: str, usage):
    """Record token usage from an API response."""
    if not usage:
        return
    inp = getattr(usage, "prompt_tokens", 0) or 0
    out = getattr(usage, "completion_tokens", 0) or 0
    SESSION_USAGE["total_input_tokens"] += inp
    SESSION_USAGE["total_output_tokens"] += out
    SESSION_USAGE["calls"] += 1
    if model not in SESSION_USAGE["by_model"]:
        SESSION_USAGE["by_model"][model] = {"input": 0, "output": 0, "calls": 0}
    SESSION_USAGE["by_model"][model]["input"] += inp
    SESSION_USAGE["by_model"][model]["output"] += out
    SESSION_USAGE["by_model"][model]["calls"] += 1


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


def client_for(provider: str):
    p = PROVIDERS[provider]
    key = os.environ.get(p["api_key_env"])
    if not key:
        raise RuntimeError(f"Missing env var {p['api_key_env']} for provider {provider!r}.")
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
    "reverse engineer", "reversing", "malware", "shellcode", "pwn", "stego",
    "privilege escalation", "privesc", "buffer overflow", " rop ", "deserializ",
    "pcap", "disassemble", "obfuscat", "hashcat",
)
STRONG_MODELS = {"k27", "k26", "glm", "glm51", "k3", "grok"}


def _is_security(task: str) -> bool:
    t = task.lower()
    return any(kw in t for kw in SECURITY_KEYWORDS)


def _pipe_models(pipe: dict) -> list[str]:
    """Every worker alias a pipeline recipe would touch."""
    m = list(pipe.get("workers", [])) + list(pipe.get("stages", []))
    for k in ("single", "drafter", "refiner", "judge"):
        if k in pipe:
            m.append(pipe[k])
    return m


def _truncate(text: str, limit: int = 0) -> str:
    """Truncate worker output to save Opus context tokens."""
    cap = limit or MAX_RESPONSE
    if len(text) <= cap:
        return text
    half = cap // 2 - 50
    return (text[:half]
            + f"\n\n... [TRUNCATED {len(text) - cap:,} chars to save tokens] ...\n\n"
            + text[-half:])


REQUEST_TIMEOUT = 60  # seconds; no create() call may hang forever


def _is_transient(e: Exception) -> bool:
    """OpenCode's relay masks upstream outages as a 400 'Upstream request
    failed' / 'Console Go' error. Those are retryable; a real 400 is not."""
    m = str(e).lower()
    return ("upstream request failed" in m or "console go" in m
            or "overloaded" in m or "timeout" in m or "timed out" in m
            or " 500" in m or " 502" in m or " 503" in m or " 529" in m)


def _create_with_retry(client, *, retries: int = 3, backoff: float = 0.8, **kwargs):
    """create() with backoff on transient upstream errors only."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    is_anthropic = hasattr(client, "messages")
    last = None
    for i in range(retries):
        try:
            if is_anthropic:
                return client.messages.create(**kwargs)
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            last = e
            if not _is_transient(e):
                raise
            time.sleep(backoff * (2 ** i))
    raise last


def chat(model: str, prompt: str, system: str = "", temperature: float = 0.2) -> str:
    """One-shot text completion against a worker. Shared by delegate + swarm."""
    provider, model_id = resolve(model)
    client = client_for(provider)
    is_anthropic = hasattr(client, "messages")

    if is_anthropic:
        kwargs = dict(model=model_id, max_tokens=4096, temperature=temperature,
                      messages=[{"role": "user", "content": prompt}])
        if system:
            kwargs["system"] = system
        r = _create_with_retry(client, **kwargs)
        
        class FakeUsage:
            prompt_tokens = r.usage.input_tokens
            completion_tokens = r.usage.output_tokens
        _track(model, FakeUsage())
        return _truncate(r.content[0].text or "")

    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    r = _create_with_retry(client, model=model_id, messages=msgs, temperature=temperature)
    _track(model, r.usage)
    return _truncate(r.choices[0].message.content or "")


def _fallbacks_for(model: str) -> list[str]:
    """Per-model failover order: same-tier siblings first (a strong model falls
    to another STRONG model, not straight to a cheap 70B), then the global
    safety-net chain. Every model gets a real fallback list."""
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
                       temperature: float = 0.2) -> str:
    """Try the primary; on failure cascade through its tiered fallbacks."""
    try:
        return chat(model, prompt, system, temperature)
    except Exception:
        for fallback in _fallbacks_for(model):
            try:
                return chat(fallback, prompt, system, temperature)
            except Exception:
                continue
        # all failed, try primary one more time and let it raise
        return chat(model, prompt, system, temperature)


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
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command in the workspace and return combined stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}},
]


def run_tool(name: str, args: dict, workspace: pathlib.Path) -> str:
    if name == "read_file":
        target = (workspace / args["path"]).resolve()
        if not str(target).startswith(str(workspace)):
            return "ERROR: path traversal blocked"
        return target.read_text(encoding="utf-8", errors="replace")[:20000]
    if name == "write_file":
        target = (workspace / args["path"]).resolve()
        if not str(target).startswith(str(workspace)):
            return "ERROR: path traversal blocked"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        return f"wrote {len(args['content'])} chars to {args['path']}"
    if name == "run_shell":
        r = subprocess.run(args["command"], shell=True, cwd=workspace,
                           capture_output=True, text=True, timeout=120)
        return (r.stdout + r.stderr)[:20000] or "(no output)"
    return f"unknown tool {name}"


# ── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def list_workers() -> str:
    """List workers and pipelines."""
    lines = ["Workers: " + ", ".join(WORKERS)]
    lines.append("Pipelines: " + ", ".join(PIPELINES))
    lines.append("Providers: " + ", ".join(PROVIDERS))
    return "\n".join(lines)


@mcp.tool()
def delegate(task: str, model: str = "flash", agent: bool = False,
             system: str = "", workspace: str = ".") -> str:
    """Send task to a worker. model: alias or provider/id. agent=True for tool loop."""
    provider, model_id = resolve(model)
    cli = client_for(provider)
    is_anthropic = hasattr(cli, "messages")

    if not agent:
        if is_anthropic:
            kwargs = dict(model=model_id, max_tokens=4096, messages=[{"role": "user", "content": task}])
            if system:
                kwargs["system"] = system
            r = _create_with_retry(cli, **kwargs)
            _track(model, type("Usage", (), {"prompt_tokens": r.usage.input_tokens, "completion_tokens": r.usage.output_tokens})())
            return _truncate(r.content[0].text or "(empty response)")
        
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": task}]
        r = _create_with_retry(cli, model=model_id, messages=msgs)
        _track(model, r.usage)
        return _truncate(r.choices[0].message.content or "(empty response)")

    ws = pathlib.Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    sys_prompt = (system or "You are a coding worker. Use the tools to complete the "
                  "task in the workspace, then reply with a short summary of what you did.")
                  
    if is_anthropic:
        # Translate OpenAI tools to Anthropic format
        anthropic_tools = [
            {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
            for t in AGENT_TOOLS
        ]
        msgs = [{"role": "user", "content": task}]
        
        for _ in range(MAX_STEPS):
            r = _create_with_retry(cli, model=model_id, max_tokens=4096, system=sys_prompt, messages=msgs, tools=anthropic_tools)
            _track(model, type("Usage", (), {"prompt_tokens": r.usage.input_tokens, "completion_tokens": r.usage.output_tokens})())
            
            msgs.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "tool_use":
                text_blocks = [b.text for b in r.content if b.type == "text"]
                return _truncate("\n".join(text_blocks) or "(done, no summary)")
                
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
        r = _create_with_retry(cli, model=model_id, messages=msgs, tools=AGENT_TOOLS)
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


@mcp.tool()
def swarm(task: str, models: str = "flash,mimo,ds-pro",
          judge: str = "k26", system: str = "") -> str:
    """Parallel workers + judge. models: comma-sep aliases. Best for hard problems."""
    workers = [m.strip() for m in models.split(",") if m.strip()]
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
            futs[ex.submit(chat_with_failover, m, task, sys_p, temp)] = m
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = f.result()
            except Exception as e:
                results[m] = f"(worker error: {e})"

    combined = "\n\n".join(f"### Worker {m}\n{out}" for m, out in results.items())
    judge_prompt = (
        f"You are the supervisor. {len(workers)} workers each attempted this task:\n\n"
        f"TASK:\n{task}\n\nWORKER ANSWERS:\n{combined}\n\n"
        "Produce the single best final answer. Fix mistakes, merge the strongest "
        "parts, and drop anything wrong. Output only the final answer."
    )
    return chat_with_failover(judge, judge_prompt, temperature=0.1)


@mcp.tool()
def pipeline(task: str, mode: str = "draft-refine", agent: bool = False,
             system: str = "", workspace: str = ".") -> str:
    """Composite recipe. mode: draft-refine|debug|swarm-budget|test-factory|reasoning|code-review|heavy-swarm|speed-run"""
    pipe = PIPELINES.get(mode)
    if not pipe:
        available = ", ".join(f'"{k}"' for k in PIPELINES)
        return f"Unknown pipeline mode {mode!r}. Available: {available}"

    # CybSec floor: a security task must not touch any cheap worker. If the
    # chosen recipe includes a non-strong model, swap to the strong "security"
    # recipe (k27 -> glm). Costs up, never quality-down.
    if _is_security(task) and any(m not in STRONG_MODELS for m in _pipe_models(pipe)):
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
        return chat_with_failover(pipe["single"], task,
                                  system or "Be concise. Answer directly.")

    # --- Multi-stage pipeline (test-factory) ---
    if "stages" in pipe:
        stages = pipe["stages"]
        result = task
        stage_prompts = [
            "Generate a comprehensive test skeleton for this task. Include setup, "
            "basic test cases, and structure. Output ONLY the test code.\n\nTASK:\n",

            "Review this test code. Add edge cases: empty inputs, boundary values, "
            "error scenarios, unicode, overflow. Output the COMPLETE updated test code.\n\n"
            "CURRENT TESTS:\n",

            "Review these tests for coverage gaps. Fix any issues. Ensure all edge "
            "cases are covered. Output the FINAL complete test code.\n\nTESTS:\n",
        ]
        for i, model in enumerate(stages):
            prompt = stage_prompts[i] + result
            result = chat_with_failover(model, prompt, system, temperature=0.2)
        return result

    # --- Draft + Refine mode (default) ---
    drafter = pipe.get("drafter", "flash")
    refiner = pipe.get("refiner", "k27")

    if agent:
        # Agent mode: let the drafter do the agentic work, then refine the result
        draft = delegate(task=task, model=drafter, agent=True,
                        system=system, workspace=workspace)
        refine_prompt = (
            f"A worker completed this task and reported:\n\n"
            f"TASK:\n{task}\n\nWORKER REPORT:\n{draft}\n\n"
            f"Review the worker's output. If there are issues, point them out. "
            f"If the work looks correct, confirm it. Be brief."
        )
        return chat_with_failover(refiner, refine_prompt, temperature=0.1)

    # Text mode: draft then refine
    draft_system = system or "You are a skilled developer. Write the solution directly."
    draft = chat_with_failover(drafter, task, draft_system, temperature=0.3)

    refine_prompt = (
        f"A developer wrote this solution for the following task:\n\n"
        f"TASK:\n{task}\n\n"
        f"THEIR SOLUTION:\n{draft}\n\n"
        f"Review their solution. Fix any bugs, improve edge case handling, "
        f"and clean up the code. Output ONLY the final corrected solution."
    )
    return chat_with_failover(refiner, refine_prompt, temperature=0.1)


# ── Task keyword routing for auto_delegate ──────────────────────────────────

TASK_ROUTES = {
    "speed-run":    ["quick", "simple", "one-liner", "convert", "format",
                     "translate", "boilerplate", "template", "regex",
                     "hello", "print", "list"],
    "debug":        ["debug", "fix", "bug", "error", "broken", "crash",
                     "traceback", "exception", "failing", "wrong output"],
    "draft-refine": ["write", "code", "function", "implement", "class",
                     "method", "script", "program", "algorithm", "create",
                     "build", "make", "add", "feature"],
    "test-factory": ["test", "unittest", "pytest", "spec", "coverage",
                     "assert", "mock"],
    "reasoning":    ["analyze", "explain", "why", "compare", "evaluate",
                     "design", "architect", "plan", "review", "audit",
                     "reason", "think"],
    "code-review":  ["review", "pr ", "pull request", "check", "security",
                     "vulnerability"],
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

    best = max(scores, key=scores.get)
    best_score = scores[best]

    if best_score == 0:
        if len(task.split()) > 20:
            best = "draft-refine"
        else:
            best = "speed-run"

    # CybSec floor: never route a security/CTF/forensics task to a cheap recipe.
    if _is_security(task):
        best = "security"

    return pipeline(task=task, mode=best, agent=agent,
                   system=system, workspace=workspace)


@mcp.tool()
def batch_delegate(tasks_json: str) -> str:
    """Run multiple tasks in ONE call. Input: JSON list of {task, model?, mode?}.
    Returns combined results. Saves Opus round-trip tokens vs calling delegate N times.
    Example: [{"task":"write hello.py","mode":"speed-run"},{"task":"write tests","mode":"test-factory"}]
    """
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if not isinstance(tasks, list) or not tasks:
        return "Expected a JSON list of task objects."

    results = []
    with cf.ThreadPoolExecutor(max_workers=min(len(tasks), 5)) as ex:
        futs = {}
        for i, t in enumerate(tasks):
            task_text = t.get("task", "")
            mode = t.get("mode")
            model = t.get("model")
            system = t.get("system", "")

            if mode:
                f = ex.submit(pipeline, task_text, mode, False, system)
            elif model:
                f = ex.submit(delegate, task_text, model, False, system)
            else:
                f = ex.submit(auto_delegate, task_text, False, system)
            futs[f] = i

        indexed = {}
        for f in cf.as_completed(futs):
            idx = futs[f]
            try:
                indexed[idx] = f.result()
            except Exception as e:
                indexed[idx] = f"ERROR: {e}"

    for i in range(len(tasks)):
        task_text = tasks[i].get("task", "(no task)")[:60]
        results.append(f"### Task {i+1}: {task_text}\n{indexed.get(i, '(missing)')}")

    return _truncate("\n\n".join(results))


@mcp.tool()
def cost_report() -> str:
    """Session token usage summary."""
    u = SESSION_USAGE
    if u["calls"] == 0:
        return "No calls yet."

    lines = [
        f"Calls: {u['calls']} | "
        f"Tokens: {u['total_input_tokens']:,} in + {u['total_output_tokens']:,} out",
    ]
    for model, s in sorted(u["by_model"].items(),
                            key=lambda x: x[1]["output"], reverse=True):
        lines.append(f"  {model}: {s['calls']}x, {s['input']:,}in/{s['output']:,}out")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
