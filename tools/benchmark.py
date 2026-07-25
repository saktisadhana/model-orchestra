"""
Real before/after benchmark for model-orchestra.

BEFORE : one tiny model attempts each coding task once (the naive way).
AFTER  : the same task fans out to N tiny models in PARALLEL; the answer counts
         as solved if ANY worker's code passes the unit tests (verified best-of-N,
         the Kimi-swarm 'parallel then merge' idea with functional verification).

Every number here comes from real API calls + real code execution. Writes
REPORT.md and REPORT.json, then compares correctness with a checked-in baseline.

Run:  python tools/benchmark.py
      python tools/benchmark.py --check-baseline  # no API calls
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server
from server import chat, resolve  # reuses the same providers/keys as the MCP server

SINGLE = "flash"                        # BEFORE: one cheap worker
SWARM = ["flash", "mimo", "ds-pro"]     # AFTER: swarm of 3 cheap coders, in parallel
ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "tools" / "benchmark_baseline.json"
JSON_REPORT_PATH = ROOT / "docs" / "REPORT.json"
MARKDOWN_REPORT_PATH = ROOT / "docs" / "REPORT.md"


class BenchmarkInfrastructureError(RuntimeError):
    """A provider failure that makes benchmark correctness unmeasurable."""


def provenance() -> tuple[str, str]:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    config_hash = hashlib.sha256((ROOT / "config.json").read_bytes()).hexdigest()[:12]
    return generated, config_hash

# Each task: a function to write + asserts that must pass. Weak models fail some
# of these alone; the point is to measure how much a swarm recovers.
TASKS = [
    ("string_to_int_atoi",
     "Write a Python function `my_atoi(s)` implementing LeetCode string-to-integer: skip "
     "leading spaces, optional +/- sign, read digits until a non-digit, ignore the rest, "
     "and CLAMP the result to the signed 32-bit range [-2**31, 2**31-1].",
     "assert my_atoi('42')==42 and my_atoi('   -42')==-42 and my_atoi('4193 with words')==4193 "
     "and my_atoi('words and 987')==0 and my_atoi('-91283472332')==-2147483648 and my_atoi('+1')==1"),
    ("coin_change",
     "Write a Python function `coin_change(coins, amount)` returning the fewest coins that sum "
     "to amount, or -1 if impossible.",
     "assert coin_change([1,2,5],11)==3 and coin_change([2],3)==-1 and coin_change([1],0)==0 "
     "and coin_change([186,419,83,408],6249)==20"),
    ("word_break",
     "Write a Python function `word_break(s, words)` returning True iff s can be segmented into a "
     "space-separated sequence of one or more words from the list `words`.",
     "assert word_break('leetcode',['leet','code'])==True and word_break('applepenapple',['apple','pen'])==True "
     "and word_break('catsandog',['cats','dog','sand','and','cat'])==False"),
    ("eval_rpn",
     "Write a Python function `eval_rpn(tokens)` evaluating a Reverse Polish Notation expression "
     "(operators + - * /). Integer division must TRUNCATE TOWARD ZERO.",
     "assert eval_rpn(['2','1','+','3','*'])==9 and eval_rpn(['4','13','5','/','+'])==6 "
     "and eval_rpn(['10','6','9','3','+','-11','*','/','*','17','+','5','+'])==22"),
    ("decode_ways",
     "Write a Python function `num_decodings(s)` counting how many ways a digit string decodes to "
     "letters where 1->A ... 26->Z. A leading or standalone '0' cannot be decoded.",
     "assert num_decodings('12')==2 and num_decodings('226')==3 and num_decodings('06')==0 "
     "and num_decodings('0')==0 and num_decodings('10')==1"),
    ("spiral_order",
     "Write a Python function `spiral(matrix)` returning all elements of the 2D list in spiral "
     "(clockwise) order as a flat list.",
     "assert spiral([[1,2,3],[4,5,6],[7,8,9]])==[1,2,3,6,9,8,7,4,5] "
     "and spiral([[1,2,3,4],[5,6,7,8],[9,10,11,12]])==[1,2,3,4,8,12,11,10,9,5,6,7]"),
]

PROMPT = ("{desc}\nReturn ONLY the function definition inside a ```python code "
          "block. No explanation, no tests, no example usage.")


def extract_code(txt: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", txt, re.S)
    return (blocks[-1] if blocks else txt).strip()


def passes(code: str, test: str, timeout: int = 15) -> bool:
    """Run candidate code + asserts in an isolated subprocess. Exit 0 == pass."""
    src = code + "\n\n" + test + "\nprint('PASS')\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     dir=ROOT, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode == 0 and "PASS" in r.stdout
    except Exception:
        return False
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def attempt(model: str, desc: str, test: str) -> tuple[bool, float, str]:
    t0 = time.time()
    try:
        output = chat(model, PROMPT.format(desc=desc))
    except Exception as error:
        diagnostic = server._safe_error(error)
        raise BenchmarkInfrastructureError(f"{model}: {diagnostic}") from error
    return passes(extract_code(output), test), time.time() - t0, output


def _measure(call: Callable[[], dict]) -> dict:
    before = server._usage_snapshot()
    result = call()
    result["usage"] = server._usage_delta(before, server._usage_snapshot())
    return result


def _selected_usage(usage: dict, model: str) -> tuple[int, int]:
    values = usage.get("by_model", {}).get(model, {})
    if values:
        prompt = (
            int(values.get("input", 0))
            + int(values.get("cached", 0))
            + int(values.get("cache_write", 0))
        )
        return prompt, int(values.get("output", 0))
    # A successful fallback is tracked under its own alias rather than the requested
    # alias. For a single attempt, aggregate usage remains the closest measured proxy.
    prompt = (
        int(usage.get("total_input_tokens", 0))
        + int(usage.get("total_cached_tokens", 0))
        + int(usage.get("total_cache_write_tokens", 0))
    )
    return prompt, int(usage.get("total_output_tokens", 0))


def _strategy_cost(usage: dict, selected_model: str) -> dict[str, float]:
    direct_input, returned_output = _selected_usage(usage, selected_model)
    worker_cost = server._usage_total_cost(usage)
    host_reingestion = server._precise_usage_cost(
        server.HOST_MODEL, returned_output, 0
    )
    direct_host = server._precise_usage_cost(
        server.HOST_MODEL, direct_input, returned_output
    )
    end_to_end = worker_cost + host_reingestion
    saving = direct_host - end_to_end
    return {
        "worker_cost": round(worker_cost, 6),
        "host_reingestion_cost": round(host_reingestion, 6),
        "end_to_end_cost": round(end_to_end, 6),
        "direct_host_cost": round(direct_host, 6),
        "saving": round(saving, 6),
        "saving_percent": round(
            saving / direct_host * 100 if direct_host else 0.0, 3
        ),
    }


def _sum_costs(costs: list[dict[str, float]]) -> dict[str, float]:
    fields = (
        "worker_cost", "host_reingestion_cost", "end_to_end_cost",
        "direct_host_cost", "saving",
    )
    total = {field: round(sum(cost[field] for cost in costs), 6) for field in fields}
    total["saving_percent"] = round(
        total["saving"] / total["direct_host_cost"] * 100
        if total["direct_host_cost"] else 0.0,
        3,
    )
    return total


def _sum_usage(usages: list[dict]) -> dict:
    total = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cached_tokens": 0,
        "total_cache_write_tokens": 0,
        "calls": 0,
        "by_model": {},
    }
    for usage in usages:
        for field in (
            "total_input_tokens", "total_output_tokens", "total_cached_tokens",
            "total_cache_write_tokens", "calls",
        ):
            total[field] += int(usage.get(field, 0))
        for model, values in usage.get("by_model", {}).items():
            target = total["by_model"].setdefault(
                model, {"input": 0, "output": 0, "cached": 0,
                        "cache_write": 0, "calls": 0}
            )
            for field in target:
                target[field] += int(values.get(field, 0))
    return total


def run_before(desc, test):
    def run() -> dict:
        ok, latency, _ = attempt(SINGLE, desc, test)
        return {"pass": ok, "latency": latency, "selected_model": SINGLE}
    result = _measure(run)
    result["cost"] = _strategy_cost(result["usage"], SINGLE)
    return result


def run_after(desc, test):
    def run() -> dict:
        t0 = time.time()
        attempts: dict[int, tuple[bool, float, str]] = {}
        infrastructure_failures: list[str] = []
        with cf.ThreadPoolExecutor(max_workers=len(SWARM)) as ex:
            futures = {
                ex.submit(attempt, model, desc, test): index
                for index, model in enumerate(SWARM)
            }
            for future in cf.as_completed(futures):
                index = futures[future]
                try:
                    attempts[index] = future.result()
                except BenchmarkInfrastructureError as error:
                    infrastructure_failures.append(str(error))
        if infrastructure_failures:
            raise BenchmarkInfrastructureError(
                "swarm provider failure(s): "
                + "; ".join(sorted(infrastructure_failures))
            )
        solved_by = [
            model for index, model in enumerate(SWARM) if attempts[index][0]
        ]
        selected = solved_by[0] if solved_by else SINGLE
        return {
            "pass": bool(solved_by),
            "latency": time.time() - t0,
            "solved_by": solved_by,
            "selected_model": selected,
        }
    result = _measure(run)
    result["cost"] = _strategy_cost(result["usage"], result["selected_model"])
    return result


def suite_fingerprint() -> str:
    payload = json.dumps(TASKS, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def resolved_models() -> dict[str, str]:
    return {
        alias: "/".join(resolve(alias))
        for alias in [SINGLE, *SWARM]
    }


def load_baseline(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2:
        raise ValueError("baseline schema_version must be 2")
    if data.get("suite_sha256") != suite_fingerprint():
        raise ValueError("baseline task suite fingerprint is stale")
    if data.get("models") != resolved_models():
        raise ValueError("baseline resolved model mapping is stale")
    minimum = data.get("minimum_passed", {})
    for key in ("single", "swarm"):
        value = minimum.get(key)
        if not isinstance(value, int) or not 0 <= value <= len(TASKS):
            raise ValueError(f"baseline minimum_passed.{key} is invalid")
    cost_minimum = data.get("minimum_saving_percent", {})
    if set(cost_minimum) != {"single"}:
        raise ValueError("baseline minimum_saving_percent must gate the single path")
    if not isinstance(cost_minimum["single"], (int, float)):
        raise ValueError("baseline minimum_saving_percent.single is invalid")
    return data


def regression_failures(report: dict, baseline: dict) -> list[str]:
    minimum = baseline["minimum_passed"]
    summary = report["summary"]
    failures = []
    for key in ("single", "swarm"):
        actual = summary[key]["passed"]
        if actual < minimum[key]:
            failures.append(
                f"{key} correctness regressed: {actual}/{len(TASKS)} "
                f"< baseline minimum {minimum[key]}/{len(TASKS)}"
            )
    for key, required in baseline.get("minimum_saving_percent", {}).items():
        actual = summary[key]["cost"]["saving_percent"]
        if actual < required:
            failures.append(
                f"{key} cost saving regressed: {actual:.1f}% "
                f"< baseline minimum {required:.1f}%"
            )
    return failures


def render_markdown(report: dict, failures: list[str]) -> str:
    summary = report["summary"]
    single = summary["single"]
    swarm = summary["swarm"]
    n = report["task_count"]
    lines = [
        "# model-orchestra - swarm benchmark",
        "",
        f"Real run, {n} coding tasks, verified by executing generated code against "
        "unit tests. The JSON report is machine-readable and baseline-checked.",
        "",
        f"- Generated UTC: `{report['generated_utc']}`",
        f"- config SHA-256: `{report['config_sha256']}`",
        f"- suite SHA-256: `{report['suite_sha256']}`",
        f"- Resolved models: `{json.dumps(report['models'], sort_keys=True)}`",
        "",
        "## Result",
        "",
        "| | Pass rate | Tasks solved | Total latency | End-to-end cost | Direct Terra | Saving |",
        "|---|---|---|---|---|---|---|",
        f"| Single Flash | {single['passed']/n*100:.0f}% | "
        f"{single['passed']}/{n} | {single['latency_seconds']:.1f}s | "
        f"{single['cost']['end_to_end_cost']:.3f} {report['currency']} | "
        f"{single['cost']['direct_host_cost']:.3f} {report['currency']} | "
        f"{single['cost']['saving_percent']:.1f}% |",
        f"| Three-worker swarm (diagnostic) | {swarm['passed']/n*100:.0f}% | "
        f"{swarm['passed']}/{n} | {swarm['latency_seconds']:.1f}s | "
        f"{swarm['cost']['end_to_end_cost']:.3f} {report['currency']} | "
        f"{swarm['cost']['direct_host_cost']:.3f} {report['currency']} | "
        f"{swarm['cost']['saving_percent']:.1f}% |",
        "",
        "The cost gate applies to Single Flash, the default cost-saving path. The swarm "
        "is retained as a correctness diagnostic and is not the default route.",
        "",
        "## Regression Gate",
        "",
        "PASS" if not failures else "FAIL",
    ]
    lines.extend(f"- {failure}" for failure in failures)
    lines += [
        "",
        "## Per-task",
        "",
        "| Task | BEFORE | AFTER | solved by (swarm) |",
        "|---|---|---|---|",
    ]
    for row in report["tasks"]:
        before = row["single"]
        after = row["swarm"]
        lines.append(
            f"| {row['name']} | {'PASS' if before['pass'] else 'fail'} "
            f"{before['latency_seconds']:.1f}s | "
            f"{'PASS' if after['pass'] else 'fail'} "
            f"{after['latency_seconds']:.1f}s | "
            f"{', '.join(after['solved_by']) or '-'} |"
        )
    return "\n".join([*lines, ""])


def _atomic_write(path: pathlib.Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=pathlib.Path, default=BASELINE_PATH,
        help="correctness baseline JSON (default: tools/benchmark_baseline.json)",
    )
    parser.add_argument(
        "--check-baseline", action="store_true",
        help="validate suite and model fingerprints without making API calls",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: invalid benchmark baseline: {error}", file=sys.stderr)
        return 2
    if args.check_baseline:
        print("ok - benchmark baseline matches task suite and resolved models")
        return 0

    print("Running benchmark (real API calls)...\n")
    rows = []
    for name, desc, test in TASKS:
        try:
            before = run_before(desc, test)
            after = run_after(desc, test)
        except BenchmarkInfrastructureError as error:
            print(f"INFRASTRUCTURE ERROR: {error}", file=sys.stderr)
            print(
                "Benchmark aborted; existing reports were not changed.",
                file=sys.stderr,
            )
            return 3
        rows.append({
            "name": name,
            "single": {
                "pass": before["pass"],
                "latency_seconds": round(before["latency"], 3),
                "usage": before["usage"],
                "cost": before["cost"],
            },
            "swarm": {
                "pass": after["pass"],
                "latency_seconds": round(after["latency"], 3),
                "solved_by": after["solved_by"],
                "selected_model": after["selected_model"],
                "usage": after["usage"],
                "cost": after["cost"],
            },
        })
        print(f"  {name:22} before={'PASS' if before['pass'] else 'fail'}  "
              f"after={'PASS' if after['pass'] else 'fail'}  "
              f"(solved_by={after['solved_by']})")

    generated, config_hash = provenance()
    single_usage = _sum_usage([row["single"]["usage"] for row in rows])
    swarm_usage = _sum_usage([row["swarm"]["usage"] for row in rows])
    report = {
        "schema_version": 2,
        "generated_utc": generated,
        "config_sha256": config_hash,
        "suite_sha256": suite_fingerprint(),
        "models": resolved_models(),
        "host_model": server.HOST_MODEL,
        "currency": server.BUDGET_CURRENCY,
        "task_count": len(TASKS),
        "summary": {
            "single": {
                "passed": sum(row["single"]["pass"] for row in rows),
                "latency_seconds": round(
                    sum(row["single"]["latency_seconds"] for row in rows), 3
                ),
                "usage": single_usage,
                "cost": _sum_costs([row["single"]["cost"] for row in rows]),
            },
            "swarm": {
                "passed": sum(row["swarm"]["pass"] for row in rows),
                "latency_seconds": round(
                    sum(row["swarm"]["latency_seconds"] for row in rows), 3
                ),
                "usage": swarm_usage,
                "cost": _sum_costs([row["swarm"]["cost"] for row in rows]),
            },
        },
        "tasks": rows,
    }
    failures = regression_failures(report, baseline)
    report["regression_gate"] = {
        "passed": not failures,
        "failures": failures,
        "minimum_passed": baseline["minimum_passed"],
        "minimum_saving_percent": baseline["minimum_saving_percent"],
    }
    _atomic_write(
        JSON_REPORT_PATH,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        MARKDOWN_REPORT_PATH,
        render_markdown(report, failures),
    )
    summary = report["summary"]
    print(
        f"\nBEFORE {summary['single']['passed']}/{len(TASKS)} -> "
        f"AFTER {summary['swarm']['passed']}/{len(TASKS)} "
        "(wrote docs/REPORT.md and docs/REPORT.json)"
    )
    for failure in failures:
        print(f"REGRESSION: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
