"""Privacy-preserving dogfood trial event store and reports."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterator

from .budget import require_local_database

TRIAL_DAYS = 14
ALLOWED_TASK_KINDS = frozenset({
    "tiny", "mechanical", "repository", "judgment", "security", "unknown"
})
ALLOWED_OUTCOMES = frozenset({
    "success", "host_skip", "infrastructure_failure", "unusable_output",
    "explicit_cost_cap", "error", "unknown",
})


@contextlib.contextmanager
def connect(database: Path) -> Iterator[sqlite3.Connection]:
    database = require_local_database(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dogfood_events (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                route TEXT NOT NULL,
                models_json TEXT NOT NULL,
                outcome TEXT NOT NULL,
                fallback_category TEXT,
                latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
                tool_steps INTEGER NOT NULL CHECK (tool_steps >= 0),
                returned_chars INTEGER NOT NULL CHECK (returned_chars >= 0),
                cash_outlay_microunits INTEGER NOT NULL DEFAULT 0,
                quota_consumed_microunits INTEGER NOT NULL DEFAULT 0,
                strong_capacity_preserved_microunits INTEGER NOT NULL DEFAULT 0,
                pending_liability_microunits INTEGER NOT NULL DEFAULT 0,
                accepted INTEGER CHECK (accepted IN (0, 1)),
                redo INTEGER CHECK (redo IN (0, 1)),
                intervention INTEGER CHECK (intervention IN (0, 1))
            )
            """
        )
        yield connection
    finally:
        connection.close()


def _safe_label(value: Any, *, maximum: int = 80) -> str:
    text = str(value or "unknown").strip()
    if not text:
        text = "unknown"
    if len(text) > maximum or any(char in text for char in "\r\n\t"):
        raise ValueError("dogfood metadata labels must be short single-line values")
    return text


def _microunits(value: Any) -> int:
    try:
        return max(0, round(float(value or 0) * 1_000_000))
    except (TypeError, ValueError):
        return 0


def record_event(
    path: Path,
    *,
    source: str,
    task_kind: str,
    route: str,
    models: list[str],
    outcome: str,
    fallback_category: str | None,
    latency_seconds: float,
    tool_steps: int,
    returned_chars: int,
    cash_outlay: float = 0,
    quota_consumed: float = 0,
    strong_capacity_preserved: float = 0,
    pending_liability: float = 0,
    created_at: str | None = None,
) -> str:
    """Append one bounded event. Prompt/source content, paths, and raw errors are absent."""
    task_kind = _safe_label(task_kind)
    if task_kind not in ALLOWED_TASK_KINDS:
        task_kind = "unknown"
    outcome = _safe_label(outcome)
    if outcome not in ALLOWED_OUTCOMES:
        outcome = "unknown"
    safe_route = _safe_label(route)
    safe_models = [_safe_label(model) for model in models[:8]]
    preserved_capacity = (
        strong_capacity_preserved
        if outcome == "success" and safe_route != "host"
        else 0
    )
    event_id = uuid.uuid4().hex
    timestamp = created_at or dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0
    ).isoformat()
    parsed_timestamp = dt.datetime.fromisoformat(timestamp)
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("dogfood event timestamp requires a timezone offset")
    timestamp = parsed_timestamp.astimezone(dt.timezone.utc).isoformat()
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO dogfood_events (
                event_id, created_at, source, task_kind, route, models_json,
                outcome, fallback_category, latency_ms, tool_steps, returned_chars,
                cash_outlay_microunits, quota_consumed_microunits,
                strong_capacity_preserved_microunits,
                pending_liability_microunits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                timestamp,
                _safe_label(source),
                task_kind,
                safe_route,
                json.dumps(safe_models, separators=(",", ":")),
                outcome,
                _safe_label(fallback_category) if fallback_category else None,
                max(0, round(float(latency_seconds or 0) * 1000)),
                max(0, int(tool_steps or 0)),
                max(0, int(returned_chars or 0)),
                _microunits(cash_outlay),
                _microunits(quota_consumed),
                _microunits(preserved_capacity),
                _microunits(pending_liability),
            ),
        )
        connection.commit()
    return event_id


def label_event(
    path: Path,
    event_id: str,
    *,
    accepted: bool,
    redo: bool = False,
    intervention: bool = False,
) -> bool:
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "UPDATE dogfood_events SET accepted = ?, redo = ?, intervention = ? "
            "WHERE event_id = ?",
            (int(accepted), int(redo), int(intervention), event_id),
        )
        connection.commit()
        return cursor.rowcount == 1


def recent_events(database: Path, *, days: int = TRIAL_DAYS) -> list[dict[str, Any]]:
    database = require_local_database(database)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, days))
    if not database.exists():
        return []
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT event_id, created_at, source, task_kind, route, models_json,
                   outcome, fallback_category, latency_ms, tool_steps, returned_chars,
                   cash_outlay_microunits, quota_consumed_microunits,
                   strong_capacity_preserved_microunits,
                   pending_liability_microunits, accepted, redo, intervention
            FROM dogfood_events WHERE created_at >= ? ORDER BY created_at, event_id
            """,
            (cutoff.isoformat(),),
        ).fetchall()
    keys = (
        "event_id", "created_at", "source", "task_kind", "route", "models_json",
        "outcome", "fallback_category", "latency_ms", "tool_steps", "returned_chars",
        "cash_outlay_microunits", "quota_consumed_microunits",
        "strong_capacity_preserved_microunits", "pending_liability_microunits",
        "accepted", "redo", "intervention",
    )
    events = []
    for row in rows:
        event = dict(zip(keys, row))
        event["models"] = json.loads(event.pop("models_json"))
        events.append(event)
    return events


def build_report(path: Path, *, days: int = TRIAL_DAYS) -> dict[str, Any]:
    events = recent_events(path, days=days)
    delegated = [event for event in events if event["route"] != "host"]
    labeled = [event for event in delegated if event["accepted"] is not None]
    accepted = [event for event in labeled if event["accepted"] == 1]
    redo = [event for event in labeled if event["redo"] == 1]
    intervention = [event for event in labeled if event["intervention"] == 1]
    security_downgrades = [
        event for event in delegated
        if event["task_kind"] == "security"
        and any(model not in {"sol", "security-unavailable"} for model in event["models"])
    ]
    judgment_downgrades = [
        event for event in delegated if event["task_kind"] == "judgment"
    ]

    def total(name: str) -> float:
        return round(sum(int(event[name]) for event in events) / 1_000_000, 6)

    accepted_rate = len(accepted) / len(labeled) if labeled else None
    redo_rate = len(redo) / len(labeled) if labeled else None
    intervention_rate = len(intervention) / len(labeled) if labeled else None
    cash = total("cash_outlay_microunits")
    quota = total("quota_consumed_microunits")
    capacity = total("strong_capacity_preserved_microunits")
    pending = total("pending_liability_microunits")
    estimated_benefit = round(capacity - cash - pending, 6)
    ready = bool(labeled) and len(labeled) == len(delegated)
    gates = {
        "all_delegations_labeled": ready,
        "accepted_rate_at_least_95_percent": (
            accepted_rate is not None and accepted_rate >= 0.95
        ),
        "redo_rate_below_5_percent": redo_rate is not None and redo_rate < 0.05,
        "intervention_rate_below_5_percent": (
            intervention_rate is not None and intervention_rate < 0.05
        ),
        "zero_security_downgrades": not security_downgrades,
        "zero_judgment_downgrades": not judgment_downgrades,
        "positive_estimated_budget_benefit": estimated_benefit > 0,
        "zero_pending_liability": pending == 0,
    }
    economic_evidence = "estimated"
    decision = (
        "INSUFFICIENT_EVIDENCE"
        if economic_evidence != "measured" or not ready
        else "GO" if all(gates.values())
        else "REWORK"
    )
    return {
        "schema_version": 1,
        "window_days": max(1, days),
        "events": len(events),
        "delegated_events": len(delegated),
        "labeled_events": len(labeled),
        "unlabeled_event_ids": [
            event["event_id"] for event in delegated
            if event["accepted"] is None
        ][:20],
        "accepted_rate": round(accepted_rate, 4) if accepted_rate is not None else None,
        "redo_rate": round(redo_rate, 4) if redo_rate is not None else None,
        "intervention_rate": (
            round(intervention_rate, 4) if intervention_rate is not None else None
        ),
        "cash_outlay": cash,
        "quota_consumed": quota,
        "strong_model_capacity_preserved": capacity,
        "pending_liability": pending,
        "economic_evidence": economic_evidence,
        "estimated_budget_benefit_before_redo_cost": estimated_benefit,
        "security_downgrades": len(security_downgrades),
        "judgment_downgrades": len(judgment_downgrades),
        "gates": gates,
        "decision": decision,
        "privacy": (
            "Metadata only. Prompts, source content, file paths, credentials, and raw errors "
            "are not stored."
        ),
    }
