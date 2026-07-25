# Model Orchestra v0.2 Alpha

## Install

```bash
python -m pip install model-orchestra==0.2.0a1
model-orchestra check --config path/to/config.json
model-orchestra doctor --config path/to/config.json
```

Start from the packaged `config.example.json`. It contains generic OpenAI-compatible and Anthropic-compatible providers and references environment-variable names only.

Maintainers build byte-reproducible wheel and sdist artifacts with:

```bash
python tools/build_release.py
```

## MCP stdio command

Use the same command in Hermes, Claude Code, or MCP Inspector:

```text
model-orchestra serve --config /absolute/path/to/config.json
```

Example MCP registration shape:

```json
{
  "mcpServers": {
    "model-orchestra": {
      "command": "model-orchestra",
      "args": ["serve", "--config", "/absolute/path/to/config.json"]
    }
  }
}
```

The alpha exposes eight stable tools:

- `list_workers`
- `route_preview`
- `delegate`
- `delegate_verified`
- `orchestrate_change`
- `batch_delegate`
- `orchestration_report`
- `cost_report`

`compact`, `pipeline`, `swarm`, and `auto_delegate` remain experimental and are removed from the public alpha registry.

## Configuration

Resolution order:

1. `--config`
2. `MODEL_ORCHESTRA_CONFIG`
3. `~/.config/model-orchestra/config.json`

Validate without importing the provider runtime:

```bash
model-orchestra check --config config.json
```

Inspect missing credential variable names without printing values:

```bash
model-orchestra doctor --config config.json
```

Migrate a legacy configuration by adding and validating schema version 1:

```bash
model-orchestra migrate-config --from legacy-config.json --output config.json
```

## Live benchmark

No provider call is made without the explicit flag:

```bash
model-orchestra benchmark --config config.json --live
```

The alpha benchmark runs one bounded Python task against caller-owned tests. It consumes provider quota or metered balance.

## Uninstall

```bash
python -m pip uninstall model-orchestra
```

Configuration and budget state live beside the selected user configuration and are not removed automatically.

## Verified support

The release gate requires repeatable stdio discovery against Hermes, Claude Code, and MCP Inspector. Zed ACP remains experimental. A compatible protocol is not itself a support claim; only recorded smoke results count.
