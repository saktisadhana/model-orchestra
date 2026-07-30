"""Transactional budget migration, health, and reporting helpers."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator

STALE_AFTER_HOURS = 24
_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def configured_paths(config_path: Path, config: dict[str, Any]) -> tuple[Path, Path]:
    budget = config["budget"]
    root = config_path.resolve().parent
    legacy = root / str(budget.get("state_file", ".model-orchestra-budget.json"))
    database = root / str(budget.get("database_file", ".model-orchestra-budget.sqlite3"))
    return legacy, database


def require_local_database(path: Path) -> Path:
    """Reject path forms that are unambiguously remote or SQLite URI based."""
    raw = str(path)
    normalized = raw.replace("\\", "/")
    if (
        raw.startswith("\\\\")
        or normalized.startswith("//")
        or raw.casefold().startswith("file:")
        or _URI_PATTERN.match(raw)
    ):
        raise ValueError(
            "budget database must be on a local filesystem; network shares and URI paths are refused"
        )
    return path.expanduser().resolve()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_reservations (
            operation_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            group_name TEXT NOT NULL,
            model TEXT NOT NULL,
            reserved_idr INTEGER NOT NULL CHECK (reserved_idr >= 0),
            state TEXT NOT NULL CHECK (
                state IN ('reserved', 'settled', 'pending_liability', 'void')
            ),
            actual_idr INTEGER,
            settlement_id TEXT UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_migrations (
            migration_id TEXT PRIMARY KEY,
            source_sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            imported_entries INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS legacy_budget_entries (
            migration_id TEXT NOT NULL,
            entry_index INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            group_name TEXT NOT NULL,
            model TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK (amount >= 0),
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (migration_id, entry_index),
            FOREIGN KEY (migration_id) REFERENCES budget_migrations(migration_id)
        )
        """
    )


@contextlib.contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    database = require_local_database(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        yield connection
    finally:
        connection.close()


def _nonnegative_int(value: Any, *, entry_index: int, field: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"legacy budget entry {entry_index} has invalid {field}"
        ) from error
    if parsed < 0:
        raise ValueError(
            f"legacy budget entry {entry_index} has negative {field}"
        )
    return parsed


def _existing_migration(connection: sqlite3.Connection) -> sqlite3.Row | tuple | None:
    return connection.execute(
        "SELECT migration_id, source_sha256, imported_entries "
        "FROM budget_migrations ORDER BY imported_at, migration_id LIMIT 1"
    ).fetchone()


def _migration_report(existing: sqlite3.Row | tuple) -> dict[str, Any]:
    return {
        "status": "already_migrated",
        "migration_id": str(existing[0]),
        "imported_entries": int(existing[2]),
        "source_sha256": str(existing[1]),
    }


def migrate_legacy_json(source: Path, database: Path) -> dict[str, Any]:
    """Import one immutable JSON ledger snapshot exactly once."""
    database = require_local_database(database)
    if database.exists():
        with connect(database) as connection:
            existing = _existing_migration(connection)
        if existing is not None:
            return _migration_report(existing)

    payload = source.read_bytes()
    source_hash = hashlib.sha256(payload).hexdigest()
    migration_id = f"legacy-json-v1:{source_hash}"
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid legacy budget JSON: {error}") from error
    entries = decoded.get("entries") if isinstance(decoded, dict) else None
    if not isinstance(entries, list):
        raise ValueError("legacy budget JSON must contain an entries array")

    prepared: list[tuple[Any, ...]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"legacy budget entry {index} must be an object")
        created = str(entry.get("at", ""))
        group = str(entry.get("group", ""))
        model = str(entry.get("model", "unknown"))
        if not created or not group:
            raise ValueError(
                f"legacy budget entry {index} requires at and group"
            )
        try:
            parsed_created = dt.datetime.fromisoformat(created)
        except ValueError as error:
            raise ValueError(
                f"legacy budget entry {index} has invalid at"
            ) from error
        if parsed_created.tzinfo is None or parsed_created.utcoffset() is None:
            raise ValueError(
                f"legacy budget entry {index} at requires a timezone offset"
            )
        created = parsed_created.astimezone(dt.timezone.utc).isoformat()
        prepared.append((
            migration_id,
            index,
            created,
            group,
            model,
            _nonnegative_int(entry.get("idr"), entry_index=index, field="idr"),
            _nonnegative_int(
                entry.get("input_tokens"), entry_index=index, field="input_tokens"
            ),
            _nonnegative_int(
                entry.get("output_tokens"), entry_index=index, field="output_tokens"
            ),
            _nonnegative_int(
                entry.get("cached_tokens"), entry_index=index, field="cached_tokens"
            ),
            _nonnegative_int(
                entry.get("cache_write_tokens"),
                entry_index=index,
                field="cache_write_tokens",
            ),
        ))

    with connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = _existing_migration(connection)
        if existing is not None:
            connection.commit()
            return _migration_report(existing)
        connection.execute(
            "INSERT INTO budget_migrations "
            "(migration_id, source_sha256, imported_at, imported_entries) "
            "VALUES (?, ?, ?, ?)",
            (
                migration_id,
                source_hash,
                dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                len(prepared),
            ),
        )
        connection.executemany(
            "INSERT INTO legacy_budget_entries "
            "(migration_id, entry_index, created_at, group_name, model, amount, "
            "input_tokens, output_tokens, cached_tokens, cache_write_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            prepared,
        )
        connection.commit()
    return {
        "status": "migrated",
        "migration_id": migration_id,
        "imported_entries": len(prepared),
        "source_sha256": source_hash,
    }


def health_report(database: Path, *, stale_after_hours: int = STALE_AFTER_HOURS) -> dict[str, Any]:
    database = require_local_database(database)
    if not database.exists():
        return {
            "database": str(database),
            "integrity": "not_initialized",
            "states": {},
            "stale_liabilities": 0,
            "stale_reservations": 0,
            "over_reservation_count": 0,
            "over_reservation_amount": 0,
            "migrations": 0,
            "local_filesystem_check": "pass",
        }
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=stale_after_hours)
    with connect(database) as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity = "ok" if integrity_rows == [("ok",)] else "failed"
        states = {
            str(state): int(count)
            for state, count in connection.execute(
                "SELECT state, COUNT(*) FROM budget_reservations GROUP BY state"
            )
        }
        stale = {
            str(state): int(count)
            for state, count in connection.execute(
                "SELECT state, COUNT(*) FROM budget_reservations "
                "WHERE state IN ('reserved', 'pending_liability') AND created_at < ? "
                "GROUP BY state",
                (cutoff.isoformat(),),
            )
        }
        migrations = int(connection.execute(
            "SELECT COUNT(*) FROM budget_migrations"
        ).fetchone()[0])
        over_reservation = connection.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(actual_idr - reserved_idr), 0) "
            "FROM budget_reservations "
            "WHERE state = 'settled' AND actual_idr > reserved_idr"
        ).fetchone()
    return {
        "database": str(database),
        "integrity": integrity,
        "states": states,
        "stale_liabilities": stale.get("pending_liability", 0),
        "stale_reservations": stale.get("reserved", 0),
        "over_reservation_count": int(over_reservation[0]),
        "over_reservation_amount": int(over_reservation[1]),
        "migrations": migrations,
        "local_filesystem_check": "pass",
    }


def financial_report(database: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Report metered cash and subscription quota as separate quantities."""
    database = require_local_database(database)
    groups = config["budget"].get("billing_modes", {})
    result: dict[str, Any] = {
        "currency": config["budget"].get("currency", "unknown"),
        "groups": {},
        "cash_outlay": 0,
        "quota_consumed": 0,
        "pending_liability": 0,
        "pending_liability_by_billing_mode": {},
        "subscription_fees": {
            str(group): settings.get("subscription_fee")
            for group, settings in groups.items()
            if isinstance(settings, dict)
            and settings.get("mode") == "subscription"
        },
        "unavailable": {},
    }
    if not database.exists():
        return result
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT group_name,
                   COALESCE(SUM(CASE WHEN state = 'settled' THEN COALESCE(actual_idr, reserved_idr) ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN state = 'reserved' THEN reserved_idr ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN state = 'pending_liability' THEN reserved_idr ELSE 0 END), 0)
            FROM budget_reservations GROUP BY group_name
            """
        ).fetchall()
        legacy_rows = {
            str(group): int(total)
            for group, total in connection.execute(
                "SELECT group_name, COALESCE(SUM(amount), 0) "
                "FROM legacy_budget_entries GROUP BY group_name"
            )
        }
    for group, settled, reserved, pending in rows:
        mode = str(groups.get(group, {}).get("mode", "unknown"))
        settled_total = int(settled) + legacy_rows.pop(str(group), 0)
        entry = {
            "billing_mode": mode,
            "settled": settled_total,
            "reserved": int(reserved),
            "pending_liability": int(pending),
        }
        result["groups"][str(group)] = entry
        result["pending_liability"] += int(pending)
        if pending:
            pending_mode = mode if mode in {
                "subscription", "quota-equivalent", "free-tier", "metered"
            } else "unknown"
            by_mode = result["pending_liability_by_billing_mode"]
            by_mode[pending_mode] = by_mode.get(pending_mode, 0) + int(pending)
        if mode in {"subscription", "quota-equivalent", "free-tier"}:
            result["quota_consumed"] += settled_total
        elif mode == "metered":
            result["cash_outlay"] += settled_total
        else:
            result["unavailable"][str(group)] = settled_total
    for group, total in legacy_rows.items():
        mode = str(groups.get(group, {}).get("mode", "unknown"))
        result["groups"][group] = {
            "billing_mode": mode,
            "settled": total,
            "reserved": 0,
            "pending_liability": 0,
        }
        if mode in {"subscription", "quota-equivalent", "free-tier"}:
            result["quota_consumed"] += total
        elif mode == "metered":
            result["cash_outlay"] += total
        else:
            result["unavailable"][group] = total
    return result
