"""Normalize a wheel zip for reproducible release artifacts."""

from __future__ import annotations

import argparse
import os
import time
import zipfile
from pathlib import Path

RECORD_SUFFIX = ".dist-info/RECORD"


def _sort_key(name: str) -> tuple[int, str]:
    """Order members by name, keeping RECORD last as wheel readers expect."""
    return (1 if name.endswith(RECORD_SUFFIX) else 0, name)


def normalize(path: Path, epoch: int) -> None:
    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}

    date_time = time.gmtime(epoch)[:6]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for name in sorted(members, key=_sort_key):
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            target.writestr(info, members[name])
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--epoch",
        type=int,
        default=int(os.environ.get("SOURCE_DATE_EPOCH", "0")),
    )
    args = parser.parse_args()
    if args.epoch <= 0:
        parser.error("set SOURCE_DATE_EPOCH or pass a positive --epoch")
    normalize(args.path, args.epoch)
    print(f"Normalized {args.path} at epoch {args.epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
