# Optional Phase 5: Durable Coding Missions

## Status

Optional post-v1 track. Do not begin until verified budget benefit and public
core gates pass. Durable missions extend the core; they do not justify it.

Ship durable missions in a separate opt-in MCP profile/namespace so enabling the
preview does not alter the frozen core tool schema. Core v0.2/v1 marketing must
not imply mission stability.

## Goal

Make long coding work feel continuous across context resets, provider changes, host/server restarts, and multi-day pauses while preserving the same capability and budget policy.

## Minimum Viable Mission

Prove one host and one local workspace first:

- structured objective and acceptance criteria;
- state machine and append-only events;
- checkpoints after accepted steps;
- bounded working-set context;
- structured worker handoffs;
- pause/resume;
- workspace/hash reconciliation;
- transactional mission budget;
- no-progress and retry bounds.

Cross-host resume, adaptive scheduling, templates, and broad side-effect integrations come later.

## Correctness Requirements

### Fenced execution

A lease alone is insufficient. Every executor receives a monotonically increasing fencing token. Workspace writes, artifact publication, subprocess result commits, and side-effect journal updates reject stale tokens. A stale executor may finish computation but cannot publish its result.

Mission controls authenticate a caller/profile and authorize mission ownership
before status, resume, rollback, export, event access, or deletion. Cross-host
resume requires reauthentication and explicit workspace attachment; possession
of a mission ID is not authorization. A mission ID alone never grants access.

### Atomic artifact publication

SQLite cannot atomically commit filesystem content. Use a staged protocol:

1. write content to a mission staging directory;
2. flush and hash it;
3. atomically rename into content-addressed storage;
4. transactionally publish the reference/event;
5. clean orphaned staged/published-unreferenced artifacts during recovery.

Workspace edits use precondition hashes and a journaled apply step. Recovery distinguishes staged, applied, published, and accepted states.

Mission transition and budget reservation share one SQLite transaction when they
use the same database. If stores differ, use a durable outbox/saga with explicit
compensation and orphan recovery; never advance the mission before reservation
durability is known.

### Side effects

Provide exactly-once intent, not a false exactly-once execution claim. Persist intent and idempotency key before action. On unknown outcome, query the target and require approval before unsafe replay. Approvals bind action, target, expected state/hash, budget, and expiry.

Adapters declare deduplication scope and retention. If an outcome cannot be
queried or deduplicated, transition to terminal `UNKNOWN` and forbid automatic
replay. A user may explicitly accept duplicate risk, but the audit trail must
record that new authorization.

### Context integrity

Never summarize away acceptance criteria, user decisions, capability/data policy, unresolved failures, or approvals. Store structured state, not raw hidden reasoning or provider-specific conversation history.

Mandatory context is canonical typed state with IDs, versions, supersession, and
conflict validation. If mandatory state exceeds the context budget, pause and
require scope consolidation; do not silently omit authority or send stale,
conflicting records.

### Privacy and retention

Redact artifacts at ingestion, enforce per-owner access, and define encryption,
retention, export, backup, and deletion propagation for mission rows and
content-addressed blobs. Separate immutable accounting retention from deletable
learning features and user artifacts. Deletion uses tombstones where audit law
or accounting integrity requires records to remain.

### Workspace and controls

Workspace identity includes HEAD, index, worktree, untracked files, submodules,
sparse checkout, LFS state, file modes/case behavior, and in-progress Git
operations. Define deterministic conflict predicates and choices: abort, reattach,
rebase plan, accept external state, or start a new mission.

Pause/cancel revoke the lease, advance the fencing token, terminate process
trees, and reject late completions. Rollback distinguishes mission metadata,
uncommitted file restoration, commit reversal, and irreversible external effects;
it never promises to undo what has no compensating operation.

## Fault-Injection Benchmark

A stable mission must survive:

- at least 100 substantive steps;
- multiple fresh model contexts;
- host/MCP/OS restarts;
- stale executor lease and fencing rejection;
- provider outage and eligible switch;
- quota pause/reset;
- external workspace/branch change;
- crash at every reservation, artifact, and transition boundary;
- no duplicate accepted edit, charge, or side effect;
- deterministic final tests and complete audit trail.

Use a seeded fault scheduler and fake external systems. Inject crashes before and
after every durable barrier and assert the exact expected state. Define a
substantive step as one accepted state transition producing verified evidence or
an explicit user decision.

Pass thresholds:

- 100% recovery to the expected state across the enumerated fault matrix;
- zero stale-fence publications;
- zero duplicate settled charges or accepted edits;
- zero lost accepted decisions/constraints;
- zero automatic replay from `UNKNOWN`;
- bounded mandatory context in every step, otherwise an explicit pause;
- final correctness equal to the uninterrupted control run;
- mission overhead does not erase Phase 3's declared budget-benefit threshold.

## Stable Gate

- One-host preview passes before second-host work begins.
- Fault suite passes on Windows and Linux.
- Mission schema/migrations are versioned.
- Context per step remains bounded.
- No critical state exists only in model context.
- Export/archive/delete/rollback/reconciliation work.
- Long-run verified budget benefit remains positive after mission overhead.

## Stop-Loss

If mission persistence erases the savings proven in Phase 3, keep missions opt-in or stop. Do not build a generic workflow engine, distributed scheduler, or IDE. The feature exists only to extend affordable coding continuity.
