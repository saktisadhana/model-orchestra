"""Generate or verify the public-alpha MCP tool schema snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SNAPSHOT = ROOT / "model_orchestra" / "data" / "tool-schemas.v1.json"


def generate() -> str:
    from model_orchestra.config import example_config_path

    os.environ["MODEL_ORCHESTRA_CONFIG"] = str(example_config_path())
    import server
    from model_orchestra.cli import _prune_to_stable_tools

    async def collect() -> list[dict]:
        await _prune_to_stable_tools(server)
        tools = await server.mcp.list_tools()
        return sorted(
            (
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                }
                for tool in tools
            ),
            key=lambda item: item["name"],
        )

    payload = {"schema_version": 1, "tools": asyncio.run(collect())}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    rendered = generate()
    if args.write:
        SNAPSHOT.write_text(rendered, encoding="utf-8")
        print(f"Wrote {SNAPSHOT}")
        return 0
    if not SNAPSHOT.is_file():
        print(f"Missing snapshot: {SNAPSHOT}")
        return 1
    if SNAPSHOT.read_text(encoding="utf-8") != rendered:
        print("Public tool schema snapshot is stale; run with --write and review the diff.")
        return 1
    print("Public tool schema snapshot matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
