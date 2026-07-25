# Model Orchestra: Public Plugin Revival Plan

*Audited against the working tree on 2026-07-24. Repository facts below are
from local inspection and executed checks. External protocol and market claims
must be re-verified before publication.*

## Decision

Model Orchestra should become a provider-neutral MCP orchestration plugin for
coding hosts. Its product promise is:

> Delegate only when an eligible worker can do the job, preserve deterministic
> capability floors, verify with caller-owned evidence, and return compact,
> auditable handoffs to the host.

The MCP server is the product. Zed ACP, Hermes, Claude Code, Codex, and other
clients are adapters or installation targets. Private model aliases, personal
budgets, editor settings, and historical experiments are example configuration,
not the public identity.

This is a hardening and reduction project before it is a feature project.
Known correctness, security, and test-isolation defects block packaging and
promotion.

## Audited State

| Fact | Current value | Evidence |
|---|---:|---|
| `server.py` | 3,313 lines | `wc -l` |
| `acp_router.py` | 460 lines | `wc -l` |
| `config.json` | 429 lines | `wc -l` |
| `README.md` | 479 lines | `wc -l` |
| Public MCP tools | 12 | `@mcp.tool()` sites |
| Collected pytest tests | 83 | `python -m pytest --collect-only -q` |
| Deterministic safety + ACP tests | 77 passed in 9.53s | `python -m pytest tests/test_safety.py tests/test_acp_router.py -q` |
| Offline usefulness check | 8.6/10; routing 12/12; context -95.5% | `python tools/usefulness_benchmark.py --check` |
| Benchmark baseline | pass | `python tools/benchmark.py --check-baseline` |
| Alias/failover invariants | 19 aliases pass | `python tests/test_resolve.py` |
| Python package metadata | absent | no `pyproject.toml` |
| CI | absent | no workflow |
| Installed MCP SDK | 1.26.0 in current environment | package metadata |

`python -m pytest -q` is **not currently an offline test command**. It collects
six functions in `tests/test_quality.py` that make live provider calls, and those
functions return booleans instead of asserting. An audit run timed out after
300 seconds. The deterministic suite and paid benchmark suite must be separated
before any green-test claim is published.

## The Product Kernel

Keep these capabilities:

1. **Capability-first routing.** Security, repository editing, host judgment,
   local edits, and bounded mechanical work have different eligibility rules.
2. **Host-owned acceptance.** Workers produce artifacts, diffs, manifests, or
   bounded summaries; the host reviews and accepts.
3. **Caller-owned verification.** Tests are supplied independently of the
   worker and run before a result is labeled verified.
4. **Artifact-first batching.** Large results remain in the workspace and return
   as hashed manifests rather than consuming host context.
5. **Bounded failure behavior.** Context overflow fails immediately; permanent
   errors do not rotate credentials; retries, fan-out, steps, output, and wall
   time are bounded.
6. **Auditable economics.** Measured provider usage and estimated host effects
   are clearly distinguished from invoices.

## The 10/10 Experience Target

The product cannot provide infinite model context, unlimited provider quota, or
unbounded spending. It can provide **durable coding missions that feel
unlimited** because context limits and process boundaries stop being visible to
the user.

The target experience is:

- start one coding mission from any supported host;
- work for hours or days across many model contexts;
- pause, restart the host or MCP server, and resume from the next useful action;
- switch eligible providers without replaying incompatible model history;
- preserve accepted requirements, decisions, diffs, tests, and unresolved risks;
- show progress, budget, current worker, and recovery state at all times;
- never repeat an external side effect or silently lose accepted work;
- finish with a reproducible artifact and audit trail rather than a long chat.

The public wording should be precise:

> Model Orchestra does not offer infinite context. It offers durable coding
> missions that survive context limits, model changes, provider outages, and
> restarts.

### 10/10 product dimensions

1. **Trust:** capability and data floors cannot be weakened by economics.
2. **Continuity:** mission state survives every expected interruption.
3. **Quality:** acceptance is based on deterministic evidence, not worker prose.
4. **Economics:** users control money, quota, latency, and reserves independently.
5. **Portability:** the same mission can be resumed from supported hosts.
6. **Observability:** every decision, reservation, transition, and failure is
   inspectable without retaining secrets or raw prompts.
7. **Recovery:** operations are idempotent, checkpointed, and rollback-aware.
8. **Usability:** first success takes minutes and long-session controls are clear.
9. **Extensibility:** providers, stores, sandboxes, and adapters use stable contracts.
10. **Evidence:** public claims are reproducible and freshness-labeled.

Demote these to adapters, examples, or experiments:

- Zed ACP integration and Zed profile installer;
- Claude/Hermes host-policy files;
- personal provider aliases and IDR budget envelopes;
- `compact`, `pipeline`, and `swarm` unless benchmarks justify stable status;
- plugin-portability and historical benchmark reports;
- private gateway configuration.

## Public Compatibility Contract

"Usable by everyone" cannot mean every provider and client work identically.
For the first public release it means a tested support matrix and graceful
behavior outside it.

### Supported hosts for v0.2

| Host | Integration | Required smoke test |
|---|---|---|
| Hermes Agent | stdio MCP | install, discover tools, call `list_workers`, call `route_preview` |
| Claude Code | stdio MCP | same four operations |
| Generic MCP Inspector | stdio MCP | initialize, list tools, invoke one local/no-model tool |
| Zed | ACP adapter, experimental | start session, route three task classes, permissioned file operation |

Codex, Cursor, Continue, and other MCP clients may be documented as community
or unverified until a repeatable smoke test exists. Do not claim support from
protocol compatibility alone.

Adapters implement an explicit host-capability contract covering streaming,
cancellation, permission prompts, workspace roots, long-running/background
operations, and artifact handles. Unsupported capabilities degrade explicitly;
the core must not infer them from a host name.

### Supported provider shapes for v0.2

- one OpenAI-compatible provider;
- one Anthropic-compatible provider;
- custom base URL with environment-variable credential reference;
- no credential values in configuration examples, logs, manifests, or errors.

Every provider also declares a data-handling class (for example `local`,
`private`, or `external`) and whether code/file content may be sent to it.
Routing eligibility is the intersection of capability, data policy, budget,
and user choice. Cost must never move sensitive source to a less trusted
provider.

A public default must use independently obtainable providers. Private aliases
remain in `examples/private-profile/` or outside the repository.

### Platform matrix

CI must cover Windows and Linux. macOS may be allowed but unverified until a
runner or external installation report exists. Path confinement, process
cleanup, and executable resolution need platform-specific tests.

## Phase 0: Establish Truth and Fix Release Blockers

**Target:** days 1-10.

No packaging refactor starts until this phase passes.

### 0.1 Separate deterministic and live tests

- Mark live tests explicitly, for example `@pytest.mark.live`.
- Convert pytest tests to assertions; scripts that return booleans are not tests.
- Move paid quality evaluation out of default collection or require both
  `--live` and configured credentials.
- Add `pytest.ini` or `pyproject.toml` marker definitions.
- Make the default command fail if it attempts network access.
- Redirect budget state, artifacts, environment, usage, and telemetry to
  per-test temporary state. Tests that monkeypatch runtime functions must use
  fixtures that restore state even when assertions fail.

**Commands:**

```bash
python -m pytest -m "not live" -q
python tools/check.py
python tools/benchmark.py --live   # explicit, paid, never CI default
```

**Pass:** a clean environment runs the first two commands with network disabled,
and collecting/running default tests cannot invoke a provider.

### 0.2 Fix capability-floor bypasses

`delegate_verified()` calls `_verify_with_tests()` directly. Explicit-model
batch verification takes the same direct path. Both can bypass the security
floor enforced by `delegate()`.

- Centralize capability authorization before every public execution path.
- Require security tasks to use the configured security role/model unless an
  explicit audited override is allowed by policy.
- Apply the guard to `delegate`, `delegate_verified`, `pipeline`,
  `auto_delegate`, `orchestrate_change`, and every batch branch.
- Do not rely on callers choosing the safe tool.

**Regression tests:**

- security task + `delegate_verified(model="flash")` is rejected or rerouted;
- security batch item + explicit cheap model + tests is rejected or rerouted;
- override is explicit and increments telemetry;
- no provider call occurs before the decision.

### 0.3 Make cost caps and budgets real concurrency controls

Current batch validation parses `max_cost_idr`, but explicit `mode` and `model`
plans do not enforce it. Budget checks read spend, release the lock, call the
provider, and record afterward, allowing parallel oversubscription.

- Enforce per-item caps for auto, explicit route, and explicit model paths.
- Reserve worst-case budget atomically before starting a provider call. The
  reservation must be process-safe, not only thread-safe: two hosts may launch
  separate MCP server processes against the same ledger.
- Reconcile the reservation against actual usage afterward.
- Release reservations on cancellation and infrastructure failure.
- Use a bounded ledger with pruning or aggregation; do not rewrite unbounded
  history forever.

**Regression tests:**

- explicit model/route over cap stops before a provider call;
- parallel calls cannot reserve more than the remaining envelope;
- separate server processes cannot reserve the same remaining balance;
- failed calls release reservations;
- actual usage adjusts the reservation without negative balances.

### 0.4 Correct routing precedence

A prompt containing `repository`, `repo`, or `codebase` currently becomes
repository work before judgment classification in several cases. Analysis and
review must stay with the host unless there is clear mutation intent.

- Define precedence: security, tiny-local, explicit mutation, judgment, then
  mechanical.
- Require both repository context and mutation intent for repository editing.
- Add mixed-intent, negation, multilingual, misspelling, and adversarial cases.

**Pass:** the labeled corpus has no security false negatives and reaches the
published threshold for other classes. False positives and false negatives are
reported separately; one aggregate accuracy number is insufficient.

### 0.5 Fix filesystem and release hygiene

- Exclude both `.pytest_cache/` and the currently generated `.pytest-cache/`,
  editor state, bytecode, local ledgers, scratch
  transcripts, generated reports, and batch artifacts.
- Remove `docs/chat.md`, `docs/test.md`, and `.obsidian/` from release scope.
- Make `release_readiness.py --check` exit nonzero for source dirt, stale
  generated evidence, or forbidden workspace debris.
- Freeze or split the current uncommitted source changes into reviewable commits.

### 0.6 Create one offline gate and CI

Create `tools/check.py` as an orchestrator over existing checks, not a second
implementation. It runs:

1. configuration/schema validation;
2. import and MCP startup smoke tests;
3. deterministic pytest suite;
4. routing/failover invariants;
5. usefulness and benchmark-baseline checks;
6. generated JSON validation;
7. release-readiness checks when requested.

Add Windows and Linux CI with supported Python versions and a network-denied
job where practical.

**Phase 0 exit gate:** fresh clone, fresh virtual environment, documented install,
`python tools/check.py` passes on Windows and Linux without credentials or
network. All four known blocker classes above have regression tests.

## Phase 1: Package and Prove the Public Install

**Target:** weeks 2-4.

### 1.1 Add normal packaging

- Add `pyproject.toml`, package/version metadata, license/classifiers, supported
  Python range, constrained dependencies, and a build backend.
- Add console commands: `model-orchestra serve`, `check`, `doctor`, and
  `benchmark --live`.
- Keep `server.py` as a compatibility shim for existing MCP registrations until
  at least the next minor release.
- Build wheel and sdist in CI; install each into an empty environment and run
  smoke tests from outside the repository.
- Verify `pipx` or `uvx` execution and publish a TestPyPI artifact before public
  PyPI promotion. Decide whether to submit to an MCP registry/catalog only after
  package installation and host smoke tests are reproducible.

**Pass:** both artifacts install, expose the same version, start the server, and
run local tools without relying on the source checkout.

### 1.2 Define configuration v1 before changing internals

Create a typed, versioned schema with migration support. Separate:

- providers: protocol, base URL, credential variable, billing mode;
- models: provider/model ID, capability roles, limits, optional price key;
- policies: capability floors, execution authority, tool permissions,
  data-handling classes, fallback constraints, budgets, provider availability,
  and whether capability override is permitted;
- routes: eligible roles and verification requirements;
- presentation: enabled MCP tools and experimental flags.

Roles describe eligibility, but roles alone do not replace policy. A model may
have `security_review` capability while policy still requires no downgrade and
a specific trust tier. Do not collapse security policy into an ordinary role.

Provide `config.example.toml` or YAML plus JSON Schema. Keep `config.json`
compatibility through a migration command and deprecation warning.

### 1.3 Run decision spikes before provider refactoring

Write short ADRs with measured spikes for these choices:

**ADR-001: provider layer, LiteLLM or native.**

Test OpenAI-compatible calls, Anthropic messages, streaming, tool calls, cache
usage, retries, cross-provider-first fallback, cancellation, pricing, and the
budget reservation API. Adopt LiteLLM only if it preserves required behavior
with less owned code and acceptable dependency/startup cost. Otherwise retain a
small native adapter layer and document the unsupported provider boundary.

**ADR-002: MCP version support.**

The official repository currently exposes stable versions through `2025-11-25`
and a `2026-07-28-RC` tag, not a verified final release. Do not describe the RC
as published final. Test the pinned Python SDK against the stable protocol and
the RC in an isolated branch. Ship only stable support; document RC findings as
forward-compatibility research.

**ADR-003: pricing modes.**

Define `metered`, `flat_rate`, `free_tier`, and `unknown`. Flat-rate marginal
cost may be zero for routing, but reports must also show subscription allocation
or `not attributable`; zero marginal cost is not the same as free total cost.
Unknown pricing disables savings claims rather than silently becoming zero.

### 1.4 Define Budget Policy v1 contract

Budgeting is a composable policy layer, not a fixed set of IDR windows. Users
choose a mode, currency, hard ceilings, reserves, and enforcement actions. A
mission consumes the same interface whether it runs for one call or several
days.

#### Budget modes

1. **`fixed`:** user supplies known limits such as monthly, weekly, daily,
   per-mission, per-step, and per-call. Only explicitly configured limits are
   enforced; suggested pacing is visible, never silently invented.
2. **`schedule`:** a fixed allowance is paced across user-defined active hours,
   timezone, weekdays/weekends, and interactive/background/emergency reserves.
   Unused allowance rolls forward inside the parent hard window.
3. **`adaptive`:** pacing and route preferences learn from trustworthy usage
   history while hard user ceilings remain immutable. Requires a minimum history
   period and explicit user approval before activation.
4. **`quota`:** tracks subscription or provider quota independently from money.
   A flat-rate call may have zero marginal cash cost but still consumes scarce
   capacity and subscription allocation.
5. **`monitor`:** records usage and produces recommendations but never blocks or
   reroutes. Recommended default during the learning period.
6. **`disabled`:** no budget decisions. Usage may still be measured. Requires an
   explicit user choice rather than absence of configuration.

#### Example configuration

```yaml
budget:
  mode: adaptive
  currency: USD
  hard_limits:
    monthly: 60
    per_mission: 8
    per_call: 0.75
  reserves:
    interactive_percent: 40
    background_percent: 35
    emergency_percent: 25
  adaptive:
    learning_window_days: 28
    minimum_history_days: 7
    target_utilization_percent: 85
  on_limit:
    per_call: require_approval
    per_mission: pause
    daily: use_cheaper_eligible
    monthly: stop
```

Supported enforcement actions:

- `warn`;
- `require_approval`;
- `pause`;
- `stop`;
- `use_cheaper_eligible`;
- `queue_until_reset`.

`use_cheaper_eligible` may only select a route with the same capability floor,
data policy, execution authority, verification contract, and user permissions.
If none exists, pause rather than weaken policy.

#### Hierarchy and reserves

A call must satisfy every applicable level:

1. global/account budget;
2. provider money or quota envelope;
3. capability allocation;
4. mission budget;
5. step budget;
6. individual-call limit.

The most restrictive policy wins. Background work cannot spend interactive or
emergency reserves unless the user explicitly reallocates them. Security work
may use an emergency reserve only if policy names that use; it never receives an
automatic downgrade.

#### Architecture

```text
BudgetPolicy
  FixedBudgetPolicy
  ScheduledBudgetPolicy
  AdaptiveBudgetPolicy
  QuotaBudgetPolicy
  MonitorBudgetPolicy
  DisabledBudgetPolicy

BudgetStore
  SQLiteBudgetStore
  InMemoryBudgetStore

PricingModel
  MeteredPricing
  FlatRatePricing
  FreeTierPricing
  UnknownPricing
```

SQLite is the default durable store. Reservations use transactions that remain
correct across multiple MCP server processes. Each provider operation follows:

```text
estimate
  -> authorize capability and data policy
  -> atomically reserve money/quota
  -> call provider
  -> record actual usage
  -> reconcile or release reservation
  -> update trustworthy statistics
```

Cancellation, timeout, and infrastructure failure release reservations. A
crash-recovery sweep expires stale reservations using leases and idempotency
keys rather than guessing from wall-clock age alone.

Money uses fixed-point decimal amounts or integer minor units, never binary
floating point. Multi-currency totals require an explicit versioned FX rate,
source, and effective time; otherwise report currencies separately. Events use
UTC timestamps while reset windows use the configured IANA timezone. Tests cover
DST transitions, leap days, clock rollback, delayed usage reports, and provider
billing periods that do not align with calendar months.

#### Adaptive learning contract

Adaptive mode may learn usage by hour/day, interactive versus background work,
cost per accepted task, retry/repair waste, quota exhaustion, latency tolerance,
and unused allocation. It must never:

- raise hard limits automatically;
- enable itself without approval;
- treat unknown pricing as zero;
- learn accepted behavior from corrupted or duplicate events;
- optimize failed work as success;
- spend protected reserves on background work;
- weaken capability or provider trust policy;
- purchase capacity or upgrade subscriptions.

Recommendations include sample size, confidence, source window, and expected
effect. The user approves a proposed configuration diff; the system never edits
hard limits silently.

Adaptive history is local by default, exportable, inspectable, and deletable.
Users choose retention and may exclude repositories/providers from learning.
Raw source, prompts, and file contents are not training features; use bounded
operational metadata and accepted-outcome labels. Detect distribution shifts and
fall back to monitor mode when confidence or data freshness drops.

#### Budget contract acceptance

- all modes, limits, actions, reserves, and reset semantics validate through the
  versioned schema;
- monetary and quota balances have separate types and report fields;
- all windows use an explicit timezone and documented reset semantics;
- accounting uses exact units and never combines currencies without explicit FX;
- migration fixtures cover the current configuration and JSON ledger;
- every decision schema can explain matched limits, remaining reserves, and
  rejected routes before runtime implementation exists;
- `doctor` validates unknown prices, reserve totals, reset rules, impossible
  schedules, and migration fixtures without exposing credentials.

Runtime rollout is staged:

1. Phase 2 implements transactional storage plus `monitor` and `fixed`.
2. `quota` follows after money/quota accounting is separated and tested.
3. `schedule` follows after timezone/reset tests and reserve accounting.
4. `adaptive` follows only after accepted-outcome history is trustworthy.

**Phase 1 exit gate:** a new user installs from an artifact, copies the public
example, configures one public provider, registers MCP in a supported host, and
calls `list_workers` and `route_preview` in under ten minutes. `doctor` diagnoses
missing prerequisites without printing secrets.

## Phase 2: Extract a Shared Runtime Without Changing Behavior

**Target:** weeks 4-7.

Introduce `OrchestraRuntime` with injected configuration, clock, clients, budget
store, telemetry, and filesystem/process backends. Preserve public MCP schemas
while moving one responsibility at a time.

Recommended order:

1. `model_orchestra/config.py`: schema, loading, migration;
2. `model_orchestra/policy.py`: capability authorization shared by all tools;
3. `model_orchestra/routing.py`: classification and route decisions;
4. `model_orchestra/retry.py`: error classes, deadlines, failover;
5. `model_orchestra/providers.py`: protocol adapters selected by ADR-001;
6. `model_orchestra/budget.py`: reservations, reconciliation, pricing modes;
7. `model_orchestra/artifacts.py`: snapshots, manifests, collision handling;
8. `model_orchestra/verification.py`: candidate extraction and trusted execution;
9. `model_orchestra/agents.py`: workspace agent loop and tools;
10. `model_orchestra/telemetry.py`: bounded metadata events;
11. `model_orchestra/mcp_server.py`: thin tool registration;
12. `model_orchestra/adapters/zed_acp.py`: ACP translation only.

Each extraction gets one commit and runs `python tools/check.py`. Do not enforce
an arbitrary 600-line target if it creates artificial fragmentation; use clear
ownership, acyclic dependencies, and testability as the gate. The 800-line
ceiling is a warning threshold, not acceptance by itself.

### Required architecture invariants

- MCP and ACP call the same policy, routing, provider, budget, and telemetry APIs.
- No adapter imports private module internals.
- No test mutates process-global configuration or usage state.
- Public tool input/output schemas have contract tests and versioning rules.
- Cancellation propagates through provider calls, subprocesses, and reservations.
- Route eligibility cannot weaken provider data-handling policy.
- Adapters negotiate host capabilities; unsupported permission, streaming,
  cancellation, or background semantics produce explicit degradation.
- Fixed and monitor budget modes use transactional SQLite reservations; current
  JSON state migration and multi-process recovery tests pass.
- `doctor` detects stale/orphaned reservations, ledger integrity failures, and
  recovery actions against the live store.

**Phase 2 exit gate:** behavior-contract suite unchanged, supported-host smoke
matrix passes, compatibility shims pass, and no adapter owns routing or provider
execution logic.

## Phase 2.5: Durable Coding Missions

**Target:** weeks 7-10. Begin after transactional budget storage and the shared
runtime contracts exist. A limited mission preview may ship experimentally; it
is not a v0.2 blocker.

### Mission state is not conversation history

Store structured, minimal state in SQLite. Raw transcripts, tool output, diffs,
and generated artifacts stay content-addressed on disk and are retrieved only
when needed. A mission record contains:

- immutable mission ID, owner/profile, workspace identity, and creation time;
- objective, acceptance criteria, constraints, and user-approved scope;
- state-machine phase, active step, dependency graph, and next useful action;
- accepted decisions with rationale and supersession links;
- changed-file manifests, artifact hashes, checkpoints, and rollback handles;
- commands, exit status, deterministic test evidence, and unresolved failures;
- capability/data policy, granted permissions, and side-effect approvals;
- budget policy snapshot, reservations, actual usage, and remaining allocation;
- current/previous worker and provider metadata without hidden reasoning;
- risks, blockers, unanswered questions, and last heartbeat.

Do not store secrets, raw hidden reasoning, or the entire prompt as mission state.
If exact user text is required as a contract, store an encrypted or explicitly
approved artifact with access policy and hash.

Use SQLite WAL on supported local filesystems with documented backup, integrity
check, and atomic restore procedures. Detect and refuse unsupported network
filesystem locking rather than claiming durability. Schema migrations are
transactional, reversible where possible, and tested from every supported
released version.

### Resumable state machine

```text
DRAFT
  -> DISCOVER
  -> PLAN
  -> IMPLEMENT
  -> VERIFY
  -> REVIEW
  -> REPAIR
  -> ACCEPT
  -> COMPLETE

Any active phase may enter:
  PAUSED | BLOCKED | WAITING_APPROVAL | RECOVERING | CANCELLED | FAILED
```

Transitions are transactional and event-sourced. Each transition has:

- expected current version/state;
- idempotency key;
- preconditions and required evidence;
- budget/quota reservation;
- intended side effects;
- resulting artifacts and state version;
- retry classification and compensation action.

Optimistic concurrency prevents two hosts from advancing the same mission step.
A lease permits one active executor; heartbeats renew it. Expired leases allow
recovery only after checking process, workspace, git, artifact, and reservation
state.

### Checkpoints and recovery

Create checkpoints after discovery, plan approval, each accepted implementation
unit, deterministic verification, review, and release preparation. A checkpoint
includes structured mission state plus hashes of referenced artifacts and the
workspace/git identity.

Recovery supports:

- host or MCP server restart;
- operating-system restart;
- model/provider switch;
- context compression or fresh model context;
- provider outage or quota reset;
- user pause and multi-day resume;
- interrupted subprocess or workspace-agent step;
- partial artifact write or stale reservation.

On resume, compare the recorded workspace identity and file hashes with current
state. If external changes conflict, pause for reconciliation; never overwrite
or replay blindly.

Workspace identity includes canonical root, repository identity, branch/worktree,
HEAD/base revision, and policy-relevant subpaths. Moving a workspace requires an
explicit reattach flow; changing branch or worktree cannot silently redirect a
mission.

### Working-set context builder

Every worker receives a bounded context assembled from:

1. compact mission brief and acceptance criteria;
2. current step and required capability/data policy;
3. relevant accepted decisions and failed approaches;
4. targeted code excerpts and current diff;
5. latest deterministic failures;
6. artifact handles for deeper retrieval;
7. expected structured handoff schema.

The builder has a token budget and records why each item was included. Context
pressure triggers checkpoint + compaction + a fresh model context before quality
falls off. Acceptance criteria, security constraints, user decisions, unresolved
failures, and side-effect approvals are never summarized away.

### Structured worker handoff

```json
{
  "schema_version": 1,
  "status": "completed",
  "summary": "Implemented typed configuration loading",
  "changed_files": [],
  "artifacts": [],
  "decisions_proposed": [],
  "tests": [],
  "risks": [],
  "blocked_on": [],
  "next_action": "Run migration compatibility checks"
}
```

Validate the schema and verify every referenced artifact hash. A handoff updates
mission state only after local verification. Worker prose is never accepted as
proof of file writes, tests, or external actions.

### Side-effect journal and exactly-once intent

True exactly-once execution is impossible for arbitrary external systems. Aim
for exactly-once **intent** with idempotent operations where supported:

- generate an idempotency key before every write, deploy, publish, payment, or
  remote mutation;
- persist intent before execution and result after execution;
- on recovery, query the external system before retrying;
- require approval again when outcome is unknown and replay could be harmful;
- never infer success from timeout alone.

Git/file edits use precondition hashes. Remote APIs use native idempotency keys
or operation IDs. Non-idempotent tools must declare compensation and recovery
behavior before mission use.

### Runaway and no-progress protection

- per-call, per-step, per-mission, and global budget/quota controls;
- maximum attempts by error category, not one global retry count;
- repeated-diff, repeated-error, and repeated-plan detection;
- no-progress score based on accepted artifacts and verification movement;
- wall-clock deadlines and quiet-hour/background scheduling;
- pause after conflicting edits or repeated infrastructure failures;
- human approval for scope growth, destructive actions, publishing, deployment,
  purchases, credential changes, or protected-reserve use.

Approvals are scoped to a named action, target, expected hash/state, budget, and
expiry. They do not survive material input changes or resume indefinitely. A
mission resumed after approval expiry returns to `WAITING_APPROVAL`.

### Mission controls and observability

Provide CLI and MCP controls without forcing every host to implement custom UI:

- `mission_create`, `mission_status`, `mission_pause`, `mission_resume`,
  `mission_cancel`, `mission_checkpoint`, `mission_rollback`, `mission_events`;
- concise status: phase, active step, progress, last checkpoint, worker, budget,
  tests, blockers, and next action;
- event stream with bounded metadata and artifact handles;
- export/import of a redacted, versioned mission bundle;
- retention, archive, deletion, and workspace-detach controls.

Evaluate whether mission controls belong in a separate opt-in MCP profile to
avoid adding schemas to short-session users.

### Mission acceptance benchmark

A release-quality continuity run must:

- execute at least 100 substantive steps over multiple days;
- cross several context resets and fresh model contexts;
- restart both host and MCP server;
- simulate operating-system restart and stale executor lease;
- switch between eligible providers and survive an outage;
- pause until a quota window resets and resume automatically or by user choice;
- detect an external workspace edit and reconcile safely;
- preserve every accepted decision and unresolved constraint;
- avoid duplicate file edits, remote side effects, and budget charges;
- finish with deterministic tests, verified artifact hashes, and a complete audit
  trail.

Measure recovery success, duplicated-work rate, lost-decision rate, context per
step, accepted changes per unit cost, latency, user interventions, and final
correctness. "100 turns" alone is not success.

**Phase 2.5 exit gate:** crash/restart fault-injection suite passes; mission
state migration is tested; budget and side-effect journals reconcile; a mission
resumes from two supported hosts; no critical state exists only in model context.

## Phase 3: Define the Stable Plugin Surface

**Target:** weeks 10-12. This follows the shared-runtime work; durable missions
may continue in parallel as an experimental profile.

### Stable v0.2 tools

Keep these enabled by default:

- `list_workers`;
- `route_preview`;
- `delegate`;
- `delegate_verified`;
- `orchestrate_change`;
- `batch_delegate`;
- `orchestration_report`;
- `cost_report`.

Keep `compact`, `pipeline`, and `swarm` experimental and disabled by default.
`auto_delegate` needs a product decision: retain it as a convenience wrapper if
host selection tests show reliable invocation; otherwise direct users to
intent-specific tools and `route_preview`.

`cost_report` is stable as accounting, not as a promise of savings. When pricing
or billing mode is unknown, measured usage remains available but comparative
cost and saving fields return `unavailable` with a reason.

Tool enablement is behavioral configuration, not a secret. Put it in the public
config schema and CLI (`model-orchestra tools enable ...`), not an environment
variable. Environment variables remain for credentials and operator overrides.

### Intent wrappers need a tool-budget experiment

Do not add `debug`, `codereview`, and `precommit` wrappers merely because their
names are clearer. Each extra MCP schema costs host context and may confuse tool
selection. Run a fixed host-selection evaluation comparing:

- current mechanism-named tools;
- improved descriptions only;
- a minimal set of intent wrappers.

Adopt wrappers only if selection accuracy improves materially without excessive
prompt growth or duplicate semantics.

### Execution trust modes

- **Trusted local:** generated code may run in a timeout-bounded subprocess with
  explicit documentation that this is not isolation.
- **Sandboxed:** container or OS isolation backend with no secrets, restricted
  filesystem/network, resource limits, and cleanup.
- **Refuse:** untrusted execution when no sandbox backend is available.

Sandboxing is optional for v0.2 only if trusted mode is explicit and untrusted
input is refused. It is required before marketing verification as safe for
untrusted code.

### Routing rules

Move rules into versioned policy data with schema validation, but keep immutable
built-in floors that user routing rules cannot accidentally weaken. Custom
security overrides must be explicit, visible in `doctor`, and telemetered.

**Phase 3 exit gate:** stable and experimental APIs are documented; default tool
schemas stay within a measured prompt budget; trusted/untrusted execution is
unambiguous; host tool-selection evaluation meets its threshold.

## Phase 4: Add Measured Optimizations and Credible Evidence

**Target:** after Phase 3 and ongoing. Evidence needed for narrow launch claims
may run before v0.2; optimization experiments do not block v0.2 unless stated.

### 4.1 Deterministic output filtering spike

Do not put a generic `git diff`/`grep` filter blindly in front of `compact()`.
`compact()` accepts arbitrary text and usually cannot know the producer format.
Instead define typed filters at known boundaries or add an explicit `format`
parameter.

For each supported format, create a golden corpus and measure:

- token reduction;
- preservation of errors, paths, hunks, exit status, and requested context;
- latency and CPU;
- fallback to unfiltered text when confidence is low.

Ship a filter only if semantic-preservation tests pass and median token reduction
is meaningful. Never filter verification failures or security evidence without
a raw-artifact handle.

### 4.2 Budget-aware fallback

Budget exhaustion may select another model only when the candidate satisfies the
same capability floor, verification contract, data-handling policy, and explicit
user cap. Security no-downgrade remains fail-closed. Route previews must explain
the fallback before execution.

### 4.3 Terse worker-output experiment

Compare normal and terse system prompts on the same execution-checked corpus.
Measure correctness, truncation, output tokens, latency, and repair count. Ship
terse mode only if correctness is non-inferior within a declared tolerance.
Never lower generation caps to create savings.

### 4.4 Evidence tracks

Maintain separate reports:

1. **Routing:** at least 100 labeled prompts; confusion matrix by class,
   multilingual cases, negation, mixed intent, adversarial wording.
2. **Correctness:** versioned polyglot exercises with hidden caller-owned tests.
   Verify licensing before adopting Exercism/Aider-derived material.
3. **Repository work:** fixture repositories with failing tests, expected
   behavior, forbidden-file rules, patch size, and host acceptance.
4. **Pair versus single:** same task, fixed host/worker pair, direct strong-model
   baseline, repeated runs, identical verification.

Every live report records commit SHA, config and suite hashes, resolved models,
provider/pricing snapshot, seeds, repetitions, pass criteria, failures, usage,
latency p50/p95, and assumptions. Show correctness, cost, and latency together.
Do not use the self-authored 8.6/10 score as the public headline.

**Phase 4 exit gate:** every README claim links to current machine-readable
evidence; stale reports are labeled automatically; no universal-savings claim
is made.

### 4.5 Additional high-leverage differentiators

These ideas reinforce the product kernel and durable-mission goal without
turning Model Orchestra into a general IDE.

**Deterministic replay and simulation.** Record bounded decision inputs,
classified outcomes, state transitions, and artifact hashes so routing, budget,
and recovery behavior can be replayed without paid model calls. Provider content
is replaced by fixtures or hashes. This becomes the fastest regression tool for
mission recovery and policy changes.

**Policy explainability.** Every route preview and mission transition can answer:
which candidates were considered, which immutable floor applied, why candidates
were rejected, what budget/reserve matched, what data policy applied, and what
user action could safely change the outcome. Explanations must be structured and
redacted, not generated rationalizations.

**Local-first/private routing.** Permit users to require local models or private
providers for selected repositories, paths, classifications, or mission phases.
Cloud fallback is opt-in for protected work. A preflight scan reports what data
would leave the machine before the first call.

**Conformance kit.** Publish fixtures and a runner for provider adapters,
sandbox backends, budget stores, and host adapters. Third-party integrations
must prove cancellation, error taxonomy, usage normalization, redaction,
capability enforcement, and recovery behavior before being labeled compatible.

**Mission templates.** Versioned templates for bug fix, feature, migration,
review, and release missions define required states and evidence without adding
new MCP tools. Templates are configuration plus schemas, not hard-coded agent
personas.

**Quality-adjusted routing.** Once enough accepted outcomes exist, compare routes
using cost per accepted result, repair count, latency, and confidence intervals,
not raw token price. Learning can recommend route changes but cannot alter hard
floors or hard budgets without approval.

## Phase 5: Public Pre-Release and Adoption Proof

### Documentation and distribution

- README under roughly 150 lines: problem, differentiator, five-minute install,
  first workflow, support matrix, trust model, and evidence links.
- Detailed architecture, configuration, adapters, security, benchmarks, and
  migration guides under `docs/`.
- `SECURITY.md`, `CONTRIBUTING.md`, changelog, issue templates, release checklist,
  code of conduct, and vulnerability-reporting channel.
- Signed/tagged release where practical, checksums, wheel/sdist, and pinned
  provenance for generated evidence.
- One-command MCP registration examples for each supported host.

Positioning:

> Routers move traffic. Model Orchestra moves responsibility: eligibility,
> verification, artifacts, and acceptance.

### External validation

Recruit at least three people who did not build the project. Observe rather than
coach them through:

1. install from release artifact;
2. configure a public provider;
3. register a supported host;
4. call `route_preview`;
5. complete a repository fixture workflow;
6. inspect manifest, tests, and telemetry;
7. uninstall cleanly.

Record time, errors, documentation gaps, OS/host/provider, and whether secrets
appeared in output. Fix blockers before stable release.

Promotion stages are separate:

1. TestPyPI/internal host matrix;
2. public `0.2.0` pre-release with a fixed feedback window;
3. release candidate after all P0/P1 onboarding defects close;
4. stable release after artifact reproducibility, rollback, and support gates
   pass through the soak period.

**Phase 5 exit gate:** three independent successful workflows across at least
two operating systems and two hosts; no private instructions; median first-use
time under ten minutes; no critical/high security findings; rollback/uninstall
verified.

## Release Gates

### v0.2 alpha

- Phase 0 and Phase 1 pass.
- Capability-floor, budget, path, manifest, and redaction tests pass.
- Wheel/sdist install smoke passes on Windows and Linux.
- Hermes, Claude Code, and MCP Inspector smoke tests pass.
- Trusted execution warning is explicit; untrusted execution is refused without
  sandboxing.
- Public example uses no private service.

### v0.2 stable

- Phase 2 and Phase 3 pass;
- configuration migration and compatibility shims pass;
- stable tool schemas and prompt budget are frozen for the minor release;
- fixed/monitor budget modes and `unavailable` pricing behavior pass;
- public pre-release completes its feedback window with no open P0/P1 defects;
- rollback, upgrade, and uninstall paths work.

### v1.0 core stable

- Shared runtime and adapter separation complete.
- Configuration migration and deprecation policy tested.
- Stable tool contracts versioned.
- At least three independent installations meet Phase 5 gate.
- Current benchmark evidence covers routing, correctness, and repository work.
- Security review has no unresolved critical/high findings.
- Upgrade and uninstall paths work.

### Durable Missions stable

- Phase 2.5 fault-injection benchmark passes on Windows and Linux;
- mission schema and migration policy are versioned;
- pause/resume works across at least two supported hosts;
- no duplicate accepted edit, side effect, or budget charge occurs in recovery
  tests;
- working-set context stays bounded across the long-run benchmark;
- export, archive, delete, rollback, and workspace reconciliation are verified;
- no critical mission state exists only in a transcript or model context.

## Do Not Regress

Every item must have an automated assertion or contract test:

- every public execution path enforces capability authorization;
- security work never silently downgrades;
- every configured failover chain eventually leaves the failed provider where
  policy permits fallback;
- generation caps retain the measured minimum quality floor;
- verification requires caller-owned tests and rejects agent mode;
- workspace and artifact paths reject traversal, symlink/junction escapes, and
  platform-specific unsafe forms;
- batch destinations reserve before model calls and verify hashes afterward;
- context overflow and permanent 4xx errors never retry or rotate credentials;
- cost caps apply to auto, explicit route, and explicit model paths;
- parallel budget reservations cannot oversubscribe an envelope;
- reservations remain correct across multiple server processes;
- capability overrides are explicit and telemetered;
- provider data policy cannot be weakened by cost or fallback logic;
- telemetry never stores prompts, source/file contents, secrets, or raw errors;
- cancellation cleans up subprocesses, temp directories, and budget reservations;
- deterministic default tests make no network calls.
- unsupported host capabilities fail or degrade explicitly rather than changing
  semantics silently.
- mission transitions are version-checked, transactional, and idempotent;
- stale leases cannot create two active executors for one mission;
- resume verifies workspace/artifact hashes before mutation;
- side effects with unknown outcomes are never replayed automatically;
- context compaction preserves acceptance criteria, security policy, user
  decisions, unresolved failures, and approvals;
- adaptive budgets never raise hard ceilings or enable themselves;
- background missions cannot consume protected reserves without approval.

## Deferred Ideas

These are valid research items, not critical-path commitments:

- MCP RC caching metadata such as `ttlMs`/`cacheScope`;
- intent-named wrappers beyond the evaluated minimum;
- automatic quota cascading;
- subscription-aware economic optimization;
- deterministic structural compression;
- terse worker personalities;
- broad provider coverage;
- swarms and deep multi-stage recipes;
- conversational state shared across models.

Raw cross-model conversation threading remains deferred by design. Durable
missions share structured state and artifact handles, not incompatible hidden
reasoning or provider-specific message history.

Each requires a spike, benchmark, and explicit adoption decision. None should
block the first trustworthy public release.

## Immediate Backlog

1. Mark live tests and make default pytest deterministic/offline.
2. Convert boolean-returning pytest functions to assertions or benchmark-only
   entry points.
3. Add regression tests and fix `delegate_verified`/batch security-floor bypass.
4. Add cost-cap enforcement for explicit batch routes/models.
5. Add atomic budget reservation/reconciliation for parallel calls.
6. Fix repository-versus-judgment routing precedence and expand the corpus.
7. Clean release scope and make release-readiness enforceable.
8. Add `tools/check.py` and Windows/Linux CI.
9. Add `pyproject.toml`, artifacts, entry points, and install smoke tests.
10. Define configuration v1 and migration behavior.
11. Complete ADR-001 provider spike, ADR-002 MCP compatibility spike, and
    ADR-003 pricing-mode design.
12. Publish a minimal public provider example and supported-host registration.
13. Extract configuration, policy, routing, and budget runtime one module per
    commit.
14. Implement fixed and monitor budget policies over transactional SQLite.
15. Finish remaining shared-runtime extraction and adapter conformance.
16. Evaluate stable tool surface and host selection accuracy.
17. Add quota and schedule policies; collect trustworthy recommendation data.
18. Add adaptive recommendations behind explicit approval.
19. Define mission schema, event log, leases, and checkpoint format.
20. Implement pause/resume and workspace reconciliation for one host.
21. Add structured worker handoffs and verified artifact ingestion.
22. Add side-effect journal, no-progress detection, and fault injection.
23. Add second-host resume, mission export/import, and long-run benchmark.
24. Publish conformance kit and policy-explanation schemas.
25. Run external installation and multi-day mission tests before stable claims.

## Final Assessment

The project has a credible kernel: deterministic capability floors, bounded
failure behavior, hashed artifact handoffs, caller-owned verification, and host
acceptance. Those features distinguish it from transparent routers and generic
multi-provider gateways.

The current plan becomes realistic only when known security and test-isolation
failures move ahead of packaging, transactional accounting precedes long-running
missions, and speculative optimizations remain behind measured decision gates.
The path to a widely usable plugin is not more tools. It is a trustworthy
offline gate, a public install, modular user-controlled economics, one shared
runtime, durable structured mission state, explicit trust boundaries, tested
recovery, and evidence users can reproduce.