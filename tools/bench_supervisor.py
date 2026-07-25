"""
Supervisor-arm benchmark: which SUPERVISOR model can you trust to run the show?

The worker layer is shared between "Opus + cheap workers" and "Sol + cheap
workers" — so the only variable that separates the two setups is the SUPERVISOR.
This script measures supervisor-tier models head-to-head on the same functionally
verified tasks (HumanEval-style pass@1: generate code, execute it against unit
tests, count a task solved only if it actually runs correctly).

Every number is from a live API call + real code execution. No estimates.

Candidates:
  flash     - cheap worker floor (baseline, NOT a supervisor)
  orchestrator-fallback - gpt-5.6-sol via api.68886868.xyz (candidate host)
  k26       - kimi-k2.6, a strong model (premium-tier stand-in; I can't API-call
              Opus itself, since Opus IS the orchestrator running this)

Run:  python bench_supervisor.py
"""

import time
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server
from benchmark import TASKS, PROMPT, extract_code, passes

CANDIDATES = ["flash", "orchestrator-fallback", "k26"]


def run_one(model, desc, test):
    t0 = time.time()
    try:
        out = server.chat(model, PROMPT.format(desc=desc), temperature=0.2)
        ok = passes(extract_code(out), test)
    except Exception as e:
        return {"pass": False, "latency": time.time() - t0, "err": str(e)[:80]}
    return {"pass": ok, "latency": time.time() - t0, "err": ""}


def main():
    print("Supervisor benchmark — live API calls, code executed against tests.\n")
    results = {m: {"pass": 0, "lat": 0.0, "errs": []} for m in CANDIDATES}

    for name, desc, test in TASKS:
        line = f"  {name:22}"
        for m in CANDIDATES:
            r = run_one(m, desc, test)
            results[m]["pass"] += r["pass"]
            results[m]["lat"] += r["latency"]
            if r["err"]:
                results[m]["errs"].append(f"{name}: {r['err']}")
            line += f"  {m}={'PASS' if r['pass'] else 'fail'}"
        print(line)

    n = len(TASKS)
    print(f"\n{'model':10} {'pass@1':>8} {'in_tok':>10} {'out_tok':>10} {'lat':>8}")
    print("-" * 50)
    for m in CANDIDATES:
        u = server.SESSION_USAGE["by_model"].get(m, {"input": 0, "output": 0})
        print(f"{m:10} {results[m]['pass']}/{n:<6} {u['input']:>10} "
              f"{u['output']:>10} {results[m]['lat']:>7.1f}s")

    print("\nErrors (if any):")
    for m in CANDIDATES:
        for e in results[m]["errs"]:
            print(f"  [{m}] {e}")

    # dump raw usage so the cost math is reproducible
    print("\nRAW_USAGE =", server.SESSION_USAGE["by_model"])


if __name__ == "__main__":
    main()
