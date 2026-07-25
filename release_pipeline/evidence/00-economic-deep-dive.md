# Phase 00 Economic Deep Dive

## Identity

- Analysis time: `2026-07-25T03:41:26Z`
- Current config SHA-256: `50ed3d96cec8ba3309727cbcfa7a69d74085606a146de021c7d6e351c92afa35`
- OpenCode Go documentation source SHA-256: `c18867f6cea21e7baf3d6077218c1fa65f832068365084326692413a363221a7`
- Network model calls during this analysis: `0` after the single Phase 00 probe
- Result: the current one-dimensional IDR cost model is not adequate for the user's subscription-based budget problem

## Executive Finding

The initial OpenCode probe produced two apparently contradictory results:

1. The current Model Orchestra estimator says the delegated path was `-68.3%` versus direct Terra.
2. OpenCode Go is a prepaid `$10/month` subscription with quota windows, so the call did not necessarily create a new cash charge.

Both statements can be true. The first measures configured quota-equivalent consumption as if it were a cash expense. The second describes marginal cash after the subscription is already paid. The plugin currently collapses these into one IDR number and calls it cost.

For this user's goal, the system must optimize three separate ledgers:

```text
cash outlay
quota/scarcity consumption
strong-model budget preserved
```

A route is desirable when it preserves scarce strong-model capacity and accepted quality, while staying within the OpenCode quota and not creating overage charges. It does not need a lower quota-equivalent token price than Terra on every call.

## OpenCode Go Contract

The official OpenCode Go documentation currently states:

- `$5` for the first month, then `$10/month`;
- rolling 5-hour usage allowance equivalent to `$12`;
- weekly allowance equivalent to `$30`;
- monthly allowance equivalent to `$60`;
- limits are quota expressed in dollar-equivalent usage, not per-call invoices;
- usage beyond limits can fall back to a separate Zen cash balance only when `Use balance` is enabled;
- DeepSeek V4 Flash is valued at `$0.14/M` input, `$0.28/M` output, and `$0.0028/M` cached read for quota accounting;
- expected DeepSeek V4 Flash capacity is roughly 31,650 typical requests per 5 hours, 79,050 per week, and 158,150 per month.

This means OpenCode Go requires both:

- a fixed subscription cash ledger;
- a rolling quota-equivalent ledger.

If Zen balance fallback is enabled, it also needs a metered-overage cash ledger and explicit approval/limit.

## Probe Reinterpreted

Observed Phase 00 probe:

| Quantity | Value |
|---|---:|
| Worker usage | 362 input, 2,692 output tokens |
| OpenCode quota-equivalent estimate | 13.176 IDR |
| Terra re-ingestion ceiling | 1.413 IDR |
| Direct Terra equivalent | 8.670 IDR |
| Scalar estimator comparison | -68.3% |
| OpenCode monthly quota share | approximately 0.00134% |

If the Go subscription was already paid and no overage was triggered:

- marginal OpenCode cash charge for the worker call is approximately zero;
- host re-ingestion remains approximately 1.413 IDR-equivalent;
- direct Terra capacity preserved is approximately 7.257 IDR-equivalent;
- the call used a very small amount of OpenCode quota;
- latency was 31.531 seconds and the output was unnecessarily long.

Therefore the correct conclusion is not simply "delegation lost money." It is:

> The current scalar cost metric rejected a call that may have been rational for preserving Terra capacity, but the call was poorly classified, too verbose, and not measured in the correct economic units.

## Current Ledger Analysis

The local budget ledger contains 308 OpenCode Go calls:

| Aggregate | Value |
|---|---:|
| Fresh input | 74,904 tokens |
| Cached input | 19,968 tokens |
| Output | 412,135 tokens |
| Quota-equivalent consumption | 4,995 IDR |
| Current monthly Go allowance equivalent | 982,800 IDR at configured FX |
| Approximate monthly quota used | 0.51% |
| Peak rolling 5-hour usage | 2,910 IDR-equivalent |
| Configured 5-hour guard | 3,100 IDR-equivalent |
| Official 5-hour Go allowance equivalent | 196,560 IDR |

The current local Go guard is far more restrictive than the official plan:

- configured monthly limit is about 18.8% of the official quota equivalent;
- configured 5-hour limit is about 1.6% of the official quota equivalent;
- observed peak use nearly hit the configured guard while using only a small fraction of official Go capacity.

This may be an intentional personal reserve, but the configuration describes it as a provider envelope rather than a user-selected reserve. Those are different concepts and must be labeled separately.

## Strong-Model Capacity Preserved

Repricing all 308 OpenCode calls as if Terra had generated the same token volume gives a configured direct-Terra equivalent of approximately `1,338.608 IDR`.

With all OpenCode output returned inline, estimated Terra re-ingestion is approximately `216.371 IDR`, leaving about `1,122.237 IDR` of Terra capacity preserved. Artifact-first handling would preserve up to approximately `1,338.608 IDR` under this equivalence assumption.

Limitations:

- this is not Terra invoice data;
- Terra might use a different number of tokens or produce different quality;
- correction/redo cost is not captured;
- the 308 calls include different task types and models;
- the subscription fee is not recovered by this small observed volume when converted to IDR, but the user's objective may be quota preservation rather than strict subscription ROI.

At the observed average, the estimated Terra capacity preserved per OpenCode call is approximately `3.644 IDR`. Roughly 44,955 similar calls would be needed to offset a `$10` subscription purely on this token-equivalence estimate. That makes subscription ROI a poor per-task gate for a lightly used account. The useful gate is whether an already-paid Go pool can absorb verified simple work and protect a more constrained Terra pool.

## Output Calibration

The current preview default is 1,536 output tokens. Actual ledger distributions show:

| Model | Calls | Output p50 | Output p90 | Output p95 | Above 1,536 |
|---|---:|---:|---:|---:|---:|
| Flash | 205 | 1,024 | 3,072 | 3,072 | 31.7% |
| MiMo | 53 | 885 | 2,563 | 3,072 | 18.9% |
| DeepSeek Pro | 30 | 1,072 | 2,185 | 3,072 | 30.0% |
| All OpenCode | 308 | 1,024 | 3,072 | 3,072 | 29.5% |

The 1,536 estimate is near the historical mean but not conservative. About 30% of calls exceed it, and p95 equals the 3,072 execution cap for the main workers. Use p50 for expected economics, p95 or hard maximum for reservation, and report both.

## Routing Findings

### Classification

A request to review/explain repository architecture was classified as repository implementation because generic repository terms are checked before judgment. This selected K3 and required an agent when the correct route was host judgment.

### Capability-first bypass

Repository implementation selects K3 whenever it is under an explicit cap, even when it misses the minimum savings floor. Capability requirement and economic approval are conflated.

### Direct-model bypass

`auto_delegate` enforces stateless savings. Public `delegate(model=...)` enforces capability floors but no economic decision. That is why the explicit Flash probe ran even though a preview would have skipped it.

### Static tier mismatch

`flash` and `mimo` are labeled `cheap`, but that bucket means capability/intent, not current cash or quota economics. Price, billing mode, quota scarcity, and capability must be separate dimensions.

## Stale Evidence and Score Inflation

Historical Single Flash evidence was generated under config hash `82042d8ad171`; the current config hash is `50ed3d96cec8`. Repricing the exact historical usage under current scalar rates changes:

- Single Flash: `+18.285%` to approximately `-62.969%`;
- swarm: `-289.807%` to approximately `-193.549%`.

The historical 6/6 correctness result remains dated quality evidence. Its savings number is stale.

The offline 8.6/10 score still gives stale live evidence only a 10% discount, awarding 1.65 savings points, 1.48 quality points, and 0.2 observability points. Conservative interpretations are:

- approximately `5.3/10` for current fully offline evidence only;
- approximately `6.8/10` when retaining historical correctness but assigning stale savings zero credit.

Do not replace 8.6 with either diagnostic number as a new marketing score. Remove the composite headline or show independent current/stale dimensions.

## Revised Economic Decision Model

For each route, report:

```text
cash_now:
  subscription_fee_attributable
  metered_charge
  overage_charge

quota:
  quota_equivalent_used
  remaining_5h / weekly / monthly
  reserve pressure

strong_pool:
  estimated host capacity avoided
  host re-ingestion
  correction/redo reserve

outcome:
  verification status
  latency
  user intervention
```

Route policy:

1. Apply capability, data, and tool-authority floors.
2. Refuse unknown/unbounded cash overage.
3. Protect user-defined reserves in each pool.
4. If a prepaid worker pool has capacity, choose the eligible route that best preserves the scarce strong pool per accepted result.
5. If the worker pool is near quota, compare scarcity-adjusted cost and latency.
6. Never call a negative-quality/redo route cheap merely because marginal cash is zero.
7. Require explicit approval for capability premiums and for metered overage fallback.

## Phase 01 Priority Changes

1. Make default tests offline and fix boolean tests.
2. Fix capability-floor bypasses and explicit batch caps.
3. Fix judgment-versus-repository classification.
4. Add billing modes: subscription, quota-equivalent, metered, unknown.
5. Separate provider allowance from user reserve.
6. Add current/stale evidence invalidation.
7. Add expected (p50) and reservation (p95/max) estimates.
8. Make direct-model selection an explicit economic override or apply normal policy.
9. Separate cost-saving routes from approved capability premiums.
10. Remove stale cost evidence from the 8.6 score.

## Decision

Continue to deterministic stabilization. Do not run another paid model benchmark yet.

OpenCode Go should remain a preferred candidate for simple work because it can preserve the limited Terra pool using an already-paid subscription. It should not be represented as per-call cash cost, and it should not be used blindly: quality, quota scarcity, output size, latency, and correction cost still matter.
