# model-orchestra — swarm benchmark

**Historical snapshot:** This report records an earlier benchmark. It is not a
current model-quality or pricing guarantee.

Historical snapshot from a real run of 6 coding tasks, verified by executing the generated code against unit tests. The original generation timestamp and configuration hash were not recorded; rerun `benchmark.py` to produce a report with provenance.

- **BEFORE** — single model (`flash`), one attempt per task.
- **AFTER** — swarm `flash, mimo, ds-pro` run in parallel; task counts as solved if ANY worker's code passes the tests (verified best-of-N).

## Result

| | Pass rate | Tasks solved | Total latency |
|---|---|---|---|
| BEFORE (single) | 100% | 6/6 | 66.1s |
| AFTER (swarm)   | 100% | 6/6 | 205.7s |

**Reliability delta: +0 percentage points** (6/6 -> 6/6 solved). On this mechanical set a single cheap worker already scores 6/6, so delegating it is lossless — that is the point (see PROOF.md for the cost this saves). The swarm spends ~3x the tokens for best-of-N insurance; keep it for genuinely hard problems where one model is flaky, not for easy grunt work.

## Per-task

| Task | BEFORE | AFTER | solved by (swarm) |
|---|---|---|---|
| string_to_int_atoi | PASS 15.5s | PASS 26.9s | flash, mimo, ds-pro |
| coin_change | PASS 6.4s | PASS 12.6s | flash, mimo, ds-pro |
| word_break | PASS 14.8s | PASS 25.1s | flash, mimo, ds-pro |
| eval_rpn | PASS 9.3s | PASS 20.5s | flash, mimo, ds-pro |
| decode_ways | PASS 11.6s | PASS 41.3s | flash, mimo, ds-pro |
| spiral_order | PASS 8.6s | PASS 79.2s | flash, mimo, ds-pro |

## How to read this

A swarm does not make a weak model smart. It buys **reliability**: with N diverse cheap workers, the chance that at least one nails a tricky task is much higher than any single one — and because they run in parallel, you pay that in tokens, not wall-clock time. This is the cheap-model version of the Kimi K2 swarm: fan out, then keep the best.
