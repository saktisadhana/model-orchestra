# Phase 0: Stabilize and Stop Leaks

## Goal

Create a trustworthy, zero-network baseline and fix defects that can waste money or violate capability policy.

## Why This Saves Money

No routing optimization matters if tests spend money unexpectedly, cheap models receive protected work, explicit cost caps are ignored, or parallel requests overspend.

## Deliverables

### 1. Isolate live tests

**Likely files**

- Modify `tests/test_quality.py`
- Create `pytest.ini` or define markers in `pyproject.toml`
- Create `tools/check.py`
- Modify deterministic test fixtures under `tests/`

**Required behavior**

- Mark `live`, `network`, and `paid` tests.
- Default pytest excludes all three categories.
- Convert boolean-returning tests to assertions or benchmark entry points.
- Block outbound sockets in the offline suite.
- Redirect ledger, artifacts, environment, and telemetry to temporary fixtures.

### 2. Close capability-floor bypasses

**Likely files**

- Modify `server.py`
- Modify `tests/test_safety.py`

Centralize authorization before every public execution path. Cover `delegate`, `delegate_verified`, pipeline paths, auto-routing, repository orchestration, and every batch branch.

### 3. Enforce all explicit cost caps

Explicit batch `model` and `mode` requests must compare a defensible maximum charge with `max_cost_idr` before any provider call. Unknown maximum cost fails closed or requires explicit approval.

### 4. Add the minimum transactional reservation backend

Implement the smallest SQLite reservation table needed to make current limits
process-safe. Define operation ID, reserved maximum, lifecycle state, provider
group, and settlement event ID before use. Failed, timed-out, or interrupted
calls become pending liabilities until settlement or explicit conservative
expiry policy. Do not release uncertain charges immediately. Phase 2 extracts
and generalizes this proven containment backend; it does not replace it with a
second accounting implementation.

### 5. Fix review-versus-edit routing

Security first, tiny local second, explicit repository mutation third, judgment fourth, mechanical last. Generic `repository`, `codebase`, or `project` wording without mutation intent stays with the host.

### 6. Clean release scope

Exclude `.pytest_cache/`, `.pytest-cache/`, `.obsidian/`, local ledgers, artifacts, transcripts, bytecode, and generated local reports. Make release readiness fail on forbidden debris.

### 7. Invalidate stale economic evidence

Reports are valid only when full hashes match for configuration, provider/model
mapping, pricing, token limits, comparison host, and task suite. A changed
price table or host alias invalidates savings evidence immediately. The report
must show `measured`, `estimated`, `stale`, or `unavailable` status per metric.

### 8. Calibrate route estimates to execution

The current estimator uses 1,536 output tokens for most stateless previews while
direct calls permit 3,072 and a recent OpenCode probe produced 2,692. Add
route-specific p50/p95 output estimates from settled usage, a hard maximum for
reservation, and a preview field showing the assumption. Under-estimation must
fail closed rather than create a false saving.

### 9. Separate capability premium from budget benefit

Repository implementation is capability-first, but current K3 scalar estimates
are negative versus Terra and the repository branch bypasses the scalar saving
floor. Add an explicit policy decision:

- `budget_benefit_required`: skip or return control to the host when the route
  fails the user's declared cash/quota/strong-pool objective;
- `capability_premium_allowed`: require user approval and report the premium,
  reason, and alternative host path.

Never describe a capability-required, more-expensive route as a cost-saving
delegation.

### 10. Make direct-model economics explicit

`auto_delegate` enforces the scalar stateless saving floor; public
`delegate(model=...)` currently does not. Choose one stable contract:

- direct model selection is an explicit `allow_economic_override` operation,
  requires a cost cap, and is telemetered; or
- direct delegation runs the same pool-aware economic planner and refuses work
  that fails the declared budget objective by default.

Do not depend on every host remembering to call `route_preview` first.

### 11. Correct the usefulness score

Stale economic evidence earns zero savings credit. Separate current offline
reliability/routing/context metrics from dated quality evidence and current
end-to-end savings. Prefer a scorecard over one blended headline. The current
8.6/10 must not be presented without its stale-config limitation.

### 12. Make routing pool-aware

The current 12-case corpus selects `0/6` mechanical tasks for delegation under
the scalar IDR planner, while capability-first repository/security routes still
run at negative scalar estimates. This defeats the core product goal and makes
manual direct delegation the only practical OpenCode path.

Add separate route fields for:

- incremental cash;
- quota-equivalent usage and remaining 5-hour/weekly/monthly headroom;
- strong-model capacity preserved;
- overage/balance fallback state;
- user-selected objective and reserve policy.

For an already-paid OpenCode pool with ample quota, simple verified work may be
eligible even when its quota-equivalent token rate exceeds Terra. It must still
pass quality, data, latency, and correction-cost gates. If the Go pool is near a
reserve or overage is enabled, the decision changes.

## Deterministic Validation

After implementation:

```bash
python -m pytest -m "not live and not network and not paid" -q
python tools/check.py
python tools/usefulness_benchmark.py --check
python tools/benchmark.py --check-baseline
python tests/test_resolve.py
```

Expected: all pass with credentials removed and outbound network blocked.

## Required Regression Tests

- Security verification cannot run on an ineligible cheap model.
- Explicit cheap batch verification is rejected/rerouted before a model call.
- Explicit route/model above cap stops before a model call.
- Concurrent threads and processes cannot reserve the same balance.
- Timeout produces pending liability, not immediately reusable funds.
- Duplicate settlement/usage events do not double-charge.
- Repository-qualified review remains host judgment.
- Default pytest cannot call a provider.
- Stale config/report hashes invalidate historical savings claims.
- A route estimate using an output assumption above its measured p95 or below its
  executable maximum fails closed.
- Capability premiums that fail the user's declared objective require explicit
  approval.
- Direct model calls require an explicit economic override or the normal saving
  gate.
- Stale cost evidence contributes zero to savings claims and release gates.
- The routing corpus includes prepaid/quota and metered cases; mechanical tasks
  can use an eligible prepaid pool without bypassing policy.

## Exit Gate

- Zero network or paid calls from the canonical offline command.
- Every known blocker above has a failing-then-passing regression.
- No unresolved critical/high capability or spending defect.
- Engineering spend recorded in the phase scorecard.

## Stop-Loss

Do not refactor the monolith during this phase beyond the smallest shared authorization/accounting seams needed for correctness. If fixes require broad architectural movement, add characterization tests first and defer extraction to later phases.
