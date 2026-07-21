"""
Real COST proof for model-orchestra.

Runs a batch of representative grunt-work coding tasks through a cheap worker,
measures the REAL token usage returned by the live API, and prices that usage
against what the identical output would cost if the supervisor (Opus) had
generated it directly. Every number comes from a real call. Writes PROOF.md.

This is the core value prop: delegation moves bulk output tokens off the
$75/M supervisor and onto a $0.28/M worker, with no loss on mechanical work
(see REPORT.md — a cheap swarm holds 100% correctness on the coding set).

Run:  python proof.py
"""

import hashlib
import pathlib
from datetime import datetime, timezone

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import SESSION_USAGE, WORKERS, chat

WORKER = "flash"  # opencode-go/deepseek-v4-flash — a $0.28/M cheap worker

# USD per 1,000,000 tokens (input, output).
#   flash : opencode-go flat rate, same in/out.
#   opus  : Claude Opus 4.8 API list price.
PRICE = {
    "flash": (0.28, 0.28),
    "opus": (15.00, 75.00),
}
PRICE_SNAPSHOT_DATE = "2026-07-21"
PRICED_WORKER_MODEL = "opencode-go/deepseek-v4-flash"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def provenance() -> tuple[str, str]:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    config_hash = hashlib.sha256((ROOT / "config.json").read_bytes()).hexdigest()[:12]
    return generated, config_hash

# Legitimate delegation targets: mechanical/bulk codegen, the ~85% of work a
# supervisor should never spend $75/M output tokens writing itself.
TASKS = [
    "Write a Python dataclass `User` with fields id:int, name:str, email:str, "
    "active:bool=True and a classmethod from_row(row: tuple) building one.",
    "Write a Python function slugify(s) -> str: lowercase, strip, replace runs "
    "of non-alphanumeric with single hyphens, trim leading/trailing hyphens.",
    "Convert this to a Python TypedDict and a matching JSON schema dict: an order "
    "with id (int), items (list of {sku:str, qty:int}), total (float), paid (bool).",
    "Write a Python function chunked(seq, n) yielding successive n-sized lists "
    "from seq; the final chunk may be shorter. Include a docstring.",
    "Write argparse boilerplate for a CLI `mytool` with subcommands `add` (takes "
    "--name required, --tags nargs*) and `list` (takes --json flag).",
    "Write a Python retry decorator retry(times=3, delay=0.5) that re-calls the "
    "wrapped function on any exception, sleeping delay*2**i between attempts.",
]


def main():
    resolved_worker = WORKERS.get(WORKER, WORKER)
    if resolved_worker != PRICED_WORKER_MODEL:
        raise RuntimeError(
            f"Price snapshot targets {PRICED_WORKER_MODEL!r}, but {WORKER!r} now "
            f"resolves to {resolved_worker!r}. Verify pricing and update proof.py."
        )
    print(f"Running cost proof: {len(TASKS)} grunt tasks on '{WORKER}' (real API)...\n")
    for i, t in enumerate(TASKS, 1):
        out = chat(WORKER, t)
        print(f"  task {i}: {len(out):>5} chars generated")

    u = SESSION_USAGE
    inp, out = u["total_input_tokens"], u["total_output_tokens"]
    generated, config_hash = provenance()

    w_in, w_out = PRICE["flash"]
    o_in, o_out = PRICE["opus"]
    worker_cost = inp / 1e6 * w_in + out / 1e6 * w_out
    opus_cost = inp / 1e6 * o_in + out / 1e6 * o_out
    savings_pct = (1 - worker_cost / opus_cost) * 100
    factor = opus_cost / worker_cost

    lines = [
        "# model-orchestra — cost proof",
        "",
        f"Real run, {len(TASKS)} mechanical codegen tasks sent to the cheap worker "
        f"`{WORKER}`. Token counts below are returned by the live API - rerun to "
        "reproduce (small variance from model non-determinism).",
        "",
        f"- Generated UTC: `{generated}`",
        f"- config SHA-256: `{config_hash}`",
        f"- Worker model at generation: `{resolved_worker}`",
        f"- Pricing snapshot: `{PRICE_SNAPSHOT_DATE}` (manual assumptions in `proof.py`; verify before reuse)",
        "",
        "## Measured usage",
        "",
        "| | Tokens |",
        "|---|---|",
        f"| Input (prompts)  | {inp:,} |",
        f"| Output (code)    | {out:,} |",
        f"| API calls        | {u['calls']} |",
        "",
        "## Cost of this same work",
        "",
        "| Where the tokens ran | Rate (in / out per 1M) | Cost |",
        "|---|---|---|",
        f"| Worker (`{WORKER}`) | ${w_in:.2f} / ${w_out:.2f} | **${worker_cost:.4f}** |",
        f"| Supervisor (Opus 4.8), if it wrote it itself | ${o_in:.2f} / ${o_out:.2f} | ${opus_cost:.4f} |",
        "",
        f"**Delegating this grunt work cost {factor:.0f}x less "
        f"({savings_pct:.1f}% cheaper).** The supervisor's own spend is just the "
        "few-hundred-token delegate call, not the bulk generation above.",
        "",
        "## How to read this",
        "",
        "Mechanical codegen is lossless to delegate (REPORT.md: a cheap swarm holds "
        "100% correctness on the coding set). So every output token a worker writes "
        "instead of Opus is priced at $0.28/M instead of $75/M. The supervisor keeps "
        "only the reasoning it can't safely hand off — decomposition, final "
        "assembly, and all security/CTF/crypto analysis (floored to strong models "
        "in code).",
        "",
    ]
    (ROOT / "docs" / "PROOF.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWorker ${worker_cost:.4f}  vs  Opus ${opus_cost:.4f}"
          f"   ({factor:.0f}x cheaper, {savings_pct:.1f}% saved)   (wrote docs/PROOF.md)")


if __name__ == "__main__":
    main()
