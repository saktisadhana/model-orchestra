"""Phase 03 budget and dogfood regression tests."""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing as mp
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_orchestra import budget as budget_store
from model_orchestra import dogfood
import model_orchestra.cli as cli
import model_orchestra.config as public_config
import server


def _reserve_from_process(database: str, start, results) -> None:
    server.BUDGET_DB_PATH = Path(database)
    server.BUDGET_STATE_PATH = Path(database).with_suffix(".legacy.json")
    server.BUDGET_PROVIDER_LIMITS = {
        "opencode-go": {
            "monthly": 1,
            "weekly": 1,
            "daily": 1,
            "five_hour": 1,
        }
    }
    start.wait()
    try:
        results.put(server._budget_reserve("flash", 1, 1))
    except RuntimeError:
        results.put("blocked")


def _config(tmp_path: Path) -> tuple[Path, dict]:
    config = public_config.load_config(public_config.example_config_path())
    config["budget"]["state_file"] = "legacy.json"
    config["budget"]["database_file"] = "budget.sqlite3"
    config["budget"]["dogfood_database_file"] = "dogfood.sqlite3"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


def test_legacy_budget_migration_is_exact_once(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"entries": [{
        "at": "2026-07-25T00:00:00+00:00",
        "group": "metered-workers",
        "model": "flash",
        "input_tokens": 10,
        "output_tokens": 20,
        "idr": 7,
    }]}), encoding="utf-8")
    database = tmp_path / "budget.sqlite3"

    first = budget_store.migrate_legacy_json(source, database)
    second = budget_store.migrate_legacy_json(source, database)
    source.unlink()
    third = budget_store.migrate_legacy_json(source, database)

    assert first["status"] == "migrated"
    assert first["imported_entries"] == 1
    assert second["status"] == "already_migrated"
    assert third == second
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_budget_entries"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM budget_migrations"
        ).fetchone() == (1,)


def test_legacy_budget_migration_cannot_reimport_a_modified_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"entries": [{
        "at": "2026-07-25T00:00:00+00:00",
        "group": "metered-workers",
        "model": "flash",
        "idr": 7,
    }]}), encoding="utf-8")
    database = tmp_path / "budget.sqlite3"
    first = budget_store.migrate_legacy_json(source, database)

    source.write_text(json.dumps({"entries": [
        {
            "at": "2026-07-25T00:00:00+00:00",
            "group": "metered-workers",
            "model": "flash",
            "idr": 7,
        },
        {
            "at": "2026-07-25T01:00:00+00:00",
            "group": "metered-workers",
            "model": "flash",
            "idr": 9,
        },
    ]}), encoding="utf-8")
    second = budget_store.migrate_legacy_json(source, database)

    assert second["status"] == "already_migrated"
    assert second["source_sha256"] == first["source_sha256"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_budget_entries"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM budget_migrations"
        ).fetchone() == (1,)


def test_legacy_budget_migration_rejects_malformed_entries_atomically(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"entries": [
        {
            "at": "2026-07-25T00:00:00+00:00",
            "group": "metered-workers",
            "model": "flash",
            "idr": 7,
        },
        {"at": "not-a-timestamp", "group": "metered-workers", "idr": 9},
    ]}), encoding="utf-8")
    database = tmp_path / "budget.sqlite3"

    with pytest.raises(ValueError, match="entry 1"):
        budget_store.migrate_legacy_json(source, database)

    assert not database.exists()


def test_legacy_budget_migration_normalizes_timestamps_to_utc(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"entries": [{
        # 01:00 at UTC-07 is 08:00 UTC. Text comparison against 07:00 UTC
        # would incorrectly exclude the unnormalized timestamp.
        "at": "2026-07-25T01:00:00-07:00",
        "group": "metered-workers",
        "model": "flash",
        "idr": 7,
    }]}), encoding="utf-8")
    database = tmp_path / "budget.sqlite3"

    budget_store.migrate_legacy_json(source, database)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT created_at, amount FROM legacy_budget_entries "
            "WHERE created_at >= ?",
            ("2026-07-25T07:00:00+00:00",),
        ).fetchone()
    assert row == ("2026-07-25T08:00:00+00:00", 7)


def test_budget_health_reports_integrity_and_stale_liability(tmp_path: Path) -> None:
    config_path, _ = _config(tmp_path)
    database = tmp_path / "budget.sqlite3"
    stale = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
    ).isoformat()
    with budget_store.connect(database) as connection:
        connection.execute(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, state) "
            "VALUES ('pending-1', ?, 'metered-workers', 'flash', 9, 'pending_liability')",
            (stale,),
        )

    report = budget_store.health_report(database)

    assert report["integrity"] == "ok"
    assert report["stale_liabilities"] == 1
    assert report["states"] == {"pending_liability": 1}
    assert cli._budget_health(SimpleNamespace(config=config_path)) == 1


def test_budget_health_reports_actual_charge_above_reservation(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite3"
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with budget_store.connect(database) as connection:
        connection.execute(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, state, actual_idr) "
            "VALUES ('overrun', ?, 'metered-workers', 'flash', 3, 'settled', 5)",
            (now,),
        )

    report = budget_store.health_report(database)

    assert report["over_reservation_count"] == 1
    assert report["over_reservation_amount"] == 2


def test_budget_ceiling_is_atomic_across_processes(tmp_path: Path) -> None:
    context = mp.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    database = tmp_path / "budget.sqlite3"
    processes = [
        context.Process(
            target=_reserve_from_process,
            args=(str(database), start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()
        assert process.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in processes]
    assert outcomes.count("blocked") == 1
    reservation_ids = [value for value in outcomes if value != "blocked"]
    assert len(reservation_ids) == 1
    assert len(reservation_ids[0]) == 32


def test_missing_provider_usage_keeps_reservation_pending(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite3"
    original_database = server.BUDGET_DB_PATH
    try:
        server.BUDGET_DB_PATH = database
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with budget_store.connect(database) as connection:
            connection.execute(
                "INSERT INTO budget_reservations "
                "(operation_id, created_at, group_name, model, reserved_idr, state) "
                "VALUES ('missing-usage', ?, 'opencode-go', 'flash', 9, 'reserved')",
                (now,),
            )

        server._budget_settle("missing-usage", "flash", None)

        with sqlite3.connect(database) as connection:
            state = connection.execute(
                "SELECT state, actual_idr FROM budget_reservations "
                "WHERE operation_id = 'missing-usage'"
            ).fetchone()
        assert state == ("pending_liability", None)
    finally:
        server.BUDGET_DB_PATH = original_database


def test_chat_reservation_includes_system_prompt_tokens() -> None:
    original_create = server._create_with_retry
    original_track = server._track
    captured: dict[str, object] = {}

    def fake_create(provider: str, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=102,
                completion_tokens=1,
                prompt_tokens_details=None,
            ),
        )

    server._create_with_retry = fake_create
    server._track = lambda *args, **kwargs: None
    try:
        assert server.chat("flash", "tiny", system="s" * 400, max_tokens=1) == "ok"
    finally:
        server._create_with_retry = original_create
        server._track = original_track

    assert int(captured["budget_input_tokens"]) >= 101


def test_direct_delegate_records_delegated_route_and_economics() -> None:
    original_impl = server._delegate_impl
    original_available = server._model_available
    original_record = server.dogfood.record_event
    captured: dict[str, object] = {}
    task = "format this JSON mechanically"
    expected = server._direct_model_economics(task, "flash")

    server._delegate_impl = lambda *args, **kwargs: "ok"
    server._model_available = lambda model: True
    server.dogfood.record_event = (
        lambda database, **kwargs: captured.update(kwargs) or "event"
    )
    try:
        assert server.delegate(task, model="flash") == "ok"
    finally:
        server._delegate_impl = original_impl
        server._model_available = original_available
        server.dogfood.record_event = original_record

    assert captured["route"] == "direct-model"
    assert captured["task_kind"] == "mechanical"
    assert captured["quota_consumed"] == expected["quota_equivalent"]
    assert captured["strong_capacity_preserved"] == expected[
        "strong_model_capacity_preserved"
    ]
    assert float(captured["strong_capacity_preserved"]) > 0


def test_nested_delegate_does_not_double_count_outer_orchestration() -> None:
    original_impl = server._delegate_impl
    original_economics = server._direct_model_economics
    original_available = server._model_available
    original_record = server.dogfood.record_event
    events: list[dict[str, object]] = []

    server._delegate_impl = lambda *args, **kwargs: "ok"
    server._model_available = lambda model: True
    server._direct_model_economics = lambda *args, **kwargs: {
        "eligible": True,
        "billing_mode": "subscription",
        "incremental_cash": 0,
        "quota_equivalent": 1,
        "strong_model_capacity_preserved": 2,
    }
    server.dogfood.record_event = (
        lambda database, **kwargs: events.append(kwargs) or "event"
    )
    outer = {"tool_steps": 0, "parent": None}
    token = server._ORCHESTRATION_COLLECTOR.set(outer)
    try:
        assert server.delegate("format this JSON mechanically", model="flash") == "ok"
    finally:
        server._ORCHESTRATION_COLLECTOR.reset(token)
        server._delegate_impl = original_impl
        server._direct_model_economics = original_economics
        server._model_available = original_available
        server.dogfood.record_event = original_record

    assert events == []


def test_unknown_metered_economics_fail_closed_in_dogfood_metadata() -> None:
    original_record = server.dogfood.record_event
    captured: dict[str, object] = {}
    server.dogfood.record_event = (
        lambda database, **kwargs: captured.update(kwargs) or "event"
    )
    try:
        server._track_orchestration(
            "delegate",
            "direct-model",
            ["flash"],
            "success",
            None,
            0.1,
            0,
            10,
            task_kind="mechanical",
            economics={
                "billing_mode": "metered",
                "incremental_cash": None,
                "strong_model_capacity_preserved": 5,
            },
        )
    finally:
        server.dogfood.record_event = original_record

    assert captured["cash_outlay"] == 0
    assert captured["strong_capacity_preserved"] == 0
    assert captured["pending_liability"] == 5


def test_metered_direct_economics_remain_pending_until_cash_known() -> None:
    group = server._provider_group("flash")
    assert group is not None
    marker = object()
    original_billing = server.BUDGET_BILLING_MODES.get(group, marker)
    original_record = server.dogfood.record_event
    captured: dict[str, object] = {}
    try:
        server.BUDGET_BILLING_MODES[group] = {
            "mode": "metered",
            "metered_overage_enabled": True,
        }
        economics = server._direct_model_economics(
            "format this JSON mechanically", "flash"
        )
        server.dogfood.record_event = (
            lambda database, **kwargs: captured.update(kwargs) or "event"
        )
        server._track_orchestration(
            "delegate",
            "direct-model",
            ["flash"],
            "success",
            None,
            0.1,
            0,
            10,
            task_kind="mechanical",
            economics=economics,
        )
    finally:
        server.dogfood.record_event = original_record
        if original_billing is marker:
            server.BUDGET_BILLING_MODES.pop(group, None)
        else:
            server.BUDGET_BILLING_MODES[group] = original_billing

    assert economics["incremental_cash"] is None
    assert captured["cash_outlay"] == 0
    assert captured["strong_capacity_preserved"] == 0
    assert captured["pending_liability"] == economics["direct_host_cost"]


def test_provider_enabled_fails_closed_on_non_boolean_values() -> None:
    provider = next(iter(server.PROVIDERS))
    marker = object()
    original = server.PROVIDERS[provider].get("enabled", marker)
    try:
        server.PROVIDERS[provider]["enabled"] = "false"
        assert server._provider_enabled(provider) is False
    finally:
        if original is marker:
            server.PROVIDERS[provider].pop("enabled", None)
        else:
            server.PROVIDERS[provider]["enabled"] = original


def test_provider_key_discovery_fails_closed_on_non_boolean_enabled() -> None:
    provider = next(iter(server.PROVIDERS))
    original_provider = dict(server.PROVIDERS[provider])
    environment_name = "MODEL_ORCHESTRA_PHASE03_SYNTHETIC_KEY"
    original_environment = server.os.environ.get(environment_name)
    try:
        server.PROVIDERS[provider]["enabled"] = "false"
        server.PROVIDERS[provider]["api_key_envs"] = [environment_name]
        server.os.environ[environment_name] = "synthetic-test-value"

        assert server._keys_for(provider) == []
    finally:
        server.PROVIDERS[provider].clear()
        server.PROVIDERS[provider].update(original_provider)
        if original_environment is None:
            server.os.environ.pop(environment_name, None)
        else:
            server.os.environ[environment_name] = original_environment


def test_provider_client_construction_fails_closed_on_non_boolean_enabled() -> None:
    provider = next(iter(server.PROVIDERS))
    marker = object()
    original = server.PROVIDERS[provider].get("enabled", marker)
    try:
        server.PROVIDERS[provider]["enabled"] = "false"

        with pytest.raises(RuntimeError, match="disabled"):
            server.client_for(provider, key="synthetic-test-value")
    finally:
        if original is marker:
            server.PROVIDERS[provider].pop("enabled", None)
        else:
            server.PROVIDERS[provider]["enabled"] = original


def test_budget_refuses_unambiguous_network_paths() -> None:
    with pytest.raises(ValueError, match="local filesystem"):
        budget_store.require_local_database(Path("//server/share/budget.sqlite3"))
    with pytest.raises(ValueError, match="local filesystem"):
        budget_store.require_local_database(Path("file://server/share/budget.sqlite3"))


def test_dogfood_refuses_unambiguous_network_paths() -> None:
    with pytest.raises(ValueError, match="local filesystem"):
        with dogfood.connect(Path("//server/share/dogfood.sqlite3")):
            pass


def test_financial_report_separates_subscription_quota_and_metered_cash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "budget.sqlite3"
    config = public_config.load_config(public_config.example_config_path())
    config["budget"]["billing_modes"] = {
        "subscription-workers": {
            "mode": "subscription",
            "metered_overage_enabled": False,
            "subscription_fee": 10,
        },
        "metered-strong": {"mode": "metered", "metered_overage_enabled": False},
    }
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with budget_store.connect(database) as connection:
        connection.executemany(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, state, actual_idr) "
            "VALUES (?, ?, ?, ?, ?, 'settled', ?)",
            [
                ("quota", now, "subscription-workers", "flash", 11, 11),
                ("cash", now, "metered-strong", "terra", 7, 7),
            ],
        )

    report = budget_store.financial_report(database, config)

    assert report["quota_consumed"] == 11
    assert report["cash_outlay"] == 7
    assert report["subscription_fees"] == {"subscription-workers": 10}
    assert report["pending_liability"] == 0
    assert report["pending_liability_by_billing_mode"] == {}


def test_financial_report_merges_legacy_and_new_totals_for_same_group(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"entries": [{
        "at": "2026-07-25T08:00:00+00:00",
        "group": "metered-workers",
        "model": "flash",
        "idr": 5,
    }]}), encoding="utf-8")
    database = tmp_path / "budget.sqlite3"
    budget_store.migrate_legacy_json(source, database)
    with budget_store.connect(database) as connection:
        connection.execute(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, "
            "state, actual_idr, settlement_id) "
            "VALUES ('new-settlement', ?, 'metered-workers', 'flash', 7, "
            "'settled', 7, 'new-settlement')",
            ("2026-07-26T08:00:00+00:00",),
        )
    config = public_config.load_config(public_config.example_config_path())
    config["budget"]["billing_modes"] = {
        "metered-workers": {"mode": "metered", "overage_enabled": True},
    }

    report = budget_store.financial_report(database, config)

    assert report["cash_outlay"] == 12
    assert report["groups"]["metered-workers"]["settled"] == 12


def test_financial_report_exposes_unknown_billing_as_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "budget.sqlite3"
    config = public_config.load_config(public_config.example_config_path())
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with budget_store.connect(database) as connection:
        connection.execute(
            "INSERT INTO budget_reservations "
            "(operation_id, created_at, group_name, model, reserved_idr, state, actual_idr) "
            "VALUES ('unknown', ?, 'unknown-workers', 'mystery', 9, 'settled', 9)",
            (now,),
        )

    report = budget_store.financial_report(database, config)

    assert report["cash_outlay"] == 0
    assert report["quota_consumed"] == 0
    assert report["unavailable"] == {"unknown-workers": 9}


def test_financial_report_separates_pending_liability_by_billing_mode(
    tmp_path: Path,
) -> None:
    database = tmp_path / "budget.sqlite3"
    config = public_config.load_config(public_config.example_config_path())
    config["budget"]["billing_modes"] = {
        "subscription-workers": {
            "mode": "subscription",
            "metered_overage_enabled": False,
        },
        "metered-workers": {
            "mode": "metered",
            "metered_overage_enabled": False,
        },
    }
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with budget_store.connect(database) as connection:
        for operation_id, group, amount in (
            ("cash-pending", "metered-workers", 11),
            ("quota-pending", "subscription-workers", 7),
            ("unknown-pending", "unknown-workers", 3),
        ):
            connection.execute(
                "INSERT INTO budget_reservations "
                "(operation_id, created_at, group_name, model, reserved_idr, state) "
                "VALUES (?, ?, ?, 'model', ?, 'pending_liability')",
                (operation_id, now, group, amount),
            )

    report = budget_store.financial_report(database, config)

    assert report["pending_liability"] == 21
    assert report["pending_liability_by_billing_mode"] == {
        "metered": 11,
        "subscription": 7,
        "unknown": 3,
    }


def test_dogfood_store_is_metadata_only_and_reports_gates(tmp_path: Path) -> None:
    database = tmp_path / "dogfood.sqlite3"
    event_id = dogfood.record_event(
        database,
        source="auto_delegate",
        task_kind="mechanical",
        route="speed-run",
        models=["flash"],
        outcome="success",
        fallback_category=None,
        latency_seconds=1.25,
        tool_steps=0,
        returned_chars=400,
        quota_consumed=2.0,
        strong_capacity_preserved=5.0,
    )
    assert dogfood.label_event(
        database, event_id, accepted=True, redo=False, intervention=False
    )

    events = dogfood.recent_events(database)
    report = dogfood.build_report(database)

    assert set(events[0]) == {
        "event_id", "created_at", "source", "task_kind", "route", "models",
        "outcome", "fallback_category", "latency_ms", "tool_steps",
        "returned_chars", "cash_outlay_microunits", "quota_consumed_microunits",
        "strong_capacity_preserved_microunits", "pending_liability_microunits",
        "accepted", "redo", "intervention",
    }
    assert report["accepted_rate"] == 1.0
    assert report["economic_evidence"] == "estimated"
    assert report["estimated_budget_benefit_before_redo_cost"] == 5.0
    assert "verified_budget_benefit_before_redo_cost" not in report
    assert report["decision"] == "INSUFFICIENT_EVIDENCE"
    assert report["gates"]["zero_security_downgrades"]
    assert "prompts" in report["privacy"].lower()


def test_dogfood_event_timestamps_are_normalized_to_utc(tmp_path: Path) -> None:
    database = tmp_path / "dogfood.sqlite3"
    event_id = dogfood.record_event(
        database,
        source="delegate",
        task_kind="mechanical",
        route="direct-model",
        models=["flash"],
        outcome="success",
        fallback_category=None,
        latency_seconds=0.1,
        tool_steps=0,
        returned_chars=2,
        created_at="2026-07-25T01:00:00-07:00",
    )

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT created_at FROM dogfood_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
    assert stored == "2026-07-25T08:00:00+00:00"


def test_dogfood_skips_and_failures_do_not_claim_preserved_capacity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dogfood.sqlite3"
    dogfood.record_event(
        database,
        source="auto_delegate",
        task_kind="judgment",
        route="host",
        models=[],
        outcome="host_skip",
        fallback_category="host_judgment",
        latency_seconds=0.01,
        tool_steps=0,
        returned_chars=20,
        strong_capacity_preserved=5,
    )
    dogfood.record_event(
        database,
        source="auto_delegate",
        task_kind="mechanical",
        route="speed-run",
        models=["flash"],
        outcome="infrastructure_failure",
        fallback_category="worker_error",
        latency_seconds=1,
        tool_steps=0,
        returned_chars=0,
        strong_capacity_preserved=6,
    )

    report = dogfood.build_report(database)

    assert report["strong_model_capacity_preserved"] == 0
    assert report["estimated_budget_benefit_before_redo_cost"] == 0


def test_dogfood_report_requires_every_delegation_to_be_labeled(tmp_path: Path) -> None:
    database = tmp_path / "dogfood.sqlite3"
    dogfood.record_event(
        database,
        source="orchestrate_change",
        task_kind="repository",
        route="repository-edit",
        models=["k27-oc"],
        outcome="success",
        fallback_category=None,
        latency_seconds=2,
        tool_steps=1,
        returned_chars=120,
    )
    report = dogfood.build_report(database)
    assert report["decision"] == "INSUFFICIENT_EVIDENCE"
    assert not report["gates"]["all_delegations_labeled"]


def test_dogfood_report_detects_judgment_downgrades(tmp_path: Path) -> None:
    database = tmp_path / "dogfood.sqlite3"
    event_id = dogfood.record_event(
        database,
        source="auto_delegate",
        task_kind="judgment",
        route="reasoning",
        models=["k27"],
        outcome="success",
        fallback_category=None,
        latency_seconds=1,
        tool_steps=0,
        returned_chars=100,
        strong_capacity_preserved=2,
    )
    dogfood.label_event(database, event_id, accepted=True)

    report = dogfood.build_report(database)

    assert report["judgment_downgrades"] == 1
    assert not report["gates"]["zero_judgment_downgrades"]


def test_disabled_provider_is_not_required_for_credentials() -> None:
    config = public_config.load_config(public_config.example_config_path())
    config["providers"]["disabled-strong"] = {
        "enabled": False,
        "base_url": "https://disabled.invalid/v1",
        "api_key_env": "DISABLED_STRONG_API_KEY",
    }
    public_config.validate_config(config)
    assert "DISABLED_STRONG_API_KEY" not in public_config.credential_names(config)


def test_phase03_cli_budget_and_trial_commands(tmp_path: Path, capsys) -> None:
    config_path, _ = _config(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"entries": []}\n', encoding="utf-8")

    assert cli.main([
        "budget", "migrate", "--config", str(config_path), "--from", str(legacy)
    ]) == 0
    assert cli.main(["budget", "health", "--config", str(config_path)]) == 0
    assert cli.main(["budget", "report", "--config", str(config_path)]) == 0
    assert cli.main(["trial", "report", "--config", str(config_path)]) == 0

    output = capsys.readouterr().out
    assert '"integrity": "ok"' in output
    assert '"decision": "INSUFFICIENT_EVIDENCE"' in output


def test_retired_provider_fails_closed_and_repository_uses_opencode() -> None:
    assert server.PROVIDERS["retired-strong"]["enabled"] is False
    assert server.resolve(server.IMPLEMENTATION_MODEL) == (
        "opencode-go", "kimi-k2.7-code"
    )
    assert server.PIPELINES["repository-edit"]["single"] == "k27-oc"
    assert server._keys_for("retired-strong") == []
    with pytest.raises(RuntimeError, match="disabled"):
        server.client_for("retired-strong", key="unused")
    security = server._route_plan("Perform a security audit of auth.py")
    assert security["selected_route"] is None
    assert "unavailable" in security["reason"].lower()
