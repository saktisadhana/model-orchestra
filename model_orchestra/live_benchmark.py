"""Bounded live smoke benchmark for an installed Model Orchestra package."""

from __future__ import annotations

import sys


def main() -> int:
    import server

    tests = "def test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0"
    result = server.delegate_verified(
        "Write a Python function add(a, b).",
        model="flash",
        tests=tests,
        attempts=1,
    )
    if not result.startswith("VERIFIED after"):
        print("Live benchmark failed verification.")
        return 1
    print("Live benchmark passed: 1/1 bounded task verified.")
    print(server.cost_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
