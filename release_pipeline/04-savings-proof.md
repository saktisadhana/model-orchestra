# Phase 3: Prove Verified Savings

## Goal

Prove on the user's actual workload that Model Orchestra improves the user's
declared budget objective without degrading accepted outcomes.

## Why This Phase Is Mandatory

The existing offline score and context-reduction metric are not total-cost proof. No additional platform work is justified until the core economic claim survives a direct comparison.

## Instrumentation

Record per task:

- task class and route decision;
- considered/rejected candidates and reason;
- direct-host estimate and assumptions;
- worker/verification/retry usage;
- host-returned context;
- latency;
- deterministic checks;
- host correction/redo work;
- accepted/rejected outcome;
- provider failure category;
- pending and settled charges.
- subscription/quota pool and remaining headroom;
- strong-model capacity preserved.

Never retain prompts, source, secrets, or raw errors in telemetry. Link content-addressed local artifacts where evidence is needed.

## Evaluation Design

### Offline calibration

Use labeled routing cases and fixture repositories to catch regressions at zero provider cost.

### Paid A/B workload

Freeze a representative task set before spending:

- tiny/local tasks;
- mechanical generation;
- repository implementation;
- judgment/review;
- security-sensitive cases;
- provider/infrastructure failures.

Compare:

1. direct strong host;
2. Model Orchestra's default metered route;
3. Model Orchestra's default prepaid/quota route.

Use identical acceptance tests, repeated runs where nondeterminism matters, and a fixed paid-evaluation cap. Abort on infrastructure-wide failures.

### Two-week dogfood

Run normal work without manually selecting models. Record tasks where delegation
was skipped, preserved strong-model capacity, consumed subscription quota,
created incremental cash, required redo, or added unacceptable latency.

## Report

Use [templates/phase-scorecard.md](templates/phase-scorecard.md). Publish
correctness, cash outlay, quota consumption, strong-model capacity preserved,
latency, redo, and intervention together. Label direct-host comparisons as
estimates unless measured directly. Never add quota-equivalent usage to cash as
if it were an invoice.

## Go Gate

Suggested minimums:

- positive total verified budget benefit under the user's declared objective;
- at least 20% improvement in the user's declared objective, or the user's
  predeclared material threshold; strict cost reduction is reported separately;
- no unapproved metered overage, balance fallback, or subscription auto-reload;
- >=95% delegated outputs pass required checks;
- <5% delegated tasks require host redo;
- zero security/judgment downgrade;
- zero hard-budget overspend;
- <5% tasks require manual model choice;
- default routes that fail the declared budget objective are disabled.

## Rework/Pause/Stop

- **REWORK:** one route fails the declared objective but others are positive;
  disable it and retest.
- **PAUSE:** provider instability invalidates comparison or evaluation budget is exhausted.
- **STOP expansion:** total verified budget benefit is non-positive after bounded
  tuning.
- **KEEP personal core:** if budget benefit is positive for the author but public
  trials fail, maintain a focused personal plugin instead of building a platform.

## Cost Gate

Before each paid run, document maximum spend, expected information gained, abort conditions, and which decision the result will change. Never run a benchmark merely to improve a headline number.
