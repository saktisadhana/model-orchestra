# Phase 00 Economic Contract Baseline

## Identity

- Decision time: `2026-07-25T02:43:52Z`
- Repository commit: `643e93c211610f76a3e6cdb97b3810a1e5ad6fcd`
- Configuration SHA-256: `50ed3d96cec8ba3309727cbcfa7a69d74085606a146de021c7d6e351c92afa35`
- Routing baseline SHA-256: `6f4c9b1aed24c3c461760d3bca9620ecfa22a331841284c575b5aeacd3052e39`
- Original economic contract SHA-256: `04fdf37ab4ff2eb67242548b8081331209b4f9a72c7922ca631b42e1c1e247b2`
- Pool-aware amended contract SHA-256: `fe4f624e964d37d23bc09ab078b8a37fc8e2395d6390a1ce5a1158bed1b5ce68`
- Deterministic test-set aggregate SHA-256: `4e4892d57ec1b9651566158a40dedb224be0b1e7b301f2ac8cea1560e81ddf5c`
- Decision: `GO` to Phase 01 deterministic stabilization only

This record uses configured cost estimates, not provider invoices. It does not authorize public release, live benchmarking, or a universal savings claim.

## North-Star Contract

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

A default route is eligible for release only when its complete path has positive
verified budget benefit under the user's declared objective while satisfying
capability, security, data, verification, quota, latency, and availability
policy. Metered-provider savings remain a separate reported metric.

## 10/10 Thresholds

| Metric | Required |
|---|---:|
| Hard-budget overspend | 0 |
| Security or judgment downgrade | 0 |
| Delegated outputs passing required checks | >=95% |
| Delegated tasks requiring host redo | <5% |
| Enabled default routes with positive verified budget benefit | 100% |
| Retry after permanent/context-overflow classification | 0 |
| Tasks requiring manual model selection | <5% |
| Material total cost reduction | provisionally >=20% |

The 20% material-benefit threshold is provisional until the user approves the
paid Phase 03 evaluation design and declares the optimized objective. It is not
evidence that current benefit meets 20%.

## Verified Offline Evidence

| Check | Result |
|---|---|
| Isolated safety and ACP tests | `77 passed in 5.41s` |
| Routing baseline | `12/12`, baseline matches suite and resolved models |
| Alias and failover invariants | all 19 workers plus passthrough/error cases pass |
| Offline usefulness | `8.6/10` |
| Synthetic host-context reduction | `95.5%` |
| Default pytest collection | 83 tests, including live/provider-dependent quality tests |

Commands executed:

```bash
python tools/usefulness_benchmark.py --check
python tools/benchmark.py --check-baseline
python tests/test_resolve.py
python -m pytest tests/test_safety.py tests/test_acp_router.py -q
python -m pytest --collect-only -q
```

The usefulness and context-reduction numbers are smoke evidence. They do not
measure total verified budget benefit, host correction cost, or provider
invoices.

## Model Orchestra / OpenCode Go Probe

One bounded documentation-analysis call was explicitly delegated through Model Orchestra to OpenCode Go alias `flash`.

| Measurement | Result |
|---|---:|
| Calls | 1 |
| Worker tokens | 362 input, 2,692 output |
| OpenCode quota-equivalent estimate | 13.176 IDR |
| Host re-ingestion ceiling | 1.413 IDR |
| Scalar quota-equivalent end-to-end estimate | 14.589 IDR |
| Direct Terra equivalent | 8.670 IDR |
| Scalar quota-equivalent difference | -5.919 IDR (-68.3%) |
| Latency | 31.531 seconds |
| Returned text | 3,412 characters |

Conclusion under the scalar quota-equivalent metric: this small documentation task
was not a quota-efficient delegation against Terra. The worker also returned too
much text. This is not, by itself, a cash loss: OpenCode Go is a prepaid
subscription with included quota. The decision depends on whether preserving
Terra capacity is more valuable than consuming Go quota and whether the Go quota
is near exhaustion. OpenCode Go remains a candidate for bounded mechanical work,
but its billing mode must be modeled explicitly.

### Provider-price analysis

The configured OpenCode Go aliases have higher **quota-equivalent token rates**
than the configured Terra comparison for several models:

| Alias | Input price / Terra | Output price / Terra |
|---|---:|---:|
| `flash` / `mimo` | 4.37x | 1.46x |
| `ds-pro` / `mimo-pro` | 13.57x | 4.52x |
| `k26` / `k27-oc` | 29.64x | 20.80x |
| `glm` | 43.68x | 22.88x |
| `k27` gateway | 0.59x | 0.47x |
| `grok` gateway | 0.16x | 0.08x |

At 1,000 input / 512 output tokens, including the current 5% reserve and
host-returned output estimate, `flash` consumes 5.1419 IDR-equivalent Go quota
versus 2.1378 IDR-equivalent Terra usage. `k27` consumes 1.3856 IDR-equivalent
gateway usage. This establishes an important distinction: OpenCode is a
provider/API route with a subscription/quota billing mode, not automatically a
cash-cost tier. The public product must rank routes by billing mode, current
user-owned reserves, quota pressure, quality, and settled outcomes, not by the
provider name or the `cheap` configuration bucket.

The current `flash` values match the official OpenCode Go documentation's
`$0.14/M` input and `$0.28/M` output rates after the configured `16,380 IDR/USD`
conversion. The official page is `https://opencode.ai/docs/go/` and says Go is
`$10/month` after its introductory month with 5-hour, weekly, and monthly usage
limits. The local rates therefore appear to be quota accounting, not an
incorrect cash invoice table.

The remaining question is objective selection: cash minimization, quota
minimization, strong-model capacity preservation, or a weighted combination.
Do not change the OpenCode rates from this analysis alone. Add billing mode and
pool semantics before comparing routes in Phase 03.

### Evidence freshness finding

The historical artifacts are not on the current configuration:

- `docs/REPORT.json` was generated with config hash `82042d8ad171`;
- `docs/PROOF.md` was generated with config hash `7a498515849d`;
- current working-tree `config.json` hash is `50ed3d96cec8`;
- `docs/USEFULNESS_BENCHMARK.json` reports `config_matches: false` for its live
  evidence.

Therefore the historical 18.3% Single Flash result is not valid evidence for
the current price table. It remains historical evidence only. A report must be
invalidated whenever provider/model mapping, price, host comparison, token
budget, or task suite changes; the report must carry the exact full config and
suite hashes, not only a short display hash.

A route preview for the same kind of documentation analysis also misclassified it as repository implementation and selected K3 despite an estimated `-367.2%` saving. That is direct evidence for the Phase 01 review-versus-edit routing fix and for checking economics before any worker call.

## Route-Preview Matrix

Read-only previews produced these current outcomes:

| Task shape | Classification/route | Estimated result |
|---|---|---|
| One-file typo | host-local | correctly skipped delegation |
| Small function with caller tests | mechanical; `flash`/`draft-refine` candidates | skipped; scalar `speed-run` estimate -99.8% |
| Repository architecture explanation | repository-edit/K3 | incorrectly classified; scalar estimate -366.5% |
| Implement `src/parser.py` | repository-edit/K3 | capability-first; scalar estimate -366.4% |
| Security audit | Sol-only | correctly protected; scalar estimate -126.6% |
| Phase 00 checklist analysis | stateless candidates | skipped; scalar `speed-run` estimate -99.8% |

The previews correctly refuse many uneconomical stateless routes. Repository
implementation is an exception: capability-first selection currently bypasses
the configured minimum-saving floor when no explicit cap blocks it. That is a
policy conflict, not merely a price issue. The Phase 01 fix must decide whether
repository work is allowed to delegate at a documented negative saving, or
whether it should return control to the host when the complete route is more
expensive. The default economic contract says the latter.

## Analysis Priorities

Phase 01 must address these before any new paid probe:

1. **Freshness gate:** invalidate reports whenever config, prices, model mapping,
   token limits, or corpus changes.
2. **Estimate calibration:** use route-specific output distributions or a
   defensible maximum, and reconcile preview assumptions with actual execution
   limits. A 1,536-token estimate is not conservative when 3,072 is allowed.
3. **Capability premium policy:** repository work may be capability-required,
   but it must either clear the savings floor or be explicitly labeled a
   user-approved quality/latency premium. It must not silently appear as a
   cost-saving route.
4. **Economic tier integrity:** replace the static `cheap` label with measured
   price/capability metadata. `flash` is currently in the cheap bucket but is
   more expensive than Terra per token under the current table.
5. **Direct-tool economics:** `auto_delegate` applies the stateless savings floor,
   but public `delegate(model=...)` does not. That is why the explicit `flash`
   probe executed despite negative preview economics. Either make direct model
   selection an explicitly named/audited economic override, or require preview
   and cap enforcement before the call.

## Corrected Usefulness Interpretation

The reported 8.6/10 usefulness score is not valid as a current economic score.
Its implementation gives stale live evidence only a 10% provenance discount,
so the old 18.3% savings result still earns:

- 1.65/2.5 points for measured savings;
- 1.48/2.0 points for quality confidence;
- 0.2 observability points for live evidence.

Repricing the historical six-task usage under current configured rates changes
Single Flash from `+18.285%` to approximately `-62.969%`; all six tasks remain
correct in the historical run. The swarm recalculates to approximately
`-193.549%`.

A conservative current-config interpretation is:

- about **5.3/10** from fully offline/current evidence only;
- about **6.8/10** if the stale 6/6 correctness result is retained as historical
  quality evidence but stale savings receives zero credit.

These are diagnostic recalculations, not a replacement public score. Phase 01
should remove the single composite headline or make stale cost evidence worth
zero. Correctness can remain dated evidence when model/suite identity matches,
but economics must match current prices, billing mode, host comparison, token
limits, and config.

## Known Release Blockers

1. Default pytest can collect paid/live provider tests.
2. Several quality tests return booleans instead of asserting.
3. `delegate_verified` and explicit-model batch verification can bypass capability floors.
4. Explicit batch model/route cost caps are parsed but not enforced.
5. Budget check/record is not transactional across concurrent threads/processes.
6. Timed-out or uncertain calls lack a pending-liability settlement contract.
7. Repository-qualified judgment can be misrouted as repository editing.
8. Public packaging, CI, and provider-neutral configuration are absent.
9. Historical savings reports are stale against the current configuration.
10. Route estimates assume 1,536 output tokens for stateless work while direct
    calls allow up to 3,072 and the OpenCode probe used 2,692 output tokens.
11. Direct `delegate(model=...)` bypasses the stateless savings decision unless
    the host voluntarily previews first.
12. The 8.6/10 score still awards substantial points to stale cost evidence.
13. The current routing corpus auto-delegates `0/6` mechanical cases under the
    scalar planner, so the normal automatic path does not currently achieve the
    project's core OpenCode delegation goal.

## Spending Envelope

### Phase 00 actual

- Paid benchmark/evaluation budget: `0 IDR`.
- OpenCode Go probe: `13.176 IDR` configured quota-equivalent usage estimate;
  actual incremental cash is unknown without subscription/overage state.
- OpenCode Go scalar quota-equivalent end-to-end ceiling: `14.589 IDR`.
- K3 infrastructure failures reported no measured token usage; they are operational failures, not proof of zero provider liability.

### Phase 01 authorization

- Paid benchmark/evaluation budget: `0 IDR`.
- Cumulative configured worker-estimate ceiling: `100 IDR-equivalent quota`.
- Per-call explicit cap: `20 IDR-equivalent quota` with no metered overage.
- Worker call requires route preview. Until pool-aware routing exists, the host
  must inspect cash, quota headroom, strong-pool preservation, quality, and
  overage state rather than relying on the scalar saving field alone.
- Stop after the first infrastructure-wide/permanent provider failure.
- Security policy, authorization, and budget-accounting fixes remain host-reviewed and cannot be downgraded for savings.
- OpenCode Go may handle bounded mechanical subtasks only; request compact structured output and verify locally.
- Do not treat quota-equivalent usage as a cash invoice. Record subscription
  fee, quota consumption, overage cash, and strong-model capacity preserved as
  separate fields.

## Unknowns Deferred to Phase 03

- Actual direct-host usage for equivalent tasks.
- Actual provider invoices and subscription/quota opportunity cost.
- Host correction and redo cost.
- Budget-benefit distribution across the user's real workload.
- Estimate calibration versus settled charges.
- Manual model-selection rate during normal work.
- Whether the provisional 20% material threshold is achievable.

## Stop-Loss

Pause immediately if:

- a hard budget can be exceeded;
- protected work reaches an ineligible model;
- a permanent/context-overflow failure is retried;
- an uncertain provider outcome is treated as free capacity;
- the Phase 01 worker ceiling is exhausted;
- a proposed worker route fails the declared budget objective or has unknown
  overage/cash exposure;
- implementation requires paid live tests before deterministic blockers are fixed.

## Decision

`GO` to `release_pipeline/01-stabilize.md` with the spending envelope above.

This is not a `GO` for public release, paid benchmarking, adaptive budgets, durable missions, additional providers, or additional MCP tools.
