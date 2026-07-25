# Model Orchestra

Model Orchestra is a policy-controlled, budget-aware MCP server for delegating bounded coding work across user-configured model providers.

The public alpha supports generic OpenAI-compatible and Anthropic-compatible endpoints, custom base URLs, capability floors, caller-owned verification, transactional budget reservations, compact artifact handoffs, and eight stable MCP tools.

## Install

```bash
python -m pip install model-orchestra==0.2.0a1
model-orchestra check --config /path/to/config.json
model-orchestra doctor --config /path/to/config.json
model-orchestra serve --config /path/to/config.json
```

Start from the packaged `config.example.json`. Configuration contains provider URLs, model aliases, policy, pricing, and budgets. Credentials are read only from the environment-variable names referenced by the configuration.

## Stable Tools

- `list_workers`
- `route_preview`
- `delegate`
- `delegate_verified`
- `orchestrate_change`
- `batch_delegate`
- `orchestration_report`
- `cost_report`

Experimental orchestration tools are not exposed by the public alpha CLI.

## Safety

- Security-sensitive work follows the configured strong-model floor.
- Repository implementation follows the configured workspace-agent floor.
- Provider attempts reserve budget transactionally before submission.
- Unknown provider outcomes remain pending liabilities.
- Default project tests are offline and exclude live, network, and paid tests.
- `benchmark` refuses provider calls unless `--live` is explicit.

## Configuration Resolution

1. `--config`
2. `MODEL_ORCHESTRA_CONFIG`
3. `~/.config/model-orchestra/config.json`

Run `model-orchestra migrate-config --from legacy.json --output config.json` to add and validate schema version 1 for a compatible legacy configuration.
