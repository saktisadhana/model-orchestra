# Phase 00 Scorecard

## Identity

- Phase: `00-economic-contract`
- Decision date: `2026-07-25T02:43:52Z`
- Commit SHA: `643e93c211610f76a3e6cdb97b3810a1e5ad6fcd`
- Configuration SHA-256: `50ed3d96cec8ba3309727cbcfa7a69d74085606a146de021c7d6e351c92afa35`
- Test-set aggregate SHA-256: `4e4892d57ec1b9651566158a40dedb224be0b1e7b301f2ac8cea1560e81ddf5c`
- Decision: `GO` to `01-stabilize.md` only

## Spend

| Category | Cap | Actual configured estimate | Notes |
|---|---:|---:|---|
| Engineering host usage | existing session | 0 paid provider calls | Host work not treated as provider invoice |
| Engineering worker usage | 100 IDR-equivalent quota for Phase 01 | 13.176 IDR-equivalent quota | One bounded OpenCode Go `flash` probe in Phase 00 |
| Paid evaluation | 0 IDR | 0 IDR | No live benchmark authorized |
| K3 repository route | no retry authorized | infrastructure failure | 503 before workspace changes; liability not inferred as zero |

## Outcome

| Metric | Required | Actual | Pass? |
|---|---:|---:|---|
| Verified budget-benefit proof | not required in Phase 00 | unmeasured | N/A |
| Offline safety/ACP tests | pass | 77 passed | PASS |
| Routing baseline | pass | 12/12 | PASS |
| Alias/failover invariants | pass | 19/19 | PASS |
| Hard-budget overspend | 0 | no Phase 00 paid evaluation | PASS |
| Capability/data-policy violations | 0 | known blockers remain | FAIL for release; remediation authorized |
| OpenCode probe scalar quota comparison | informational | -68.3% | N/A; not a cash invoice |
| OpenCode monthly quota share | bounded | approximately 0.00134% | PASS |
| Incremental OpenCode cash | no overage | unknown; subscription already paid | UNKNOWN |

## Deterministic Evidence

- `python tools/usefulness_benchmark.py --check`: pass, 8.6/10; routing 12/12; synthetic context reduction 95.5%.
- `python tools/benchmark.py --check-baseline`: pass.
- `python tests/test_resolve.py`: pass, 19 aliases plus error/failover cases.
- `python -m pytest tests/test_safety.py tests/test_acp_router.py -q`: 77 passed.
- `python -m pytest --collect-only -q`: 83 collected, including live/provider-dependent quality tests.
- Evidence: `evidence/00-economic-contract-baseline.md`.

## Paid Evidence

- Approval/reference: bounded OpenCode Go probe only; no live benchmark approval.
- Maximum authorized evaluation spend: `0 IDR`.
- Frozen corpus: one concise Phase 00 baseline-analysis prompt.
- Abort condition: non-positive end-to-end economics; no retry after K3 503.
- Result: one `flash` call, 362 input / 2,692 output tokens; scalar
  quota-equivalent ceiling 14.589 IDR versus direct Terra estimate 8.670 IDR.
  OpenCode incremental cash was not measured; the subscription was prepaid.

## Decision

- Proven: deterministic baseline is healthy enough to begin stabilization; OpenCode Go is available through Model Orchestra alias `flash`.
- Failed: the one-dimensional planner cannot represent prepaid quota economics;
  it auto-delegates none of six mechanical routing cases. The probe was too
  verbose, and route classification selected the wrong repository-edit path for
  a documentation analysis task.
- Disabled: paid evaluation, public release, durable missions, adaptive budgets, extra tools/providers.
- Next phase allowed: `01-stabilize.md`, with zero paid-evaluation budget and a
  100 IDR-equivalent engineering-worker quota ceiling. Route previews are
  required, but their scalar saving field is informational until billing modes
  and pool-aware policy are implemented.
