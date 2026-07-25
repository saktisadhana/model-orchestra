# Phase 1: Installable Public Alpha

## Goal

Ship a provider-neutral v0.2 alpha that a new user can install and evaluate without the author's private setup.

## Why This Saves Money

A personal source checkout cannot prove the plugin helps anyone, and setup friction consumes expensive host-model time. The alpha tests whether the core workflow is usable before more infrastructure is built.

## Deliverables

### Package and entry points

- Create `pyproject.toml`.
- Create package directory `model_orchestra/` incrementally.
- Keep `server.py` as a compatibility shim.
- Provide `model-orchestra serve`, `check`, `doctor`, and `benchmark --live`.
- Build wheel and sdist; test installation outside the checkout.

### Configuration v1

Separate providers, models, immutable policy floors, routes, pricing/billing, budgets, and presentation. Provide a versioned schema and migration from `config.json`.

Public examples contain environment-variable names only. Remove private aliases, private gateways, machine paths, and personal IDR limits from first-run documentation.

### Provider and host minimum

Support and test:

- one OpenAI-compatible provider;
- one Anthropic-compatible provider;
- custom base URL;
- Hermes stdio MCP;
- Claude Code stdio MCP;
- MCP Inspector.

Zed ACP remains experimental. Other hosts remain unverified until repeatable smoke tests exist.

### Stable alpha surface

Default tools:

- `list_workers`
- `route_preview`
- `delegate`
- `delegate_verified`
- `orchestrate_change`
- `batch_delegate`
- `orchestration_report`
- `cost_report`

Keep `compact`, `pipeline`, `swarm`, and new intent wrappers disabled or experimental unless measured evidence justifies them.

## Validation

```bash
python -m build
python -m venv .tmp-install-venv
# Install the built wheel using the platform-appropriate venv Python.
python tools/check.py
```

CI matrix: supported Python versions on Windows and Linux. Install wheel and sdist from an empty environment, run MCP startup/discovery, invoke `list_workers` and `route_preview`, and verify clean shutdown.

## User Trial

A user who did not build the project must:

1. install from the artifact;
2. copy the public configuration;
3. set one credential variable;
4. register one supported host;
5. preview a route;
6. complete one bounded delegated task;
7. inspect usage and uninstall.

Target: under ten minutes without private instructions.

## Exit Gate: v0.2 Alpha

- Phase 0 still passes.
- Wheel and sdist are reproducible and install on Windows/Linux.
- No secret or private service appears in examples/logs/errors.
- Three supported-client smoke tests pass.
- One expected missing-credential failure is actionable and redacted.
- Alpha tool schemas are snapshotted.

## Stop-Loss

Do not add more providers, hosts, tools, or a UI during alpha. If a new user cannot complete the core flow in ten minutes, spend the next cycle only on setup and diagnostics.
