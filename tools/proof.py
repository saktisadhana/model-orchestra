"""Live cost proof for the current Zed Terra host.

Runs representative mechanical coding tasks through Flash, records actual API
usage, and reports two end-to-end delegation cases: returning all worker output
into Terra context and keeping artifacts on disk. Writes docs/PROOF.md.

Run: python tools/proof.py
"""

import hashlib
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server
from server import WORKERS, chat

WORKER = "flash"
HOST = "terra"
PRICE_SNAPSHOT_DATE = "2026-07-22"
PRICED_WORKER_MODEL = "opencode-go/deepseek-v4-flash"
PRICED_HOST_MODEL = "68886868/gpt-5.6-terra"
ROOT = pathlib.Path(__file__).resolve().parent.parent

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


def provenance() -> tuple[str, str]:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    config_hash = hashlib.sha256((ROOT / "config.json").read_bytes()).hexdigest()[:12]
    return generated, config_hash


def _saving(direct: float, delegated: float) -> tuple[float, float]:
    net = direct - delegated
    percent = net / direct * 100 if direct else 0.0
    return net, percent


def main() -> None:
    resolved_worker = WORKERS.get(WORKER, WORKER)
    resolved_host = WORKERS.get(HOST, HOST)
    if resolved_worker != PRICED_WORKER_MODEL or resolved_host != PRICED_HOST_MODEL:
        raise RuntimeError(
            "Priced aliases changed; verify provider pricing and update proof.py "
            f"(worker={resolved_worker!r}, host={resolved_host!r})."
        )

    print(f"Running cost proof: {len(TASKS)} mechanical tasks on {WORKER!r} (real API)...\n")
    outputs = []
    for index, task in enumerate(TASKS, 1):
        output = chat(WORKER, task)
        outputs.append(output)
        print(f"  task {index}: {len(output):>5} chars generated")

    usage = server._usage_snapshot()
    input_tokens = usage["total_input_tokens"]
    prompt_tokens = (
        input_tokens
        + usage.get("total_cached_tokens", 0)
        + usage.get("total_cache_write_tokens", 0)
    )
    output_tokens = usage["total_output_tokens"]
    returned_tokens = max(1, round(sum(len(output) for output in outputs) / server.CHARS_PER_TOKEN))
    worker_cost = server._usage_total_cost(usage)
    direct_host = server._precise_usage_cost(HOST, prompt_tokens, output_tokens)
    inline_reingestion = server._precise_usage_cost(HOST, returned_tokens, 0)
    inline_cost = worker_cost + inline_reingestion
    disk_cost = worker_cost
    inline_net, inline_percent = _saving(direct_host, inline_cost)
    disk_net, disk_percent = _saving(direct_host, disk_cost)
    generated, config_hash = provenance()

    worker_price = server.BUDGET_PRICES[WORKER]
    host_price = server.BUDGET_PRICES[HOST]
    lines = [
        "# model-orchestra - current-host cost proof",
        "",
        f"Real run of {len(TASKS)} mechanical code-generation tasks on `{WORKER}`. "
        "Worker token counts come from the live API. Direct-host and Terra "
        "re-ingestion costs are configured-rate estimates, not invoices.",
        "",
        f"- Generated UTC: `{generated}`",
        f"- config SHA-256: `{config_hash}`",
        f"- Worker: `{resolved_worker}`",
        f"- Current Zed host: `{resolved_host}`",
        f"- Pricing snapshot: `{PRICE_SNAPSHOT_DATE}` (configured IDR rates; verify before reuse)",
        "",
        "## Measured usage",
        "",
        "| | Tokens |",
        "|---|---:|",
        f"| Worker input | {input_tokens:,} |",
        f"| Worker output | {output_tokens:,} |",
        f"| Returned artifact estimate | {returned_tokens:,} |",
        f"| API calls | {usage['calls']} |",
        "",
        "## End-to-end cost",
        "",
        "| Path | Cost | Net saving vs direct Terra |",
        "|---|---:|---:|",
        f"| Direct Terra equivalent | {direct_host:.3f} IDR | baseline |",
        f"| Flash, full output returned inline | {inline_cost:.3f} IDR | "
        f"{inline_net:.3f} IDR ({inline_percent:.1f}%) |",
        f"| Flash, artifacts kept on disk | {disk_cost:.3f} IDR | "
        f"{disk_net:.3f} IDR ({disk_percent:.1f}%) |",
        "",
        "Configured rates per million tokens:",
        "",
        f"- Flash: {worker_price['input']:,} IDR input / {worker_price['output']:,} IDR output",
        f"- Terra: {host_price['input']:,} IDR input / {host_price['output']:,} IDR output",
        "",
        "## Interpretation",
        "",
        "The relevant comparison is the current Terra host, not historical Opus "
        "pricing. Flash output is only moderately cheaper than Terra output after "
        "Terra re-ingests inline artifacts. Manifest-first batching removes that "
        "return-context charge and preserves the larger saving. Multi-model recipes "
        "are reserved for capability needs because they generally do not reduce cost.",
        "",
    ]
    (ROOT / "docs" / "PROOF.md").write_text("\n".join(lines), encoding="utf-8")

    print(
        f"\nDirect Terra {direct_host:.3f} IDR; inline {inline_cost:.3f} IDR "
        f"({inline_percent:.1f}% saved); disk {disk_cost:.3f} IDR "
        f"({disk_percent:.1f}% saved). Wrote docs/PROOF.md"
    )


if __name__ == "__main__":
    main()
