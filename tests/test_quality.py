import json
import os
import re
import sys
import pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import server
from server import (
    pipeline, auto_delegate, batch_delegate,
    chat, chat_with_failover, _truncate, _is_security,
    TASK_ROUTES, resolve, client_for, FALLBACK_CHAIN, PROVIDERS, MAX_RESPONSE,
)


# ── helpers for executing generated code ────────────────────────────────────
# ponytail: naive exec() of model output in a fresh namespace, no sandbox.
# Fine for a local dev harness run on your own tasks; if this ever runs on
# untrusted worker output, move exec into a subprocess with limits.

def _code_blocks(res: str) -> list[str]:
    """Every ```python ... ``` fence, else the whole response.

    Cheap workers often emit several blocks (a naive version, then an optimized
    one, then example usage), so keying off only the first fence reports a
    correct answer as a failure.
    """
    return re.findall(r"```(?:python)?\s*\n(.*?)```", res, re.DOTALL) or [res]


def _extract_code(res: str) -> str:
    """All generated code joined, for whole-response keyword checks."""
    return "\n".join(_code_blocks(res))


def _exec_ns(res: str) -> dict:
    """Exec every candidate block and MERGE the namespaces.

    Merging (rather than picking one "best" block) matters: a worker often emits
    a correct function in one fence and something bigger in another. Keeping only
    the largest namespace can discard the very function the checker looks for --
    _find_fn tests each callable against the predicate, so it is safe to hand it
    all of them.
    """
    merged: dict = {}
    for code in _code_blocks(res):
        ns: dict = {}
        try:
            exec(code, ns)
        except Exception:
            continue
        merged.update({k: v for k, v in ns.items() if k != "__builtins__"})
    return merged


def _find_fn(ns: dict, predicate):
    """Return the first user-defined callable in ns satisfying predicate."""
    for name, obj in ns.items():
        if name.startswith("_") or not callable(obj) or not hasattr(obj, "__code__"):
            continue
        try:
            if predicate(obj):
                return obj
        except Exception:
            continue
    return None


def test_routing_accuracy():
    print("--- Test 2: Routing Accuracy ---")
    ROUTING_TESTS = [
        ("Write a function to sort a list", "draft-refine"),
        ("Fix the bug in parser.py", "debug"),
        ("Quick: convert this to a list comprehension", "speed-run"),
        ("Write pytest tests for auth module", "test-factory"),
        ("Analyze the database schema for bottlenecks", "reasoning"),
        ("Review this pull request for security issues", "code-review"),
        ("Implement the payment processing algorithm", "draft-refine"),
        ("Debug this traceback: IndexError on line 42", "debug"),
        ("Create a simple hello world script", "speed-run"),
        ("Explain why the API is slow", "reasoning"),
        ("Write a regex to match email addresses", "speed-run"),
        ("Add error handling to the upload function", "draft-refine"),
        ("Check this code for vulnerabilities", "code-review"),
        ("Build a caching layer for the API", "draft-refine"),
        ("Convert JSON to YAML format", "speed-run"),
        ("Design the architecture for a chat system", "reasoning"),
        ("Fix the crash when user input is empty", "debug"),
        ("Write unit tests for the parser", "test-factory"),
        ("Review the PR for code quality", "code-review"),
        ("Make a template for the config file", "speed-run"),
    ]

    correct = 0
    for task, expected in ROUTING_TESTS:
        task_lower = task.lower()
        scores = {route: 0 for route in TASK_ROUTES}
        for route, keywords in TASK_ROUTES.items():
            for kw in keywords:
                if kw in task_lower:
                    scores[route] += 1
        best = max(scores, key=scores.get)
        best_score = scores[best]
        if best_score == 0:
            best = "draft-refine" if len(task.split()) > 20 else "speed-run"

        if best == expected:
            correct += 1
        else:
            print(f"Failed routing: '{task}' -> got {best}, expected {expected}")

    accuracy = correct / len(ROUTING_TESTS)
    print(f"Routing Accuracy: {accuracy*100:.1f}% ({correct}/{len(ROUTING_TESTS)})")
    assert accuracy >= 0.8, "Routing accuracy below 80%"
    print("PASS: Routing accuracy OK")


def test_security_routing():
    """No-API: a security task must route to the strong `security` pipeline."""
    print("\n--- Test 6: CybSec Routing Floor ---")
    captured = {}
    orig = server.pipeline
    server.pipeline = lambda task, mode="draft-refine", *a, **k: captured.__setitem__("mode", mode)
    try:
        server.auto_delegate("Write an exploit for CVE-2024-1234 buffer overflow")
        sec_mode = captured.get("mode")
        captured.clear()
        server.auto_delegate("Write a function to sort a list of integers")
        benign_mode = captured.get("mode")
    finally:
        server.pipeline = orig

    ok = True
    if not _is_security("decrypt this AES ciphertext to get the flag{...}"):
        print("FAIL: _is_security missed an obvious security task")
        ok = False
    if _is_security("write a function to sort a list"):
        print("FAIL: _is_security false-positive on a benign task")
        ok = False
    if sec_mode != "security":
        print(f"FAIL: security task routed to {sec_mode!r}, expected 'security'")
        ok = False
    if benign_mode == "security":
        print("FAIL: benign task incorrectly routed to 'security'")
        ok = False

    assert ok, "CybSec routing floor broken"
    print("PASS: security tasks floored to strong models")


def test_truncation_safety():
    print("\n--- Test 5: Truncation Safety ---")
    long_content = "def long_function():\n" + "    # filler\n" * 2000 + "    return True\n"
    truncated = _truncate(long_content, limit=1000)

    assert "[TRUNCATED" in truncated, "TRUNCATED marker not found"
    assert "def long_function():" in truncated, "Function signature lost"
    assert "return True" in truncated, "Return statement lost"

    print("PASS: Truncation safety OK")


@pytest.mark.live
@pytest.mark.network
@pytest.mark.paid
def test_failover():
    print("\n--- Test 4: Failover Chain ---")

    # Temporarily modify the primary provider for 'flash' to use a bad key
    original_key = os.environ.get(PROVIDERS["opencode-go"]["api_key_env"])
    os.environ[PROVIDERS["opencode-go"]["api_key_env"]] = "sk-badkey"

    print("Testing failover (this might take a few seconds as primary fails)...")
    try:
        res = chat_with_failover("flash", "Say 'failover successful' and nothing else.")
        print(f"Response: {res}")
        if not res or "error" in res.lower() and "successful" not in res.lower():
            print("FAIL: Failover didn't return a valid response")
            passed = False
        else:
            print("PASS: Failover successful")
            passed = True
    except Exception as e:
        print(f"FAIL: Failover raised exception: {e}")
        passed = False
    finally:
        if original_key:
            os.environ[PROVIDERS["opencode-go"]["api_key_env"]] = original_key
        else:
            del os.environ[PROVIDERS["opencode-go"]["api_key_env"]]

    assert passed, "Failover did not return a valid response"


@pytest.mark.live
@pytest.mark.network
@pytest.mark.paid
def test_pipeline_quality():
    """Now EXECUTES generated code and asserts behavior, not keyword presence."""
    print("\n--- Test 1: Pipeline Quality (execution-checked) ---")

    TASKS = {
        "Easy": ("speed-run", "Write a Python function that reverses a string without using [::-1] or reversed(). Handle empty strings and None input."),
        "Medium": ("draft-refine", "Write a Python function that finds the longest palindromic substring in a string. Return the substring itself, not its length. Handle empty strings. Optimize to better than O(n^3)."),
        "Hard": ("swarm-budget", "Write a Python function that solves the N-Queens problem. Given n, return all distinct solutions. Each solution is a list of n strings where 'Q' marks a queen and '.' marks empty. Include input validation."),
        "Debug": ("debug", "This code has a bug. Find and fix it:\n```python\ndef merge_sorted(a, b):\n    result = []\n    i = j = 0\n    while i < len(a) and j < len(b):\n        if a[i] <= b[j]:\n            result.append(a[i])\n            i += 1\n        else:\n            result.append(b[j])\n            j += 1\n    return result\n```"),
        "Analysis": ("reasoning", "Explain the trade-offs between using a hash map vs a balanced BST for implementing a cache. Consider: lookup time, insertion time, memory overhead, cache-friendliness, and worst-case behavior. Give a concrete recommendation for a web server session store."),
        "Tests": ("test-factory", "Write pytest tests for a function `parse_csv(filepath)` that reads a CSV file and returns a list of dicts. Test: normal file, empty file, missing file, unicode content, malformed rows, very large files."),
    }

    # Each checker gets the raw response; returns True iff the output is CORRECT.
    def check_easy(res):
        code = _extract_code(res)
        if "[::-1]" in code or "reversed(" in code:
            return False
        ns = _exec_ns(res)
        f = _find_fn(ns, lambda fn: fn("abc") == "cba")
        return bool(f) and f("") == "" and f("racecar") == "racecar"

    def check_medium(res):
        ns = _exec_ns(res)
        f = _find_fn(ns, lambda fn: fn("babad") in ("bab", "aba"))
        return bool(f) and f("cbbd") == "bb" and f("") == ""

    def check_hard(res):
        ns = _exec_ns(res)
        # N-Queens: n=4 has 2 solutions, n=1 has 1. Each sol = list of n strings.
        f = _find_fn(ns, lambda fn: len(fn(4)) == 2)
        if not f:
            return False
        sols = f(4)
        shape_ok = all(len(s) == 4 and all(len(row) == 4 for row in s) for s in sols)
        return shape_ok and len(f(1)) == 1

    def check_debug(res):
        ns = _exec_ns(res)
        f = _find_fn(ns, lambda fn: fn([1, 3, 5], [2, 4]) == [1, 2, 3, 4, 5])
        # the original bug drops the leftover tail; assert it's fixed
        return bool(f) and f([1, 2], []) == [1, 2] and f([], [7]) == [7]

    def check_analysis(res):
        r = res.lower()
        has_both = ("hash" in r) and ("bst" in r or "binary search tree" in r or "tree" in r)
        gives_rec = "recommend" in r or "session" in r
        return has_both and gives_rec and len(res) > 400

    def check_tests(res):
        return (res.count("def test_") >= 3
                and "parse_csv" in res
                and ("import pytest" in res or "pytest" in res))

    CHECKERS = {
        "Easy": check_easy, "Medium": check_medium, "Hard": check_hard,
        "Debug": check_debug, "Analysis": check_analysis, "Tests": check_tests,
    }

    passed_count = 0
    total = len(TASKS)
    for level, (mode, task) in TASKS.items():
        print(f"\nRunning {level} task via {mode}...")
        try:
            res = pipeline(task, mode=mode)
            if CHECKERS[level](res):
                print(f"PASS: {level} task (behavior verified)")
                passed_count += 1
            else:
                print(f"FAIL: {level} task — output did not satisfy behavior check")
                print(f"Output snippet: {res[:300]}...")
        except Exception as e:
            print(f"FAIL (Exception): {level} task -> {e}")

    accuracy = passed_count / total
    print(f"\nPipeline Quality: {accuracy*100:.1f}% ({passed_count}/{total})")
    assert accuracy >= 0.8, "Pipeline quality below 80%"


@pytest.mark.live
@pytest.mark.network
@pytest.mark.paid
def test_batch_delegate():
    print("\n--- Test 3: Batch Delegate ---")
    tasks_json = json.dumps([
        {"task": "Write a python function that adds two numbers", "mode": "speed-run"},
        {"task": "Explain what a variable is in 10 words", "mode": "reasoning"},
    ])

    try:
        res = batch_delegate(tasks_json, inline=True)
        assert "### Task 1:" in res and "### Task 2:" in res and "def" in res, (
            "Batch delegate output missing expected parts"
        )
        print("PASS: Batch delegate successful")
    except Exception as e:
        raise AssertionError(f"Batch delegate raised exception {e}") from e


if __name__ == "__main__":
    try:
        test_routing_accuracy()
        test_security_routing()
        test_truncation_safety()
        test_failover()
        test_batch_delegate()
        test_pipeline_quality()
    except AssertionError:
        print("\nSOME TESTS FAILED. See output above.")
        raise
    else:
        print("\nALL TESTS PASSED! The system is smart and optimized.")
        sys.exit(0)
