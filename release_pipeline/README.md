# Model Orchestra Release Pipeline

## Purpose

Turn Model Orchestra from an advanced personal prototype into a dependable cost-saving coding plugin without spending indefinitely on orchestration itself.

The product exists to preserve a limited strong-model budget:

> Delegate simple, bounded, verifiable coding work to the cheapest eligible worker only when the complete delegated path is expected to cost less than direct host work.

"Eligible" includes capability, security, data policy, tool authority, verification, quota, latency, and availability. Cheap is never sufficient by itself.

## North-Star Metric

```text
verified budget benefit =
  strong-model capacity preserved
  - incremental cash outlay
  - quota scarcity cost
  - verification/retry cost
  - host re-ingestion cost
  - correction/redo cost
  - attributable orchestration overhead
```

Report three ledgers separately: `cash_outlay`, `quota_consumed`, and
`strong_model_capacity_preserved`. A subscription call is not free capacity:
it consumes quota. A quota-equivalent estimate is not a cash invoice. Unknown
cost or overage is `unavailable`, never zero.

Strict `verified savings` remains a supported objective for metered providers.
For prepaid/quota providers, the user chooses whether routing optimizes cash,
quota headroom, strong-model preservation, or a weighted policy.

A feature belongs in the default product only if it:

1. improves verified budget benefit;
2. protects a capability, data, or spending boundary;
3. reduces required user intervention; or
4. makes those outcomes measurable.

Everything else is experimental or deferred.

## Release Sequence

| Gate | Document | Outcome |
|---|---|---|
| Economic contract | [00-economic-contract.md](00-economic-contract.md) | Defines success, spend controls, and stop-loss rules |
| Phase 0 | [01-stabilize.md](01-stabilize.md) | Fixes unsafe tests and known enforcement defects |
| v0.2 alpha | [02-public-alpha.md](02-public-alpha.md) | Installable, provider-neutral MCP plugin |
| v0.2 stable | [03-budget-engine.md](03-budget-engine.md) | Transactional fixed/monitor budgets and honest accounting |
| v1 proof gate | [04-savings-proof.md](04-savings-proof.md) | Proves budget benefit on real work before expansion |
| v1 core | [05-public-release.md](05-public-release.md) | Hardened public product with a narrow stable surface |
| Optional | [06-durable-missions.md](06-durable-missions.md) | Long-running resumable missions, only after savings proof |

Release gates are cumulative. A later release inherits every earlier gate.

## Current Status

Phases 00 and 01 are complete. Phase 02 is complete as a locally verified
private-alpha candidate; public publication remains paused until the first
remote Windows/Linux CI matrix succeeds. Evidence:

- [Phase 00 baseline](evidence/00-economic-contract-baseline.md)
- [Phase 00 economic deep dive](evidence/00-economic-deep-dive.md)
- [Phase 01 stabilization scorecard](evidence/01-stabilize-scorecard.md)
- [Phase 02 public-alpha scorecard](evidence/02-public-alpha-scorecard.md)

No paid benchmark is authorized. Live budget benefit remains unproven.

## Execution Rule

Implement one phase at a time. Before entering the next phase:

1. complete its deterministic checks;
2. fill in [templates/phase-scorecard.md](templates/phase-scorecard.md);
3. record engineering and provider spend;
4. make an explicit `GO`, `REWORK`, `PAUSE`, or `STOP` decision;
5. archive evidence by commit/config/suite hash.

Do not run paid tests during ordinary development. Live evaluation requires a named budget, explicit approval, fixed task corpus, and abort thresholds.

## Current Baseline

Known deterministic evidence at planning time:

- 95 offline tests pass; 3 live/network/paid tests are deselected by default;
- routing baseline is 12/12;
- alias/failover invariants pass for 19 aliases;
- the usefulness tool reports 7.2/10 after stale economic evidence is assigned
  zero savings credit;
- synthetic host-context reduction is 95.5%.

These are smoke signals, not proof of total savings. Default `pytest` is now the
canonical offline release command; paid evaluation still requires explicit
selection and approval.

## Non-Goals Until Core Proof

Do not put these on the critical path:

- swarms or deep pipelines;
- broad provider coverage;
- adaptive autonomous budget changes;
- many intent-wrapper tools;
- universal sandbox infrastructure;
- cross-model conversation threading;
- durable multi-day missions;
- support claims for untested hosts;
- universal savings or quality claims.
