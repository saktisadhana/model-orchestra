# Phase 2: Transactional Modular Budget Engine

## Goal

Make spending control real, user-owned, and reusable across routes without building adaptive automation prematurely.

## Why This Saves Money

The plugin's central promise fails if concurrent calls overspend, uncertain calls free funds too early, or unknown prices look free.

## Policy Model

Budget controls are orthogonal, not one mutually exclusive mode:

- **Cash ceilings:** fixed global/provider/mission/step/call limits and overage controls.
- **Quota ceilings:** requests, tokens, subscription windows, or provider-specific units.
- **Billing mode:** metered, prepaid subscription, quota-equivalent, free-tier,
  or unknown.
- **Pacing:** none, scheduled, or later adaptive recommendation.
- **Enforcement:** monitor, warn, require approval, pause, stop, queue, or cheaper-eligible fallback.
- **Reserves:** interactive, background, strong-model, and emergency allocations.

A user can combine fixed cash, subscription quota, scheduled pacing, and
monitor-only enforcement independently. Record a subscription fee once at the
pool level and per-call quota consumption separately. Metered balance fallback
requires its own cap and explicit approval.

## Architecture

Extract and generalize the minimum Phase 0 SQLite backend into focused modules
after characterization tests:

- `model_orchestra/budget/types.py`: exact money/quota units and decisions.
- `model_orchestra/budget/policy.py`: hierarchy and reserve evaluation.
- `model_orchestra/budget/store.py`: store protocol.
- `model_orchestra/budget/sqlite.py`: transactional implementation.
- `model_orchestra/budget/pricing.py`: metered, flat-rate, free-tier, unknown.
- `model_orchestra/budget/migrate.py`: current JSON import.
- `tests/test_budget.py`: deterministic accounting/concurrency suite.

Use integer minor units or decimal values, never binary float. Keep currencies separate unless a versioned FX source/rate/time is supplied.

## Reservation Lifecycle

```text
proposed -> reserved -> submitted -> settled
                               -> pending_liability
                               -> void
```

- Reserve a defensible maximum charge before submission.
- A timeout/cancellation after possible submission becomes `pending_liability`.
- Only provider confirmation or an explicit conservative settlement policy voids it.
- Actual charges above reservation consume reserve/overage policy and trigger an alert; they never create an invisible negative balance.
- Unbounded or stale-priced operations require approval or are refused.
- Idempotency keys prevent duplicate reservation/settlement events.
- Provider operation IDs and settlement event IDs are unique; corrections append
  compensating events rather than mutating settled history.

SQLite transactions must be process-safe on supported local filesystems. Document WAL, locking, backup, integrity check, migration, and network-filesystem refusal.

## First Runtime Modes

Ship only:

- fixed cash/quota ceilings;
- monitor enforcement;
- hard stop/approval enforcement;
- transparent cash, quota, pool reserve, and pending liability reports.

Scheduled pacing follows only after reset behavior is explicit: inclusive/exclusive
boundaries, weekly/monthly anchors, DST folds/gaps, timezone changes, parent-window
overlap, rollover, and whether queued work requires confirmation at reset.
Adaptive recommendations follow only after real accepted-outcome history exists
and never activate or raise ceilings automatically.

## Deterministic Validation

```bash
python -m pytest tests/test_budget.py -q
python tools/check.py
```

Test threads and separate processes, crash between each lifecycle transition, delayed/out-of-order usage, duplicate events, price changes, DST/leap day/clock rollback, migration replay, corrupted state, and reservation reconciliation.

## Exit Gate: v0.2 Stable Budget

- Hard ceilings never overspend in deterministic concurrency/fault tests.
- Pending liabilities remain unavailable until settled.
- Unknown pricing yields `unavailable`, not zero.
- Cash and quota report separately.
- Subscription fee, quota-equivalent usage, strong-model capacity preserved,
  and metered overage are never collapsed into one scalar invoice.
- Current JSON ledger migrates exactly once.
- `doctor` identifies stale liabilities and integrity failures.
- Phase 0 and alpha gates still pass.

## Stop-Loss

Do not build adaptive routing, financial forecasting, or automatic subscription optimization in this phase. If transactional fixed budgets do not improve trust/usefulness, stop budget expansion there.
