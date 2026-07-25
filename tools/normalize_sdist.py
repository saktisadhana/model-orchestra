"""Normalize an sdist tar.gz for reproducible release artifacts."""

from __future__ import annotations

import argparse
import gzip
import io
import os
import tarfile
from pathlib import Path


def normalize(path: Path, epoch: int) -> None:
    with tarfile.open(path, "r:gz") as source:
        members: list[tuple[tarfile.TarInfo, bytes]] = []
        for original in source.getmembers():
            data = b""
            if original.isfile():
                stream = source.extractfile(original)
                data = stream.read() if stream else b""
            info = tarfile.TarInfo(original.name)
            info.size = len(data)
            info.mode = original.mode
            info.type = original.type
            info.linkname = original.linkname
            info.mtime = epoch
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.pax_headers = {}
            members.append((info, data))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as target:
        for info, data in sorted(members, key=lambda item: item[0].name):
            target.addfile(info, io.BytesIO(data) if info.isfile() else None)

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
            compressed.write(buffer.getvalue())
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
