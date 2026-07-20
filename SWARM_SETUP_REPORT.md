# Swarm Setup Report — Opus-supervised tiny-model orchestra

**Goal:** Opus 4.8 (Claude Code) plans and supervises; cheap models on OpenRouter,
NVIDIA, and OpenCode Go do the grunt work — a Kimi-style "fan out, keep the best"
experience. This report analyses your installed plugins, three reference repos,
how Kimi's swarm works, what was built, and a **real before/after benchmark**.

---

## TL;DR

- Your setup already has the supervisor (Opus) + the delegation tool (`model-orchestra` MCP). This report adds a **`swarm`** tool: one task → N cheap workers in parallel → a judge model merges the best answer.
- **Benchmark (real, live API calls):** single cheap model solved **5/6** hard coding tasks; the 3-worker swarm solved **6/6** — **+17 points reliability** at **~3× tokens but ~same wall-clock** (workers run in parallel). See [REPORT.md](REPORT.md).
- The big frontier trick (Kimi's 300 sub-agents / native orchestration) is **not** reproducible with 8B workers. What *is* reproducible and works: **best-of-N with verification** + a supervisor merge. That's what you now have.

---

## 1. Installed plugins (what you have, what matters here)

Active plugins (from `~/.claude/plugins/cache`):

| Plugin | Version | Relevance to this goal |
|---|---|---|
| **ecc** | 2.0.0 | Biggest lever — orchestration commands (`orch-*`), worktree parallelism, memory hooks, model-routing guidance. Directly reusable. |
| **claude-mem** (thedotmack) | 13.11.0 | Cross-session memory — good for a persistent supervisor. |
| **ralph-wiggum** | 1.0.0 | Autonomous loop runner — pairs with a swarm for long jobs. |
| **feature-dev** | 1.0.0 | `code-architect` / `code-explorer` subagents — planning layer above workers. |
| **pr-review-toolkit** | 1.0.0 | Review subagents — a "judge" pattern you can borrow for swarm merging. |
| **caveman / ponytail** | — | Output-style shaping (terse / lazy). Cosmetic to this goal. |
| **security-guidance** | 2.0.0 | The `shell=True` warning you saw. Keep. |
| hookify, plugin-dev, agent-sdk-dev, commit-commands, code-review, frontend-design, obsidian, claude-opus-4-5-migration | — | General tooling, not swarm-specific. |

You also have the full **official marketplace** catalog available (github, playwright,
serena, context7, linear, etc.) but *not enabled* — leave them off; ecc already warns
that >10 MCP servers / >80 tools shrinks usable context from ~200k to ~70k tokens.

**Takeaway:** ecc + feature-dev + pr-review-toolkit already give you a
planner->worker->reviewer shape. `model-orchestra` adds the missing piece: those
workers can now be *cheap non-Anthropic models*, which subagents alone cannot do.

---

## 2. Reference repos

### `alishahryar1/free-claude-code` — proxy replacement
- **Mechanism:** local gateway at `ANTHROPIC_BASE_URL=http://localhost:8082`, dummy token `freecc`. Claude Code's Anthropic-format traffic is intercepted and re-routed to **25+ providers** (NVIDIA NIM, OpenRouter, DeepSeek, Groq, Ollama, LM Studio…). Model picked as `<provider>/<model-id>`.
- **Cost trick:** tier overrides `MODEL_OPUS` / `MODEL_SONNET` / `MODEL_HAIKU` — map each Claude "tier" to a different backend.
- **Verdict:** this *replaces* Opus entirely. **Opposite of your goal** (you want Opus as the boss). But the **tier-routing idea is worth stealing**: let Opus route Haiku-grade subtasks to your `cheap` worker automatically.

### `ruvnet/open-claude-code` — from-scratch clone
- **Mechanism:** a full CC reimplementation (`v2/`, ~8.3k lines), 25 built-in tools, 6 permission modes, `.mcp.json` over stdio/SSE/HTTP/WebSocket.
- **Multi-agent:** hierarchical **subagent spawning** + a **`SendMessage`** inter-agent channel, gated by `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`. Turn-based, **not** a true parallel swarm.
- **Verdict:** confirms the pattern Claude Code already gives you natively (Agent + SendMessage). Nothing to install — you have the real thing. Borrow the *team messaging* idea if workers ever need to coordinate.

### `affaan-m/ecc` — efficiency layer (you have it installed)
- **Mechanism:** skills + 67 agents + hooks + rules. Efficiency from context trimming, memory hooks (`session-start/end`), continuous-learning instincts, and **worktree-based parallel execution**.
- **Orchestration:** `orch-*` commands, a "cascade" of sequential agent handoffs, and **iterative retrieval** (refine subagent queries instead of dumping the whole codebase).
- **Verdict:** your efficiency backbone. The **worktree lifecycle** is the clean way to run swarm workers that touch files without colliding. Combine ecc worktrees + `model-orchestra` swarm for parallel *agentic* workers.

---

## 3. How Kimi's agent swarm works

From Moonshot's K2.5/K2.6 material:

- An **orchestrator** (lead model) decomposes a job, assigns sub-tasks to **specialised sub-agents that run in parallel**, maintains a **global task graph**, handles failures, and **merges** into one coherent output.
- K2.5 introduced **PARL** (Parallel-Agent RL); scale grew from ~100 sub-agents / 1,500 steps (K2.5) to **~300 sub-agents / 4,000 steps** (K2.6).
- Reported up to **~4.5× faster** than single-agent on parallelisable work.
- Key differentiator: **orchestration is native to the model** — no external framework needed to decompose/route/merge.

**What you can realistically copy:** the *shape* — decompose -> parallel workers -> merge —
not the scale. With 8B/32B workers you get reliability and speed on parallelisable
subtasks, not emergent 300-agent planning. Opus plays the "native orchestrator" role
that K2.6 does internally.

Sources: [DataCamp guide](https://www.datacamp.com/tutorial/kimi-k2-agent-swarm-guide),
[Kimi K2.5 swarm](https://kimi-k25.com/blog/kimi-k2-5-agent-swarm),
[K2.6 300 sub-agents](https://lushbinary.com/blog/kimi-k2-6-agent-swarm-300-sub-agents-guide/).

---

## 4. What was built

`model-orchestra` (MCP server, Opus is the supervisor) now exposes three tools:

| Tool | Pattern | Use |
|---|---|---|
| `delegate(task, model, agent)` | 1 worker, text or agentic loop | routine grunt work — cheapest |
| `swarm(task, models, judge)` | **N workers in parallel -> judge merges** | hard one-shot subtasks (best-of-N) |
| `list_workers()` | — | discover aliases |

Worker aliases (`config.json`): `cheap` (OpenRouter Llama-8B), `fast` (NVIDIA Llama-8B),
`coder` (Qwen-2.5-Coder-32B), `zen` (Kimi K3), `grok` (Grok 4.5).

The `swarm` tool is the Kimi shape at small scale: fan the same task to several diverse
cheap models at once, then a judge model synthesises the strongest answer. Opus decides
*when* to swarm vs delegate — it stays the orchestrator.

---

## 5. Benchmark — real before/after

Method: 6 LeetCode-medium coding tasks with nasty edge cases (int32 clamp, division
toward zero, DP, zero-handling). Each model's generated code is **executed against unit
tests** — pass/fail is functional, not judged. Live API calls, reproducible via
`python benchmark.py`. Full table: [REPORT.md](REPORT.md).

| | Pass rate | Solved | Total latency |
|---|---|---|---|
| **BEFORE** — single `cheap` | 83% | 5/6 | 29.8s |
| **AFTER** — swarm `cheap,fast,coder` | 100% | 6/6 | 31.6s |

- The single model failed **`eval_rpn`** (got integer-division-toward-zero wrong). The parallel swarm recovered it — redundancy across 3 attempts / 2 model families caught what one shot missed.
- **Reliability +17 points** for **~3× tokens** but **~same wall-clock** (parallel).
- Honest caveats: (a) small sample — the exact delta wobbles run to run; part of the gain is simply drawing multiple independent samples, which is *the point* of a swarm; (b) on trivial tasks the single model already scored 6/6 — **swarm only pays off on tasks hard enough to have a real failure rate.**

**Rule of thumb this produces:** default to `delegate` (1×). Escalate to `swarm` (N×)
only for subtasks where a single cheap model is unreliable. Reserve Opus itself for
planning, judging, and anything the cheap tier keeps failing.

---

## 6. Use it / next steps

1. Restart Claude Code once so the `swarm` tool + updated aliases load. Then: *"Use model-orchestra: swarm this task across cheap, fast, coder."*
2. For parallel **agentic** (file-editing) workers, run each in an **ecc git worktree** so they don't collide.
3. Rerun `python benchmark.py` anytime to re-measure (e.g. after swapping worker models in `config.json`, or adding `zen`/`grok` to the swarm for a stronger — but slower — pool).
4. Optional borrow from free-claude-code: an Opus rule that auto-routes trivial subtasks to `cheap` without being asked.

**Bottom line:** you have the Kimi *shape* (orchestrator + parallel workers + merge) at
cheap-model scale, with a measured, real reliability win. Not 300 agents — but honest,
reproducible, and it runs on the keys you already configured.
