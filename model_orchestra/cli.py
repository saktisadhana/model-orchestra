"""Command-line entry points for the public Model Orchestra package."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import (
    CONFIG_ENV,
    credential_names,
    load_config,
    resolve_config_path,
    validate_config,
)

STABLE_TOOLS = {
    "list_workers",
    "route_preview",
    "delegate",
    "delegate_verified",
    "orchestrate_change",
    "batch_delegate",
    "orchestration_report",
    "cost_report",
}


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="path to configuration v1 JSON")


def _validated_path(explicit: Path | None) -> tuple[Path, dict]:
    path = resolve_config_path(explicit)
    config = load_config(path)
    validate_config(config)
    return path, config


def _set_runtime_config(path: Path) -> None:
    os.environ[CONFIG_ENV] = str(path)


async def _prune_to_stable_tools(server) -> set[str]:
    names = {tool.name for tool in await server.mcp.list_tools()}
    for name in sorted(names - STABLE_TOOLS):
        server.mcp.remove_tool(name)
    return {tool.name for tool in await server.mcp.list_tools()}


def _check(args: argparse.Namespace) -> int:
    try:
        path, _ = _validated_path(args.config)
    except (OSError, ValueError) as error:
        print(f"Configuration: invalid ({error})")
        return 1
    print(f"Configuration: valid ({path})")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    try:
        path, config = _validated_path(args.config)
    except (OSError, ValueError) as error:
        print(f"Configuration: invalid ({error})")
        return 1
    print(f"Configuration: valid ({path})")
    missing = [name for name in credential_names(config) if not os.environ.get(name)]
    if missing:
        print("Missing credential variables: " + ", ".join(missing))
        return 1
    print("Credentials: configured")
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        path, _ = _validated_path(args.config)
    except (OSError, ValueError) as error:
        print(f"Configuration: invalid ({error})", file=sys.stderr)
        return 1
    _set_runtime_config(path)
    import server

    asyncio.run(_prune_to_stable_tools(server))
    server.mcp.run()
    return 0


def _tools(args: argparse.Namespace) -> int:
    try:
        path, _ = _validated_path(args.config)
    except (OSError, ValueError) as error:
        print(f"Configuration: invalid ({error})")
        return 1
    _set_runtime_config(path)
    import server

    names = asyncio.run(_prune_to_stable_tools(server))
    missing = sorted(STABLE_TOOLS - names)
    if missing:
        print("Stable tool surface missing: " + ", ".join(missing))
        return 1
    print("Stable tool surface: " + ", ".join(sorted(STABLE_TOOLS)))
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    if not args.live:
        print("Refusing provider calls without explicit --live")
        return 2
    try:
        path, _ = _validated_path(args.config)
    except (OSError, ValueError) as error:
        print(f"Configuration: invalid ({error})")
        return 1
    _set_runtime_config(path)
    command = [sys.executable, "-m", "model_orchestra.live_benchmark"]
    return subprocess.run(command, check=False).returncode


def _migrate(args: argparse.Namespace) -> int:
    source = args.from_config.expanduser().resolve()
    destination = args.output.expanduser().resolve()
    try:
        config = load_config(source)
        if "schema_version" not in config:
            config = {"schema_version": 1, **config}
        validate_config(config)
    except (OSError, ValueError) as error:
        print(f"Migration failed: {error}")
        return 1
    if destination.exists() and not args.force:
        print(f"Migration refused: {destination} exists; pass --force to replace it")
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"Migrated configuration to {destination}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="model-orchestra")
    commands = root.add_subparsers(dest="command", required=True)
    for name, handler in (("serve", _serve), ("check", _check), ("doctor", _doctor), ("tools", _tools)):
        command = commands.add_parser(name)
        _config_argument(command)
        command.set_defaults(handler=handler)
    benchmark = commands.add_parser("benchmark")
    _config_argument(benchmark)
    benchmark.add_argument("--live", action="store_true", help="authorize paid/network calls")
    benchmark.set_defaults(handler=_benchmark)
    migrate = commands.add_parser("migrate-config")
    migrate.add_argument("--from", dest="from_config", type=Path, required=True)
    migrate.add_argument("--output", type=Path, required=True)
    migrate.add_argument("--force", action="store_true")
    migrate.set_defaults(handler=_migrate)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.handler(args))