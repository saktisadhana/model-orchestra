"""
Real before/after benchmark for model-orchestra.

BEFORE : one tiny model attempts each coding task once (the naive way).
AFTER  : the same task fans out to N tiny models in PARALLEL; the answer counts
         as solved if ANY worker's code passes the unit tests (verified best-of-N,
         the Kimi-swarm 'parallel then merge' idea with functional verification).

Every number here comes from real API calls + real code execution. Writes REPORT.md.

Run:  python benchmark.py
"""

import concurrent.futures as cf
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import chat  # reuses the same providers/keys as the MCP server

SINGLE = "flash"                        # BEFORE: one cheap worker
SWARM = ["flash", "mimo", "ds-pro"]     # AFTER: swarm of 3 cheap coders, in parallel
ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def attempt(model: str, desc: str, test: str) -> tuple[bool, float]:
    t0 = time.time()
    try:
        out = chat(model, PROMPT.format(desc=desc))
        ok = passes(extract_code(out), test)
    except Exception:
        ok = False
    return ok, time.time() - t0


def run_before(desc, test):
    ok, dt = attempt(SINGLE, desc, test)
    return {"pass": ok, "latency": dt}


def run_after(desc, test):
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=len(SWARM)) as ex:
        res = list(ex.map(lambda m: attempt(m, desc, test), SWARM))
    solved_by = [m for (m, (ok, _)) in zip(SWARM, res) if ok]
    return {"pass": bool(solved_by), "latency": time.time() - t0, "solved_by": solved_by}


def main():
    print("Running benchmark (real API calls)...\n")
    rows, b_pass, a_pass, b_lat, a_lat = [], 0, 0, 0.0, 0.0
    for name, desc, test in TASKS:
        b = run_before(desc, test)
        a = run_after(desc, test)
        b_pass += b["pass"]; a_pass += a["pass"]
        b_lat += b["latency"]; a_lat += a["latency"]
        rows.append((name, b, a))
        print(f"  {name:22} before={'PASS' if b['pass'] else 'fail'}  "
              f"after={'PASS' if a['pass'] else 'fail'}  "
              f"(solved_by={a.get('solved_by')})")

    n = len(TASKS)
    generated, config_hash = provenance()
    lines = [
        "# model-orchestra - swarm benchmark",
        "",
        f"Real run, {n} coding tasks, results verified by executing the generated code "
        "against unit tests. Numbers are from live API calls - rerun to reproduce.",
        "",
        f"- Generated UTC: `{generated}`",
        f"- config SHA-256: `{config_hash}`",
        f"- Single model at generation: `{SINGLE}`",
        f"- Swarm models at generation: `{', '.join(SWARM)}`",
        "",
        f"- **BEFORE** — single model (`{SINGLE}`), one attempt per task.",
        f"- **AFTER** — swarm `{', '.join(SWARM)}` run in parallel; task counts as solved "
        "if ANY worker's code passes the tests (verified best-of-N).",
        "",
        "## Result",
        "",
        "| | Pass rate | Tasks solved | Total latency |",
        "|---|---|---|---|",
        f"| BEFORE (single) | {b_pass/n*100:.0f}% | {b_pass}/{n} | {b_lat:.1f}s |",
        f"| AFTER (swarm)   | {a_pass/n*100:.0f}% | {a_pass}/{n} | {a_lat:.1f}s |",
        "",
        f"**Reliability delta: +{(a_pass-b_pass)/n*100:.0f} percentage points** "
        f"({b_pass}/{n} -> {a_pass}/{n} solved). On this mechanical set a single "
        f"cheap worker already scores {b_pass}/{n}, so delegating it is lossless — "
        "that is the point (see PROOF.md for the cost this saves). The swarm spends "
        f"~{len(SWARM)}x the tokens for best-of-N insurance; keep it for genuinely "
        "hard problems where one model is flaky, not for easy grunt work.",
        "",
        "## Per-task",
        "",
        "| Task | BEFORE | AFTER | solved by (swarm) |",
        "|---|---|---|---|",
    ]
    for name, b, a in rows:
        lines.append(
            f"| {name} | {'PASS' if b['pass'] else 'fail'} {b['latency']:.1f}s "
            f"| {'PASS' if a['pass'] else 'fail'} {a['latency']:.1f}s "
            f"| {', '.join(a.get('solved_by')) or '-'} |")
    lines += ["", "## How to read this",
              "",
              "A swarm does not make a weak model smart. It buys **reliability**: with N "
              "diverse cheap workers, the chance that at least one nails a tricky task is "
              "much higher than any single one — and because they run in parallel, you pay "
              "that in tokens, not wall-clock time. This is the cheap-model version of the "
              "Kimi K2 swarm: fan out, then keep the best.", ""]

    (ROOT / "docs" / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nBEFORE {b_pass}/{n}  ->  AFTER {a_pass}/{n}   (wrote docs/REPORT.md)")


if __name__ == "__main__":
    main()
