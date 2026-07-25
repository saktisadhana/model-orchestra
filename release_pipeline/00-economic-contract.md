# Economic Contract and Stop-Loss Policy

## Goal

Prevent additional project spending unless each increment protects or measurably improves the core cost-saving workflow.

## User Outcome

The user asks the host normally. Model Orchestra should automatically:

1. keep tiny local work and judgment with the host;
2. route simple work to a cheaper eligible worker;
3. reserve money/quota before execution;
4. verify proportionally to risk;
5. return compact artifacts or manifests;
6. fall back to the host once when delegation fails;
7. report whether delegation actually saved money.

The user should not manually choose models for routine work.

## Product Scorecard

A 10/10 personal plugin requires all of these over a representative two-week workload:

| Metric | 10/10 threshold |
|---|---:|
| Hard budget overspend | 0 |
| Security/judgment downgrade | 0 |
| Delegated outputs passing required checks | >=95% |
| Delegated tasks requiring host redo | <5% |
| Default routes with positive verified budget benefit | 100% |
| Infrastructure failure retried after permanent classification | 0 |
| Context-overflow retries | 0 |
| Tasks requiring manual model choice | <5% |
| Estimate error | within configured safety reserve |
| Total verified budget benefit | positive and material to the user |

The user chooses what "material" means before live evaluation. Suggested initial gate: at least 20% lower total cost than direct-host execution without a statistically meaningful correctness regression.

## Engineering Spend Envelope

Every phase receives two budgets:

- **Engineering budget:** host/worker tokens and paid calls used to build the phase.
- **Evaluation budget:** paid calls used only on the frozen benchmark/workload.

Rules:

1. Default development and CI spend zero provider money.
2. Paid evaluation has a fixed maximum before it starts.
3. Stop at the first infrastructure-wide failure; do not rotate models hoping for a different result.
4. Do not spend more validating a feature than its plausible 90-day savings unless it protects safety or budget enforcement.
5. After two failed implementation attempts on the same acceptance test, pause for root-cause review.
6. Sunk cost is never evidence to continue.

## Go/No-Go Decisions

- **GO:** all release blockers pass; expected benefit exceeds remaining implementation/evaluation cost.
- **REWORK:** core outcome remains plausible and failure has a bounded, testable cause.
- **PAUSE:** evidence is incomplete, provider conditions are unstable, or spend envelope is exhausted.
- **STOP:** no positive verified budget benefit, correction cost erases the
  benefit, users still manage routing manually, or complexity exceeds the
  personal benefit.

## Required Economic Model

Before a route can claim budget benefit, include:

```text
strong-model capacity preserved
- incremental cash outlay
- quota scarcity cost
- retries and verification
- host returned context
- correction probability * correction cost
- orchestration overhead
```

Track `cash_outlay`, `quota_consumed`, and `strong_model_capacity_preserved`
separately. Provider price tables are versioned inputs, not truth. Subscription
fees are account-level cash; per-call usage consumes quota; optional overage is
metered cash. Unknown/timed-out outcomes remain pending liabilities until
settled; they are not released as free capacity.

For metered providers, strict verified savings can be the objective. For
prepaid/quota providers, the user explicitly chooses cash, quota headroom,
strong-model preservation, or a weighted policy. Changing billing mode
invalidates economic evidence.

## Change Admission Test

For every proposed feature, answer:

1. Which scorecard metric changes?
2. What is the cheapest experiment proving it?
3. What is the maximum implementation/evaluation spend?
4. What deterministic regression protects it?
5. What result kills or defers it?

A feature without concrete answers does not enter the release pipeline.
