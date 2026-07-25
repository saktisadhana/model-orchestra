"""Build reproducible wheel and sdist release artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EPOCH = 1_784_952_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--epoch", type=int, default=DEFAULT_EPOCH)
    args = parser.parse_args()
    output = args.out_dir.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(args.epoch)
    build = subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if build.returncode:
        return build.returncode
    sdist = next(output.glob("*.tar.gz"), None)
    if sdist is None:
        print("Build did not produce an sdist.")
        return 1
    wheel = next(output.glob("*.whl"), None)
    if wheel is None:
        print("Build did not produce a wheel.")
        return 1
    # Both artifacts must be normalized; neither failure may mask the other.
    failed = 0
    for script, artifact in (
        ("normalize_sdist.py", sdist),
        ("normalize_wheel.py", wheel),
    ):
        code = subprocess.run(
            [sys.executable, str(ROOT / "tools" / script),
             str(artifact), "--epoch", str(args.epoch)],
            cwd=ROOT,
            check=False,
        ).returncode
        failed = failed or code
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
