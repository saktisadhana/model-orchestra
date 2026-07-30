"""Command-line entry points for the public Model Orchestra package."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
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
from . import budget as budget_store
from . import dogfood

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
    _, database = budget_store.configured_paths(path, config)
    try:
        health = budget_store.health_report(database)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Budget database: invalid ({error})")
        return 1
    print("Budget database: " + health["integrity"])
    if health["integrity"] == "failed":
        return 1
    if health["stale_liabilities"] or health["stale_reservations"]:
        print(
            "Stale budget state: "
            f"{health['stale_liabilities']} pending liabilities, "
            f"{health['stale_reservations']} reservations"
        )
        return 1
    if health["over_reservation_count"]:
        print(
            "Budget reservation overrun: "
            f"{health['over_reservation_count']} settled operation(s), "
            f"{health['over_reservation_amount']} unreserved units"
        )
        return 1
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


def _budget_paths(args: argparse.Namespace) -> tuple[Path, dict, Path, Path]:
    path, config = _validated_path(args.config)
    legacy, database = budget_store.configured_paths(path, config)
    return path, config, legacy, database


def _budget_migrate(args: argparse.Namespace) -> int:
    try:
        _, _, configured_legacy, database = _budget_paths(args)
        source = (
            args.from_ledger.expanduser().resolve()
            if args.from_ledger else configured_legacy
        )
        report = budget_store.migrate_legacy_json(source, database)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Budget migration failed: {error}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _budget_health(args: argparse.Namespace) -> int:
    try:
        _, _, _, database = _budget_paths(args)
        report = budget_store.health_report(database)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Budget health failed: {error}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    healthy = (
        report["integrity"] in {"ok", "not_initialized"}
        and not report["over_reservation_count"]
        and not report["stale_liabilities"]
        and not report["stale_reservations"]
    )
    return 0 if healthy else 1


def _budget_report(args: argparse.Namespace) -> int:
    try:
        _, config, _, database = _budget_paths(args)
        report = budget_store.financial_report(database, config)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Budget report failed: {error}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _dogfood_path(path: Path, config: dict) -> Path:
    name = str(config["budget"].get(
        "dogfood_database_file", ".model-orchestra-dogfood.sqlite3"
    ))
    return path.resolve().parent / name


def _trial_report(args: argparse.Namespace) -> int:
    try:
        path, config = _validated_path(args.config)
        report = dogfood.build_report(_dogfood_path(path, config), days=args.days)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Trial report failed: {error}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _trial_label(args: argparse.Namespace) -> int:
    try:
        path, config = _validated_path(args.config)
        updated = dogfood.label_event(
            _dogfood_path(path, config),
            args.event_id,
            accepted=args.accepted,
            redo=args.redo,
            intervention=args.intervention,
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Trial label failed: {error}")
        return 1
    if not updated:
        print(f"Trial event not found: {args.event_id}")
        return 1
    print(f"Labeled trial event: {args.event_id}")
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
    budget = commands.add_parser("budget")
    budget_commands = budget.add_subparsers(dest="budget_command", required=True)
    budget_migrate = budget_commands.add_parser("migrate")
    _config_argument(budget_migrate)
    budget_migrate.add_argument("--from", dest="from_ledger", type=Path)
    budget_migrate.set_defaults(handler=_budget_migrate)
    for name, handler in (("health", _budget_health), ("report", _budget_report)):
        command = budget_commands.add_parser(name)
        _config_argument(command)
        command.set_defaults(handler=handler)
    trial = commands.add_parser("trial")
    trial_commands = trial.add_subparsers(dest="trial_command", required=True)
    trial_report = trial_commands.add_parser("report")
    _config_argument(trial_report)
    trial_report.add_argument("--days", type=int, default=dogfood.TRIAL_DAYS)
    trial_report.set_defaults(handler=_trial_report)
    trial_label = trial_commands.add_parser("label")
    _config_argument(trial_label)
    trial_label.add_argument("event_id")
    accepted = trial_label.add_mutually_exclusive_group(required=True)
    accepted.add_argument("--accepted", action="store_true")
    accepted.add_argument("--rejected", dest="accepted", action="store_false")
    trial_label.add_argument("--redo", action="store_true")
    trial_label.add_argument("--intervention", action="store_true")
    trial_label.set_defaults(handler=_trial_label)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.handler(args))