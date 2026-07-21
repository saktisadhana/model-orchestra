# model-orchestra — cost proof

**Historical snapshot:** This report records an earlier worker-cost run. It is not
current billing evidence or a statement of the current Zed host default.

Historical snapshot from a real run of 6 mechanical codegen tasks sent to the cheap worker `flash`. The original generation timestamp and configuration hash were not recorded. Token counts came from the live API, but model aliases and prices may have changed; rerun `proof.py` to produce provenance before using this for a current decision.

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
