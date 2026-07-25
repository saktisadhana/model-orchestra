# Phase 01 Stabilization Scorecard

## Identity

- Phase: `01-stabilize`
- Decision date: `2026-07-25`
- Decision: `GO` to `02-public-alpha.md`
- Worker route: K3 attempted through Model Orchestra; provider returned 503 `model_not_found` before tool execution
- Fallback: host-local implementation, once, as required by repository policy

## Spend

| Category | Cap | Actual | Notes |
|---|---:|---:|---|
| Paid evaluation | `0 IDR` | `0 IDR` | No live benchmark run |
| Phase worker allowance | `100 IDR-equivalent quota` | `0` after failed K3 route | No provider tool step or workspace change |
| Host implementation | existing session | not an invoice | Local fallback after recorded infrastructure failure |

## Outcome

| Metric | Required | Actual | Pass? |
|---|---:|---:|---|
| Default offline provider calls | 0 | 0; 3 live tests deselected | PASS |
| Capability-floor bypass regressions | all pass | pass | PASS |
| Explicit batch cap enforcement | before worker call | pass | PASS |
| Process-safe reservations | no double reservation | pass | PASS |
| Uncertain provider outcomes | pending liability | pass | PASS |
| Direct economic bypass | explicit audited override | pass | PASS |
| Repository review classification | host judgment | pass | PASS |
| Stale economic savings credit | zero | pass | PASS |
| Prepaid mechanical routing | pool-aware selection | 6 mechanical routes selected in corpus | PASS |
| Live verified budget benefit | not required in Phase 01 | unmeasured | DEFERRED |

## Deterministic Evidence

- `pytest`: `90 passed, 3 deselected` in the canonical offline suite.
- `python tools/usefulness_benchmark.py --check`: pass; routing `12/12`, context reduction `95.5%`, score `7.2/10`.
- `python tools/benchmark.py --check-baseline`: pass.
- `python tests/test_resolve.py`: pass; 19 workers, passthroughs, invalid inputs, and failover invariants.
- `python -m compileall -q server.py acp_router.py tools tests`: pass.
- Markdown validation: 13 files, no errors.
- Release readiness: no local runtime debris; existing source/generated/unrelated changes remain classified rather than removed.

## Implemented Controls

- Default pytest excludes `live`, `network`, and `paid` tests.
- Offline tests block external sockets while permitting loopback sockets required by Windows asyncio.
- JSON budget usage remains historical; SQLite reservations are the authority for new provider attempts.
- Reservations use process-safe transactions, idempotent settlement IDs, and `pending_liability` for uncertain outcomes.
- Retry and key-rotation attempts each reserve and settle independently.
- Agent loops use the same reservation lifecycle.
- Review-only repository language stays with host judgment.
- Explicit batch caps are enforced before worker execution.
- Direct model delegation requires the normal economic policy or an audited economic override.
- Batch verification applies capability floors before `_verify_with_tests`.
- Route previews expose objective, billing mode, quota-equivalent estimate, incremental cash, capacity-preservation estimate, and output bounds.
- Stale live economics earns zero savings credit.
- Runtime cache and ledger files are classified as local debris.

## Remaining Limitations

- No fresh paid A/B workload has proven total budget benefit.
- `7.2/10` is an offline diagnostic, not a universal quality or savings claim.
- Public packaging and clean external installation remain Phase 02 work.
- SQLite network-filesystem refusal, migration tooling, and richer pool-headroom reporting remain Phase 02 budget-engine work.
- Durable missions remain deferred until the core budget benefit is measured.

## Decision

Phase 01 is complete for deterministic stabilization. Proceed to Public Alpha only for packaging, public configuration, and host smoke tests. Do not claim public savings, renew subscriptions, or run paid evaluation without the Phase 03 approval and frozen workload described in the release pipeline.
