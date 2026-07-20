# model-orchestra — cost proof

Real run, 6 mechanical codegen tasks sent to the cheap worker `flash`. Token counts below are returned by the live API — rerun to reproduce (small variance from model non-determinism).

## Measured usage

| | Tokens |
|---|---|
| Input (prompts)  | 727 |
| Output (code)    | 8,153 |
| API calls        | 6 |

## Cost of this same work

| Where the tokens ran | Rate (in / out per 1M) | Cost |
|---|---|---|
| Worker (`flash`) | $0.28 / $0.28 | **$0.0025** |
| Supervisor (Opus 4.8), if it wrote it itself | $15.00 / $75.00 | $0.6224 |

**Delegating this grunt work cost 250x less (99.6% cheaper).** The supervisor's own spend is just the few-hundred-token delegate call, not the bulk generation above.

## How to read this

Mechanical codegen is lossless to delegate (REPORT.md: a cheap swarm holds 100% correctness on the coding set). So every output token a worker writes instead of Opus is priced at $0.28/M instead of $75/M. The supervisor keeps only the reasoning it can't safely hand off — decomposition, final assembly, and all security/CTF/crypto analysis (floored to strong models in code).
