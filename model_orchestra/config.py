"""Public configuration discovery and lightweight validation."""

from __future__ import annotations

import json
import os
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

CONFIG_ENV = "MODEL_ORCHESTRA_CONFIG"
# Mirrors model_orchestra/data/config.schema.json; keep the two in step.
ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
SUPPORTED_CLIENTS = frozenset({"openai", "anthropic"})
SUPPORTED_OBJECTIVES = frozenset({"strict_savings", "preserve_strong_model"})
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "model-orchestra" / "config.json"


def example_config_path() -> Path:
    return Path(str(files("model_orchestra.data").joinpath("config.example.json")))


def schema_path() -> Path:
    return Path(str(files("model_orchestra.data").joinpath("config.schema.json")))


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """Resolve explicit, environment, then per-user configuration."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get(CONFIG_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH.resolve()
    raise FileNotFoundError(
        f"No configuration found. Pass --config, set {CONFIG_ENV}, or create "
        f"{DEFAULT_CONFIG_PATH}. Start from {example_config_path()}."
    )


def load_config(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {candidate}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("configuration root must be an object")
    return value


def validate_config(config: dict[str, Any]) -> None:
    """Validate the stable v1 fields needed before importing the runtime."""
    if config.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    for field in ("providers", "workers", "pipelines", "cost_control", "budget"):
        if not isinstance(config.get(field), dict) or not config[field]:
            raise ValueError(f"{field} must be a non-empty object")

    providers = config["providers"]
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            raise ValueError(f"provider {name!r} must be an object")
        if not isinstance(provider.get("base_url"), str) or not provider["base_url"]:
            raise ValueError(f"provider {name!r} requires base_url")
        env_name = provider.get("api_key_env")
        if not isinstance(env_name, str) or not env_name:
            raise ValueError(f"provider {name!r} requires api_key_env")
        if not ENV_NAME_PATTERN.fullmatch(env_name):
            raise ValueError(
                f"provider {name!r} api_key_env {env_name!r} must be an uppercase "
                "environment variable name"
            )
        client = provider.get("client")
        if client is not None and client not in SUPPORTED_CLIENTS:
            raise ValueError(
                f"provider {name!r} client {client!r} must be one of: "
                + ", ".join(sorted(SUPPORTED_CLIENTS))
            )

    workers = config["workers"]
    for alias in ("flash", "terra", "sol", "k3"):
        if alias not in workers:
            raise ValueError(f"workers requires compatibility alias {alias!r}")
    for alias, spec in workers.items():
        if not isinstance(spec, str) or "/" not in spec:
            raise ValueError(f"worker {alias!r} must use provider/model-id")
        provider = spec.split("/", 1)[0]
        if provider not in providers:
            raise ValueError(f"worker {alias!r} references unknown provider {provider!r}")

    required_routes = {
        "speed-run", "draft-refine", "debug", "test-factory",
        "security", "repository-edit",
    }
    missing_routes = sorted(required_routes - set(config["pipelines"]))
    if missing_routes:
        raise ValueError(
            "pipelines requires compatibility routes: " + ", ".join(missing_routes)
        )
    for route, pipeline in config["pipelines"].items():
        # Underscore-prefixed keys are the inline-comment convention used
        # throughout these configs; they are notes, not routes.
        if route.startswith("_"):
            continue
        if not isinstance(pipeline, dict):
            raise ValueError(f"pipeline {route!r} must be an object")
        references = [
            value for key, value in pipeline.items()
            if key in {"single", "drafter", "refiner", "judge"}
            and isinstance(value, str)
        ]
        references.extend(
            value for value in pipeline.get("stages", [])
            if isinstance(value, str)
        )
        unknown = sorted(set(references) - set(workers))
        if unknown:
            raise ValueError(
                f"pipeline {route!r} references unknown workers: " + ", ".join(unknown)
            )

    cost_control = config["cost_control"]
    for field in ("host_model", "implementation_model"):
        alias = cost_control.get(field)
        if not isinstance(alias, str) or not alias:
            raise ValueError(f"cost_control.{field} must be a non-empty string")
        if alias not in workers:
            raise ValueError(
                f"cost_control.{field} references unknown worker {alias!r}"
            )
    objective = cost_control.get("objective")
    if objective not in SUPPORTED_OBJECTIVES:
        raise ValueError(
            "cost_control.objective must be one of: "
            + ", ".join(sorted(SUPPORTED_OBJECTIVES))
        )

    budget = config["budget"]
    currency = budget.get("currency")
    if not isinstance(currency, str) or not currency:
        raise ValueError("budget.currency must be a non-empty string")
    for field in (
        "provider_groups",
        "provider_limits",
        "billing_modes",
        "pricing_per_million_tokens",
    ):
        if not isinstance(budget.get(field), dict):
            raise ValueError(f"budget.{field} must be an object")


def credential_names(config: dict[str, Any]) -> tuple[str, ...]:
    names: set[str] = set()
    for provider in config.get("providers", {}).values():
        if not isinstance(provider, dict):
            continue
        values = provider.get("api_key_envs") or [provider.get("api_key_env")]
        names.update(value for value in values if isinstance(value, str) and value)
    return tuple(sorted(names))
