# model-orchestra — swarm benchmark

Real run, 6 coding tasks, results verified by executing the generated code against unit tests. Numbers are from live API calls — rerun to reproduce.

- **BEFORE** — single model (`cheap`), one attempt per task.
- **AFTER** — swarm `cheap, fast, coder` run in parallel; task counts as solved if ANY worker's code passes the tests (verified best-of-N).

## Result

| | Pass rate | Tasks solved | Total latency |
|---|---|---|---|
| BEFORE (single) | 83% | 5/6 | 29.8s |
| AFTER (swarm)   | 100% | 6/6 | 31.6s |

**Reliability delta: +17 percentage points** (5/6 -> 6/6 solved). Cost trade: swarm spends ~3x the tokens (N workers per task); latency stays close to a single call because workers run in parallel.

## Per-task

| Task | BEFORE | AFTER | solved by (swarm) |
|---|---|---|---|
| string_to_int_atoi | PASS 7.9s | PASS 5.9s | cheap, fast |
| coin_change | PASS 5.5s | PASS 5.1s | cheap, fast, coder |
| word_break | PASS 3.6s | PASS 3.8s | cheap, fast |
| eval_rpn | fail 5.4s | PASS 5.8s | cheap, fast, coder |
| decode_ways | PASS 6.3s | PASS 5.2s | cheap, fast |
| spiral_order | PASS 1.0s | PASS 5.8s | cheap, fast |

## How to read this

A swarm does not make a weak model smart. It buys **reliability**: with N diverse cheap workers, the chance that at least one nails a tricky task is much higher than any single one — and because they run in parallel, you pay that in tokens, not wall-clock time. This is the cheap-model version of the Kimi K2 swarm: fan out, then keep the best.
